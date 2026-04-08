"""
sound-editor/backend/eq_editor.py
───────────────────────────────────
EQ curve re-fitting helper for the sound editor.

Imports _eq_to_biquads lazily from training/modules/eq_fitter so the
rest of the backend doesn't pull in heavy training dependencies at startup.
"""

import sys
from pathlib import Path

import numpy as np

# Make training/modules importable regardless of working directory
_REPO_ROOT = Path(__file__).parent.parent.parent
_TRAINING  = _REPO_ROOT / "training" / "modules"
if str(_TRAINING) not in sys.path:
    sys.path.insert(0, str(_TRAINING))


def refit_biquads(freqs_hz: list, gains_db: list, sr: int = 44100, n_sections: int = 5) -> list:
    """
    Fit a new biquad cascade from an edited EQ curve.

    Args:
        freqs_hz:   Frequency grid (Hz).
        gains_db:   Gain values at each frequency (dB).
        sr:         Sample rate.
        n_sections: Number of biquad sections (default 5).

    Returns:
        List of biquad dicts [{"b": [b0,b1,b2], "a": [a1,a2]}, ...].
        Returns [] if fitting fails.
    """
    from eq_fitter import _eq_to_biquads  # lazy import

    try:
        return _eq_to_biquads(
            np.array(freqs_hz, dtype=np.float64),
            np.array(gains_db, dtype=np.float64),
            sr, n_sections=n_sections,
        )
    except Exception:
        return []
