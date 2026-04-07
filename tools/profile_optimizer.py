#!/usr/bin/env python3
"""
tools/profile_optimizer.py
──────────────────────────
Learn "good note" parameter profiles from manually scored notes and
apply corrections to notes that deviate from the learned profile.

Principle: good-sounding notes (score >= 0.8) define the parameter
sweet spot for each register.  Bad notes are corrected toward the
profile of their good neighbors, preserving genuine per-note variation
while eliminating extraction artifacts.

Usage:
    python tools/profile_optimizer.py soundbanks/pl-grand.json \
        --scores "21:0.9,26:0.9,38:0.9,41:0.83,62:0.98,84:0.87,88:0.98,91:0.99,95:0.99,98:0.99,25:0.3,50:0.34,57:0.30,74:0.30" \
        --out soundbanks/pl-grand-optimized.json
"""

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy.interpolate import interp1d

REPO_ROOT = Path(__file__).parent.parent.resolve()


def main():
    parser = argparse.ArgumentParser(description="Optimize soundbank from listening scores")
    parser.add_argument("soundbank", help="Input soundbank JSON")
    parser.add_argument("--scores", required=True,
                        help="Manual scores: midi:score,midi:score,...")
    parser.add_argument("--out", required=True, help="Output optimized JSON")
    parser.add_argument("--threshold", type=float, default=0.6,
                        help="Score below which notes are corrected (default: 0.6)")
    parser.add_argument("--good-threshold", type=float, default=0.75,
                        help="Score above which notes define the profile (default: 0.75)")
    args = parser.parse_args()

    # Parse scores
    scores = {}
    for pair in args.scores.split(","):
        m, s = pair.strip().split(":")
        scores[int(m)] = float(s)

    bank = json.load(open(args.soundbank))
    notes = bank.get("notes", {})

    good_midis = sorted([m for m, s in scores.items() if s >= args.good_threshold])
    bad_midis  = sorted([m for m, s in scores.items() if s < args.threshold])

    print(f"Good notes (>= {args.good_threshold}): {good_midis}")
    print(f"Bad notes  (<  {args.threshold}): {bad_midis}")
    print()

    # ── Step 1: Extract profile from good notes ─────────────────────────────

    # For each good note, extract key parameter ratios as function of MIDI
    profile_data = {
        "midi": [],
        "tau1_k1": [],       # tau1 of fundamental
        "tau2_k1": [],       # tau2 of fundamental
        "a1_k1": [],         # a1 of fundamental
        "tilt_5": [],        # A0(k=5) / A0(k=1) in dB
        "tilt_10": [],       # A0(k=10) / A0(k=1) in dB
        "A_noise": [],
        "attack_tau": [],
    }

    for midi in good_midis:
        # Find best velocity layer for this note
        for vel in [4, 3, 5, 2, 6, 1, 7, 0]:
            key = f"m{midi:03d}_vel{vel}"
            if key in notes:
                break
        else:
            continue

        n = notes[key]
        parts = n.get("partials", [])
        if len(parts) < 2:
            continue

        p1 = parts[0]
        a0_1 = p1.get("A0", 1e-12)
        if a0_1 < 1e-12:
            continue

        profile_data["midi"].append(midi)
        profile_data["tau1_k1"].append(p1.get("tau1", 0.5))
        profile_data["tau2_k1"].append(p1.get("tau2", 2.0))
        profile_data["a1_k1"].append(p1.get("a1", 0.5))
        profile_data["A_noise"].append(n.get("A_noise", 0.5))
        profile_data["attack_tau"].append(n.get("attack_tau", 0.03))

        # Spectral tilt
        a0_5 = parts[4]["A0"] if len(parts) > 4 else a0_1 * 0.1
        a0_10 = parts[9]["A0"] if len(parts) > 9 else a0_1 * 0.01
        profile_data["tilt_5"].append(20 * math.log10(max(a0_5, 1e-15) / a0_1))
        profile_data["tilt_10"].append(20 * math.log10(max(a0_10, 1e-15) / a0_1))

    if len(profile_data["midi"]) < 3:
        print("ERROR: need at least 3 good notes to build profile")
        return

    # ── Step 2: Build interpolation functions ────────────────────────────────

    midi_arr = np.array(profile_data["midi"])
    profiles = {}
    for key in ["tau1_k1", "tau2_k1", "a1_k1", "tilt_5", "tilt_10",
                "A_noise", "attack_tau"]:
        vals = np.array(profile_data[key])
        # Log-domain for tau values
        if "tau" in key:
            vals = np.log(np.maximum(vals, 1e-6))
        profiles[key] = interp1d(midi_arr, vals, kind="linear",
                                  fill_value="extrapolate")

    print(f"Profile built from {len(midi_arr)} good notes")
    print(f"MIDI range: {midi_arr[0]}-{midi_arr[-1]}")
    print()

    # ── Step 3: Evaluate all notes against profile ───────────────────────────

    print(f"{'MIDI':>4} {'Score':>5} {'tau1':>6} {'prof':>6} {'dev':>5} {'Action':>8}")
    print("-" * 50)

    n_corrected = 0
    for midi in range(21, 109):
        for vel in range(8):
            key = f"m{midi:03d}_vel{vel}"
            if key not in notes:
                continue

            n = notes[key]
            parts = n.get("partials", [])
            if len(parts) < 2:
                continue

            # Check if this note needs correction
            manual_score = scores.get(midi, None)
            needs_fix = (manual_score is not None and manual_score < args.threshold)

            # Also fix unscored notes that deviate significantly from profile
            if manual_score is None and midi_arr[0] <= midi <= midi_arr[-1]:
                p1 = parts[0]
                tau1_actual = p1.get("tau1", 0.5)
                tau1_profile = math.exp(float(profiles["tau1_k1"](midi)))
                ratio = max(tau1_actual, 1e-6) / max(tau1_profile, 1e-6)
                if ratio > 5.0 or ratio < 0.2:
                    needs_fix = True  # >5x deviation from profile

            if not needs_fix:
                continue

            # ── Apply profile correction ─────────────────────────────────
            p1 = parts[0]
            old_tau1 = p1.get("tau1", 0.5)

            # Interpolate target from good-note profile
            target_tau1 = math.exp(float(profiles["tau1_k1"](midi)))
            target_tau2 = math.exp(float(profiles["tau2_k1"](midi)))
            target_a1   = float(profiles["a1_k1"](midi))
            target_noise = float(profiles["A_noise"](midi))
            target_atk   = math.exp(float(profiles["attack_tau"](midi)))

            # Blend: 70% profile, 30% original (preserve some note character)
            blend = 0.7
            inv = 1.0 - blend

            p1["tau1"] = max(inv * old_tau1 + blend * target_tau1, 0.05)
            old_tau2 = p1.get("tau2", target_tau2)
            p1["tau2"] = max(inv * old_tau2 + blend * target_tau2, p1["tau1"])
            old_a1 = p1.get("a1", 0.5)
            p1["a1"] = max(0.01, min(0.99, inv * old_a1 + blend * target_a1))

            # Apply similar correction to first 8 partials
            for ki in range(1, min(8, len(parts))):
                pk = parts[ki]
                if pk.get("tau1") and pk["tau1"] < 0.06:
                    # Scale by same ratio as k=1 correction
                    scale = target_tau1 / max(old_tau1, 0.01)
                    pk["tau1"] = max(pk["tau1"] * scale, 0.05)
                    if pk.get("tau2") and pk["tau2"] < pk["tau1"]:
                        pk["tau2"] = pk["tau1"] * 1.5

            n["A_noise"] = inv * n.get("A_noise", 0.5) + blend * target_noise
            n["attack_tau"] = inv * n.get("attack_tau", 0.03) + blend * target_atk

            n_corrected += 1

            if vel == 4:  # print only vel4 for readability
                score_str = f"{manual_score:.2f}" if manual_score else "  -  "
                print(f"{midi:4d} {score_str} {old_tau1:6.3f} {target_tau1:6.3f} "
                      f"{old_tau1/target_tau1:5.1f}x FIXED")

    print(f"\nCorrected {n_corrected} note×velocity entries")

    # Write output
    with open(args.out, "w") as f:
        json.dump(bank, f, indent=None, separators=(",", ":"))
    sz = Path(args.out).stat().st_size
    print(f"Written: {args.out} ({sz/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
