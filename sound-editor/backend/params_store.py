"""
sound-editor/backend/params_store.py
──────────────────────────────────────
In-memory store for the active params dict.

Loaded from a soundbank JSON.
Provides per-note / per-partial access and layer extraction.
"""

import json
from pathlib import Path
from typing import Optional


class ParamsStore:
    """
    Holds the active params dict and provides structured access.

    Key format: "m{midi:03d}_vel{vel}"  e.g. "m060_vel3"
    """

    def __init__(self):
        self._params:    dict = {}      # raw notes dict  (never modified by preview)
        self._meta:      dict = {}      # format, sr, etc.
        self._path:      Optional[Path] = None
        self._overrides: dict[str, dict[str, float]] = {}  # layer_id → {note_key: value}

    # ── Loading ───────────────────────────────────────────────────────────────

    def load_file(self, path: str) -> int:
        """Load a soundbank JSON. Returns number of notes loaded."""
        p = Path(path)
        with open(p) as f:
            data = json.load(f)
        self._meta   = {k: v for k, v in data.items() if k != "notes"}
        self._params = data.get("notes", {})
        self._path   = p
        return len(self._params)

    def load_dict(self, data: dict) -> int:
        """Load from an already-parsed dict."""
        self._meta   = {k: v for k, v in data.items() if k != "notes"}
        self._params = data.get("notes", {})
        return len(self._params)

    # ── Access ────────────────────────────────────────────────────────────────

    def note_key(self, midi: int, vel: int) -> str:
        return f"m{midi:03d}_vel{vel}"

    def get_note(self, midi: int, vel: int) -> Optional[dict]:
        return self._params.get(self.note_key(midi, vel))

    def all_notes(self) -> dict:
        return self._params

    def midi_range(self) -> tuple[int, int]:
        midis = [v["midi"] for v in self._params.values() if "midi" in v]
        return (min(midis), max(midis)) if midis else (21, 108)

    def vel_range(self) -> tuple[int, int]:
        vels = [v["vel"] for v in self._params.values() if "vel" in v]
        return (min(vels), max(vels)) if vels else (0, 7)

    # ── Layer extraction ──────────────────────────────────────────────────────

    def extract_layer(self, layer_id: str) -> dict[str, float]:
        """
        Extract all (midi, vel) values for a given layer_id.

        Returns { "m060_vel3": 0.41, ... } for existing notes only.
        """
        result = {}

        # Determine if this is a partial layer (e.g. "tau1_k3")
        partial_key, k = _parse_partial_layer(layer_id)

        for note_key, note in self._params.items():
            try:
                if k is not None:
                    # Per-partial layer
                    partials = note.get("partials", [])
                    idx = k - 1  # k is 1-indexed
                    if idx < len(partials):
                        result[note_key] = float(partials[idx][partial_key])
                else:
                    # Scalar layer
                    if layer_id in note:
                        result[note_key] = float(note[layer_id])
            except (KeyError, IndexError, TypeError, ValueError):
                pass

        return result

    # ── Layer update ─────────────────────────────────────────────────────────

    def update_layer_values(self, layer_id: str, values: dict[str, float]):
        """
        Write spline-computed values back into the params store.

        values: { "m060_vel3": 0.45, ... }
        """
        partial_key, k = _parse_partial_layer(layer_id)

        for note_key, value in values.items():
            if note_key not in self._params:
                continue
            note = self._params[note_key]
            if k is not None:
                partials = note.get("partials", [])
                idx = k - 1
                if idx < len(partials):
                    partials[idx][partial_key] = value
            else:
                note[layer_id] = value

    # ── Keep / override ──────────────────────────────────────────────────────

    def keep_layer(self, layer_id: str, values: dict[str, float]):
        """Commit blended values as override (used instead of originals on export)."""
        self._overrides[layer_id] = dict(values)

    def unkeep_layer(self, layer_id: str):
        self._overrides.pop(layer_id, None)

    def kept_layers(self) -> list[str]:
        return list(self._overrides)

    # ── Export ────────────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Return soundbank dict, applying overrides on top of originals."""
        import copy
        notes = copy.deepcopy(self._params)

        for layer_id, values in self._overrides.items():
            partial_key, k = _parse_partial_layer(layer_id)
            for note_key, value in values.items():
                if note_key not in notes:
                    continue
                note = notes[note_key]
                if k is not None:
                    partials = note.get("partials", [])
                    idx = k - 1
                    if idx < len(partials):
                        partials[idx][partial_key] = value
                else:
                    note[layer_id] = value

        return {**self._meta, "notes": notes}

    def save(self, path: str):
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_partial_layer(layer_id: str) -> tuple[Optional[str], Optional[int]]:
    """
    Parse "tau1_k3" → ("tau1", 3)
    Parse "f0_hz"   → (None, None)
    """
    if "_k" in layer_id:
        parts = layer_id.rsplit("_k", 1)
        if len(parts) == 2 and parts[1].isdigit():
            return parts[0], int(parts[1])
    return None, None
