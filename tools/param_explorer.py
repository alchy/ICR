#!/usr/bin/env python3
"""
tools/param_explorer.py
───────────────────────
Random parameter exploration with human feedback.
Generates 4 random variations per round, user picks best.
Logs everything for correlation analysis.

Usage:
    python tools/param_explorer.py --port "loopMIDI Port 1" \
        --params soundbanks/pl-grand-04072006.json \
        --midi 55,63,69,75
"""

import argparse
import json
import math
import random
import struct
import sys
import time
from pathlib import Path
from datetime import datetime

try:
    import mido
except ImportError:
    print("pip install mido python-rtmidi")
    sys.exit(1)


def sx_send(port, data):
    port.send(mido.Message('sysex', data=data))
    time.sleep(0.005)


def sx_note(port, midi, vel, pid, val):
    raw = struct.pack('>f', val)
    i32 = int.from_bytes(raw, 'big')
    data = [0x7D, 0x01, 0x01, midi, vel, pid,
            (i32 >> 28) & 0x0F, (i32 >> 21) & 0x7F,
            (i32 >> 14) & 0x7F, (i32 >> 7) & 0x7F, i32 & 0x7F]
    sx_send(port, data)


def sx_partial(port, midi, vel, k, pid, val):
    raw = struct.pack('>f', val)
    i32 = int.from_bytes(raw, 'big')
    data = [0x7D, 0x01, 0x02, midi, vel, k, pid,
            (i32 >> 28) & 0x0F, (i32 >> 21) & 0x7F,
            (i32 >> 14) & 0x7F, (i32 >> 7) & 0x7F, i32 & 0x7F]
    sx_send(port, data)


def play(port, midi, vel=65, dur=3):
    port.send(mido.Message('note_on', note=midi, velocity=vel))
    time.sleep(dur)
    port.send(mido.Message('note_off', note=midi, velocity=0))
    time.sleep(0.8)


def random_variation(base_parts, base_note, rng):
    """Generate one random parameter variation."""
    v = {
        "k1_mult":     rng.uniform(0.5, 6.0),     # fundamental boost
        "k2_mult":     rng.uniform(0.5, 6.0),     # 2nd harmonic
        "body_mult":   rng.uniform(0.3, 3.0),     # k=3-6 multiplier
        "upper_mult":  rng.uniform(0.1, 4.0),     # k=7-15 multiplier
        "tau1_scale":  rng.uniform(0.3, 1.5),     # decay speed
        "a1":          rng.uniform(0.3, 0.95),     # fast/slow ratio
        "beat_hz":     rng.uniform(0.1, 1.5),     # beating
        "attack_tau":  rng.uniform(0.002, 0.015),  # hammer sharpness
        "A_noise":     rng.uniform(0.5, 1.0),     # noise amount
        "width":       rng.uniform(0.8, 2.0),     # stereo
        "rms_scale":   rng.uniform(0.5, 3.0),     # loudness
    }
    return v


def apply_variation(port, midi, parts, note, var):
    """Apply a variation to all vel layers via SysEx."""
    for v in range(8):
        # k=1
        if len(parts) > 0:
            sx_partial(port, midi, v, 1, 0x11, parts[0]['A0'] * var['k1_mult'])
            sx_partial(port, midi, v, 1, 0x12, max(parts[0]['tau1'] * var['tau1_scale'], 0.05))
            sx_partial(port, midi, v, 1, 0x14, var['a1'])
            sx_partial(port, midi, v, 1, 0x15, var['beat_hz'])

        # k=2
        if len(parts) > 1:
            sx_partial(port, midi, v, 2, 0x11, parts[1]['A0'] * var['k2_mult'])
            sx_partial(port, midi, v, 2, 0x12, max(parts[1]['tau1'] * var['tau1_scale'], 0.05))
            sx_partial(port, midi, v, 2, 0x14, var['a1'])
            sx_partial(port, midi, v, 2, 0x15, var['beat_hz'])

        # k=3-6 (body)
        for k in range(3, min(7, len(parts) + 1)):
            sx_partial(port, midi, v, k, 0x11, parts[k-1]['A0'] * var['body_mult'])
            sx_partial(port, midi, v, k, 0x12, max(parts[k-1]['tau1'] * var['tau1_scale'], 0.05))
            sx_partial(port, midi, v, k, 0x14, var['a1'])
            sx_partial(port, midi, v, k, 0x15, var['beat_hz'] * 1.1)

        # k=7-15 (upper)
        for k in range(7, min(16, len(parts) + 1)):
            sx_partial(port, midi, v, k, 0x11, parts[k-1]['A0'] * var['upper_mult'])
            sx_partial(port, midi, v, k, 0x15, var['beat_hz'] * 0.9)

        sx_note(port, midi, v, 0x03, var['attack_tau'])
        sx_note(port, midi, v, 0x04, var['A_noise'])
        sx_note(port, midi, v, 0x07, var['width'])
        sx_note(port, midi, v, 0x05, note.get('rms_gain', 0.01) * var['rms_scale'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--params", required=True)
    parser.add_argument("--midi", required=True, help="MIDI notes (e.g. 55,63,69)")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--dur", type=float, default=3.0)
    args = parser.parse_args()

    port_name = args.port
    available = mido.get_output_names()
    if port_name not in available:
        matches = [p for p in available if port_name.lower() in p.lower()]
        if matches:
            port_name = matches[0]

    port = mido.open_output(port_name)
    bank = json.load(open(args.params))
    notes = bank['notes']
    target_midis = [int(m) for m in args.midi.split(",")]

    rng = random.Random(42)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = f"exploration-{ts}.json"
    all_results = []

    print(f"Parameter Explorer — {len(target_midis)} notes, {args.rounds} rounds each")
    print(f"Pick best: 1/2/3/4, s=skip, q=quit\n")

    for midi in target_midis:
        key = None
        for v in [4, 5, 3]:
            k = f"m{midi:03d}_vel{v}"
            if k in notes:
                key = k
                break
        if not key:
            continue

        note = notes[key]
        parts = note.get('partials', [])
        if not parts:
            continue

        names = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]
        nname = f"{names[midi%12]}{midi//12-1}"
        print(f"{'='*40}")
        print(f"  {nname} (MIDI {midi})")
        print(f"{'='*40}")

        best_var = None
        for round_n in range(args.rounds):
            # Generate 4 random variations
            variations = [random_variation(parts, note, rng) for _ in range(4)]

            for i, var in enumerate(variations):
                apply_variation(port, midi, parts, note, var)
                time.sleep(0.2)
                print(f"  {i+1}", end="", flush=True)
                play(port, midi, dur=args.dur)

            print()
            inp = input(f"  Round {round_n+1}: best (1-4, s=skip, q=quit)? ").strip().lower()
            if inp == 'q':
                port.close()
                with open(log_path, 'w') as f:
                    json.dump(all_results, f, indent=2)
                print(f"Saved: {log_path}")
                return
            if inp == 's':
                break
            if inp in ['1', '2', '3', '4']:
                winner = int(inp) - 1
                best_var = variations[winner]
                all_results.append({
                    "midi": midi, "round": round_n,
                    "winner": winner, "params": best_var,
                })
                print(f"  Winner: {inp} — k1={best_var['k1_mult']:.1f}x k2={best_var['k2_mult']:.1f}x "
                      f"body={best_var['body_mult']:.1f}x beat={best_var['beat_hz']:.2f} "
                      f"a1={best_var['a1']:.2f} tau={best_var['tau1_scale']:.2f}x")

                # Next round: mutate around winner
                rng2 = random.Random()
                for v in variations:
                    for key in best_var:
                        # Small perturbation around winner
                        center = best_var[key]
                        v[key] = center * rng2.uniform(0.7, 1.4)

        print()

    port.close()
    with open(log_path, 'w') as f:
        json.dump(all_results, f, indent=2)
    print(f"\nSaved: {log_path}")

    # Quick analysis
    if all_results:
        import numpy as np
        print(f"\nWinning parameter averages across all notes:")
        params = list(all_results[0]['params'].keys())
        for p in params:
            vals = [r['params'][p] for r in all_results]
            print(f"  {p:>12}: {np.mean(vals):.3f} +/- {np.std(vals):.3f}  [{min(vals):.3f} - {max(vals):.3f}]")


if __name__ == "__main__":
    main()
