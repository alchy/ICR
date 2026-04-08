"""
anchor_helper.py - Text-mode helper for Instrument DNA anchor note selection.

Usage:
    python tools/anchor_helper.py
    python tools/anchor_helper.py --load soundbanks-additive/my.json
    python tools/anchor_helper.py --load soundbanks-additive/my.json --anchors anchors/my.json

Commands (at the prompt):
    load <path>              Load a soundbank JSON
    add-bank <path>          Add a second bank to the session (multi-bank mode)
    list-banks               List all loaded banks
    use <bank_idx>           Switch active bank for marking (default: 0)
    screen                   Run auto-screener and show summary
    list [midi_range]        List notes with current quality (e.g. 'list 60-72')
    show <midi>              Show all velocity layers for a MIDI note
    mark <midi> <spec>       Mark quality - spec examples:
                               mark 64 all:1.0
                               mark 64 5:1.0 6:1.0 7:0.9
                               mark 64 0-3:0.0 4-7:1.0
    auto <midi>              Apply auto-screener to one note
    load-anchors <path>      Load existing anchor file (to continue editing)
    save [path]              Save anchor file (default: anchors/<bank_stem>.json)
    status                   Show how many notes marked
    help                     Show this help
    quit / exit              Exit
"""

import io
import json
import math
import sys

# Force UTF-8 output on Windows to handle box-drawing characters gracefully.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
import os
import re
from pathlib import Path
from typing import Dict, Optional

# ---------------------------------------------------------------------------
# Auto-screener: objective quality pre-assessment
# ---------------------------------------------------------------------------

def _auto_quality(note: dict) -> tuple[float, list[str]]:
    """
    Returns (quality_score, [reasons]) for a single note entry.
    Score: 0.0 = clearly broken, 0.5 = uncertain, 1.0 = looks plausible.
    """
    reasons = []
    score = 1.0

    if not note.get("partials"):
        return 0.0, ["no partials"]

    p0 = note["partials"][0]
    B = note.get("B", 0.0)
    beat = p0.get("beat_hz", 0.0)
    a1 = p0.get("a1", 1.0)
    tau1 = p0.get("tau1", 0.0)
    tau2 = p0.get("tau2", 0.0)
    A0 = p0.get("A0", 0.0)
    interp = note.get("is_interpolated", False)

    # Degenerate bi-exponential: tau1 = tau2 (or a1 = 1.0 exactly)
    if a1 >= 0.9999 or abs(tau1 - tau2) < 1e-6:
        score = min(score, 0.0)
        reasons.append("bi-exp degenerate (a1=1 / tau1~=tau2)")

    # No beating detected
    if beat == 0.0:
        score = min(score, 0.1)
        reasons.append("beat=0")

    # Inharmonicity physically unreasonable
    if B <= 1e-15:
        score = min(score, 0.0)
        reasons.append(f"B≈0 ({B:.1e})")
    elif B > 0.1:
        score = min(score, 0.0)
        reasons.append(f"B too large ({B:.2f})")

    # A0 anomaly check is done at bank level.
    # Here we only flag obviously non-physical values.
    if A0 <= 0.0:
        score = min(score, 0.0)
        reasons.append("A0<=0")

    # NN-interpolated: lower trust than measured by default
    if interp:
        score = min(score, 0.4)
        reasons.append("NN-interpolated")

    if not reasons:
        reasons.append("looks plausible")

    return round(score, 2), reasons


# ---------------------------------------------------------------------------
# Anchor file I/O
# ---------------------------------------------------------------------------

def _default_anchor_path(bank_path: str) -> str:
    stem = Path(bank_path).stem
    return str(Path("anchors") / f"{stem}.json")


def _empty_anchor_doc(bank_path: str, instrument_type: str = "unknown") -> dict:
    return {
        "instrument_type": instrument_type,
        "description": "",
        "banks": [
            {
                "bank": str(Path(bank_path)),
                "notes": {}
            }
        ]
    }


def load_anchor_file(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def save_anchor_file(doc: dict, path: str):
    os.makedirs(Path(path).parent, exist_ok=True)
    with open(path, "w") as f:
        json.dump(doc, f, indent=2)
    print(f"Saved: {path}")


# ---------------------------------------------------------------------------
# Note key helpers
# ---------------------------------------------------------------------------

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

def midi_to_name(midi: int) -> str:
    octave = (midi // 12) - 1
    name = NOTE_NAMES[midi % 12]
    return f"{name}{octave}"


def note_key(midi: int, vel: int) -> str:
    return f"m{midi:03d}_vel{vel}"


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

QUALITY_BAR = {
    1.0: "[########] 1.0",
    0.9: "[#######.] 0.9",
    0.8: "[######..] 0.8",
    0.7: "[#####...] 0.7",
    0.6: "[####....] 0.6",
    0.5: "[###.....] 0.5",
    0.4: "[##......] 0.4",
    0.3: "[#.......] 0.3",
    0.2: "[o.......] 0.2",
    0.1: "[o.......] 0.1",
    0.0: "[........] 0.0",
}


def _quality_bar(q: Optional[float]) -> str:
    if q is None:
        return "         ---"
    rounded = round(round(q * 10) / 10, 1)
    return QUALITY_BAR.get(rounded, f"         {q:.1f}")


def _show_note(bank_notes: dict, midi: int, anchor_notes: dict):
    print(f"\n  Note m{midi:03d} ({midi_to_name(midi)}):")
    print(f"  {'vel':<5} {'quality':<16} {'B':>10} {'beat_hz':>9} {'a1':>6} {'tau1':>7} {'tau2':>8} {'A0_k1':>9}  flags")
    print(f"  {'-'*95}")
    for vel in range(8):
        key = note_key(midi, vel)
        note = bank_notes.get(key)
        if note is None:
            print(f"  {vel:<5} {'(missing)'}")
            continue
        aq, reasons = _auto_quality(note)
        p0 = note["partials"][0] if note.get("partials") else {}
        B = note.get("B", 0.0)
        beat = p0.get("beat_hz", 0.0)
        a1 = p0.get("a1", 1.0)
        tau1 = p0.get("tau1", 0.0)
        tau2 = p0.get("tau2", 0.0)
        A0 = p0.get("A0", 0.0)
        user_q = anchor_notes.get(key)
        q_bar = _quality_bar(user_q)
        auto_str = f"auto={aq:.1f}"
        flag_str = "; ".join(reasons) if reasons != ["looks plausible"] else ""
        print(f"  {vel:<5} {q_bar:<16} {B:>10.2e} {beat:>9.4f} {a1:>6.3f} {tau1:>7.3f} {tau2:>8.3f} {A0:>9.4f}  {auto_str}  {flag_str}")
    print()


# ---------------------------------------------------------------------------
# Parse mark specification
# ---------------------------------------------------------------------------

def _parse_mark_spec(spec_tokens: list[str]) -> Dict[int, float]:
    """
    Parse tokens like:  all:1.0  5:0.9  0-3:0.0  4:1.0 5:1.0 6:0.8
    Returns {vel: quality} dict.
    """
    result = {}
    for token in spec_tokens:
        if ":" not in token:
            print(f"  [warn] Ignoring token without ':' → '{token}'")
            continue
        vel_part, q_part = token.split(":", 1)
        try:
            q = float(q_part)
        except ValueError:
            print(f"  [warn] Bad quality value: '{q_part}'")
            continue
        if not (0.0 <= q <= 1.0):
            print(f"  [warn] Quality must be 0.0–1.0, got {q}")
            continue

        if vel_part.lower() == "all":
            for v in range(8):
                result[v] = q
        elif "-" in vel_part:
            parts = vel_part.split("-", 1)
            try:
                v0, v1 = int(parts[0]), int(parts[1])
                for v in range(v0, v1 + 1):
                    result[v] = q
            except ValueError:
                print(f"  [warn] Bad velocity range: '{vel_part}'")
        else:
            try:
                result[int(vel_part)] = q
            except ValueError:
                print(f"  [warn] Bad velocity: '{vel_part}'")
    return result


# ---------------------------------------------------------------------------
# Main REPL
# ---------------------------------------------------------------------------

class AnchorSession:
    def __init__(self):
        self.banks: list[dict] = []          # list of loaded soundbank dicts
        self.bank_paths: list[str] = []
        self.active_bank: int = 0
        self.anchor_doc: Optional[dict] = None
        self.anchor_path: Optional[str] = None

    def _active_notes(self) -> dict:
        if not self.banks:
            return {}
        return self.banks[self.active_bank].get("notes", {})

    def _anchor_notes_for_bank(self, bank_idx: int) -> dict:
        if self.anchor_doc is None:
            return {}
        banks = self.anchor_doc.get("banks", [])
        if bank_idx < len(banks):
            return banks[bank_idx].get("notes", {})
        return {}

    def _active_anchor_notes(self) -> dict:
        return self._anchor_notes_for_bank(self.active_bank)

    def _ensure_anchor_doc(self):
        if self.anchor_doc is None and self.banks:
            self.anchor_doc = _empty_anchor_doc(self.bank_paths[0])

    def cmd_load(self, path: str):
        path = path.strip()
        if not os.path.exists(path):
            print(f"  File not found: {path}")
            return
        with open(path) as f:
            data = json.load(f)
        self.banks = [data]
        self.bank_paths = [path]
        self.active_bank = 0
        n = len(data.get("notes", {}))
        print(f"  Loaded: {path}  ({n} notes)")
        if self.anchor_doc is None:
            self._ensure_anchor_doc()

    def cmd_add_bank(self, path: str):
        path = path.strip()
        if not os.path.exists(path):
            print(f"  File not found: {path}")
            return
        with open(path) as f:
            data = json.load(f)
        self.banks.append(data)
        self.bank_paths.append(path)
        idx = len(self.banks) - 1
        n = len(data.get("notes", {}))
        print(f"  Added bank [{idx}]: {path}  ({n} notes)")
        self._ensure_anchor_doc()
        # Extend anchor doc banks list
        self.anchor_doc["banks"].append({"bank": str(Path(path)), "notes": {}})

    def cmd_list_banks(self):
        for i, (p, b) in enumerate(zip(self.bank_paths, self.banks)):
            active_str = " <-- active" if i == self.active_bank else ""
            n = len(b.get("notes", {}))
            print(f"  [{i}] {p}  ({n} notes){active_str}")

    def cmd_use(self, idx_str: str):
        try:
            idx = int(idx_str)
        except ValueError:
            print("  Usage: use <bank_index>")
            return
        if idx < 0 or idx >= len(self.banks):
            print(f"  Invalid index. Available: 0..{len(self.banks)-1}")
            return
        self.active_bank = idx
        print(f"  Active bank: [{idx}] {self.bank_paths[idx]}")

    def cmd_screen(self):
        notes = self._active_notes()
        if not notes:
            print("  No bank loaded.")
            return

        import statistics
        counts = {0.0: 0, 0.5: 0, 1.0: 0}
        broken_reasons: Dict[str, int] = {}
        a0_vals = [n["partials"][0]["A0"] for n in notes.values()
                   if n.get("partials") and n["partials"][0]["A0"] > 0]
        a0_median = statistics.median(a0_vals) if a0_vals else 1.0
        a0_outlier_count = 0

        for key, note in notes.items():
            q, reasons = _auto_quality(note)
            # Bank-level A0 outlier: > 20x or < 0.05x the bank median
            if note.get("partials"):
                a0 = note["partials"][0].get("A0", 0)
                if a0 > 0 and (a0 > a0_median * 20 or a0 < a0_median * 0.05):
                    a0_outlier_count += 1
                    q = min(q, 0.1)
                    reasons = [r for r in reasons if not r.startswith("A0")]
                    reasons.append("A0 outlier vs bank median")
            bucket = 0.0 if q < 0.2 else (0.5 if q < 0.8 else 1.0)
            counts[bucket] = counts.get(bucket, 0) + 1
            for r in reasons:
                if r not in ("looks plausible",):
                    # Aggregate similar reasons
                    key_r = r if not r.startswith("A0 suspicious") else "A0 suspicious (value>10)"
                    broken_reasons[key_r] = broken_reasons.get(key_r, 0) + 1

        total = len(notes)
        print(f"\n  Auto-screen summary ({Path(self.bank_paths[self.active_bank]).name}):")
        print(f"  Total notes   : {total}")
        print(f"  Clearly broken: {counts.get(0.0,0):4d}  ({100*counts.get(0.0,0)/total:.0f}%)")
        print(f"  Uncertain     : {counts.get(0.5,0):4d}  ({100*counts.get(0.5,0)/total:.0f}%)")
        print(f"  Plausible     : {counts.get(1.0,0):4d}  ({100*counts.get(1.0,0)/total:.0f}%)")
        print(f"  A0 bank-median: {a0_median:.4f}  (A0 outliers: {a0_outlier_count})")
        print(f"\n  Failure reasons (top causes):")
        for reason, cnt in sorted(broken_reasons.items(), key=lambda x: -x[1])[:12]:
            print(f"    {cnt:4d}x  {reason}")
        print()

    def cmd_list(self, midi_range: str = ""):
        notes = self._active_notes()
        anchor_notes = self._active_anchor_notes()
        if not notes:
            print("  No bank loaded.")
            return

        # Parse range
        midi_min, midi_max = 21, 108
        if midi_range:
            m = re.match(r"(\d+)-(\d+)", midi_range.strip())
            if m:
                midi_min, midi_max = int(m.group(1)), int(m.group(2))
            else:
                try:
                    midi_min = midi_max = int(midi_range.strip())
                except ValueError:
                    print(f"  Bad range: {midi_range}")
                    return

        print(f"\n  {'midi':<6} {'name':<5} {'qual v0..v7':<50}  bad_vels")
        print(f"  {'-'*80}")
        for midi in range(midi_min, midi_max + 1):
            row_q = []
            bad_count = 0
            for vel in range(8):
                key = note_key(midi, vel)
                note = notes.get(key)
                if note is None:
                    row_q.append("-")
                    continue
                aq, reasons = _auto_quality(note)
                uq = anchor_notes.get(key)
                q = uq if uq is not None else aq
                row_q.append(f"{q:.1f}")
                if q < 0.3:
                    bad_count += 1
            q_str = " ".join(row_q)
            print(f"  {midi:<6} {midi_to_name(midi):<5} {q_str:<50}  {bad_count}/8 bad")
        print()

    def cmd_show(self, midi_str: str):
        try:
            midi = int(midi_str)
        except ValueError:
            print("  Usage: show <midi>")
            return
        notes = self._active_notes()
        anchor_notes = self._active_anchor_notes()
        if not notes:
            print("  No bank loaded.")
            return
        _show_note(notes, midi, anchor_notes)

    def cmd_mark(self, midi_str: str, spec_tokens: list[str]):
        try:
            midi = int(midi_str)
        except ValueError:
            print("  Usage: mark <midi> <spec>   e.g.: mark 64 all:1.0 3:0.5")
            return
        self._ensure_anchor_doc()
        anchor_notes = self._active_anchor_notes()
        vel_quality = _parse_mark_spec(spec_tokens)
        for vel, q in sorted(vel_quality.items()):
            key = note_key(midi, vel)
            anchor_notes[key] = q
            print(f"    {key}: quality={q:.2f}")
        # Write back
        self.anchor_doc["banks"][self.active_bank]["notes"] = anchor_notes

    def cmd_auto(self, midi_str: str):
        """Apply auto-screener to one note and show results (without saving)."""
        try:
            midi = int(midi_str)
        except ValueError:
            print("  Usage: auto <midi>")
            return
        notes = self._active_notes()
        for vel in range(8):
            key = note_key(midi, vel)
            note = notes.get(key)
            if note is None:
                print(f"    {key}: missing")
                continue
            q, reasons = _auto_quality(note)
            print(f"    {key}: auto_quality={q:.2f}  - {'; '.join(reasons)}")

    def cmd_load_anchors(self, path: str):
        path = path.strip()
        if not os.path.exists(path):
            print(f"  File not found: {path}")
            return
        self.anchor_doc = load_anchor_file(path)
        self.anchor_path = path
        total = sum(len(b.get("notes", {})) for b in self.anchor_doc.get("banks", []))
        print(f"  Loaded anchors: {path}  ({total} entries across {len(self.anchor_doc.get('banks',[]))} bank(s))")

    def cmd_save(self, path: str = ""):
        self._ensure_anchor_doc()
        if not path:
            if self.anchor_path:
                path = self.anchor_path
            elif self.bank_paths:
                path = _default_anchor_path(self.bank_paths[0])
            else:
                print("  No path specified and no bank loaded.")
                return
        self.anchor_path = path
        save_anchor_file(self.anchor_doc, path)

    def cmd_status(self):
        if self.anchor_doc is None:
            print("  No anchor data yet.")
            return
        for i, bank_entry in enumerate(self.anchor_doc.get("banks", [])):
            notes = bank_entry.get("notes", {})
            good = sum(1 for q in notes.values() if q >= 0.5)
            total = len(notes)
            path = bank_entry.get("bank", "?")
            active_str = " <-- active" if i == self.active_bank else ""
            print(f"  Bank [{i}] {Path(path).name}: {total} marked  ({good} with quality>=0.5){active_str}")
        print(f"  Anchor file: {self.anchor_path or '(not saved yet)'}")
        print(f"  Instrument type: {self.anchor_doc.get('instrument_type', '?')}")

    def cmd_set_type(self, instrument_type: str):
        self._ensure_anchor_doc()
        self.anchor_doc["instrument_type"] = instrument_type.strip()
        print(f"  Instrument type set: {instrument_type.strip()}")

    def cmd_set_desc(self, description: str):
        self._ensure_anchor_doc()
        self.anchor_doc["description"] = description.strip()
        print(f"  Description set: {description.strip()}")


def print_help():
    print("""
  Commands:
    load <path>              Load a soundbank JSON
    add-bank <path>          Add bank to session (multi-bank mode)
    list-banks               List all loaded banks
    use <idx>                Switch active bank
    screen                   Auto-screen: show quality summary
    list [midi_range]        List all notes (e.g. 'list 48-72')
    show <midi>              Show all velocity layers for a MIDI note
    mark <midi> <spec>       Mark quality, examples:
                               mark 64 all:1.0
                               mark 64 5:1.0 6:1.0 7:0.9
                               mark 64 0-3:0.0 4-7:1.0
    auto <midi>              Show auto-screener result for one note
    load-anchors <path>      Load existing anchor file
    save [path]              Save anchor file
    set-type <type>          Set instrument type (rhodes / piano / electric_piano)
    set-desc <text>          Set description
    status                   Show session status
    help                     Show this help
    quit / exit              Exit
""")


def run_repl(initial_bank: str = "", initial_anchors: str = ""):
    session = AnchorSession()

    print("\n=== Instrument DNA - Anchor Selection Helper ===")
    print("  Type 'help' for commands.\n")

    if initial_bank:
        session.cmd_load(initial_bank)
        session.cmd_screen()

    if initial_anchors:
        session.cmd_load_anchors(initial_anchors)

    while True:
        try:
            raw = input("anchor> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not raw:
            continue

        parts = raw.split(None, 1)
        cmd = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        if cmd in ("quit", "exit", "q"):
            break
        elif cmd == "help":
            print_help()
        elif cmd == "load":
            session.cmd_load(rest)
        elif cmd == "add-bank":
            session.cmd_add_bank(rest)
        elif cmd == "list-banks":
            session.cmd_list_banks()
        elif cmd == "use":
            session.cmd_use(rest)
        elif cmd == "screen":
            session.cmd_screen()
        elif cmd == "list":
            session.cmd_list(rest)
        elif cmd == "show":
            session.cmd_show(rest.strip())
        elif cmd == "mark":
            tokens = rest.split()
            if len(tokens) < 2:
                print("  Usage: mark <midi> <spec>")
            else:
                session.cmd_mark(tokens[0], tokens[1:])
        elif cmd == "auto":
            session.cmd_auto(rest.strip())
        elif cmd == "load-anchors":
            session.cmd_load_anchors(rest)
        elif cmd == "save":
            session.cmd_save(rest.strip())
        elif cmd == "status":
            session.cmd_status()
        elif cmd == "set-type":
            session.cmd_set_type(rest)
        elif cmd == "set-desc":
            session.cmd_set_desc(rest)
        else:
            print(f"  Unknown command: '{cmd}'. Type 'help'.")

    print("Bye.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Instrument DNA anchor helper")
    parser.add_argument("--load", default="", help="Soundbank to load on start")
    parser.add_argument("--anchors", default="", help="Anchor file to load on start")
    args = parser.parse_args()
    run_repl(initial_bank=args.load, initial_anchors=args.anchors)
