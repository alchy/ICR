"""
training/modules/outlier_filter.py
────────────────────────────────────
Outlier detection and removal for extracted physics parameters.

Public API:
    cleaned = OutlierFilter().filter(params, z=10.0)

Detects extraction-error outliers by comparing each sample's physics
parameters against a locally-smoothed trend over adjacent MIDI notes
(same velocity layer). Parameters checked: B, tau1_mean, A0_mean, f0_ratio.
"""

import copy
from collections import defaultdict

import numpy as np


# ─────────────────────────────────────────────────────────────────────────────
# OutlierFilter
# ─────────────────────────────────────────────────────────────────────────────

class OutlierFilter:
    """
    Remove samples with extraction errors from a params dict.

    Usage:
        params = OutlierFilter().filter(params, z=10.0)

    The returned dict has outlier samples removed and n_samples updated.
    The input dict is NOT modified in place.
    """

    # Number of partials to average for tau1 / A0 features
    N_PARTIALS = 6
    # Median smoother half-width in notes
    SMOOTH_WINDOW = 5

    def filter(self, params: dict, z: float = 10.0) -> dict:
        """
        Detect and drop outlier samples.

        Args:
            params:  Full params dict (keys: bank_dir, n_samples, summary, samples).
            z:       Z-score threshold (robust sigmas). Default 10.0 is conservative.

        Returns:
            New params dict with outliers removed.
        """
        features = self._parse_features(params["samples"])
        outliers = self._detect_outliers(features, z_thresh=z)

        if not outliers:
            print("OutlierFilter: no outliers detected.")
            return params

        outlier_keys = {o["key"] for o in outliers}
        print(f"OutlierFilter: dropping {len(outlier_keys)} samples (z > {z}):")
        for o in outliers:
            print(f"  {o['key']:<18} midi={o['midi']:>3} vel={o['vel']:>2}  "
                  f"{o['feature']:<12} z={o['z_score']:.1f}")

        # Build a new params dict without the outlier samples
        result = copy.deepcopy(params)
        for key in outlier_keys:
            result["samples"].pop(key, None)
        result["n_samples"] = len(result["samples"])
        return result

    # ── Feature extraction ────────────────────────────────────────────────────

    def _parse_features(self, samples: dict) -> dict:
        """Return {(midi, vel): feature_dict} for all samples."""
        out = {}
        for key, s in samples.items():
            midi  = s["midi"]
            vel   = s["vel"]
            parts = s["partials"][:self.N_PARTIALS]
            if not parts:
                continue

            tau1_vals = [p["tau1"] for p in parts if p.get("tau1") is not None]
            A0_vals   = [p["A0"]   for p in parts]
            f0_nom    = s.get("f0_nominal_hz", 0.0)
            f0_fit    = s.get("f0_fitted_hz",  0.0)

            out[(midi, vel)] = {
                "key":        key,
                "B":          s.get("B", 0.0),
                "tau1_mean":  float(np.mean(tau1_vals)) if tau1_vals else 0.0,
                "A0_mean":    float(np.mean(A0_vals))   if A0_vals   else 0.0,
                "f0_ratio":   (f0_fit / f0_nom) if f0_nom > 0 else 1.0,
                "n_partials": s["n_partials"],
            }
        return out

    # ── Outlier detection ─────────────────────────────────────────────────────

    _FEATURES = ["B", "tau1_mean", "A0_mean", "f0_ratio"]
    _LOG_SPACE = {"B", "A0_mean"}   # these span many decades → work in log-space

    def _detect_outliers(self, features: dict, z_thresh: float) -> list:
        """
        For each velocity layer, sort samples by MIDI, compute residuals from
        a median smoother, flag samples exceeding z_thresh robust sigmas.
        """
        by_vel: dict[int, list] = defaultdict(list)
        for (midi, vel), feat in features.items():
            by_vel[vel].append((midi, feat))

        raw_outliers = []
        for vel in sorted(by_vel):
            entries = sorted(by_vel[vel], key=lambda x: x[0])
            if len(entries) < 4:
                continue
            for feat_name in self._FEATURES:
                raw_outliers.extend(
                    self._scan_feature(entries, feat_name, z_thresh)
                )

        return self._deduplicate(raw_outliers)

    def _scan_feature(self, entries: list, feat_name: str, z_thresh: float) -> list:
        """Scan one velocity layer for one feature; return outlier records."""
        vals = np.array([e[1][feat_name] for e in entries])
        log_space = feat_name in self._LOG_SPACE
        if log_space:
            vals = np.log1p(vals)

        smoothed  = _median_smooth(vals, self.SMOOTH_WINDOW)
        residuals = vals - smoothed
        sigma     = _mad_sigma(residuals)

        outliers = []
        for i, (midi, feat) in enumerate(entries):
            z = abs(residuals[i]) / sigma
            if z > z_thresh:
                smoothed_val = float(np.expm1(smoothed[i])) if log_space else float(smoothed[i])
                outliers.append({
                    "key":      feat["key"],
                    "midi":     midi,
                    "vel":      entries[i][1].get("vel", 0),
                    "feature":  feat_name,
                    "value":    feat[feat_name],
                    "smoothed": smoothed_val,
                    "z_score":  float(z),
                })
        return outliers

    def _deduplicate(self, outliers: list) -> list:
        """Keep only the worst-z outlier record per sample key."""
        seen: dict[str, dict] = {}
        for o in sorted(outliers, key=lambda x: -x["z_score"]):
            k = o["key"]
            if k not in seen or o["z_score"] > seen[k]["z_score"]:
                seen[k] = o
        return sorted(seen.values(), key=lambda x: -x["z_score"])


# ─────────────────────────────────────────────────────────────────────────────
# Robust statistics helpers
# ─────────────────────────────────────────────────────────────────────────────

def _mad_sigma(x: np.ndarray) -> float:
    """Median absolute deviation converted to Gaussian-equivalent sigma."""
    return float(1.4826 * np.median(np.abs(x - np.median(x))) + 1e-12)


def _median_smooth(vals: np.ndarray, half_width: int) -> np.ndarray:
    """Per-element median over a sliding window of width 2*half_width+1."""
    n   = len(vals)
    out = np.empty(n)
    for i in range(n):
        lo      = max(0, i - half_width)
        hi      = min(n, i + half_width + 1)
        out[i]  = np.median(vals[lo:hi])
    return out
