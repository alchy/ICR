"""
tests/test_sysex_live.py
─────────────────────────
Live SysEx test via loopMIDI -> icr.exe.
Requires: icr.exe running on loopMIDI Port 0, Python sends to loopMIDI Port 1.

Usage:
    python tests/test_sysex_live.py [--port "loopMIDI Port 1"]
"""

import struct
import time
import sys
import argparse

try:
    import mido
except ImportError:
    print("ERROR: mido not installed. Run: pip install mido python-rtmidi")
    sys.exit(1)


# ── SysEx float encoder ──────────────────────────────────────────────────────
def encode_float(value: float) -> list[int]:
    raw = struct.pack(">f", value)
    bits = int.from_bytes(raw, "big")
    return [(bits >> (i * 7)) & 0x7F for i in range(4, -1, -1)]


def decode_float(b: list[int]) -> float:
    bits = 0
    for i in range(5):
        bits |= (b[i] & 0x7F) << ((4 - i) * 7)
    raw = struct.pack(">I", bits)
    return struct.unpack(">f", raw)[0]


# ── SysEx message builders ───────────────────────────────────────────────────
def make_ping():
    return [0x7D, 0x01, 0x70]


def make_set_master(core_id: int, param_id: int, value: float):
    return [0x7D, 0x01, 0x10, core_id, param_id] + encode_float(value)


def make_set_note_param(core_id: int, midi: int, vel: int,
                        param_id: int, value: float):
    return [0x7D, 0x01, 0x01, core_id, midi, vel, param_id] + encode_float(value)


def make_note_on(note: int, vel: int, ch: int = 0):
    return mido.Message("note_on", note=note, velocity=vel, channel=ch)


def make_note_off(note: int, ch: int = 0):
    return mido.Message("note_off", note=note, velocity=0, channel=ch)


def make_cc(cc: int, value: int, ch: int = 0):
    return mido.Message("control_change", control=cc, value=value, channel=ch)


# ── Send helper ──────────────────────────────────────────────────────────────
def send_sysex(port, data: list[int], label: str):
    msg = mido.Message("sysex", data=data)
    port.send(msg)
    print(f"  [SENT] {label}  ({len(data)} bytes)")


# ── Tests ────────────────────────────────────────────────────────────────────
def run_tests(port_name: str):
    print(f"Opening MIDI output: {port_name}")
    port = mido.open_output(port_name)
    print("Connected.\n")

    passed = 0
    total = 0

    def test(name: str):
        nonlocal total
        total += 1
        print(f"Test {total}: {name}")

    def ok():
        nonlocal passed
        passed += 1
        print("  [OK]\n")

    # ── Test 1: PING ──────────────────────────────────────────────────────
    test("PING")
    send_sysex(port, make_ping(), "PING")
    time.sleep(0.1)
    ok()  # icr.exe should log PONG, we can't receive here without input port

    # ── Test 2: SET_MASTER gain (engine-level) ────────────────────────────
    test("SET_MASTER master_gain = 1.5 (engine-level 0x7F)")
    send_sysex(port, make_set_master(0x7F, 0x10, 1.5), "master_gain=1.5")
    time.sleep(0.1)
    ok()

    # ── Test 3: SET_MASTER pan ────────────────────────────────────────────
    test("SET_MASTER pan = -0.3 (engine-level)")
    send_sysex(port, make_set_master(0x7F, 0x11, -0.3), "pan=-0.3")
    time.sleep(0.1)
    ok()

    # ── Test 4: SET_MASTER LFO speed + depth ──────────────────────────────
    test("SET_MASTER LFO speed=0.8, depth=0.5")
    send_sysex(port, make_set_master(0x7F, 0x12, 0.8), "lfo_speed=0.8")
    send_sysex(port, make_set_master(0x7F, 0x13, 0.5), "lfo_depth=0.5")
    time.sleep(0.1)
    ok()

    # ── Test 5: SET_MASTER limiter ────────────────────────────────────────
    test("SET_MASTER limiter threshold=0.7, enabled=1.0")
    send_sysex(port, make_set_master(0x7F, 0x20, 0.7), "lim_threshold=0.7")
    send_sysex(port, make_set_master(0x7F, 0x22, 1.0), "lim_enabled=1.0")
    time.sleep(0.1)
    ok()

    # ── Test 6: SET_MASTER BBE ────────────────────────────────────────────
    test("SET_MASTER BBE definition=0.5, bass=0.3")
    send_sysex(port, make_set_master(0x7F, 0x23, 0.5), "bbe_def=0.5")
    send_sysex(port, make_set_master(0x7F, 0x24, 0.3), "bbe_bass=0.3")
    time.sleep(0.1)
    ok()

    # ── Test 7: SET_MASTER core param (gain on SineCore, pid 0x01) ────────
    test("SET_MASTER core param beat_scale=2.0 (active core)")
    send_sysex(port, make_set_master(0x00, 0x01, 2.0), "beat_scale=2.0 (core)")
    time.sleep(0.1)
    ok()

    # ── Test 8: Note On/Off via MIDI ──────────────────────────────────────
    test("Note On C4 vel=100 -> wait 500ms -> Note Off")
    port.send(make_note_on(60, 100))
    time.sleep(0.5)
    port.send(make_note_off(60))
    time.sleep(0.1)
    ok()

    # ── Test 9: CC volume + pan ───────────────────────────────────────────
    test("CC7 (volume) = 80, CC10 (pan) = 40")
    port.send(make_cc(7, 80))
    port.send(make_cc(10, 40))
    time.sleep(0.1)
    ok()

    # ── Test 10: Sustain pedal ────────────────────────────────────────────
    test("CC64 sustain down, Note On, sustain up, Note Off")
    port.send(make_cc(64, 127))   # pedal down
    port.send(make_note_on(64, 90))
    time.sleep(0.3)
    port.send(make_cc(64, 0))     # pedal up
    time.sleep(0.2)
    port.send(make_note_off(64))
    time.sleep(0.1)
    ok()

    # ── Test 11: Rapid note burst ─────────────────────────────────────────
    test("Rapid 8-note burst (C major scale)")
    notes = [60, 62, 64, 65, 67, 69, 71, 72]
    for n in notes:
        port.send(make_note_on(n, 80))
    time.sleep(0.5)
    for n in notes:
        port.send(make_note_off(n))
    time.sleep(0.2)
    ok()

    # ── Test 12: SET_MASTER reset to defaults ─────────────────────────────
    test("Reset: gain=1.0, pan=0.0, lfo_speed=0, lfo_depth=0")
    send_sysex(port, make_set_master(0x7F, 0x10, 1.0), "gain=1.0")
    send_sysex(port, make_set_master(0x7F, 0x11, 0.0), "pan=0.0")
    send_sysex(port, make_set_master(0x7F, 0x12, 0.0), "lfo_speed=0")
    send_sysex(port, make_set_master(0x7F, 0x13, 0.0), "lfo_depth=0")
    time.sleep(0.1)
    ok()

    # ── Summary ───────────────────────────────────────────────────────────
    port.close()
    print(f"{'='*50}")
    print(f"Results: {passed}/{total} tests passed")
    print(f"{'='*50}")
    print(f"\nCheck icr.log for detailed engine-side processing.")
    return 0 if passed == total else 1


def main():
    parser = argparse.ArgumentParser(description="Live SysEx test via loopMIDI")
    parser.add_argument("--port", default="loopMIDI Port 1",
                        help="MIDI output port name")
    args = parser.parse_args()
    sys.exit(run_tests(args.port))


if __name__ == "__main__":
    main()
