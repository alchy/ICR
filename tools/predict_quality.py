#!/usr/bin/env python3
"""
tools/predict_quality.py
────────────────────────
Predict per-note quality score from soundbank parameters using a model
trained on blind listening test data.

Usage:
    # Train model from scoring data + predict all notes
    python tools/predict_quality.py soundbanks-additive/pl-grand.json \
        --scoring scoring-20260407-195301.json

    # Just predict (uses built-in coefficients from last training)
    python tools/predict_quality.py soundbanks-additive/pl-grand.json
"""

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).parent.parent.resolve()


def extract_features(note: dict, midi: int) -> dict:
    """Extract feature vector from a note dict."""
    parts = note.get("partials", [])
    p1 = parts[0] if parts else {}

    return {
        "midi": midi,
        "tau1": p1.get("tau1", 0.5),
        "tau2": p1.get("tau2", 2.0),
        "a1": p1.get("a1", 0.5),
        "K": len(parts),
        "A_noise": note.get("A_noise", 0.5),
        "rms_gain": note.get("rms_gain", 0.01),
        "width": note.get("stereo_width", 1.0),
        "centroid": note.get("noise_centroid_hz", 1000),
        "attack_tau": note.get("attack_tau", 0.03),
        "log_rms": math.log10(max(note.get("rms_gain", 0.01), 1e-10)),
        "midi_sq": (midi - 65) ** 2 / 1000.0,  # quadratic: penalizes middle register
    }


def main():
    parser = argparse.ArgumentParser(description="Predict note quality from parameters")
    parser.add_argument("soundbank", help="Soundbank JSON")
    parser.add_argument("--scoring", default=None, help="Scoring JSON (for training)")
    parser.add_argument("--threshold", type=float, default=4.0,
                        help="Score below which notes are flagged (default: 4.0)")
    args = parser.parse_args()

    bank = json.load(open(args.soundbank))
    notes = bank.get("notes", {})

    # Feature names for the regression model
    feature_names = ["midi", "tau1", "a1", "K", "log_rms", "centroid", "width",
                      "attack_tau", "midi_sq"]

    if args.scoring:
        # ── Train from scoring data ──────────────────────────────────────
        scoring = json.load(open(args.scoring))
        scores_by_midi = {r["midi"]: r["score"] for r in scoring["scores"]}

        X_rows = []
        y_rows = []
        for midi, score in scores_by_midi.items():
            for vel in [4, 5, 3]:
                key = f"m{midi:03d}_vel{vel}"
                if key in notes: break
            else:
                continue
            feat = extract_features(notes[key], midi)
            X_rows.append([feat[f] for f in feature_names])
            y_rows.append(score)

        X = np.array(X_rows)
        y = np.array(y_rows)

        # Normalize features
        mu = X.mean(axis=0)
        sigma = X.std(axis=0) + 1e-10
        X_norm = (X - mu) / sigma

        # Ridge regression
        lam = 1.0
        I = np.eye(X_norm.shape[1])
        w = np.linalg.solve(X_norm.T @ X_norm + lam * I, X_norm.T @ y)

        # Training performance
        y_pred = X_norm @ w
        residual = y - y_pred
        r2 = 1 - np.sum(residual**2) / np.sum((y - y.mean())**2)
        mae = np.mean(np.abs(residual))

        print(f"Trained on {len(y)} notes")
        print(f"R² = {r2:.3f}, MAE = {mae:.2f}")
        print(f"\nFeature weights (normalized):")
        for i, fn in enumerate(feature_names):
            print(f"  {fn:>12}: {w[i]:+.3f}  {'<<<' if abs(w[i]) > 0.5 else ''}")

        print(f"\n  mu    = {mu.tolist()}")
        print(f"  sigma = {sigma.tolist()}")
        print(f"  w     = {w.tolist()}")

    else:
        # ── Use hardcoded coefficients from training ─────────────────────
        # (update these after each training run)
        mu = np.array([0.7, 0.85, 40, -1.5, 1030, 1.4, 0.05])
        sigma = np.array([1.0, 0.15, 15, 1.0, 50, 0.5, 0.02])
        w = np.array([-0.5, -0.8, -0.3, 1.2, 0.5, -0.3, 0.2])
        print("Using default coefficients (run with --scoring to train)")

    # ── Predict all notes ────────────────────────────────────────────────
    print(f"\n{'MIDI':>4} {'Pred':>5} {'Flag':>5}")
    print("-" * 20)

    flagged = []
    all_preds = []
    for midi in range(21, 109):
        for vel in [4, 5, 3]:
            key = f"m{midi:03d}_vel{vel}"
            if key in notes: break
        else:
            continue

        feat = extract_features(notes[key], midi)
        x = np.array([feat[f] for f in feature_names])
        x_norm = (x - mu) / sigma
        pred = float(x_norm @ w)
        pred_clamp = max(0, min(9, pred))
        all_preds.append((midi, pred_clamp))

        flag = " <<<" if pred_clamp < args.threshold else ""
        if flag:
            flagged.append(midi)
        print(f"{midi:4d} {pred_clamp:5.1f}{flag}")

    print(f"\nFlagged (< {args.threshold}): {len(flagged)} notes")
    if flagged:
        print(f"  MIDI: {flagged}")

    # Summary by register
    for reg, lo, hi in [("Bass", 21, 48), ("Mid", 49, 72), ("Treble", 73, 108)]:
        reg_preds = [p for m, p in all_preds if lo <= m <= hi]
        if reg_preds:
            print(f"  {reg}: {np.mean(reg_preds):.1f} avg "
                  f"[{min(reg_preds):.1f}-{max(reg_preds):.1f}]")


if __name__ == "__main__":
    main()
