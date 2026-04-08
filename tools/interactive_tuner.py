#!/usr/bin/env python3
"""
tools/interactive_tuner.py
──────────────────────────
Interactive per-note tuner: play → listen → score → adjust → repeat.

Sends SysEx parameter changes to ICR via MIDI, plays the note,
gets user score, adjusts parameters based on score and physical
knowledge, repeats until the user is satisfied.

Usage:
    python tools/interactive_tuner.py --port "loopMIDI Port 1" \
        --params soundbanks-additive/pl-grand-laws.json \
        --midi 55

    python tools/interactive_tuner.py --port "loopMIDI Port 1" \
        --params soundbanks-additive/pl-grand-laws.json \
        --auto-bad   # automatically iterate on worst-scoring notes
"""

import argparse
import json
import math
import struct
import sys
import time
from pathlib import Path

import numpy as np

try:
    import mido
except ImportError:
    print("ERROR: pip install mido python-rtmidi")
    sys.exit(1)

REPO_ROOT = Path(__file__).parent.parent.resolve()


# ── SysEx encoding (matches core_engine.cpp protocol) ────────────────────────

SYSEX_MFR     = [0x7D]           # educational/dev manufacturer ID
CMD_SET_NOTE  = 0x01             # SET_NOTE_PARAM

def float_to_bytes(val: float) -> list:
    """Encode float32 as 5 × 7-bit MIDI bytes."""
    raw = struct.pack(">f", val)
    i32 = int.from_bytes(raw, "big")
    return [
        (i32 >> 28) & 0x0F,
        (i32 >> 21) & 0x7F,
        (i32 >> 14) & 0x7F,
        (i32 >>  7) & 0x7F,
        (i32      ) & 0x7F,
    ]

def make_set_note_sysex(midi: int, vel: int, param_id: int, value: float) -> mido.Message:
    """Build SysEx message to set a note parameter."""
    data = SYSEX_MFR + [CMD_SET_NOTE, midi & 0x7F, vel & 0x7F, param_id & 0x7F]
    data += float_to_bytes(value)
    return mido.Message("sysex", data=data)

# Parameter IDs (from SYSEX_PROTOCOL.md)
PARAM_IDS = {
    "f0_hz":             0x01,
    "B":                 0x02,
    "attack_tau":        0x03,
    "A_noise":           0x04,
    "rms_gain":          0x05,
    "phi_diff":          0x06,
    "stereo_width":      0x07,
}

# Per-partial param IDs
PARTIAL_PARAM_IDS = {
    "f_hz":    0x10,
    "A0":      0x11,
    "tau1":    0x12,
    "tau2":    0x13,
    "a1":      0x14,
    "beat_hz": 0x15,
    "phi":     0x16,
}


def note_name(midi: int) -> str:
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    return f"{names[midi % 12]}{midi // 12 - 1}"


# ── Physical laws ─────────────────────────────────────────────────────────────

def law_tau1(midi): return math.exp(-0.038 * midi + 1.27)
def law_tau2(midi): return math.exp(-0.040 * midi + 3.12)
def law_rms(midi):  return math.exp(0.098 * midi - 10.84)
def law_noise(midi): return -0.000055 * midi**2 + 0.0102 * midi + 0.476


# ── Tuning strategies ────────────────────────────────────────────────────────

def suggest_adjustment(midi: int, score: int, current: dict, history: list) -> dict:
    """Suggest parameter adjustments based on score and history.

    Returns dict of {param_name: new_value} to try next.
    """
    adjustments = {}

    if score >= 7:
        # Good — small random perturbation to explore nearby
        return {}

    # Analyze trend from history
    improving = len(history) >= 2 and history[-1]["score"] > history[-2]["score"]

    if score <= 3:
        # Bad — aggressive correction toward law
        blend = 0.8
    elif score <= 5:
        # Mediocre — moderate correction
        blend = 0.4
    else:
        # Decent — light touch
        blend = 0.2

    # tau1: if too far from law, correct
    ideal_tau1 = law_tau1(midi)
    cur_tau1 = current.get("tau1", ideal_tau1)
    if cur_tau1 / ideal_tau1 > 2.0 or cur_tau1 / ideal_tau1 < 0.5:
        adjustments["tau1"] = (1 - blend) * cur_tau1 + blend * ideal_tau1

    # rms_gain: correct toward law
    ideal_rms = law_rms(midi)
    cur_rms = current.get("rms_gain", ideal_rms)
    if cur_rms / ideal_rms > 3.0 or cur_rms / ideal_rms < 0.33:
        adjustments["rms_gain"] = (1 - blend) * cur_rms + blend * ideal_rms

    # A_noise: correct toward law
    ideal_noise = law_noise(midi)
    cur_noise = current.get("A_noise", ideal_noise)
    adjustments["A_noise"] = (1 - blend) * cur_noise + blend * ideal_noise

    # If we're not improving after 3 tries, try more extreme
    if len(history) >= 3 and all(h["score"] <= 4 for h in history[-3:]):
        adjustments["tau1"] = ideal_tau1  # full law
        adjustments["rms_gain"] = ideal_rms

    return adjustments


def send_params(port, midi: int, vel: int, params: dict):
    """Send parameter changes via SysEx."""
    for key, value in params.items():
        if key in PARAM_IDS:
            msg = make_set_note_sysex(midi, vel, PARAM_IDS[key], value)
            port.send(msg)
            time.sleep(0.01)


def play_and_score(port, midi: int, velocity: int, duration: float) -> int:
    """Play a note and get user score."""
    port.send(mido.Message("note_on", note=midi, velocity=velocity))
    time.sleep(duration)
    port.send(mido.Message("note_off", note=midi, velocity=0))
    time.sleep(0.3)

    while True:
        try:
            inp = input(f"  Score (0-9, r=replay, s=skip): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return -1

        if inp == "r":
            port.send(mido.Message("note_on", note=midi, velocity=velocity))
            time.sleep(duration)
            port.send(mido.Message("note_off", note=midi, velocity=0))
            time.sleep(0.3)
            continue
        elif inp == "s":
            return -2
        elif inp in [str(i) for i in range(10)]:
            return int(inp)
        else:
            print("  Invalid. Enter 0-9, r, or s.")


def tune_note(port, midi: int, velocity: int, duration: float,
              bank_notes: dict, max_rounds: int = 8):
    """Interactively tune a single note."""
    # Load current parameters
    key = f"m{midi:03d}_vel5"
    for v in [5, 4, 3, 6]:
        k = f"m{midi:03d}_vel{v}"
        if k in bank_notes:
            key = k
            break

    if key not in bank_notes:
        print(f"  Note {midi} not in bank")
        return []

    note = bank_notes[key]
    vel_idx = int(key.split("vel")[1])

    current = {
        "tau1": note["partials"][0]["tau1"] if note["partials"] else 0.5,
        "rms_gain": note.get("rms_gain", 0.01),
        "A_noise": note.get("A_noise", 0.5),
        "attack_tau": note.get("attack_tau", 0.03),
    }

    history = []

    print(f"\n{'='*50}")
    print(f"  Tuning {note_name(midi)} (MIDI {midi})")
    print(f"  Law targets: tau1={law_tau1(midi):.3f} rms={law_rms(midi):.6f} noise={law_noise(midi):.3f}")
    print(f"  Current:     tau1={current['tau1']:.3f} rms={current['rms_gain']:.6f} noise={current['A_noise']:.3f}")
    print(f"{'='*50}")

    for round_n in range(max_rounds):
        print(f"\n  Round {round_n + 1}/{max_rounds}")

        # Play and score
        score = play_and_score(port, midi, velocity, duration)
        if score == -1:  # quit
            return history
        if score == -2:  # skip
            break

        history.append({"round": round_n, "score": score, "params": dict(current)})
        print(f"  -> Score: {score}/9")

        if score >= 8:
            print(f"  Excellent! Moving on.")
            break

        # Get adjustments
        adj = suggest_adjustment(midi, score, current, history)
        if not adj:
            print(f"  No adjustment suggested (score OK).")
            break

        # Apply via SysEx
        print(f"  Adjusting: {', '.join(f'{k}={v:.4f}' for k, v in adj.items())}")
        send_params(port, midi, vel_idx, adj)
        current.update(adj)

        time.sleep(0.2)  # let SysEx propagate

    return history


def main():
    parser = argparse.ArgumentParser(description="Interactive per-note tuner")
    parser.add_argument("--port", required=True, help="MIDI output port")
    parser.add_argument("--params", required=True, help="Soundbank JSON (for reference)")
    parser.add_argument("--midi", type=str, default=None,
                        help="Specific MIDI note(s) to tune (e.g., '55' or '49,55,63')")
    parser.add_argument("--auto-bad", action="store_true",
                        help="Automatically pick worst notes from latest scoring")
    parser.add_argument("--scoring", default=None,
                        help="Scoring JSON (for --auto-bad, default: latest)")
    parser.add_argument("--velocity", type=int, default=81,
                        help="Playback velocity (default: 81 = vel_idx 5 exact)")
    parser.add_argument("--duration", type=float, default=8.0,
                        help="Note duration in seconds (default: 8)")
    args = parser.parse_args()

    # Open MIDI port
    available = mido.get_output_names()
    port_name = args.port
    if port_name not in available:
        matches = [p for p in available if port_name.lower() in p.lower()]
        if matches:
            port_name = matches[0]
        else:
            print(f"ERROR: port '{args.port}' not found. Available: {available}")
            return

    port = mido.open_output(port_name)
    bank = json.load(open(args.params))
    notes = bank.get("notes", {})

    # Select notes to tune
    if args.midi:
        target_midis = [int(m) for m in args.midi.split(",")]
    elif args.auto_bad:
        # Find latest scoring
        if args.scoring:
            scoring_path = args.scoring
        else:
            scoring_files = sorted(Path(".").glob("scoring-*.json"), reverse=True)
            if not scoring_files:
                print("No scoring files found. Run blind_scoring.py first.")
                return
            scoring_path = str(scoring_files[0])

        scoring = json.load(open(scoring_path))
        target_midis = sorted([r["midi"] for r in scoring["scores"] if r["score"] <= 4])
        print(f"Auto-selected {len(target_midis)} notes with score <= 4 from {scoring_path}")
    else:
        print("Specify --midi or --auto-bad")
        return

    print(f"Notes to tune: {target_midis}")
    print(f"Velocity: {args.velocity}, Duration: {args.duration}s")
    print(f"Port: {port_name}")

    all_history = {}
    for midi in target_midis:
        h = tune_note(port, midi, args.velocity, args.duration, notes)
        if h:
            all_history[midi] = h
        if h and h[-1].get("score", -1) == -1:
            break  # user quit

    port.close()

    # Save results
    if all_history:
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_path = f"tuning-{ts}.json"
        with open(out_path, "w") as f:
            json.dump({"timestamp": ts, "params_file": args.params,
                       "notes": {str(k): v for k, v in all_history.items()}}, f, indent=2)
        print(f"\nSaved tuning history: {out_path}")


if __name__ == "__main__":
    main()
