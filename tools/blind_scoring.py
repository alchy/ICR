#!/usr/bin/env python3
"""
tools/blind_scoring.py
──────────────────────
Blind listening test via MIDI loopback.

Sends random notes to ICR via MIDI, user scores each 0-9.
Results are saved for profile analysis.

Prerequisites:
    - ICR running with MIDI input (icrgui.exe or icr.exe)
    - MIDI loopback driver (e.g. loopMIDI) connecting this script to ICR
    - pip install mido python-rtmidi

Usage:
    python tools/blind_scoring.py --port "loopMIDI Port" --params soundbanks-additive/pl-grand.json

The script:
    1. Selects ~44 notes (every 2nd MIDI from 21-108) in random order
    2. Sends note_on (vel=80 ~ vel_idx 5) for 10 seconds
    3. Sends note_off
    4. Prompts user for score 0-9 (or 's' to skip, 'q' to quit)
    5. Saves results to scoring-{timestamp}.json
"""

import argparse
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import mido
except ImportError:
    print("ERROR: pip install mido python-rtmidi")
    sys.exit(1)


def note_name(midi: int) -> str:
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    return f"{names[midi % 12]}{midi // 12 - 1}"


def main():
    parser = argparse.ArgumentParser(description="Blind MIDI listening test")
    parser.add_argument("--port", required=True, help="MIDI output port name")
    parser.add_argument("--params", default=None,
                        help="Soundbank JSON (for metadata in results, not used for playback)")
    parser.add_argument("--velocity", type=int, default=80,
                        help="MIDI velocity (default: 80 ~ vel_idx 5)")
    parser.add_argument("--duration", type=float, default=10.0,
                        help="Note duration in seconds (default: 10)")
    parser.add_argument("--step", type=int, default=2,
                        help="MIDI step between notes (default: 2, gives ~44 notes)")
    parser.add_argument("--midi-from", type=int, default=21,
                        help="Lowest MIDI note (default: 21)")
    parser.add_argument("--midi-to", type=int, default=108,
                        help="Highest MIDI note (default: 108)")
    args = parser.parse_args()

    # List available ports
    available = mido.get_output_names()
    print(f"Available MIDI ports: {available}")

    if args.port not in available:
        # Try partial match
        matches = [p for p in available if args.port.lower() in p.lower()]
        if matches:
            args.port = matches[0]
            print(f"Using: {args.port}")
        else:
            print(f"ERROR: port '{args.port}' not found")
            return

    port = mido.open_output(args.port)

    # Build note list and randomize
    all_midis = list(range(args.midi_from, args.midi_to + 1, args.step))
    random.shuffle(all_midis)

    results = []
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = Path(f"scoring-{ts}.json")

    print(f"\n{'='*60}")
    print(f"  BLIND LISTENING TEST")
    print(f"  {len(all_midis)} notes, {args.duration}s each, vel={args.velocity}")
    print(f"  Score: 0-9 (9=excellent, 0=unusable)")
    print(f"  Commands: s=skip, q=quit, r=replay")
    print(f"{'='*60}\n")

    note_idx = 0
    while note_idx < len(all_midis):
        midi = all_midis[note_idx]

        # Play note
        print(f"  [{note_idx+1}/{len(all_midis)}] Playing note #{note_idx+1}...")
        port.send(mido.Message("note_on", note=midi, velocity=args.velocity))

        time.sleep(args.duration)

        port.send(mido.Message("note_off", note=midi, velocity=0))
        time.sleep(0.3)  # brief silence

        # Get score
        while True:
            try:
                inp = input(f"  Score (0-9, s=skip, r=replay, q=quit): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                inp = "q"

            if inp == "q":
                print("\nQuitting.")
                break
            elif inp == "s":
                print("  Skipped.")
                note_idx += 1
                break
            elif inp == "r":
                # Replay same note
                print(f"  Replaying...")
                port.send(mido.Message("note_on", note=midi, velocity=args.velocity))
                time.sleep(args.duration)
                port.send(mido.Message("note_off", note=midi, velocity=0))
                time.sleep(0.3)
                continue
            elif inp in [str(i) for i in range(10)]:
                score = int(inp)
                results.append({
                    "midi": midi,
                    "note": note_name(midi),
                    "score": score,
                    "velocity": args.velocity,
                    "order": note_idx,
                })
                print(f"  -> {note_name(midi)} (MIDI {midi}): {score}/9")
                note_idx += 1
                break
            else:
                print("  Invalid. Enter 0-9, s, r, or q.")

        if inp == "q":
            break

    port.close()

    # Save results
    output = {
        "timestamp": ts,
        "velocity": args.velocity,
        "duration_s": args.duration,
        "params_file": args.params,
        "n_scored": len(results),
        "n_total": len(all_midis),
        "scores": results,
    }

    # Summary statistics
    if results:
        scores_arr = [r["score"] for r in results]
        by_register = {}
        for r in results:
            reg = "bass" if r["midi"] <= 48 else ("mid" if r["midi"] <= 72 else "treble")
            by_register.setdefault(reg, []).append(r["score"])

        output["summary"] = {
            "mean": round(sum(scores_arr) / len(scores_arr), 2),
            "min": min(scores_arr),
            "max": max(scores_arr),
            "by_register": {k: round(sum(v)/len(v), 2) for k, v in by_register.items()},
        }

        print(f"\n{'='*60}")
        print(f"  RESULTS: {len(results)} notes scored")
        print(f"  Mean: {output['summary']['mean']}/9")
        for reg, avg in output["summary"]["by_register"].items():
            print(f"  {reg:>7}: {avg}/9")
        print(f"{'='*60}")

    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nSaved: {out_path}")

    # Print scores sorted by MIDI for quick reference
    if results:
        print(f"\nScores by MIDI:")
        for r in sorted(results, key=lambda x: x["midi"]):
            bar = "#" * r["score"]
            print(f"  {r['midi']:3d} {r['note']:>4} : {r['score']} {bar}")


if __name__ == "__main__":
    main()
