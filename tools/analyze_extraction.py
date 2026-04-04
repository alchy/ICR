"""
analyze_extraction.py - Analyze extraction quality from a params JSON.

Usage:
    python tools/analyze_extraction.py generated/pl-grand-extracted.json
    python tools/analyze_extraction.py generated/pl-grand-extracted.json --register
    python tools/analyze_extraction.py generated/pl-grand-extracted.json --compare generated/pl-upright-extracted.json
"""

import io
import json
import sys
import os
import argparse
import math

# UTF-8 output on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

def midi_name(midi):
    return NOTE_NAMES[midi % 12] + str(midi // 12 - 1)

REGISTERS = [
    ("Bass",    21,  47),
    ("Mid-low", 48,  60),
    ("Mid",     61,  72),
    ("Treble",  73,  84),
    ("High",    85, 108),
]

def register_name(midi):
    for name, lo, hi in REGISTERS:
        if lo <= midi <= hi:
            return name
    return "?"


def classify_note(note):
    """
    Returns dict with quality flags for a single extracted note entry.
    """
    partials = note.get("partials", [])
    if not partials:
        return {"has_partials": False, "biexp": False, "beat": False, "B_ok": False, "n_partials": 0}

    p0 = partials[0]
    B = note.get("B", 0.0)
    a1 = p0.get("a1", 1.0)
    tau1 = p0.get("tau1") or 0.0
    tau2 = p0.get("tau2") or 0.0
    beat = p0.get("beat_hz", 0.0)
    mono = p0.get("mono", True)

    biexp = (not mono) and (a1 < 0.999) and (abs(tau1 - tau2) > 1e-4)
    beat_ok = beat > 0.05
    B_ok = 1e-6 < B < 0.05

    return {
        "has_partials": True,
        "biexp": biexp,
        "beat": beat_ok,
        "B_ok": B_ok,
        "n_partials": len(partials),
        "tau1": tau1,
        "tau2": tau2 if not mono else None,
        "a1": a1,
        "beat_hz": beat,
        "B": B,
        "dur": note.get("duration_s", 0.0),
    }


def analyze(params: dict, label: str = ""):
    samples = params.get("samples", {})
    total = len(samples)
    if total == 0:
        print("  No samples found.")
        return

    print(f"\n{'='*70}")
    print(f"  {label or 'Extraction analysis'}  ({total} notes)")
    print(f"{'='*70}")

    # Overall stats
    n_biexp = 0; n_beat = 0; n_B_ok = 0; n_partials_ok = 0
    for key, note in samples.items():
        c = classify_note(note)
        if c["biexp"]: n_biexp += 1
        if c["beat"]:  n_beat += 1
        if c["B_ok"]:  n_B_ok += 1
        if c["has_partials"]: n_partials_ok += 1

    print(f"\n  Overall:")
    print(f"    Has partials : {n_partials_ok:4d} / {total}  ({100*n_partials_ok/total:.0f}%)")
    print(f"    B in range   : {n_B_ok:4d} / {total}  ({100*n_B_ok/total:.0f}%)")
    print(f"    Bi-exp fit   : {n_biexp:4d} / {total}  ({100*n_biexp/total:.0f}%)")
    print(f"    Beat detect  : {n_beat:4d} / {total}  ({100*n_beat/total:.0f}%)")

    # Per-register breakdown
    print(f"\n  Per register (all velocities pooled):")
    print(f"  {'Register':<10} {'notes':>6} {'biexp%':>8} {'beat%':>7} {'B_ok%':>7}  failures")
    print(f"  {'-'*65}")
    for reg_name, lo, hi in REGISTERS:
        reg_keys = [k for k, v in samples.items() if lo <= v.get("midi", 0) <= hi]
        if not reg_keys:
            continue
        rc = [classify_note(samples[k]) for k in reg_keys]
        rb = sum(c["biexp"] for c in rc)
        rbt = sum(c["beat"] for c in rc)
        rB = sum(c["B_ok"] for c in rc)
        n = len(rc)
        # Failure modes
        fail_mono = sum(1 for c in rc if c["has_partials"] and not c["biexp"])
        fail_beat = sum(1 for c in rc if c["has_partials"] and not c["beat"])
        fail_B = sum(1 for c in rc if c["has_partials"] and not c["B_ok"])
        fails = []
        if fail_mono: fails.append(f"mono={fail_mono}")
        if fail_beat: fails.append(f"nobeat={fail_beat}")
        if fail_B:    fails.append(f"Bbad={fail_B}")
        print(f"  {reg_name:<10} {n:>6} {100*rb/n:>7.0f}% {100*rbt/n:>6.0f}% {100*rB/n:>6.0f}%  {', '.join(fails)}")

    # Per-velocity breakdown
    print(f"\n  Per velocity (all MIDI pooled):")
    print(f"  {'vel':>4} {'biexp%':>8} {'beat%':>7}")
    print(f"  {'-'*25}")
    for vel in range(8):
        vk = [k for k, v in samples.items() if v.get("vel") == vel]
        vc = [classify_note(samples[k]) for k in vk]
        n = len(vc)
        if n == 0: continue
        vb = sum(c["biexp"] for c in vc)
        vbt = sum(c["beat"] for c in vc)
        print(f"  {vel:>4} {100*vb/n:>7.0f}% {100*vbt/n:>6.0f}%")

    # Per MIDI note heatmap (vel4 only)
    print(f"\n  Per-MIDI heatmap (vel=4, key params):")
    print(f"  {'midi':<5} {'name':<5} {'biexp':>6} {'beat_hz':>8} {'tau1':>6} {'tau2':>7} {'B':>10}  note")
    print(f"  {'-'*75}")
    for midi in range(21, 109):
        key = f"m{midi:03d}_vel4"
        note = samples.get(key)
        if not note:
            continue
        c = classify_note(note)
        if not c["has_partials"]:
            print(f"  {midi:<5} {midi_name(midi):<5} {'NO PARTIALS':>47}")
            continue
        biexp_s = "YES" if c["biexp"] else "no "
        tau2_s = f"{c['tau2']:.1f}" if c["tau2"] else "  --"
        tau1_s = f"{c['tau1']:.2f}" if c["tau1"] else " --"
        beat_s = f"{c['beat_hz']:.3f}" if c["beat_hz"] else "0.000"
        B_s = f"{c['B']:.3e}"
        flag = ""
        if not c["biexp"]: flag += " [mono]"
        if not c["beat"]:  flag += " [nobeat]"
        if not c["B_ok"]: flag += " [B!]"
        print(f"  {midi:<5} {midi_name(midi):<5} {biexp_s:>6} {beat_s:>8} {tau1_s:>6} {tau2_s:>7} {B_s:>10}  {flag}")

    # Summary of worst MIDI notes (bi-exp fails across all velocities)
    print(f"\n  MIDI notes with 0 successful bi-exp extractions (any velocity):")
    bad_midis = []
    for midi in range(21, 109):
        successes = sum(
            1 for vel in range(8)
            if classify_note(samples.get(f"m{midi:03d}_vel{vel}", {})).get("biexp", False)
        )
        if successes == 0:
            bad_midis.append(midi)
    if bad_midis:
        grouped = []
        start = bad_midis[0]; prev = bad_midis[0]
        for m in bad_midis[1:]:
            if m == prev + 1:
                prev = m
            else:
                grouped.append(f"{start}-{prev}" if start != prev else str(start))
                start = prev = m
        grouped.append(f"{start}-{prev}" if start != prev else str(start))
        print(f"    {', '.join(grouped)}  ({len(bad_midis)} notes)")
    else:
        print(f"    None — all MIDI notes have at least one good extraction!")

    print()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("params_file", help="Extracted params JSON")
    parser.add_argument("--register", action="store_true")
    parser.add_argument("--compare", help="Second params JSON to compare")
    args = parser.parse_args()

    if not os.path.exists(args.params_file):
        print(f"File not found: {args.params_file}")
        sys.exit(1)

    print(f"Loading {args.params_file}...")
    with open(args.params_file, encoding="utf-8") as f:
        params = json.load(f)

    label = os.path.basename(args.params_file).replace(".json", "")
    analyze(params, label)

    if args.compare:
        if not os.path.exists(args.compare):
            print(f"Compare file not found: {args.compare}")
        else:
            print(f"\nLoading {args.compare}...")
            with open(args.compare, encoding="utf-8") as f:
                params2 = json.load(f)
            label2 = os.path.basename(args.compare).replace(".json", "")
            analyze(params2, label2)


if __name__ == "__main__":
    main()
