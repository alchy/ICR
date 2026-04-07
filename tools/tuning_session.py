#!/usr/bin/env python3
"""
tools/tuning_session.py
───────────────────────
Scoring endpoint for interactive tuning sessions.
Claude sends notes via helper, user scores here.

Reads commands from tuning_cmd.txt (written by Claude's helper).
Writes scores to tuning_log.txt (read by Claude).

Commands in tuning_cmd.txt:
    PLAY <midi> <velocity> <duration_s>
    SYSEX <midi> <vel_idx> <param_id> <float_value>
    QUIT

Usage:
    python tools/tuning_session.py --port "loopMIDI Port 1"
"""

import argparse
import struct
import sys
import time
from pathlib import Path

try:
    import mido
except ImportError:
    print("ERROR: pip install mido python-rtmidi")
    sys.exit(1)

CMD_FILE = Path("tuning_cmd.txt")
LOG_FILE = Path("tuning_log.txt")

SYSEX_MFR = [0x7D]
CMD_SET_NOTE = 0x01


def float_to_bytes(val: float) -> list:
    raw = struct.pack(">f", val)
    i32 = int.from_bytes(raw, "big")
    return [(i32 >> 28) & 0x0F, (i32 >> 21) & 0x7F,
            (i32 >> 14) & 0x7F, (i32 >> 7) & 0x7F, i32 & 0x7F]


def log(msg: str):
    with open(LOG_FILE, "a", encoding="utf-8", buffering=1) as f:
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        f.write(line + "\n")
        f.flush()
    print(line)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    args = parser.parse_args()

    available = mido.get_output_names()
    port_name = args.port
    if port_name not in available:
        matches = [p for p in available if port_name.lower() in p.lower()]
        if matches:
            port_name = matches[0]
        else:
            print(f"Port not found: {args.port}. Available: {available}")
            return

    port = mido.open_output(port_name)

    # Init files
    CMD_FILE.write_text("")
    LOG_FILE.write_text("")
    log("SESSION START — waiting for commands in tuning_cmd.txt")
    log("Score: 0-9, s=skip, q=quit")

    last_cmd_mtime = 0

    while True:
        # Poll for new command
        try:
            if not CMD_FILE.exists():
                time.sleep(0.2)
                continue
            mtime = CMD_FILE.stat().st_mtime
            if mtime <= last_cmd_mtime:
                time.sleep(0.2)
                continue
            last_cmd_mtime = mtime

            lines = CMD_FILE.read_text().strip().split("\n")
            cmd_line = lines[-1].strip()
            if not cmd_line:
                time.sleep(0.2)
                continue

        except Exception:
            time.sleep(0.2)
            continue

        parts = cmd_line.split()
        cmd = parts[0].upper()

        if cmd == "QUIT":
            log("QUIT received")
            break

        elif cmd == "PLAY":
            midi = int(parts[1])
            vel = int(parts[2])
            dur = float(parts[3])
            names = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]
            nname = f"{names[midi%12]}{midi//12-1}"
            log(f"PLAY {nname} (MIDI {midi}) vel={vel} dur={dur}s")

            port.send(mido.Message("note_on", note=midi, velocity=vel))
            time.sleep(dur)
            port.send(mido.Message("note_off", note=midi, velocity=0))
            time.sleep(0.3)

            # Get score
            while True:
                try:
                    inp = input("  Score (0-9, r=replay, s=skip): ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    inp = "q"
                if inp == "r":
                    port.send(mido.Message("note_on", note=midi, velocity=vel))
                    time.sleep(dur)
                    port.send(mido.Message("note_off", note=midi, velocity=0))
                    time.sleep(0.3)
                    continue
                elif inp == "s":
                    log(f"SCORE {midi} SKIP")
                    break
                elif inp == "q":
                    log("QUIT by user")
                    port.close()
                    return
                elif inp in [str(i) for i in range(10)]:
                    log(f"SCORE {midi} {inp}")
                    break
                else:
                    print("  0-9, r, s, q")

        elif cmd == "SYSEX":
            midi = int(parts[1])
            vel_idx = int(parts[2])
            param_id = int(parts[3], 0)  # hex support
            value = float(parts[4])
            data = SYSEX_MFR + [CMD_SET_NOTE, midi & 0x7F, vel_idx & 0x7F, param_id & 0x7F]
            data += float_to_bytes(value)
            port.send(mido.Message("sysex", data=data))
            log(f"SYSEX midi={midi} vel={vel_idx} param=0x{param_id:02x} val={value:.6f}")

        elif cmd == "NOTE":
            # Just a comment/note from Claude
            log(f"NOTE: {' '.join(parts[1:])}")

        time.sleep(0.1)

    port.close()
    log("SESSION END")


if __name__ == "__main__":
    main()
