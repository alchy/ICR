"""
Analyze spectral EQ response from soundbank.
Shows gains_db curve and actual biquad frequency response for selected notes.
"""
import json
import math
import sys
import numpy as np
import scipy.signal as sig

BANK = "soundbanks-additive/pl-grand.json"
NOTES = ["m048_vel3", "m060_vel3", "m072_vel3", "m084_vel3", "m096_vel3"]

def biquad_response(biquads, freqs_hz, sr):
    """Compute cascade frequency response in dB at given frequencies."""
    mag2 = np.ones(len(freqs_hz))
    for bq in biquads:
        b0, b1, b2 = bq["b"]
        a1, a2 = bq["a"]
        w = 2 * math.pi * np.array(freqs_hz) / sr
        ejw = np.exp(-1j * w)
        ej2w = np.exp(-2j * w)
        H = (b0 + b1*ejw + b2*ej2w) / (1.0 + a1*ejw + a2*ej2w)
        mag2 *= np.abs(H)**2
    return 10 * np.log10(np.maximum(mag2, 1e-20))

def main():
    with open(BANK) as f:
        bank = json.load(f)

    sr = bank["metadata"].get("sr", 44100)
    print(f"Bank: {BANK}  sr={sr}\n")

    test_freqs = [50, 100, 200, 500, 1000, 2000, 4000, 8000, 12000, 16000, 20000]

    for key in NOTES:
        note = bank["notes"].get(key)
        if not note:
            print(f"{key}: not found")
            continue

        midi = note["midi"]
        sw = note.get("stereo_width", 1.0)
        biquads = note.get("eq_biquads", [])
        seq = note.get("spectral_eq", {})
        gains_db = seq.get("gains_db", [])
        freqs_hz = seq.get("freqs_hz", [])

        f0 = note.get("f0_hz", 0.0)
        n_str = 1 if midi <= 27 else (2 if midi <= 48 else 3)

        print(f"=== {key}  midi={midi} f0={f0:.1f}Hz  {n_str}-string  stereo_width={sw:.3f}  biquads={len(biquads)} ===")

        # gains_db curve
        if gains_db and freqs_hz:
            g = np.array(gains_db)
            f = np.array(freqs_hz)
            bands = [
                ("  0-500Hz  ", f < 500),
                ("  500-2kHz ", (f >= 500) & (f < 2000)),
                ("  2-5kHz   ", (f >= 2000) & (f < 5000)),
                ("  5-12kHz  ", (f >= 5000) & (f < 12000)),
                ("  12k+Hz   ", f >= 12000),
            ]
            print("  spectral_eq gains_db:")
            for label, mask in bands:
                if mask.any():
                    print(f"    {label}: mean={g[mask].mean():.1f} dB  min={g[mask].min():.1f} dB  max={g[mask].max():.1f} dB")
        else:
            print("  (no spectral_eq)")

        # biquad actual frequency response
        if biquads:
            print("  biquad EQ response at key frequencies:")
            resp = biquad_response(biquads, test_freqs, sr)
            for f_hz, db in zip(test_freqs, resp):
                bar = "#" * max(0, int(db + 20)) + "|" * (1 if abs(db) < 0.5 else 0)
                print(f"    {f_hz:6d} Hz: {db:+6.2f} dB")
        else:
            print("  (no biquad EQ)")

        # Check highest/lowest partial frequencies
        partials = note.get("partials", [])
        if partials:
            f_vals = [p["f_hz"] for p in partials]
            print(f"  partials: k={partials[0]['k']}..{partials[-1]['k']}  f={min(f_vals):.1f}..{max(f_vals):.1f} Hz")
            # Check A0 falloff at high k
            a0_vals = [(p["k"], p["A0"]) for p in partials]
            high_k = [(k, a) for k, a in a0_vals if k >= 40]
            if high_k:
                top_a0 = max(p["A0"] for p in partials)
                print("  High-k partial amplitudes (relative to peak A0):")
                for k, a in high_k[-10:]:
                    rel = a / top_a0 if top_a0 > 0 else 0
                    print(f"    k={k:3d}  A0={a:.4f}  ({rel*100:.2f}% of peak)")

        print()

if __name__ == "__main__":
    main()
