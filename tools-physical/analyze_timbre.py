"""
tools-physical/analyze_timbre.py
─────────────────────────────────
Compare harmonic structure of real piano samples vs physical model output.

Usage:
    python tools-physical/analyze_timbre.py \
        --ref "C:/SoundBanks/IthacaPlayer/ks-grand" \
        --synth tmp_audio/phys_test \
        --midi 60 --vel 3 --sr-tag f44

Produces:
    - Harmonic amplitude comparison (first 20 partials)
    - Spectral envelope comparison
    - Attack/sustain spectral tilt comparison
    - Odd/even ratio comparison
"""

import argparse
import struct
import sys
import os
import numpy as np
from pathlib import Path


def read_wav(path):
    """Read WAV file, return (samples_mono, sr)."""
    with open(path, 'rb') as f:
        f.read(4)  # RIFF
        f.read(4)  # size
        f.read(4)  # WAVE
        sr = 44100
        channels = 1
        bits = 16
        fmt_tag = 1
        while True:
            chunk_id = f.read(4)
            if len(chunk_id) < 4:
                break
            chunk_size = struct.unpack('<I', f.read(4))[0]
            if chunk_id == b'fmt ':
                fmt_tag = struct.unpack('<H', f.read(2))[0]
                channels = struct.unpack('<H', f.read(2))[0]
                sr = struct.unpack('<I', f.read(4))[0]
                f.read(6)  # byte_rate + block_align
                bits = struct.unpack('<H', f.read(2))[0]
                if chunk_size > 16:
                    f.read(chunk_size - 16)
            elif chunk_id == b'data':
                raw = f.read(chunk_size)
                break
            else:
                f.read(chunk_size)

        if fmt_tag == 3 and bits == 32:
            samples = np.frombuffer(raw, dtype=np.float32)
        elif fmt_tag == 1 and bits == 16:
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        else:
            raise ValueError(f"Unsupported format: tag={fmt_tag} bits={bits}")

        if channels == 2:
            samples = (samples[0::2] + samples[1::2]) / 2.0
        return samples, sr


def analyze_harmonics(samples, sr, f0, n_harmonics=20, window_start_s=0.05,
                      window_dur_s=0.5, label=""):
    """Extract harmonic amplitudes from a windowed FFT."""
    s0 = int(window_start_s * sr)
    s1 = int((window_start_s + window_dur_s) * sr)
    s1 = min(s1, len(samples))
    if s1 - s0 < 256:
        return np.zeros(n_harmonics)

    chunk = samples[s0:s1] * np.hanning(s1 - s0)
    fft = np.abs(np.fft.rfft(chunk))
    freqs = np.fft.rfftfreq(len(chunk), 1.0 / sr)

    amps = np.zeros(n_harmonics)
    for k in range(n_harmonics):
        f_target = f0 * (k + 1)
        # Find peak near target frequency (±3% tolerance)
        mask = (freqs > f_target * 0.97) & (freqs < f_target * 1.03)
        if np.any(mask):
            amps[k] = np.max(fft[mask])

    # Normalize to fundamental
    if amps[0] > 0:
        amps = amps / amps[0]

    return amps


def spectral_tilt(samples, sr, f0, window_start_s=0.05, window_dur_s=0.5):
    """Compute spectral tilt (dB/octave) from harmonic amplitudes."""
    amps = analyze_harmonics(samples, sr, f0, 20, window_start_s, window_dur_s)
    amps_db = 20 * np.log10(amps + 1e-10)

    # Linear fit in log-frequency space
    k_vals = np.arange(1, len(amps) + 1)
    log_k = np.log2(k_vals)
    valid = amps > 1e-6
    if np.sum(valid) < 3:
        return 0.0
    slope, _ = np.polyfit(log_k[valid], amps_db[valid], 1)
    return slope  # dB/octave


def odd_even_ratio(amps):
    """Ratio of odd harmonic energy to even harmonic energy."""
    odd = np.sum(amps[0::2] ** 2)   # k=1,3,5,...
    even = np.sum(amps[1::2] ** 2)  # k=2,4,6,...
    if even < 1e-20:
        return float('inf')
    return odd / even


def main():
    parser = argparse.ArgumentParser(description="Compare real vs synth timbre")
    parser.add_argument("--ref", required=True, help="Reference WAV bank directory")
    parser.add_argument("--synth", default=None, help="Synth WAV directory (from icr batch render)")
    parser.add_argument("--midi", type=int, nargs="+", default=[36, 48, 60, 72, 84])
    parser.add_argument("--vel", type=int, default=3, help="Velocity index (0-7)")
    parser.add_argument("--sr-tag", default="f44")
    parser.add_argument("--bank", default=None, help="Physical bank JSON to render synth")
    args = parser.parse_args()

    # If synth dir not provided, render from bank
    if args.synth is None and args.bank:
        import json
        import subprocess
        args.synth = "tmp_audio/timbre_analysis"
        os.makedirs(args.synth, exist_ok=True)
        batch = [{"midi": m, "vel_idx": args.vel, "duration_s": 3.0} for m in args.midi]
        batch_path = os.path.join(args.synth, "batch.json")
        with open(batch_path, 'w') as f:
            json.dump(batch, f)
        icr = str(Path(__file__).parent.parent / "build" / "bin" / "Release" / "icr.exe")
        sr = 44100 if args.sr_tag == "f44" else 48000
        subprocess.run([icr, "--core", "PhysicalModelingPianoCore",
                        "--params", args.bank,
                        "--render-batch", batch_path,
                        "--out-dir", args.synth,
                        "--sr", str(sr)], check=True)

    print(f"{'MIDI':>5} {'f0':>7} | {'Ref tilt':>9} {'Syn tilt':>9} {'Diff':>6} | "
          f"{'Ref O/E':>8} {'Syn O/E':>8} | {'Attack':>7}")
    print("-" * 80)

    for midi in args.midi:
        f0 = 440.0 * 2.0 ** ((midi - 69) / 12.0)

        # Reference WAV
        ref_path = os.path.join(args.ref, f"m{midi:03d}-vel{args.vel}-{args.sr_tag}.wav")
        if not os.path.exists(ref_path):
            print(f"{midi:>5} {f0:>7.1f} | (reference not found: {ref_path})")
            continue
        ref_samples, ref_sr = read_wav(ref_path)

        # Synth WAV
        syn_path = None
        if args.synth:
            sr_k = 44 if args.sr_tag == "f44" else 48
            syn_path = os.path.join(args.synth, f"m{midi:03d}-v{args.vel:02d}-f{sr_k}.wav")
            if not os.path.exists(syn_path):
                syn_path = None

        # Reference analysis
        ref_amps_attack = analyze_harmonics(ref_samples, ref_sr, f0, 20, 0.01, 0.1)
        ref_amps_sustain = analyze_harmonics(ref_samples, ref_sr, f0, 20, 0.3, 0.5)
        ref_tilt = spectral_tilt(ref_samples, ref_sr, f0, 0.05, 0.5)
        ref_oe = odd_even_ratio(ref_amps_sustain)

        # Attack brightness (HF energy in first 50ms vs 300-800ms)
        ref_attack_tilt = spectral_tilt(ref_samples, ref_sr, f0, 0.01, 0.05)

        if syn_path:
            syn_samples, syn_sr = read_wav(syn_path)
            syn_tilt = spectral_tilt(syn_samples, syn_sr, f0, 0.05, 0.5)
            syn_amps = analyze_harmonics(syn_samples, syn_sr, f0, 20, 0.3, 0.5)
            syn_oe = odd_even_ratio(syn_amps)
            syn_attack = spectral_tilt(syn_samples, syn_sr, f0, 0.01, 0.05)

            diff = syn_tilt - ref_tilt
            print(f"{midi:>5} {f0:>7.1f} | {ref_tilt:>+9.1f} {syn_tilt:>+9.1f} {diff:>+6.1f} | "
                  f"{ref_oe:>8.1f} {syn_oe:>8.1f} | {syn_attack-ref_attack_tilt:>+7.1f}")
        else:
            print(f"{midi:>5} {f0:>7.1f} | {ref_tilt:>+9.1f} {'---':>9} {'---':>6} | "
                  f"{ref_oe:>8.1f} {'---':>8} | {'---':>7}")

        # Detailed harmonic comparison
        if syn_path and midi == 60:
            print(f"\n  Harmonic detail (MIDI 60, sustain 0.3-0.8s):")
            print(f"  {'k':>3} {'Ref dB':>7} {'Syn dB':>7} {'Diff':>6}")
            ref_db = 20 * np.log10(ref_amps_sustain + 1e-10)
            syn_db = 20 * np.log10(syn_amps + 1e-10)
            for k in range(min(15, len(ref_db))):
                d = syn_db[k] - ref_db[k]
                marker = " **" if abs(d) > 6 else ""
                print(f"  {k+1:>3} {ref_db[k]:>+7.1f} {syn_db[k]:>+7.1f} {d:>+6.1f}{marker}")
            print()


if __name__ == "__main__":
    main()
