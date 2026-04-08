#!/usr/bin/env python3
"""
tools/apply_laws.py
───────────────────
Apply discovered physical laws to a soundbank — correct all notes
toward the mathematically ideal profile derived from blind scoring.

Three laws (from 27 good-scoring notes, fixed velocity):
  K(midi)        = -0.0084*midi^2 + 0.274*midi + 61.4   (R²=0.954)
  rms_gain(midi) = exp(0.098*midi - 10.84)               (R²=0.893)
  tau1(midi)     = exp(-0.038*midi + 1.27)                (R²=0.672)

Usage:
    python tools/apply_laws.py soundbanks-additive/pl-grand-04072006.json \
        --out soundbanks-additive/pl-grand-laws.json
"""

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np


# ── Physical laws (from blind scoring analysis) ──────────────────────────────

def law_K(midi: int) -> float:
    """Ideal partial count as function of MIDI note."""
    return -0.0084 * midi**2 + 0.274 * midi + 61.4

def law_rms_gain(midi: int) -> float:
    """Ideal RMS gain as function of MIDI note."""
    return math.exp(0.098 * midi - 10.84)

def law_tau1(midi: int) -> float:
    """Ideal prompt decay tau1 as function of MIDI note."""
    return math.exp(-0.038 * midi + 1.27)

# Supporting laws (weaker R² but physically motivated)
def law_A_noise(midi: int) -> float:
    """Ideal noise amplitude as function of MIDI note."""
    return -0.000055 * midi**2 + 0.0102 * midi + 0.476

def law_tau2(midi: int) -> float:
    """Ideal aftersound decay tau2 as function of MIDI note."""
    return math.exp(-0.040 * midi + 3.12)


def main():
    parser = argparse.ArgumentParser(description="Apply physical laws to soundbank")
    parser.add_argument("soundbank", help="Input soundbank JSON")
    parser.add_argument("--out", required=True, help="Output corrected JSON")
    parser.add_argument("--blend", type=float, default=0.5,
                        help="Blend factor: 0=original, 1=full law replacement (default: 0.5)")
    parser.add_argument("--force", action="store_true",
                        help="Apply to ALL notes (not just deviating ones)")
    args = parser.parse_args()

    bank = json.load(open(args.soundbank))
    notes = bank.get("notes", {})
    blend = args.blend

    print(f"Input:  {args.soundbank} ({len(notes)} notes)")
    print(f"Blend:  {blend:.0%} law / {1-blend:.0%} original")
    print()

    n_corrected = 0
    corrections = {"tau1": 0, "rms_gain": 0, "K_trim": 0, "A_noise": 0, "tau2": 0}

    for key, note in notes.items():
        midi = note.get("midi", 0)
        if midi < 21 or midi > 108:
            continue

        partials = note.get("partials", [])
        if not partials:
            continue

        p1 = partials[0]
        changed = False

        # ── tau1 correction ──────────────────────────────────────────────
        ideal_tau1 = law_tau1(midi)
        actual_tau1 = p1.get("tau1", ideal_tau1)
        ratio = actual_tau1 / ideal_tau1 if ideal_tau1 > 0.001 else 1

        if args.force or ratio > 3.0 or ratio < 0.33:
            new_tau1 = (1 - blend) * actual_tau1 + blend * ideal_tau1
            new_tau1 = max(new_tau1, 0.05)

            # Apply proportional correction to all partials
            scale = new_tau1 / max(actual_tau1, 0.001)
            for p in partials:
                old = p.get("tau1", 0.5)
                p["tau1"] = max(old * scale, 0.05)
            corrections["tau1"] += 1
            changed = True

        # ── tau2 correction (apply same proportional approach) ───────────
        ideal_tau2 = law_tau2(midi)
        actual_tau2 = p1.get("tau2", ideal_tau2)
        ratio2 = actual_tau2 / ideal_tau2 if ideal_tau2 > 0.01 else 1

        if args.force or ratio2 > 4.0 or ratio2 < 0.25:
            new_tau2 = (1 - blend) * actual_tau2 + blend * ideal_tau2
            scale2 = new_tau2 / max(actual_tau2, 0.001)
            for p in partials:
                old = p.get("tau2", p.get("tau1", 0.5))
                p["tau2"] = max(old * scale2, p["tau1"])
            corrections["tau2"] += 1
            changed = True

        # ── rms_gain correction ──────────────────────────────────────────
        ideal_gain = law_rms_gain(midi)
        actual_gain = note.get("rms_gain", ideal_gain)
        gain_ratio = actual_gain / ideal_gain if ideal_gain > 1e-10 else 1

        if args.force or gain_ratio > 5.0 or gain_ratio < 0.2:
            note["rms_gain"] = (1 - blend) * actual_gain + blend * ideal_gain
            corrections["rms_gain"] += 1
            changed = True

        # ── A_noise correction ───────────────────────────────────────────
        ideal_noise = law_A_noise(midi)
        actual_noise = note.get("A_noise", ideal_noise)
        noise_ratio = actual_noise / ideal_noise if ideal_noise > 0.01 else 1

        if args.force or noise_ratio > 3.0 or noise_ratio < 0.33:
            note["A_noise"] = min((1 - blend) * actual_noise + blend * ideal_noise, 1.0)
            corrections["A_noise"] += 1
            changed = True

        # ── Partial count trim ───────────────────────────────────────────
        ideal_K = int(max(3, law_K(midi)))
        actual_K = len(partials)

        # Don't add partials (can't create from nothing), but trim excess
        # if significantly above ideal — excess partials may contain noise
        if actual_K > ideal_K * 1.5 and not args.force:
            # Soft trim: zero A0 of excess partials instead of removing
            for ki in range(ideal_K, actual_K):
                partials[ki]["A0"] *= 0.1  # reduce to 10%
            corrections["K_trim"] += 1
            changed = True

        if changed:
            n_corrected += 1

    print(f"Corrected {n_corrected} notes:")
    for k, v in corrections.items():
        if v > 0:
            print(f"  {k}: {v} notes")

    # Write output
    with open(args.out, "w") as f:
        json.dump(bank, f, separators=(",", ":"))
    sz = Path(args.out).stat().st_size
    print(f"\nWritten: {args.out} ({sz/1e6:.1f} MB)")

    # Print law curves for reference
    print(f"\nPhysical law reference curves:")
    print(f"{'MIDI':>4} {'K':>4} {'tau1':>7} {'tau2':>7} {'rms_gain':>10} {'A_noise':>7}")
    for midi in range(21, 109, 8):
        print(f"{midi:4d} {law_K(midi):4.0f} {law_tau1(midi):7.3f} {law_tau2(midi):7.2f} "
              f"{law_rms_gain(midi):10.6f} {law_A_noise(midi):7.4f}")


if __name__ == "__main__":
    main()
