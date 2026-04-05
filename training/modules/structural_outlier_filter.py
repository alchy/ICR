"""
training/modules/structural_outlier_filter.py
──────────────────────────────────────────────
Curve-fitting outlier detection for extracted samples.

Algorithm
─────────
  1. Build a feature matrix  {feature: {midi: {vel: value}}}
  2. For each midi note       → fit poly curve along the velocity axis
  3. For each velocity layer  → fit poly curve along the midi axis
  4. Flag any sample whose residual exceeds `sigma` MAD-sigmas in
     EITHER direction (too high OR too low) in EITHER axis.

Tracked features
────────────────
  Structural:
    duration    (duration_s)      — log-space
    n_partials  (n_partials)      — linear

  Physical:
    B           (inharmonicity)   — log-space  (skipped if B ≤ 0)
    tau1_mean   (mean of k=1–6)   — log-space
    A0_mean     (mean of k=1–6)   — log-space

Public API
──────────
    params = StructuralOutlierFilter().filter(params, sigma=3.0)
"""

import copy
from collections import defaultdict
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np


# ─────────────────────────────────────────────────────────────────────────────
# Feature extraction helpers  (module-level for clarity)
# ─────────────────────────────────────────────────────────────────────────────

# Number of leading partials used for tau1 / A0 statistics
_N_STAT = 6


def _feat_duration(s: dict) -> Optional[float]:
    return s.get("duration_s")


def _feat_n_partials(s: dict) -> Optional[float]:
    v = s.get("n_partials")
    return float(v) if v is not None else None


def _feat_B(s: dict) -> Optional[float]:
    v = s.get("B")
    return float(v) if (v is not None and v > 1e-9) else None


def _feat_tau1_mean(s: dict) -> Optional[float]:
    parts = s.get("partials", [])[:_N_STAT]
    vals  = [p["tau1"] for p in parts if p.get("tau1", 0) > 0]
    return float(np.mean(vals)) if vals else None


def _feat_A0_mean(s: dict) -> Optional[float]:
    parts = s.get("partials", [])[:_N_STAT]
    vals  = [p["A0"] for p in parts if p.get("A0", 0) > 0]
    return float(np.mean(vals)) if vals else None


# ─────────────────────────────────────────────────────────────────────────────
# StructuralOutlierFilter
# ─────────────────────────────────────────────────────────────────────────────

class StructuralOutlierFilter:
    """
    Remove samples whose structural or physical parameters deviate from smooth
    curves across the midi × velocity grid.

    Usage:
        params = StructuralOutlierFilter().filter(params, sigma=3.0)

    For each feature the filter fits a degree-2 polynomial through:
      - the velocity series for every midi note,
      - the midi series for every velocity layer,
    and removes samples that deviate by more than `sigma` MAD-sigmas in either
    direction (above OR below) along either axis.

    Returns a new params dict; the input is not modified.
    """

    POLY_DEG   = 2   # polynomial degree
    MIN_POINTS = 4   # minimum points to fit a curve

    # {name: {fn: callable(sample)->float|None, log_space: bool}}
    _FEATURES: Dict[str, dict] = {
        "duration":   {"fn": _feat_duration,   "log_space": True},
        "n_partials": {"fn": _feat_n_partials, "log_space": False},
        "B":          {"fn": _feat_B,          "log_space": True},
        "tau1_mean":  {"fn": _feat_tau1_mean,  "log_space": True},
        "A0_mean":    {"fn": _feat_A0_mean,    "log_space": True},
    }

    def filter(self, params: dict, sigma: float = 3.0) -> dict:
        """
        Detect and drop structural + physical outliers.

        Args:
            params:  Full params dict (keys: bank_dir, n_samples, summary, samples).
            sigma:   MAD-sigma threshold (both tails). Default 3.0.

        Returns:
            New params dict with outlier samples removed and n_samples updated.
        """
        matrix, key_map = self._build_matrix(params["notes"])
        flagged          = self._detect_outliers(matrix, key_map, sigma)

        if not flagged:
            print("StructuralOutlierFilter: no outliers detected.")
            return params

        outlier_keys = {f["key"] for f in flagged}
        print(f"StructuralOutlierFilter: dropping {len(outlier_keys)} samples "
              f"(|z| > {sigma:.1f}):")
        for f in flagged:
            print(f"  {f['key']:<18}  midi={f['midi']:>3}  vel={f['vel']:>2}  "
                  f"{f['feature']:<12}  z={f['z']:+.2f}  "
                  f"val={f['value']:.3g}  curve={f['curve']:.3g}")

        result = copy.deepcopy(params)
        for key in outlier_keys:
            result["notes"].pop(key, None)
        return result

    # ── Matrix construction ───────────────────────────────────────────────────

    def _build_matrix(
        self, samples: dict
    ) -> Tuple[Dict[str, Dict], Dict[Tuple[int, int], str]]:
        """
        Returns:
            matrix:  {feat_name: {midi: {vel: float}}}
            key_map: {(midi, vel): sample_key}
        """
        matrix: Dict[str, Dict] = {f: defaultdict(dict) for f in self._FEATURES}
        key_map: Dict[Tuple[int, int], str] = {}

        for key, s in samples.items():
            midi = s["midi"]
            vel  = s["vel"]
            key_map[(midi, vel)] = key
            for feat_name, cfg in self._FEATURES.items():
                val = cfg["fn"](s)
                if val is not None:
                    matrix[feat_name][midi][vel] = val

        return matrix, key_map

    # ── Outlier detection ─────────────────────────────────────────────────────

    def _detect_outliers(
        self,
        matrix:       Dict[str, Dict],
        key_map:      Dict[Tuple[int, int], str],
        sigma_thresh: float,
    ) -> List[dict]:
        all_flags: List[dict] = []

        for feat_name, cfg in self._FEATURES.items():
            log_space   = cfg["log_space"]
            feat_matrix = matrix[feat_name]

            # ── Velocity axis: for each midi, fit curve over vels ─────────────
            for midi, vel_dict in feat_matrix.items():
                vels = sorted(vel_dict.keys())
                vals = [vel_dict[v] for v in vels]
                all_flags.extend(self._scan_series(
                    x_vals       = vels,
                    y_vals       = vals,
                    feat_name    = feat_name,
                    log_space    = log_space,
                    sigma_thresh = sigma_thresh,
                    key_fn       = lambda vel, _m=midi: key_map.get((_m, vel)),
                    label_fn     = lambda vel, _m=midi: (_m, vel),
                ))

            # ── Midi axis: for each vel layer, fit curve over midis ───────────
            vel_to_midi: Dict[int, Dict[int, float]] = defaultdict(dict)
            for midi2, vel_dict in feat_matrix.items():
                for vel, val in vel_dict.items():
                    vel_to_midi[vel][midi2] = val

            for vel, midi_dict in vel_to_midi.items():
                midis = sorted(midi_dict.keys())
                vals  = [midi_dict[m] for m in midis]
                all_flags.extend(self._scan_series(
                    x_vals       = midis,
                    y_vals       = vals,
                    feat_name    = feat_name,
                    log_space    = log_space,
                    sigma_thresh = sigma_thresh,
                    key_fn       = lambda midi2, _v=vel: key_map.get((midi2, _v)),
                    label_fn     = lambda midi2, _v=vel: (midi2, _v),
                ))

        return self._deduplicate(all_flags)

    def _scan_series(
        self,
        x_vals:       List[int],
        y_vals:       List[float],
        feat_name:    str,
        log_space:    bool,
        sigma_thresh: float,
        key_fn:       Callable,
        label_fn:     Callable,
    ) -> List[dict]:
        """Fit polynomial, return records for both-tail outliers."""
        if len(x_vals) < self.MIN_POINTS:
            return []

        x_arr = np.array(x_vals, dtype=float)
        y_arr = np.array(y_vals, dtype=float)
        y_fit = np.log(np.maximum(y_arr, 1e-12)) if log_space else y_arr.copy()

        x_mean = x_arr.mean()
        x_std  = x_arr.std() + 1e-12
        x_n    = (x_arr - x_mean) / x_std

        try:
            coeffs = np.polyfit(x_n, y_fit, self.POLY_DEG)
        except (np.linalg.LinAlgError, ValueError):
            return []

        y_pred    = np.polyval(coeffs, x_n)
        residuals = y_fit - y_pred
        sigma     = _mad_sigma(residuals)

        flags = []
        for i, xi in enumerate(x_arr):
            z = residuals[i] / sigma
            if abs(z) <= sigma_thresh:
                continue
            key = key_fn(int(xi))
            if key is None:
                continue
            midi, vel = label_fn(int(xi))
            curve_val = float(np.exp(y_pred[i])) if log_space else float(y_pred[i])
            flags.append({
                "key":     key,
                "midi":    midi,
                "vel":     vel,
                "feature": feat_name,
                "value":   float(y_arr[i]),
                "curve":   curve_val,
                "z":       float(z),
            })
        return flags

    def _deduplicate(self, flags: List[dict]) -> List[dict]:
        """Keep one record per sample key — the one with the highest |z|."""
        seen: Dict[str, dict] = {}
        for f in sorted(flags, key=lambda x: -abs(x["z"])):
            k = f["key"]
            if k not in seen or abs(f["z"]) > abs(seen[k]["z"]):
                seen[k] = f
        return sorted(seen.values(), key=lambda x: -abs(x["z"]))


# ─────────────────────────────────────────────────────────────────────────────
# Robust statistics helper
# ─────────────────────────────────────────────────────────────────────────────

def _mad_sigma(x: np.ndarray) -> float:
    """Median absolute deviation → Gaussian-equivalent sigma."""
    return float(1.4826 * np.median(np.abs(x - np.median(x))) + 1e-12)
