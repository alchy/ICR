#!/usr/bin/env python3
"""
tools/inspect_bank.py
─────────────────────
Inspect a soundbank JSON and print diagnostic statistics per register.

Usage:
    python tools/inspect_bank.py soundbanks-additive/pl-grand.json
    python tools/inspect_bank.py soundbanks-additive/pl-grand.json --midi 36
    python tools/inspect_bank.py soundbanks-additive/pl-grand.json --midi 36 --vel 0,7
"""

import json
import sys
import argparse
import math
from pathlib import Path
from collections import defaultdict

import numpy as np


def load_bank(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def note_name(midi: int) -> str:
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    return f"{names[midi % 12]}{midi // 12 - 1}"


def register_name(midi: int) -> str:
    if midi <= 33:  return "bass"
    if midi <= 48:  return "low-mid"
    if midi <= 72:  return "middle"
    if midi <= 90:  return "upper"
    return "treble"


def spectral_tilt_db(partials: list, k_ref: int = 1, k_test: int = 10) -> float:
    """Compute A0 ratio in dB between partial k_test and k_ref."""
    a0_ref = a0_test = None
    for p in partials:
        if p["k"] == k_ref:  a0_ref = p["A0"]
        if p["k"] == k_test: a0_test = p["A0"]
    if a0_ref and a0_test and a0_ref > 1e-15 and a0_test > 1e-15:
        return 20 * math.log10(a0_test / a0_ref)
    return float("nan")


def print_register_summary(notes: list, label: str):
    """Print summary statistics for a group of notes."""
    if not notes:
        return

    n_partials = [n["n_partials"] for n in notes]
    attack_taus = [n["attack_tau"] for n in notes]
    a_noises = [n["A_noise"] for n in notes]
    centroids = [n["noise_centroid_hz"] for n in notes]
    rms_gains = [n.get("rms_gain", 0) for n in notes]

    # Spectral tilt: A0(k=10) / A0(k=1) in dB
    tilts = [spectral_tilt_db(n["partials"], 1, 10) for n in notes]
    tilts = [t for t in tilts if not math.isnan(t)]

    # Fit quality stats
    all_fq = []
    n_damping_derived = 0
    for n in notes:
        for p in n["partials"]:
            fq = p.get("fit_quality", 0)
            if fq > 0.01:
                all_fq.append(fq)
            if p.get("damping_derived"):
                n_damping_derived += 1

    # tau1 of k=1 (fundamental decay)
    tau1_k1 = []
    for n in notes:
        for p in n["partials"]:
            if p["k"] == 1 and p.get("tau1", 0) > 0:
                tau1_k1.append(p["tau1"])

    print(f"\n{'=' * 60}")
    print(f"  {label}  ({len(notes)} notes, MIDI {min(n['midi'] for n in notes)}-{max(n['midi'] for n in notes)})")
    print(f"{'=' * 60}")
    print(f"  partials     {np.mean(n_partials):5.0f} avg  [{min(n_partials)}-{max(n_partials)}]")
    print(f"  attack_tau   {np.mean(attack_taus):5.3f} s    [{min(attack_taus):.3f}-{max(attack_taus):.3f}]")
    print(f"  A_noise      {np.mean(a_noises):5.4f}     [{min(a_noises):.4f}-{max(a_noises):.4f}]")
    print(f"  centroid_hz  {np.mean(centroids):5.0f} Hz   [{min(centroids):.0f}-{max(centroids):.0f}]")
    print(f"  rms_gain     {np.mean(rms_gains):8.5f}  [{min(rms_gains):.5f}-{max(rms_gains):.5f}]")
    if tau1_k1:
        print(f"  tau1(k=1)    {np.mean(tau1_k1):5.2f} s    [{min(tau1_k1):.2f}-{max(tau1_k1):.2f}]")
    if tilts:
        print(f"  tilt k10/k1  {np.mean(tilts):5.1f} dB   [{min(tilts):.1f} to {max(tilts):.1f}]")
    if all_fq:
        print(f"  fit_quality  {np.mean(all_fq):5.3f} avg  [{min(all_fq):.3f}-{max(all_fq):.3f}]")
        n_bad = sum(1 for q in all_fq if q < 0.7)
        print(f"               {n_bad}/{len(all_fq)} partials below 0.7")
    if n_damping_derived:
        print(f"  damping_derived: {n_damping_derived} partials had tau1 replaced by R+eta*f^2")


def print_note_detail(note: dict):
    """Print detailed info for a single note."""
    midi = note["midi"]
    vel = note.get("vel", "?")
    print(f"\n{'-' * 60}")
    print(f"  {note_name(midi)} (MIDI {midi})  vel={vel}  {register_name(midi)}")
    print(f"  f0={note.get('f0_hz', 0):.2f} Hz  B={note.get('B', 0):.2e}")
    print(f"  attack_tau={note.get('attack_tau', 0):.4f}s  A_noise={note.get('A_noise', 0):.4f}")
    print(f"  centroid={note.get('noise_centroid_hz', 0):.0f} Hz  rms_gain={note.get('rms_gain', 0):.6f}")
    if note.get("stereo_width"):
        print(f"  stereo_width={note['stereo_width']:.3f}")
    print(f"  partials: {len(note.get('partials', []))}")
    print()
    print(f"  {'k':>3} {'f_hz':>8} {'A0':>10} {'tau1':>6} {'tau2':>6} {'a1':>5} {'beat':>7} {'Q':>5} {'D':>2}")
    print(f"  {'-'*3} {'-'*8} {'-'*10} {'-'*6} {'-'*6} {'-'*5} {'-'*7} {'-'*5} {'-'*2}")
    for p in note.get("partials", [])[:30]:  # cap display at 30
        fq = p.get("fit_quality", 0)
        dd = "d" if p.get("damping_derived") else " "
        fq_str = f"{fq:.2f}" if fq > 0.01 else "  -  "
        tau2_str = f"{p.get('tau2', 0):6.2f}" if p.get("a1", 1) < 0.999 else "     -"
        beat_str = f"{p.get('beat_hz', 0):7.3f}" if p.get("beat_hz", 0) > 1e-6 else "      0"
        print(f"  {p['k']:3d} {p['f_hz']:8.2f} {p['A0']:10.6f} {p['tau1']:6.2f} {tau2_str} {p.get('a1', 1):5.3f} {beat_str} {fq_str} {dd}")

    n_shown = min(30, len(note.get("partials", [])))
    n_total = len(note.get("partials", []))
    if n_total > n_shown:
        print(f"  ... ({n_total - n_shown} more partials)")

    # Spectral tilt summary
    tilts = []
    for k_test in [5, 10, 15, 20, 30]:
        t = spectral_tilt_db(note.get("partials", []), 1, k_test)
        if not math.isnan(t):
            tilts.append((k_test, t))
    if tilts:
        print(f"\n  Spectral tilt (dB re k=1):")
        for k, t in tilts:
            bar = "+" * max(0, int(t / 2)) if t > 0 else "-" * max(0, int(-t / 2))
            print(f"    k={k:2d}: {t:+6.1f} dB  {bar}")


def main():
    parser = argparse.ArgumentParser(description="Inspect ICR soundbank JSON")
    parser.add_argument("bank", help="Path to soundbank JSON")
    parser.add_argument("--midi", type=str, default=None,
                        help="Show detail for specific MIDI note(s), e.g. 36 or 36,60,84")
    parser.add_argument("--vel", type=str, default=None,
                        help="Filter velocity layers, e.g. 0,7 (default: all)")
    args = parser.parse_args()

    bank = load_bank(args.bank)
    notes_dict = bank.get("notes", {})

    # Parse notes into list
    all_notes = []
    for key, note in notes_dict.items():
        if "partials" in note:
            note["n_partials"] = len(note["partials"])
            all_notes.append(note)

    if not all_notes:
        print("No notes found in bank.")
        return

    # Filter by velocity
    vel_filter = None
    if args.vel:
        vel_filter = set(int(v) for v in args.vel.split(","))
        all_notes = [n for n in all_notes if n.get("vel") in vel_filter]

    print(f"Bank: {args.bank}")
    print(f"Total notes: {len(all_notes)}")
    if bank.get("metadata"):
        m = bank["metadata"]
        print(f"SR: {m.get('sr', '?')}  target_rms: {m.get('target_rms', '?')}")

    # Detail mode: specific MIDI note(s)
    if args.midi:
        midi_list = [int(m) for m in args.midi.split(",")]
        for midi in midi_list:
            matching = [n for n in all_notes if n["midi"] == midi]
            matching.sort(key=lambda n: n.get("vel", 0))
            for n in matching:
                print_note_detail(n)
        return

    # Register summary mode
    registers = defaultdict(list)
    for n in all_notes:
        registers[register_name(n["midi"])].append(n)

    for reg in ["bass", "low-mid", "middle", "upper", "treble"]:
        if reg in registers:
            print_register_summary(registers[reg], reg.upper())

    # Cross-velocity comparison: pick a representative note per register
    print(f"\n{'=' * 60}")
    print("  VELOCITY COMPARISON (spectral tilt k10/k1 in dB)")
    print(f"{'=' * 60}")
    test_midis = [28, 36, 48, 60, 72, 84, 96]
    print(f"  {'note':>6} {'vel0':>7} {'vel3':>7} {'vel5':>7} {'vel7':>7} {'spread':>7}")
    for midi in test_midis:
        row = {}
        for n in all_notes:
            if n["midi"] == midi:
                row[n.get("vel", 0)] = spectral_tilt_db(n.get("partials", []), 1, 10)
        if not row:
            continue
        vals = [row.get(v, float("nan")) for v in [0, 3, 5, 7]]
        valid = [v for v in vals if not math.isnan(v)]
        spread = max(valid) - min(valid) if len(valid) >= 2 else float("nan")
        def fmt(v): return f"{v:+6.1f}" if not math.isnan(v) else "     -"
        spread_str = f"{spread:+6.1f}" if not math.isnan(spread) else "     -"
        print(f"  {note_name(midi):>6} {fmt(vals[0])} {fmt(vals[1])} {fmt(vals[2])} {fmt(vals[3])} {spread_str}")


if __name__ == "__main__":
    main()
