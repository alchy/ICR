"""
training/modules/b_spline_fitter.py
──────────────────────────────────────
Fit inharmonicity coefficient B as a smooth 1-D spline over MIDI.

## Physics of B

The inharmonicity coefficient B determines how piano partial frequencies
deviate from integer multiples of the fundamental:

    f_k = k · f0 · sqrt(1 + B · k²)

B is defined by string physics:

    B = (π² · E · I) / (T · L²)

where:
    E  — Young's modulus of the string material
    I  — second moment of area (depends on string diameter)
    T  — string tension
    L  — string length

None of these depend on keystrike velocity.  B is a fixed property of
each string and varies smoothly across the keyboard as strings change
in length, diameter, and material (plain wire → wound).

## Why extracted B varies with velocity

Extraction yields different B values per velocity layer for the same note:

    MIDI 33 example: vel0=0.00003  vel3=0.00014  vel4=0.00015  vel6=0.00012
    → factor ~5 variation

Three sources of this variation — all are measurement artefacts, not physics:

1. **SNR noise** — at low velocity the signal is quiet → higher frequency
   estimation error → noisier partial fits → noisier B.

2. **Multiple strings per note** — most notes have 2–3 slightly detuned
   strings (chorus effect).  At different velocities, relative string
   amplitudes vary → the apparent combined B shifts.

3. **Large-amplitude non-linearity** — at fff the string tension increases
   slightly with amplitude → B changes very weakly with velocity.  This
   effect is real but small compared to (1) and (2).

## What is the "true" B

Best estimate: average log(B) across all velocity layers per MIDI, then
fit a smoothing spline over MIDI 21–108.  This averages out velocity noise
and enforces the physically expected smooth variation with MIDI number.

This is exactly what BSplneFitter does.  The velocity scatter in extracted
B is noise, not signal — which is why training the NN to predict B per
(midi, vel) caused B loss to dominate the multi-task gradient (~5–8× other
terms) without improving output quality.

Public API:
    fitter = BSplneFitter()
    fitter.fit(notes)            # fit from measured notes in params["notes"]
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
            if note.get("is_interpolated"):
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
        log(B) is clamped to [-20, 0] before exp to prevent overflow (inf) on
        extreme MIDI positions outside the measured range.
        Result is always in [1e-10, 1.0].
        """
        if self._spline is None:
            raise RuntimeError("BSplneFitter.predict called before fit().")
        log_b = float(np.clip(float(self._spline(midi)), -20.0, 0.0))
        return max(float(np.exp(log_b)), 1e-10)
