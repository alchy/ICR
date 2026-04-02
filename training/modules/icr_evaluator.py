"""
training/modules/icr_evaluator.py
───────────────────────────────────
ICR-based MRSTFT evaluator for the icr-eval training pipeline.

Uses ICR.exe --render-batch to synthesize a representative set of notes
via the C++ PianoCore, then computes MRSTFT against the original WAV files.
This is the ground-truth perceptual metric — identical to what the user hears.

Public API:
    evaluator = ICRBatchEvaluator(icr_exe, bank_dir, ...)
    score     = evaluator.eval(model, params)   # float — lower is better
    evaluator.close()
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf
import torch

from training.modules.exporter import SoundbankExporter
from training.mrstft_loss import mrstft_numpy


# ── Constants ────────────────────────────────────────────────────────────────

_WAV_RE = re.compile(r"m(\d+)-vel(\d+)-.*\.wav", re.IGNORECASE)
_RENDERED_RE = re.compile(r"m(\d+)_vel(\d+)\.wav", re.IGNORECASE)


class ICRBatchEvaluator:
    """
    Evaluates a trained InstrumentProfileEncExp by rendering notes through
    ICR.exe and computing MRSTFT against original reference WAVs.

    Non-differentiable — used only for eval/early-stopping, not for training.

    Args:
        icr_exe:      Path to ICR.exe binary.
        bank_dir:     Directory with original reference WAV files.
        sr:           Sample rate (must match reference WAVs).
        eval_midi:    12 MIDI note numbers to evaluate (auto-selected if None).
        eval_vels:    Velocity indices to evaluate, e.g. [0, 5].
        note_dur:     Duration in seconds for each rendered note.
        out_dir:      Directory for rendered WAVs (None = auto temp, cleaned up).
        sr_tag:       WAV filename suffix, e.g. "f48" or "f44".
    """

    WAV_PATTERN          = "m*-vel*-f48.wav"
    WAV_PATTERN_FALLBACK = "m*-vel*-f44.wav"
    N_EVAL_MIDI          = 12
    ICR_TIMEOUT_S        = 120   # max seconds for one renderBatch call

    def __init__(
        self,
        icr_exe:   str,
        bank_dir:  str,
        sr:        int          = 48000,
        eval_midi: list         = None,
        eval_vels: list         = None,
        note_dur:  float        = 3.0,
        out_dir:   str          = None,
        sr_tag:    str          = "f48",
    ):
        self.icr_exe  = str(Path(icr_exe).resolve())
        self.bank_dir = bank_dir
        self.sr       = sr
        self.note_dur = note_dur
        self.sr_tag   = sr_tag
        self.eval_vels = eval_vels or [0, 5]

        self._auto_out = out_dir is None
        self._out_dir  = out_dir or tempfile.mkdtemp(prefix="icr_eval_")

        # Load reference WAVs once
        self._refs: dict[tuple[int, int], np.ndarray] = {}
        self._load_references(sr_tag)

        # Select eval notes from available refs
        self._eval_notes = self._select_eval_notes(eval_midi)

        if not self._eval_notes:
            raise RuntimeError(
                f"ICRBatchEvaluator: no eval notes found in {bank_dir}. "
                f"Check sr_tag='{sr_tag}' and that reference WAVs exist."
            )

        print(f"ICRBatchEvaluator: {len(self._eval_notes)} eval notes "
              f"({len(set(m for m,_ in self._eval_notes))} MIDI x "
              f"{len(set(v for _,v in self._eval_notes))} vel)  "
              f"dur={note_dur}s  sr={sr}")

    # ── Public API ────────────────────────────────────────────────────────────

    def eval(self, model, params: dict) -> float:
        """
        Export model to temp soundbank, render via ICR.exe, compute MRSTFT.

        Returns mean ICR-MRSTFT over all eval notes (lower = better).
        Returns float('inf') on render failure.
        """
        with tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, prefix="icr_bank_"
        ) as tf:
            bank_path = tf.name

        batch_path = bank_path.replace(".json", "_batch.json")
        try:
            # 1. Export current model to temp soundbank
            SoundbankExporter().hybrid(model, params, bank_path)

            # 2. Write batch spec
            batch = [
                {"midi": midi, "vel_idx": vel_idx, "duration_s": self.note_dur}
                for midi, vel_idx in self._eval_notes
            ]
            with open(batch_path, "w") as f:
                json.dump(batch, f)

            # 3. Run ICR.exe --render-batch
            os.makedirs(self._out_dir, exist_ok=True)
            print(f"    ICR eval: rendering {len(self._eval_notes)} notes "
                  f"via {os.path.basename(self.icr_exe)} ...", flush=True)
            cmd = [
                self.icr_exe,
                "--core",         "PianoCore",
                "--params",       bank_path,
                "--render-batch", batch_path,
                "--out-dir",      self._out_dir,
                "--sr",           str(self.sr),
            ]
            t0 = time.time()
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=self.ICR_TIMEOUT_S,
            )
            elapsed = time.time() - t0

            # Print ICR output (flows into training log via _Tee)
            if result.stdout:
                for line in result.stdout.decode("utf-8", errors="replace").splitlines():
                    print(f"    [ICR] {line}")

            if result.returncode != 0:
                print(f"    ICR render failed (exit {result.returncode}) in {elapsed:.1f}s")
                return float("inf")

            # 4. Compute MRSTFT
            scores = []
            n_samples = int(self.note_dur * self.sr)
            for midi, vel_idx in self._eval_notes:
                wav_name = f"m{midi:03d}_vel{vel_idx}.wav"
                wav_path = os.path.join(self._out_dir, wav_name)
                if not os.path.exists(wav_path):
                    continue

                try:
                    rendered, fsr = sf.read(wav_path, dtype="float32", always_2d=False)
                except Exception:
                    continue

                ref = self._refs.get((midi, vel_idx))
                if ref is None:
                    continue

                # Align to same length
                n = min(len(rendered), len(ref), n_samples)
                if n < 256:
                    continue

                score = mrstft_numpy(rendered[:n], ref[:n])
                scores.append(score)

            if not scores:
                print("    ICR-MRSTFT: no valid renders")
                return float("inf")

            mean_score = float(np.mean(scores))
            print(f"    ICR-MRSTFT = {mean_score:.4f}  "
                  f"({len(scores)}/{len(self._eval_notes)} notes, {elapsed:.1f}s)")
            return mean_score

        finally:
            for p in (bank_path, batch_path):
                try:
                    os.unlink(p)
                except OSError:
                    pass

    def close(self):
        """Remove temp output directory if it was auto-created."""
        if self._auto_out and os.path.isdir(self._out_dir):
            shutil.rmtree(self._out_dir, ignore_errors=True)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    # ── Internals ─────────────────────────────────────────────────────────────

    def _load_references(self, sr_tag: str):
        bank = Path(self.bank_dir)
        pattern  = f"m*-vel*-{sr_tag}.wav"
        fallback = "m*-vel*-f44.wav" if sr_tag != "f44" else "m*-vel*-f48.wav"

        paths = sorted(bank.glob(pattern))
        if not paths:
            paths = sorted(bank.glob(fallback))
        if not paths:
            paths = sorted(bank.glob("m*-vel*-*.wav"))

        n_samples = int(self.note_dur * self.sr)
        for path in paths:
            m = _WAV_RE.match(path.name)
            if not m:
                continue
            midi, vel = int(m.group(1)), int(m.group(2))
            try:
                audio, fsr = sf.read(str(path), dtype="float32", always_2d=True)
                mono = audio.mean(axis=1)
                if fsr != self.sr:
                    # Simple decimation/repetition fallback
                    if fsr % self.sr == 0:
                        mono = mono[::fsr // self.sr]
                    elif self.sr % fsr == 0:
                        mono = np.repeat(mono, self.sr // fsr)
                # Crop / pad
                if len(mono) >= n_samples:
                    mono = mono[:n_samples]
                else:
                    mono = np.concatenate(
                        [mono, np.zeros(n_samples - len(mono), dtype=np.float32)]
                    )
                self._refs[(midi, vel)] = mono
            except Exception:
                pass

        print(f"ICRBatchEvaluator: loaded {len(self._refs)} reference WAVs "
              f"from {self.bank_dir}")

    def _select_eval_notes(self, eval_midi: Optional[list]) -> list[tuple[int, int]]:
        """
        Select up to N_EVAL_MIDI MIDI notes evenly spaced across available
        reference notes, at each of the eval_vels velocity indices.
        """
        available_midi = sorted(set(m for m, v in self._refs))
        if not available_midi:
            return []

        if eval_midi is None:
            n = self.N_EVAL_MIDI
            if len(available_midi) <= n:
                chosen_midi = available_midi
            else:
                step = (len(available_midi) - 1) / (n - 1)
                chosen_midi = [available_midi[round(i * step)] for i in range(n)]
        else:
            chosen_midi = [m for m in eval_midi if m in set(available_midi)]

        notes = []
        for midi in chosen_midi:
            for vel in self.eval_vels:
                if (midi, vel) in self._refs:
                    notes.append((midi, vel))

        return notes
