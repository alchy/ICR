"""
sound-editor/backend/catalog_store.py
--------------------------------------
Persistent catalog of rated notes across multiple soundbank extractions.

The catalog is a JSON file that grows over time as the user browses
different extractions and marks good notes. It survives editor restarts.
"""

import json
import copy
import os
from datetime import datetime
from pathlib import Path
from typing import Optional


CATALOG_PATH = Path(__file__).parent.parent.parent / "catalog.json"


class CatalogEntry:
    def __init__(self, midi: int, vel: int, rating: int,
                 bank_file: str, bank_path: str,
                 timestamp: str = "", entry_id: int = 0):
        self.id = entry_id
        self.midi = midi
        self.vel = vel
        self.rating = rating
        self.bank_file = bank_file
        self.bank_path = bank_path
        self.timestamp = timestamp or datetime.now().isoformat(timespec="seconds")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "midi": self.midi,
            "vel": self.vel,
            "rating": self.rating,
            "bank_file": self.bank_file,
            "bank_path": self.bank_path,
            "timestamp": self.timestamp,
        }

    @staticmethod
    def from_dict(d: dict) -> "CatalogEntry":
        return CatalogEntry(
            midi=d["midi"], vel=d["vel"], rating=d.get("rating", 3),
            bank_file=d.get("bank_file", ""), bank_path=d.get("bank_path", ""),
            timestamp=d.get("timestamp", ""), entry_id=d.get("id", 0),
        )


class CatalogStore:
    def __init__(self, path: Optional[Path] = None):
        self._path = path or CATALOG_PATH
        self._entries: list[CatalogEntry] = []
        self._next_id = 1
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                with open(self._path) as f:
                    data = json.load(f)
                self._entries = [CatalogEntry.from_dict(e) for e in data.get("entries", [])]
                if self._entries:
                    self._next_id = max(e.id for e in self._entries) + 1
            except Exception:
                self._entries = []

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            json.dump({"entries": [e.to_dict() for e in self._entries]}, f, indent=2)

    def add(self, midi: int, vel: int, rating: int,
            bank_file: str, bank_path: str) -> CatalogEntry:
        entry = CatalogEntry(
            midi=midi, vel=vel, rating=rating,
            bank_file=bank_file, bank_path=bank_path,
            entry_id=self._next_id,
        )
        self._next_id += 1
        self._entries.append(entry)
        self._save()
        return entry

    def remove(self, entry_id: int) -> bool:
        before = len(self._entries)
        self._entries = [e for e in self._entries if e.id != entry_id]
        if len(self._entries) < before:
            self._save()
            return True
        return False

    def all(self) -> list[dict]:
        return [e.to_dict() for e in self._entries]

    def find(self, midi: int, vel: Optional[int] = None) -> list[dict]:
        result = [e for e in self._entries if e.midi == midi]
        if vel is not None:
            result = [e for e in result if e.vel == vel]
        return [e.to_dict() for e in result]

    def clear(self):
        self._entries.clear()
        self._next_id = 1
        self._save()


class BankAssembler:
    """Assembles a target bank from a base bank + catalog deep-copies."""

    def __init__(self):
        self._base: dict = {}           # full bank dict (with "notes" key)
        self._target: dict = {}         # working copy
        self._base_file: str = ""
        self._sources: dict[str, str] = {}  # "m060_vel3" -> source description

    def init_from_base(self, bank_path: str) -> int:
        """Load a base bank as starting point. Returns note count."""
        with open(bank_path) as f:
            self._base = json.load(f)
        self._target = copy.deepcopy(self._base)
        self._base_file = Path(bank_path).name
        notes = self._target.get("notes", {})
        self._sources = {k: f"base:{self._base_file}" for k in notes}
        return len(notes)

    def deep_copy_note(self, midi: int, vel: int, source_bank_path: str) -> bool:
        """Replace a note in target with one from a different bank."""
        try:
            with open(source_bank_path) as f:
                source = json.load(f)
        except Exception:
            return False

        key = f"m{midi:03d}_vel{vel}"
        source_notes = source.get("notes", {})
        if key not in source_notes:
            return False

        target_notes = self._target.setdefault("notes", {})
        target_notes[key] = copy.deepcopy(source_notes[key])
        self._sources[key] = f"copy:{Path(source_bank_path).name}"
        return True

    def deep_copy_all_vel(self, midi: int, source_bank_path: str) -> int:
        """Copy all velocity layers for a MIDI note from source bank."""
        try:
            with open(source_bank_path) as f:
                source = json.load(f)
        except Exception:
            return 0

        count = 0
        source_notes = source.get("notes", {})
        target_notes = self._target.setdefault("notes", {})
        for vel in range(8):
            key = f"m{midi:03d}_vel{vel}"
            if key in source_notes:
                target_notes[key] = copy.deepcopy(source_notes[key])
                self._sources[key] = f"copy:{Path(source_bank_path).name}"
                count += 1
        return count

    def summary(self) -> dict:
        """Count notes by source."""
        from_base = sum(1 for v in self._sources.values() if v.startswith("base:"))
        from_copy = sum(1 for v in self._sources.values() if v.startswith("copy:"))
        from_edit = sum(1 for v in self._sources.values() if v.startswith("edit:"))
        total = len(self._sources)
        return {
            "total": total,
            "from_base": from_base,
            "from_copy": from_copy,
            "from_edit": from_edit,
            "base_file": self._base_file,
        }

    def get_note_source(self, midi: int, vel: int) -> str:
        key = f"m{midi:03d}_vel{vel}"
        return self._sources.get(key, "unknown")

    def get_all_sources(self) -> dict[str, str]:
        return dict(self._sources)

    def save(self, output_dir: str, bank_name: str = "edit") -> str:
        """Save target bank with timestamp. Returns output path."""
        ts = datetime.now().strftime("%m%d%H%M")
        filename = f"{bank_name}-{ts}.json"
        out_path = Path(output_dir) / filename
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(self._target, f)
        return str(out_path)

    def target_dict(self) -> dict:
        return self._target

    @property
    def is_initialized(self) -> bool:
        return bool(self._target.get("notes"))
