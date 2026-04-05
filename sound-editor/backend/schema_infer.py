"""
sound-editor/backend/schema_infer.py
──────────────────────────────────────
Infer layer schema dynamically from a loaded soundbank.

No hardcoded keys — reads the first available note and detects:
  scalar      float/int keys at top level (f0_hz, B, rms_gain, ...)
  per_partial keys inside partials[0] (tau1, A0, beat_hz, ...)
  eq          array keys inside spectral_eq (gains_db, freqs_hz, ...)

Result feeds /schema endpoint; frontend builds tabs + layer rows from it.
"""

from __future__ import annotations

# Keys to always skip at top level (not useful to edit as layers)
_SKIP_TOP = {"midi", "vel", "K_valid", "partials", "spectral_eq",
             "noise", "eq_biquads", "duration_s", "is_interpolated"}

# Per-partial key to skip
_SKIP_PARTIAL = {"k"}

# EQ array keys to skip (freqs_hz is the X axis, not editable)
_SKIP_EQ = {"freqs_hz"}


def infer_schema(notes: dict) -> dict:
    """
    Infer schema from the first note in the soundbank.

    Returns:
        {
          "scalar":      ["f0_hz", "B", "A_noise", ...],
          "per_partial": ["f_hz", "A0", "tau1", ...],
          "eq":          ["gains_db"],
          "k_max":       60,
        }
    """
    schema: dict = {"scalar": [], "per_partial": [], "eq": [], "k_max": 0}

    for note in notes.values():
        # ── Scalar: numeric top-level keys ────────────────────────────────────
        for key, val in note.items():
            if key in _SKIP_TOP:
                continue
            if isinstance(val, (int, float)):
                schema["scalar"].append(key)

        # ── Per-partial: keys inside partials[0] ──────────────────────────────
        partials = note.get("partials", [])
        if partials:
            for key in partials[0]:
                if key not in _SKIP_PARTIAL:
                    schema["per_partial"].append(key)
            schema["k_max"] = len(partials)

        # ── EQ: array keys inside spectral_eq ─────────────────────────────────
        for key, val in note.get("spectral_eq", {}).items():
            if key not in _SKIP_EQ and isinstance(val, list):
                schema["eq"].append(key)

        break  # only need first note

    return schema
