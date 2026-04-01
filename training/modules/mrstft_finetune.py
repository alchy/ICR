"""
training/modules/mrstft_finetune.py
──────────────────────────────────────
Closed-loop MRSTFT fine-tuning of InstrumentProfile.

Public API:
    finetuner = MRSTFTFinetuner()
    model     = finetuner.finetune(model, bank_dir, epochs=200)

Wraps the gradient-based loop from closed_loop_finetune.py.
All fine-tuning logic (mini-batch, cosine LR, best-state tracking) is
preserved exactly; this class just provides a clean callable interface.
"""

import math
import random
import re
import time
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf
import torch
import torch.nn as nn

from training.modules.profile_trainer import InstrumentProfile
from training.mrstft_loss import mrstft
from training.modules.synthesizer import _render_differentiable as _render_diff_fn


# ─────────────────────────────────────────────────────────────────────────────
# MRSTFTFinetuner
# ─────────────────────────────────────────────────────────────────────────────

class MRSTFTFinetuner:
    """
    Fine-tune an InstrumentProfile by minimising MRSTFT vs original WAVs.

    Usage:
        model = MRSTFTFinetuner().finetune(model, bank_dir="path/to/wavs", epochs=200)
    """

    # Default WAV pattern within the bank directory
    WAV_PATTERN = "m*-vel*-f44.wav"

    def finetune(
        self,
        model:        InstrumentProfile,
        bank_dir:     str,
        epochs:       int   = 200,
        lr:           float = 3e-4,
        batch_size:   int   = 8,
        duration:     float = 3.0,
        sr:           int   = 44_100,
        target_rms:   float = 0.06,
        vel_gamma:    float = 0.7,
        noise_level:  float = 1.0,
        beat_scale:   float = 1.0,
        k_max:        int   = 60,
        eval_every:   int   = 20,
        seed:         int   = 42,
        wav_pattern:  str   = None,
    ) -> InstrumentProfile:
        """
        Fine-tune model weights via gradient descent on MRSTFT loss.

        Args:
            model:       InstrumentProfile to fine-tune (modified in-place).
            bank_dir:    Directory with original WAV files.
            epochs:      Number of training epochs.
            lr:          Adam learning rate.
            batch_size:  Notes per gradient step (gradient-accumulated).
            duration:    Proxy render + reference crop in seconds.
            sr:          Sample rate.
            target_rms:  RMS normalisation target.
            vel_gamma:   Velocity curve exponent.
            noise_level: Noise amplitude multiplier.
            beat_scale:  Beat frequency multiplier.
            k_max:       Max partials in proxy (lower = less memory).
            eval_every:  Run full eval every N epochs.
            seed:        Random seed for batch shuffling.
            wav_pattern: Glob pattern for reference WAVs (default: m*-vel*-f44.wav).

        Returns:
            Fine-tuned InstrumentProfile (best checkpoint, eval mode).
        """
        pattern    = wav_pattern or self.WAV_PATTERN
        ref_notes  = self._load_reference_wavs(bank_dir, pattern, sr, duration)

        print(f"MRSTFTFinetuner: {len(ref_notes)} reference notes, "
              f"batch={batch_size}, epochs={epochs}, lr={lr}")

        return self._run_finetune_loop(
            model, ref_notes,
            epochs=epochs, lr=lr, batch_size=batch_size,
            duration=duration, sr=sr, target_rms=target_rms,
            vel_gamma=vel_gamma, noise_level=noise_level,
            beat_scale=beat_scale, k_max=k_max,
            eval_every=eval_every, seed=seed,
        )

    # ── Reference WAV loading ─────────────────────────────────────────────────

    _WAV_RE = re.compile(r"m(\d+)-vel(\d+)-.*\.wav", re.IGNORECASE)

    def _load_reference_wavs(
        self, bank_dir: str, pattern: str, sr: int, duration: float
    ) -> list:
        bank = Path(bank_dir)
        if not bank.is_dir():
            raise FileNotFoundError(f"Bank directory not found: {bank}")

        ref_notes = []
        skipped   = 0
        for path in sorted(bank.glob(pattern)):
            m = self._WAV_RE.match(path.name)
            if not m:
                continue
            midi, vel = int(m.group(1)), int(m.group(2))
            wav = self._load_wav_mono(path, sr, duration)
            if wav is None:
                skipped += 1
                continue
            ref_notes.append((midi, vel, wav))

        print(f"Loaded {len(ref_notes)} reference WAVs"
              + (f" ({skipped} skipped)" if skipped else ""))
        return sorted(ref_notes)

    def _load_wav_mono(
        self, path: Path, sr: int, duration: float
    ) -> Optional[torch.Tensor]:
        try:
            audio, file_sr = sf.read(str(path), dtype="float32", always_2d=True)
        except Exception:
            return None

        mono = audio.mean(axis=1)

        # Resample if needed
        if file_sr != sr:
            try:
                import resampy
                mono = resampy.resample(mono, file_sr, sr)
            except ImportError:
                if file_sr % sr == 0:
                    mono = mono[::file_sr//sr]
                elif sr % file_sr == 0:
                    mono = np.repeat(mono, sr//file_sr)

        # Crop / zero-pad to exact duration
        n = int(duration*sr)
        if len(mono) >= n:
            mono = mono[:n]
        else:
            mono = np.concatenate([mono, np.zeros(n-len(mono), dtype=np.float32)])

        return torch.from_numpy(mono)

    # ── Fine-tuning loop (original closed_loop_finetune.finetune logic) ───────

    def _run_finetune_loop(
        self,
        model:       InstrumentProfile,
        ref_notes:   list,
        epochs:      int,
        lr:          float,
        batch_size:  int,
        duration:    float,
        sr:          int,
        target_rms:  float,
        vel_gamma:   float,
        noise_level: float,
        beat_scale:  float,
        k_max:       int,
        eval_every:  int,
        seed:        int,
    ) -> InstrumentProfile:
        if not ref_notes:
            raise ValueError("No reference notes — cannot fine-tune.")

        optimizer = torch.optim.Adam(model.parameters(), lr=lr)
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
            optimizer, T_max=epochs, eta_min=lr*0.05
        )

        n_notes  = len(ref_notes)
        batch_sz = min(batch_size, n_notes)
        rng      = random.Random(seed)

        # Initial evaluation
        print(f"\n[epoch 0 / {epochs}] initial eval:")
        mean0      = self._evaluate(model, ref_notes, sr, duration,
                                    noise_level, target_rms, vel_gamma, k_max)
        best_loss  = mean0
        best_state = {k: v.clone() for k, v in model.state_dict().items()}

        for epoch in range(1, epochs+1):
            model.train()
            shuffled  = list(ref_notes)
            rng.shuffle(shuffled)
            n_steps   = math.ceil(n_notes / batch_sz)
            step_sum  = 0.0
            t0        = time.time()

            for step in range(n_steps):
                batch = shuffled[step*batch_sz : (step+1)*batch_sz]
                if not batch:
                    continue

                optimizer.zero_grad()
                step_loss = torch.zeros((), dtype=torch.float32)
                n_valid   = 0

                for midi, vel, ref_wav in batch:
                    try:
                        pred = _render_diff_fn(
                            model, midi, vel,
                            sr=sr, duration=duration,
                            beat_scale=beat_scale, noise_level=noise_level,
                            target_rms=target_rms, vel_gamma=vel_gamma,
                            k_max=k_max, rng_seed=epoch,
                        )
                        loss_i = mrstft(pred, ref_wav.to(pred.device))
                        (loss_i / len(batch)).backward()
                        step_loss = step_loss + loss_i.detach()
                        n_valid  += 1
                    except Exception as exc:
                        print(f"  WARN step {step} m{midi:03d}v{vel}: {exc}")

                if n_valid > 0:
                    nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                    optimizer.step()
                    step_sum += (step_loss/n_valid).item()

            scheduler.step()

            if epoch % 10 == 0 or epoch == epochs:
                avg = step_sum/n_steps if n_steps else float("nan")
                print(f"[epoch {epoch:4d}/{epochs}] "
                      f"loss={avg:.4f}  lr={scheduler.get_last_lr()[0]:.2e}  "
                      f"t={time.time()-t0:.1f}s")

            if epoch % eval_every == 0 or epoch == epochs:
                print(f"\n[epoch {epoch} eval]")
                mean = self._evaluate(model, ref_notes, sr, duration,
                                      noise_level, target_rms, vel_gamma, k_max)
                if mean < best_loss:
                    best_loss  = mean
                    best_state = {k: v.clone() for k, v in model.state_dict().items()}
                    print(f"  new best: {best_loss:.4f}")

        # Restore best weights
        model.load_state_dict(best_state)
        print(f"\nRestored best model (MRSTFT={best_loss:.4f})")
        model.eval()
        return model

    def _evaluate(
        self,
        model:       InstrumentProfile,
        ref_notes:   list,
        sr:          int,
        duration:    float,
        noise_level: float,
        target_rms:  float,
        vel_gamma:   float,
        k_max:       int,
    ) -> float:
        """Compute mean MRSTFT over all reference notes (no gradient)."""
        model.eval()
        losses = []
        with torch.no_grad():
            for midi, vel, ref_wav in ref_notes:
                try:
                    pred = _render_diff_fn(
                        model, midi, vel,
                        sr=sr, duration=duration,
                        noise_level=noise_level,
                        target_rms=target_rms, vel_gamma=vel_gamma, k_max=k_max,
                    )
                    losses.append(mrstft(pred, ref_wav.to(pred.device)).item())
                except Exception as exc:
                    print(f"  WARN m{midi:03d} vel{vel}: {exc}")
                    losses.append(float("nan"))

        valid = [l for l in losses if not math.isnan(l)]
        mean  = float(np.mean(valid)) if valid else float("nan")
        print(f"  mean MRSTFT = {mean:.4f}  ({len(valid)}/{len(losses)} notes)")
        model.train()
        return mean
