"""
training/modules/b_spline_fitter.py
──────────────────────────────────────
Fit inharmonicity coefficient B as a smooth 1-D spline over MIDI.

B is physically velocity-independent (depends on string material and tension
only), so a single per-MIDI curve captures all relevant information without NN
capacity being consumed by the noisy per-velocity scatter in extracted values.

Public API:
    fitter = BSplneFitter()
    fitter.fit(samples)          # fit from measured notes in params["samples"]
    B = fitter.predict(midi)     # scalar B for given MIDI number
"""

import math

import numpy as np
from scipy.interpolate import UnivariateSpline


class BSplneFitter:
    """
    Smoothing spline for inharmonicity B over MIDI.

    B is fitted in log-space (B spans several orders of magnitude across the
    keyboard) and back-transformed on predict.  All velocity layers at the
    same MIDI position are averaged before fitting, since B is assumed
    velocity-independent.
    """

    def __init__(self, stiffness: float = 2.0, degree: int = 3):
        """
        Args:
            stiffness: Higher = more rigid spline (less wiggle).
                       Default 2.0 is stiffer than spline_fix default (1.0)
                       because B should follow a smooth physical curve.
            degree:    Spline polynomial degree (1–5).  3 = cubic.
        """
        self.stiffness = stiffness
        self.degree    = degree
        self._spline   = None
        self._midi_min = None
        self._midi_max = None

    def fit(self, samples: dict) -> "BSplneFitter":
        """
        Fit the B spline from measured (non-interpolated) notes.

        Args:
            samples: Dict keyed "m{midi:03d}_vel{vel}" → sample dict.
                     Non-measured notes (``_interpolated=True``) are skipped.
                     Velocity layers are averaged per MIDI before fitting.

        Returns:
            self (for chaining).

        Raises:
            ValueError: If fewer than 4 measured MIDI positions have valid B.
        """
        midi_vals: dict[int, list[float]] = {}
        for note in samples.values():
            if note.get("_interpolated"):
                continue
            b = note.get("B")
            if not b or b <= 1e-12:
                continue
            m = int(note["midi"])
            midi_vals.setdefault(m, []).append(math.log(float(b)))

        if len(midi_vals) < 4:
            raise ValueError(
                f"BSplneFitter.fit: only {len(midi_vals)} measured MIDI positions "
                f"with valid B — need at least 4."
            )

        midis  = sorted(midi_vals)
        log_Bs = [float(np.mean(midi_vals[m])) for m in midis]

        x = np.array(midis,  dtype=np.float64)
        y = np.array(log_Bs, dtype=np.float64)
        s = max(len(x) / (self.stiffness * 10.0), 0.01)

        self._spline   = UnivariateSpline(x, y, k=self.degree, s=s)
        self._midi_min = int(midis[0])
        self._midi_max = int(midis[-1])

        b_lo = float(np.exp(self._spline(self._midi_min)))
        b_hi = float(np.exp(self._spline(self._midi_max)))
        print(
            f"  BSplneFitter: fitted over MIDI {self._midi_min}–{self._midi_max} "
            f"({len(midis)} positions)  "
            f"B range: {b_lo:.2e} … {b_hi:.2e}",
            flush=True,
        )
        return self

    def predict(self, midi: int) -> float:
        """
        Return B for a given MIDI note.

        Extrapolates smoothly beyond the fitted range (spline extrapolation).
        Result is always >= 1e-10.
        """
        if self._spline is None:
            raise RuntimeError("BSplneFitter.predict called before fit().")
        return max(float(np.exp(float(self._spline(midi)))), 1e-10)
