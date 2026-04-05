"""
training/modules/icr_round_trip.py
────────────────────────────────────
ICR round-trip processor: render measured notes through ICR.exe, re-extract
params, return corrected training targets.

Motivation
──────────
Direct MSE training (NN output vs extracted params) ignores systematic offsets
introduced by the ICR synthesis model. If ICR renders tau1=0.41 as a decay that
the extractor measures as 0.38, the NN should learn to predict 0.38 — not 0.41.

Round-trip corrected targets capture this offset automatically:

    params_smooth  →  ICR render  →  WAV  →  extract  →  params_rt
                                                              ↕ MSE
                                                          (training target)

The NN learns to predict params_rt — values consistent with ICR's synthesis model.
This corrects the systematic offset between what the extractor measures from the real
piano (smooth_params) and what it measures from ICR-rendered audio of those same params
(params_rt). The NN output is thus calibrated to ICR's operating range, not to raw
physical measurements.

Render duration
───────────────
Each note is rendered for its full natural duration (duration_s from smooth_params)
rather than a fixed short window. This is critical for:
  - DF (beating) estimation: frequency resolution = 1/duration; short renders cannot
    resolve slow beat frequencies (< 0.3 Hz) common in the bass register.
  - tau2 estimation: bi-exponential fitting requires t[-1]*0.9 > tau2; a 3 s render
    caps tau2_max at 2.7 s, which is too short for bass notes with tau2 ~ 8–15 s.

EQ handling
───────────
spectral_eq / eq_biquads are excluded from the round-trip (neutral EQ used for
rendering). The EQ head will be handled as a separate training branch later.
Round-trip params keep spectral_eq from the smooth_params input unchanged.

Usage
─────
    from training.modules.icr_round_trip import ICRRoundTripProcessor

    rt = ICRRoundTripProcessor(icr_exe="build/bin/Release/icr.exe", sr=48000)
    params_rt = rt.process(params_smooth, workers=8)
    # params_rt has same structure as params_smooth, physical params corrected
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np


# ── Neutral EQ helpers ────────────────────────────────────────────────────────

def _strip_eq(sample: dict) -> dict:
    """Return a copy of sample with spectral_eq removed (neutral rendering)."""
    s = copy.copy(sample)
    s.pop("spectral_eq", None)
    return s


# ── ICRRoundTripProcessor ─────────────────────────────────────────────────────

class ICRRoundTripProcessor:
    """
    Render all measured notes through ICR with neutral EQ, re-extract params,
    return round-trip corrected training targets.

    Args:
        icr_exe:  Path to ICR.exe binary.
        sr:       Sample rate (must match sr_tag).
        sr_tag:   SR tag used for output WAV naming (default: "f48").
        note_dur: Duration in seconds for each rendered note (default: 3.0).
    """

    ICR_TIMEOUT_S = 600

    def __init__(
        self,
        icr_exe:  str,
        sr:       int   = 48000,
        sr_tag:   str   = "f48",
        note_dur: float = 3.0,
    ):
        self.icr_exe  = str(icr_exe)
        self.sr       = sr
        self.sr_tag   = sr_tag
        self.note_dur = note_dur

    def process(self, params_smooth: dict, workers: int = None) -> dict:
        """
        Render measured notes from params_smooth through ICR, re-extract params.

        Args:
            params_smooth: Params dict (spline-smoothed measured notes).
            workers:       Parallel worker count for re-extraction (None = auto).

        Returns:
            params_rt — same structure as params_smooth, with physical params
            replaced by round-trip extracted values. spectral_eq is preserved
            unchanged from params_smooth (EQ not part of round-trip).
        """
        measured = {k: v for k, v in params_smooth["notes"].items()
                    if not v.get("is_interpolated")}
        n = len(measured)
        print(f"\n[ICR round-trip] {n} measured notes → ICR render → re-extract")

        tmp_bank = tempfile.mkdtemp(prefix="icr_rt_bank_")
        tmp_wav  = tempfile.mkdtemp(prefix="icr_rt_wav_")
        try:
            # ── 1. Export smooth_params with neutral EQ to temp bank ──────────
            bank_json = os.path.join(tmp_bank, "bank.json")
            self._export_neutral(params_smooth, measured, bank_json)

            # ── 2. Build batch spec for all measured notes ───────────────────
            # Use per-note duration_s from smooth_params (matches original WAV
            # duration) so the extractor sees the full decay and beating signal.
            # Falls back to self.note_dur if duration_s is missing.
            batch_json = os.path.join(tmp_bank, "batch.json")
            batch = [
                {"midi": int(v["midi"]), "vel_idx": int(v["vel"]),
                 "duration_s": float(v.get("duration_s", self.note_dur))}
                for v in measured.values()
            ]
            with open(batch_json, "w") as f:
                json.dump(batch, f)

            # ── 3. ICR render ─────────────────────────────────────────────────
            print(f"  Rendering {n} notes via ICR.exe ...", flush=True)
            t0  = time.time()
            cmd = [
                self.icr_exe,
                "--core",         "PianoCore",
                "--params",       bank_json,
                "--render-batch", batch_json,
                "--out-dir",      tmp_wav,
                "--sr",           str(self.sr),
            ]
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=self.ICR_TIMEOUT_S,
            )
            elapsed = time.time() - t0
            if result.stdout:
                for line in result.stdout.decode("utf-8", errors="replace").splitlines():
                    print(f"  [ICR] {line}")
            if result.returncode != 0:
                raise RuntimeError(
                    f"ICR round-trip render failed (exit {result.returncode})"
                )
            print(f"  Render done: {elapsed:.1f}s", flush=True)

            # ── 4. Rename WAVs: m060_vel3.wav → m060-vel3-f48.wav ────────────
            self._rename_wavs(tmp_wav)

            # ── 5. Re-extract params from rendered WAVs ───────────────────────
            from training.modules.extractor import ParamExtractor
            print(f"  Re-extracting params from rendered WAVs ...", flush=True)
            params_rt_raw = ParamExtractor().extract_bank(
                tmp_wav, workers=workers, sr_tag=self.sr_tag
            )

            # ── 6. Merge: round-trip physical params + original spectral_eq ──
            params_rt = self._merge(params_smooth, params_rt_raw)
            n_rt = len([k for k, v in params_rt["notes"].items()
                        if not v.get("is_interpolated")])
            print(f"  Round-trip complete: {n_rt}/{n} notes extracted")
            return params_rt

        finally:
            shutil.rmtree(tmp_bank, ignore_errors=True)
            shutil.rmtree(tmp_wav,  ignore_errors=True)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _export_neutral(
        self,
        params_smooth: dict,
        measured:      dict,
        out_path:      str,
    ) -> None:
        """Export measured notes with neutral EQ to a minimal bank JSON."""
        from training.modules.exporter import SoundbankExporter, SR_DEFAULT, \
            DURATION_DEFAULT, TARGET_RMS_DEFAULT, RNG_SEED_DEFAULT

        # Strip spectral_eq before export so ICR applies no EQ
        neutral_samples = {k: _strip_eq(v) for k, v in measured.items()}
        neutral_params  = {**params_smooth, "notes": neutral_samples}

        SoundbankExporter().from_params(
            neutral_params, out_path,
            sr=self.sr,
        )

    def _rename_wavs(self, wav_dir: str) -> None:
        """
        Rename ICR output WAVs from m060_vel3.wav to m060-vel3-f48.wav
        so ParamExtractor can find them.
        """
        import re
        pattern = re.compile(r"m(\d+)_vel(\d+)\.wav", re.IGNORECASE)
        for p in Path(wav_dir).glob("m*_vel*.wav"):
            m = pattern.match(p.name)
            if m:
                midi, vel = int(m.group(1)), int(m.group(2))
                new_name = f"m{midi:03d}-vel{vel}-{self.sr_tag}.wav"
                p.rename(p.parent / new_name)

    def _merge(self, params_smooth: dict, params_rt_raw: dict) -> dict:
        """
        Build merged params dict:
        - Physical params (tau1, tau2, A0, B, f0_hz, partials, noise, …)
          from round-trip extraction (params_rt_raw).
        - spectral_eq: preserved from params_smooth (EQ not in round-trip scope).
        - Notes absent from round-trip fall back to params_smooth values
          (extraction may fail on very quiet notes at extremes).
        """
        merged_samples = {}

        for key, s_smooth in params_smooth["notes"].items():
            s_rt = params_rt_raw["notes"].get(key)

            if s_rt is None:
                # Round-trip extraction failed for this note — keep smooth
                merged_samples[key] = copy.deepcopy(s_smooth)
                continue

            # Round-trip physical params as base
            merged = copy.deepcopy(s_rt)

            # Preserve spectral_eq from smooth (EQ handled separately)
            if "spectral_eq" in s_smooth:
                merged["spectral_eq"] = copy.deepcopy(s_smooth["spectral_eq"])
            else:
                merged.pop("spectral_eq", None)

            # Preserve is_interpolated flag
            if "is_interpolated" in s_smooth:
                merged["is_interpolated"] = s_smooth["is_interpolated"]

            merged_samples[key] = merged

        return {**params_smooth, "notes": merged_samples}
