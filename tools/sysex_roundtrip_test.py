"""
tools/sysex_roundtrip_test.py
──────────────────────────────
SysEx round-trip validator: send a soundbank to ICR via SET_BANK (0x03),
ask ICR to export it back via EXPORT_BANK (0xF2), then compare field-by-field.

Usage
─────
  # List available MIDI ports
  python tools/sysex_roundtrip_test.py --list-ports

  # Run round-trip test (ICR.exe must be running and listening on the port)
  python tools/sysex_roundtrip_test.py --port "loopMIDI Port 1" --bank soundbanks-additive/params-vv-rhodes.json

  # Skip sending SET_BANK (use bank already loaded in ICR)
  python tools/sysex_roundtrip_test.py --port "loopMIDI Port 1" --bank soundbanks-additive/params-vv-rhodes.json --skip-send

ICR.exe startup reminder
────────────────────────
  ICR.exe --core AdditiveSynthesisPianoCore --params soundbanks-additive/params-vv-rhodes.json --port <midi_port_index>

Fields compared (round-trip safe)
──────────────────────────────────
  Per-note:    f0_hz, B, phi_diff, attack_tau, A_noise, rms_gain
  Per-partial: k, f_hz, A0, tau1, tau2, a1, beat_hz, phi
  EQ biquads:  b[0..2], a[0..1]   (if present in original)

Fields NOT compared (not exported by ICR)
──────────────────────────────────────────
  K_valid, spectral_eq  — only in original soundbank, ICR doesn't re-export them
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO_ROOT / "sound-editor" / "backend"))

from sysex_bridge import SysExBridge, list_output_ports   # noqa: E402


# ── Tolerances ────────────────────────────────────────────────────────────────

# float32 survives JSON -> C++ float -> JSON with ~1e-6 relative error
ATOL = 1e-4   # absolute tolerance for all float comparisons


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_notes(path: str) -> dict[tuple[int, int], dict]:
    """
    Load soundbank JSON and return a flat dict keyed by (midi, vel_idx).
    Handles both the original dict format ('m033_vel0': {...}) and the
    array format produced by ICR's exportBankJson.
    """
    with open(path, encoding="utf-8") as f:
        root = json.load(f)

    notes_raw = root["notes"]
    result: dict[tuple[int, int], dict] = {}

    if isinstance(notes_raw, dict):
        entries = notes_raw.values()
    else:
        entries = notes_raw

    for n in entries:
        key = (int(n["midi"]), int(n["vel"]))
        result[key] = n

    return result


def _approx_eq(a: float, b: float, atol: float = ATOL) -> bool:
    return math.isclose(float(a), float(b), rel_tol=1e-5, abs_tol=atol)


def compare_banks(original_path: str, exported_path: str) -> bool:
    """
    Compare two soundbank JSONs field-by-field.
    Prints a summary and returns True if they match within tolerance.
    """
    orig  = _load_notes(original_path)
    exprt = _load_notes(exported_path)

    errors: list[str] = []
    checked_notes = 0
    missing_in_export: list[tuple[int, int]] = []

    for key, on in sorted(orig.items()):
        midi, vel = key
        if key not in exprt:
            missing_in_export.append(key)
            continue

        en = exprt[key]
        checked_notes += 1

        # ── Scalar fields ──────────────────────────────────────────────────
        for field in ("f0_hz", "B", "phi_diff", "attack_tau", "A_noise", "rms_gain"):
            ov = on.get(field, 0.0)
            ev = en.get(field, 0.0)
            if not _approx_eq(ov, ev):
                errors.append(
                    f"  midi={midi} vel={vel}  {field}: orig={ov:.6g}  export={ev:.6g}"
                    f"  diff={abs(float(ov)-float(ev)):.2e}"
                )

        # ── Partials ───────────────────────────────────────────────────────
        orig_parts  = {p["k"]: p for p in on["partials"]}
        exprt_parts = {p["k"]: p for p in en["partials"]}

        for k, op in orig_parts.items():
            if k not in exprt_parts:
                errors.append(f"  midi={midi} vel={vel}  partial k={k} missing in export")
                continue
            ep = exprt_parts[k]
            for field in ("f_hz", "A0", "tau1", "tau2", "a1", "beat_hz", "phi"):
                ov = op.get(field, 0.0)
                ev = ep.get(field, 0.0)
                if not _approx_eq(ov, ev):
                    errors.append(
                        f"  midi={midi} vel={vel} k={k}  {field}: "
                        f"orig={ov:.6g}  export={ev:.6g}  diff={abs(float(ov)-float(ev)):.2e}"
                    )

        # ── EQ biquads (optional) ──────────────────────────────────────────
        if "eq_biquads" in on and "eq_biquads" in en:
            for bi, (ob, eb) in enumerate(zip(on["eq_biquads"], en["eq_biquads"])):
                for ci, (ov, ev) in enumerate(zip(ob["b"], eb["b"])):
                    if not _approx_eq(ov, ev):
                        errors.append(
                            f"  midi={midi} vel={vel} biquad[{bi}].b[{ci}]: "
                            f"orig={ov:.6g}  export={ev:.6g}"
                        )
                for ci, (ov, ev) in enumerate(zip(ob["a"], eb["a"])):
                    if not _approx_eq(ov, ev):
                        errors.append(
                            f"  midi={midi} vel={vel} biquad[{bi}].a[{ci}]: "
                            f"orig={ov:.6g}  export={ev:.6g}"
                        )
        elif "eq_biquads" in on and "eq_biquads" not in en:
            errors.append(f"  midi={midi} vel={vel}  eq_biquads present in orig but missing in export")

    # ── Extra notes in export (unexpected) ────────────────────────────────
    extra_in_export = [k for k in exprt if k not in orig]

    # ── Report ─────────────────────────────────────────────────────────────
    print(f"\nOriginal notes : {len(orig)}")
    print(f"Exported notes : {len(exprt)}")
    print(f"Compared notes : {checked_notes}")

    if missing_in_export:
        print(f"\nMissing in export ({len(missing_in_export)}):")
        for m, v in missing_in_export[:10]:
            print(f"  midi={m} vel={v}")
        if len(missing_in_export) > 10:
            print(f"  ... and {len(missing_in_export)-10} more")

    if extra_in_export:
        print(f"\nExtra in export (unexpected) ({len(extra_in_export)}):")
        for m, v in extra_in_export[:5]:
            print(f"  midi={m} vel={v}")

    if errors:
        print(f"\nField mismatches ({len(errors)}):")
        for e in errors[:30]:
            print(e)
        if len(errors) > 30:
            print(f"  ... and {len(errors)-30} more")
        print(f"\nRESULT: FAIL  ({len(errors)} mismatches, {len(missing_in_export)} missing notes)")
        return False
    else:
        print(f"\nRESULT: PASS  -- all {checked_notes} notes match within atol={ATOL}")
        return True


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="SysEx round-trip test: SET_BANK -> EXPORT_BANK -> compare",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--list-ports", action="store_true",
                        help="List available MIDI output ports and exit")
    parser.add_argument("--port",  default=None,
                        help="MIDI output port name (e.g. 'loopMIDI Port 1')")
    parser.add_argument("--bank",  default=None,
                        help="Original soundbank JSON to send and compare against")
    parser.add_argument("--skip-send", action="store_true",
                        help="Skip SET_BANK (use bank already loaded in ICR)")
    parser.add_argument("--export-path", default=None,
                        help="Where ICR should write the exported JSON "
                             "(default: auto temp file; must be absolute path on ICR host)")
    parser.add_argument("--wait", type=float, default=1.5,
                        help="Seconds to wait after EXPORT_BANK before reading file "
                             "(default: 1.5)")
    args = parser.parse_args()

    if args.list_ports:
        ports = list_output_ports()
        if not ports:
            print("No MIDI output ports found (is mido/python-rtmidi installed?)")
            return 1
        print("Available MIDI output ports:")
        for i, p in enumerate(ports):
            print(f"  {i}: {p}")
        return 0

    if not args.port:
        parser.error("--port is required (use --list-ports to see options)")
    if not args.bank and not args.skip_send:
        parser.error("--bank is required unless --skip-send is set")
    if not args.bank:
        parser.error("--bank is required for comparison (even with --skip-send)")

    # ── Resolve export path ────────────────────────────────────────────────
    if args.export_path:
        export_path = args.export_path
    else:
        # Use a predictable temp path (ICR writes to the local filesystem)
        export_path = str(Path(tempfile.gettempdir()) / "icr_exported_bank.json")
        # Remove stale file from a previous run
        try:
            os.remove(export_path)
        except FileNotFoundError:
            pass

    print(f"MIDI port      : {args.port}")
    print(f"Bank file      : {args.bank}")
    print(f"Export path    : {export_path}")
    print(f"Skip SET_BANK  : {args.skip_send}")

    bridge = SysExBridge()
    try:
        bridge.open(args.port)
    except Exception as e:
        print(f"\nFailed to open MIDI port: {e}")
        return 1

    try:
        # ── PING check ────────────────────────────────────────────────────
        print("\nSending PING ...", end=" ", flush=True)
        bridge.ping()
        time.sleep(0.1)
        print("sent (no PONG check over output port)")

        # ── SET_BANK ──────────────────────────────────────────────────────
        if not args.skip_send:
            bank_bytes = Path(args.bank).read_bytes()
            print(f"Sending SET_BANK ({len(bank_bytes):,} bytes) ...", end=" ", flush=True)
            bridge.set_bank(bank_bytes)
            time.sleep(0.5)   # let ICR finish processing the last chunk
            print("done")
        else:
            print("SET_BANK skipped")

        # ── EXPORT_BANK ───────────────────────────────────────────────────
        print(f"Sending EXPORT_BANK -> {export_path} ...", end=" ", flush=True)
        bridge.export_bank(export_path)
        print("sent")

        # ── Poll for file ─────────────────────────────────────────────────
        deadline = time.time() + max(args.wait, 5.0)
        while time.time() < deadline:
            if os.path.exists(export_path):
                # Make sure write is complete (wait for size to stabilise)
                s0 = os.path.getsize(export_path)
                time.sleep(0.1)
                s1 = os.path.getsize(export_path)
                if s1 == s0 and s1 > 0:
                    break
            time.sleep(0.1)
        else:
            print(f"\nTimeout: ICR did not write {export_path} within {args.wait:.1f}s")
            print("Check that ICR.exe is running and the correct MIDI port is selected.")
            return 1

        print(f"Export file received ({os.path.getsize(export_path):,} bytes)")

        # ── Compare ───────────────────────────────────────────────────────
        ok = compare_banks(args.bank, export_path)
        return 0 if ok else 1

    finally:
        bridge.close()


if __name__ == "__main__":
    sys.exit(main())
