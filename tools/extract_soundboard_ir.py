#!/usr/bin/env python3
"""
tools/extract_soundboard_ir.py
──────────────────────────────
Extract the effective soundboard impulse response by deconvolving
the additive synthesis output from the original WAV recordings.

The IR captures everything the additive model misses:
  - Soundboard body resonance and formants
  - Room character / mic placement
  - String-bridge coupling effects
  - Mechanical noise floor

Architecture:
  original(t) ≈ synth(t) * soundboard_ir(t)
  H(f) = FFT(original) / FFT(synth)
  ir(t) = IFFT(H(f))

Averaged across multiple notes to reduce note-specific artifacts.

Usage:
    python tools/extract_soundboard_ir.py soundbanks-additive/pl-grand-04071611.json \
        --bank C:/SoundBanks/IthacaPlayer/pl-grand \
        --out soundbanks-additive/pl-grand-soundboard.wav
"""

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

REPO_ROOT = Path(__file__).parent.parent.resolve()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from training.modules.exporter import _render_note_rms_ref


def extract_transfer_function(orig: np.ndarray, synth: np.ndarray,
                               sr: int, n_fft: int = 16384) -> np.ndarray:
    """Compute smoothed transfer function H(f) = orig/synth in frequency domain.

    Uses the sustain portion (0.2-2.0s) where both signals are stable.
    Returns complex H(f) array of length n_fft//2+1.
    """
    # Use sustain portion to avoid attack transient differences
    s1 = int(0.2 * sr)
    s2 = min(int(2.0 * sr), len(orig), len(synth))
    if s2 - s1 < n_fft:
        return None

    # Average multiple overlapping frames for stability
    hop = n_fft // 2
    n_frames = max(1, (s2 - s1 - n_fft) // hop)
    window = np.hanning(n_fft)

    H_accum = np.zeros(n_fft // 2 + 1, dtype=np.complex128)
    count = 0

    for i in range(n_frames):
        start = s1 + i * hop
        o_frame = orig[start:start + n_fft].astype(np.float64) * window
        s_frame = synth[start:start + n_fft].astype(np.float64) * window

        O = np.fft.rfft(o_frame)
        S = np.fft.rfft(s_frame)

        # Regularized deconvolution (Wiener-like)
        S_conj = np.conj(S)
        S_power = np.abs(S) ** 2
        eps = np.max(S_power) * 1e-4  # regularization floor
        H = (O * S_conj) / (S_power + eps)

        H_accum += H
        count += 1

    if count == 0:
        return None
    return H_accum / count


def main():
    parser = argparse.ArgumentParser(
        description="Extract soundboard IR from recordings vs synthesis")
    parser.add_argument("soundbank", help="Path to soundbank JSON")
    parser.add_argument("--bank", required=True, help="WAV bank directory")
    parser.add_argument("--out", default=None,
                        help="Output WAV path (default: soundbanks-additive/{name}-soundboard.wav)")
    parser.add_argument("--vel", type=int, default=4, help="Velocity index (default: 4)")
    parser.add_argument("--sr-tag", default="f48", help="SR suffix (default: f48)")
    parser.add_argument("--ir-length-ms", type=float, default=25.0,
                        help="IR length in ms (default: 25 — body resonance only, no echo)")
    args = parser.parse_args()

    bank = json.load(open(args.soundbank))
    bank_dir = Path(args.bank)
    bank_name = Path(args.soundbank).stem

    if args.out is None:
        args.out = str(REPO_ROOT / "soundbanks" / f"{bank_name}-soundboard.wav")

    # Select notes spread across the keyboard
    # Skip extremes (very low bass and very high treble have unusual spectra)
    test_midis = list(range(33, 96, 3))  # every 3rd note from A1 to B6

    notes = bank.get("notes", {})
    sr = None

    print(f"Extracting soundboard IR from {len(test_midis)} notes...")
    print(f"Soundbank: {args.soundbank}")
    print(f"WAV bank:  {args.bank}")
    print()

    H_total = None
    n_good = 0
    n_fft = 16384

    for midi in test_midis:
        key = f"m{midi:03d}_vel{args.vel}"
        if key not in notes:
            continue

        wav_path = bank_dir / f"m{midi:03d}-vel{args.vel}-{args.sr_tag}.wav"
        if not wav_path.exists():
            continue

        n = notes[key]
        orig, orig_sr = sf.read(str(wav_path), dtype="float32")
        if orig.ndim == 2:
            orig = orig.mean(axis=1)
        if sr is None:
            sr = orig_sr
        elif orig_sr != sr:
            continue

        # Render pure partials (no EQ, no noise) — the "dry" additive synth
        synth = _render_note_rms_ref(
            partials=n.get("partials", []),
            phi_diff=n.get("phi_diff", 0.0),
            attack_tau=n.get("attack_tau", 0.05),
            A_noise=0.0,       # no noise — we want only partials
            centroid_hz=3000,
            eq_biquads=[],     # no EQ — soundboard IR replaces this
            midi=midi,
            sr=sr,
            duration=3.0,
        )
        rms_gain = n.get("rms_gain", 1.0)
        synth = synth * rms_gain

        # RMS-normalize both to same level
        orig_rms = np.sqrt(np.mean(orig[:int(2*sr)]**2) + 1e-30)
        synth_rms = np.sqrt(np.mean(synth[:int(2*sr)]**2) + 1e-30)
        if synth_rms < 1e-12:
            continue
        synth = synth * (orig_rms / synth_rms)

        H = extract_transfer_function(orig, synth, sr, n_fft)
        if H is None:
            continue

        if H_total is None:
            H_total = np.zeros_like(H)
        H_total += H
        n_good += 1

        print(f"  MIDI {midi:3d}: OK (H magnitude range: "
              f"{20*np.log10(np.abs(H).max()+1e-15):+.1f} / "
              f"{20*np.log10(np.abs(H).min()+1e-15):+.1f} dB)")

    if n_good == 0 or H_total is None:
        print("ERROR: no valid notes found")
        return

    H_avg = H_total / n_good
    print(f"\nAveraged {n_good} notes")

    # Normalize H so that average magnitude in 500-2000 Hz is unity (0 dB).
    # This preserves the relative spectral shape (body resonance, warmth)
    # while preventing overall level boost / clipping.
    freqs_h = np.fft.rfftfreq((len(H_avg) - 1) * 2, 1.0 / sr)
    norm_mask = (freqs_h >= 2000) & (freqs_h <= 6000)
    if norm_mask.any():
        avg_mag = np.mean(np.abs(H_avg[norm_mask]))
        if avg_mag > 1e-12:
            H_avg /= avg_mag
            print(f"Normalized: 2000-6000 Hz avg magnitude -> 0 dB (was +{20*np.log10(avg_mag):.1f} dB)")

    # Convert to time domain IR
    ir_full = np.fft.irfft(H_avg)

    # Truncate to desired length
    ir_samples = int(args.ir_length_ms * 0.001 * sr)
    ir = ir_full[:ir_samples].astype(np.float32)

    # Normalize peak to prevent clipping (keep < 1.0)
    peak = np.max(np.abs(ir))
    if peak > 0.95:
        ir = ir * (0.95 / peak)

    # Window the tail to avoid truncation artifacts
    fade_len = min(int(0.01 * sr), ir_samples // 4)
    if fade_len > 0:
        fade = np.linspace(1.0, 0.0, fade_len, dtype=np.float32)
        ir[-fade_len:] *= fade

    # Write
    sf.write(args.out, ir, sr)
    print(f"\nWritten: {args.out}")
    print(f"  Length: {len(ir)} samples ({len(ir)/sr*1000:.1f} ms)")
    print(f"  Peak: {np.max(np.abs(ir)):.4f}")
    print(f"  SR: {sr}")

    # Also show the frequency response of the extracted IR
    H_ir = np.fft.rfft(ir, n=n_fft)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    bands = [(100, 250), (250, 500), (500, 1000), (1000, 2000),
             (2000, 4000), (4000, 8000), (8000, 16000)]
    print(f"\n  IR frequency response (avg per band):")
    for lo, hi in bands:
        mask = (freqs >= lo) & (freqs < hi)
        if not mask.any():
            continue
        mag_db = 20 * np.log10(np.abs(H_ir[mask]).mean() + 1e-15)
        bar = "+" * max(0, int(mag_db / 2)) if mag_db > 0 else "-" * max(0, int(-mag_db / 2))
        print(f"    {lo:5d}-{hi:5d} Hz: {mag_db:+6.1f} dB  {bar}")


if __name__ == "__main__":
    main()
