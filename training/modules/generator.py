"""
training/modules/generator.py
──────────────────────────────
Generate WAV sample banks from an InstrumentProfile or a params dict.

Public API:
    gen = SampleGenerator()
    gen.generate_bank(source, out_dir, midi_range=(21,108), vel_count=8, ...)
    wav = gen.generate_note(source, midi=60, vel=3, ...)
"""

import math
from pathlib import Path

import numpy as np
import scipy.io.wavfile as wavfile

from training.modules.synthesizer import Synthesizer


# ─────────────────────────────────────────────────────────────────────────────
# SampleGenerator
# ─────────────────────────────────────────────────────────────────────────────

class SampleGenerator:
    """
    Generate WAV sample banks from a trained model or extracted params dict.

    The ``source`` argument accepts:
      - An InstrumentProfile (torch.nn.Module) — NN predictions are used.
      - A params dict (keys: samples, …) — real extracted params are used.

    Usage:
        gen = SampleGenerator()

        # From NN model
        gen.generate_bank(model, out_dir="generated/ks-grand/")

        # From params dict
        gen.generate_bank(params, out_dir="generated/ks-grand/")

        # Single note
        audio = gen.generate_note(model, midi=60, vel=3)
    """

    def generate_bank(
        self,
        source,
        out_dir:    str,
        midi_range: tuple = (21, 108),
        vel_count:  int   = 8,
        sr:         int   = 44_100,
        duration:   float = 3.0,
        beat_scale: float = 1.0,
        noise_level: float = 1.0,
        eq_strength: float = 1.0,
        target_rms:  float = 0.06,
    ) -> None:
        """
        Render a full WAV sample bank to out_dir.

        Output filename format:  m{midi:03d}_vel{vel}.wav

        Args:
            source:     InstrumentProfile or params dict.
            out_dir:    Directory to write WAV files into.
            midi_range: (lo, hi) inclusive MIDI note range.
            vel_count:  Number of velocity layers (0 … vel_count-1).
            sr:         Sample rate for the output WAVs.
            duration:   Render duration per note in seconds.
            beat_scale: Beat frequency multiplier.
            noise_level: Attack noise amplitude multiplier.
            eq_strength: Spectral EQ blend [0=off, 1=full].
            target_rms:  RMS normalisation target.
        """
        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        midi_lo, midi_hi = midi_range
        total = (midi_hi - midi_lo + 1) * vel_count
        done  = 0

        for midi in range(midi_lo, midi_hi + 1):
            for vel in range(vel_count):
                audio = self.generate_note(
                    source, midi=midi, vel=vel,
                    sr=sr, duration=duration,
                    beat_scale=beat_scale, noise_level=noise_level,
                    eq_strength=eq_strength, target_rms=target_rms,
                )

                wav_file = out_path / f"m{midi:03d}_vel{vel}.wav"
                self._write_wav(wav_file, audio, sr)

                done += 1
                if done % 88 == 0 or done == total:
                    print(f"  {done}/{total} notes rendered …")

        print(f"generate_bank: {done} files → {out_dir}")

    def generate_note(
        self,
        source,
        midi:        int,
        vel:         int,
        sr:          int   = 44_100,
        duration:    float = 3.0,
        beat_scale:  float = 1.0,
        noise_level: float = 1.0,
        eq_strength: float = 1.0,
        target_rms:  float = 0.06,
        **synth_params,
    ) -> np.ndarray:
        """
        Render a single note to a (N, 2) float32 stereo array.

        Args:
            source: InstrumentProfile or params dict.
            midi:   MIDI note number.
            vel:    Velocity index 0–7.

        Returns:
            np.ndarray of shape (N, 2), dtype float32.
        """
        sample_dict = self._get_sample_dict(source, midi, vel, sr)

        return Synthesizer().render(
            sample_dict,
            midi=midi, vel=vel,
            sr=sr, duration=duration,
            beat_scale=beat_scale,
            noise_level=noise_level,
            eq_strength=eq_strength,
            target_rms=target_rms,
            **synth_params,
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get_sample_dict(self, source, midi: int, vel: int, sr: int) -> dict:
        """
        Resolve `source` to a sample dict for (midi, vel).

        If source is a params dict, look up the key directly.
        If source is an InstrumentProfile, run NN inference.
        """
        # Duck-type: params dict has a "samples" key
        if isinstance(source, dict) and "samples" in source:
            key = f"m{midi:03d}_vel{vel}"
            if key not in source["samples"]:
                raise KeyError(f"Key {key} not found in params dict")
            return source["samples"][key]

        # Otherwise assume InstrumentProfile (torch.nn.Module)
        return self._nn_predict(source, midi, vel, sr)

    def _nn_predict(self, model, midi: int, vel: int, sr: int) -> dict:
        """Run NN inference for one (midi, vel) and return a sample dict."""
        import torch
        from training.modules.profile_trainer import (
            InstrumentProfile, generate_profile, build_dataset,
        )

        # generate_profile needs a dataset for eq_freqs; we pass an empty one.
        ds      = {"batches": {}, "eq_freqs": None}
        samples = generate_profile(
            model, ds,
            midi_from=midi, midi_to=midi,
            sr=sr, orig_samples=None,
        )
        key = f"m{midi:03d}_vel{vel}"
        if key not in samples:
            raise KeyError(f"NN prediction returned no entry for {key}")
        return samples[key]

    def _write_wav(self, path: Path, audio: np.ndarray, sr: int) -> None:
        """Write (N, 2) float32 stereo array to a WAV file."""
        # scipy.io.wavfile expects int16 or float32 array
        audio_clipped = np.clip(audio, -1.0, 1.0)
        wavfile.write(str(path), sr, audio_clipped)
