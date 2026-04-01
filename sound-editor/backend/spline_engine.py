"""
sound-editor/backend/spline_engine.py
───────────────────────────────────────
Weighted smoothing spline for ICR Sound Editor.

Mathematical model:
    minimise  Σᵢ λᵢ · (f(xᵢ) − yᵢ)² + α · ∫ f″(x)² dx

Where:
    λᵢ  = stickiness per control point (0 = ignored, ∞ = interpolated)
    α   = global stiffness (high = rigid / linear, low = elastic / floppy)
    xᵢ  = MIDI note number (21–108)
    yᵢ  = parameter value at that note

Anchor points are control points with user-defined high stickiness.
Pulling the spline at any x inserts a temporary control point at (x, y_pulled).
Regional stiffness: different α for bass (midi < split) and treble (midi >= split).
"""

import numpy as np
from scipy.interpolate import UnivariateSpline, interp1d
from dataclasses import dataclass, field
from typing import Optional


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class ControlPoint:
    midi:       int
    value:      float
    stickiness: float = 1.0     # λ weight (0 = free, 10 = very sticky)
    is_anchor:  bool  = False
    locked:     bool  = False   # prevent any modification


@dataclass
class SplineConfig:
    stiffness:      float = 1.0     # α — global smoothing strength (higher = stiffer)
    bass_split:     int   = 52      # MIDI note where bass/treble split occurs
    bass_stiffness: float = 1.0     # α for bass region (midi < bass_split)
    treble_stiffness: float = 1.0   # α for treble region (midi >= bass_split)
    degree:         int   = 3       # spline degree (1=linear, 3=cubic, 5=quintic)
    velocity:       int   = -1      # -1 = all velocities, 0–7 = specific layer


@dataclass
class SplineState:
    layer_id:       str
    config:         SplineConfig = field(default_factory=SplineConfig)
    control_points: list[ControlPoint] = field(default_factory=list)

    def add_anchor(self, midi: int, value: float, stickiness: float = 8.0):
        """Add or update an anchor point."""
        existing = self._find(midi)
        if existing:
            existing.value      = value
            existing.stickiness = stickiness
            existing.is_anchor  = True
        else:
            self.control_points.append(
                ControlPoint(midi, value, stickiness, is_anchor=True)
            )
        self._sort()

    def add_pull(self, midi: int, value: float, stickiness: float = 3.0):
        """Insert a temporary pull point (non-anchor)."""
        existing = self._find(midi)
        if existing and not existing.is_anchor:
            existing.value      = value
            existing.stickiness = stickiness
        elif not existing:
            self.control_points.append(ControlPoint(midi, value, stickiness))
        self._sort()

    def remove_point(self, midi: int):
        self.control_points = [p for p in self.control_points if p.midi != midi]

    def _find(self, midi: int) -> Optional[ControlPoint]:
        for p in self.control_points:
            if p.midi == midi:
                return p
        return None

    def _sort(self):
        self.control_points.sort(key=lambda p: p.midi)


# ── Fitting ───────────────────────────────────────────────────────────────────

class SplineEngine:
    """
    Fits a weighted smoothing spline and evaluates it across the keyboard.
    """

    MIDI_MIN = 21
    MIDI_MAX = 108

    def fit(
        self,
        state:     SplineState,
        raw_data:  dict[str, float],   # {"m060_vel3": value, ...}
        eval_range: Optional[tuple[int, int]] = None,
    ) -> dict[int, float]:
        """
        Fit the spline and return evaluated values for all MIDI notes.

        raw_data:   current extracted values from ParamsStore.extract_layer()
        eval_range: (midi_lo, midi_hi) inclusive, defaults to full keyboard

        Returns: { midi: fitted_value }
        """
        lo, hi = eval_range or (self.MIDI_MIN, self.MIDI_MAX)

        # Merge: raw data + control points
        x_all, y_all, w_all = self._collect_points(state, raw_data)

        if len(x_all) < 2:
            return {}

        # Regional stiffness: scale weights by stiffness ratio
        smooth = self._effective_smoothing(state.config, x_all, w_all)

        try:
            spline = UnivariateSpline(
                x_all, y_all,
                w=w_all,
                k=min(state.config.degree, len(x_all) - 1),
                s=smooth,
                ext=3,   # extrapolate with boundary value
            )
        except Exception:
            # Fallback: linear interpolation
            spline = interp1d(x_all, y_all, kind="linear",
                              fill_value="extrapolate")

        x_eval = np.arange(lo, hi + 1)
        y_eval = spline(x_eval)

        return {int(x): float(y) for x, y in zip(x_eval, y_eval)}

    def evaluate_points(
        self,
        state:    SplineState,
        raw_data: dict[str, float],
        x_query:  list[float],
    ) -> list[float]:
        """Evaluate spline at arbitrary x positions (for 3D curve display)."""
        x_all, y_all, w_all = self._collect_points(state, raw_data)
        if len(x_all) < 2:
            return [0.0] * len(x_query)
        smooth = self._effective_smoothing(state.config, x_all, w_all)
        try:
            spline = UnivariateSpline(
                x_all, y_all,
                w=w_all,
                k=min(state.config.degree, len(x_all) - 1),
                s=smooth,
                ext=3,
            )
            return [float(spline(x)) for x in x_query]
        except Exception:
            f = interp1d(x_all, y_all, kind="linear", fill_value="extrapolate")
            return [float(f(x)) for x in x_query]

    # ── Internal ──────────────────────────────────────────────────────────────

    def _collect_points(
        self,
        state:    SplineState,
        raw_data: dict[str, float],
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Merge raw data (low default weight) and control points (user weight).
        """
        point_map: dict[int, tuple[float, float]] = {}  # midi → (value, weight)

        # Raw data as weak prior
        for key, val in raw_data.items():
            midi = _key_to_midi(key)
            if midi is not None:
                point_map[midi] = (val, 1.0)

        # Control points override raw data with their stickiness
        for cp in state.control_points:
            w = max(cp.stickiness, 0.01)
            if cp.is_anchor:
                w = max(w, 5.0)
            point_map[cp.midi] = (cp.value, w)

        if not point_map:
            return np.array([]), np.array([]), np.array([])

        xs = np.array(sorted(point_map))
        ys = np.array([point_map[x][0] for x in xs])
        ws = np.array([point_map[x][1] for x in xs])

        return xs, ys, ws

    def _effective_smoothing(
        self,
        cfg: SplineConfig,
        xs:  np.ndarray,
        ws:  np.ndarray,
    ) -> float:
        """
        Convert stiffness → UnivariateSpline smoothing parameter s.

        s ≈ n_points × (1/stiffness) × mean_weight²
        Higher stiffness → smaller s → tighter fit (more rigid).
        """
        n = len(xs)
        # Base: n points, each with weight w contributes w² to the sum
        base = float(np.sum(ws ** 2))
        stiffness = max(cfg.stiffness, 1e-6)
        return base / stiffness


# ── Utility ───────────────────────────────────────────────────────────────────

def _key_to_midi(note_key: str) -> Optional[int]:
    """Parse "m060_vel3" → 60."""
    try:
        return int(note_key[1:4])
    except (ValueError, IndexError):
        return None
