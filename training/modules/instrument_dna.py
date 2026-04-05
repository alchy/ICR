"""
training/modules/instrument_dna.py
────────────────────────────────────
Instrument DNA: physics-law fitting + GP residuals + velocity enforcement.
Generates a complete soundbank from a set of quality-weighted anchor notes.

Concept: docs/INSTRUMENT_DNA.md

Pipeline:
    1. Auto-detect quality from extraction flags (bi-exp, beat, B range)
    2. Fit smooth physical laws on anchor notes only (quality-weighted WLS)
    3. Compute per-note residuals; fit GP on residuals
    4. Generate all 88×8 notes: physics + GP correction
    5. Enforce velocity monotonicity (A0 up, tau1 down)
    6. Export as soundbank JSON (with rms_gain calibration)

Usage:
    from training.modules.instrument_dna import InstrumentDNA

    # Fit from extracted params
    dna = InstrumentDNA()
    dna.fit("generated/pl-grand-extracted-v2.json")
    dna.save_bank("soundbanks/pl-grand-dna.json", sr=48000)

    # With custom quality weights (from anchor_helper)
    dna.fit("generated/pl-grand-extracted-v2.json",
            anchors="anchors/pl-grand.json")
"""

import json
import math
import os
import warnings
from pathlib import Path
from typing import Optional

import numpy as np
from scipy.optimize import curve_fit

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Try optional imports
# ---------------------------------------------------------------------------
try:
    from sklearn.gaussian_process import GaussianProcessRegressor
    from sklearn.gaussian_process.kernels import RBF, WhiteKernel, ConstantKernel
    from sklearn.isotonic import IsotonicRegression
    _HAS_SKLEARN = True
except ImportError:
    _HAS_SKLEARN = False
    print("[instrument_dna] WARNING: scikit-learn not found — GP disabled, "
          "falling back to smoothing spline.")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MIDI_MIN, MIDI_MAX = 21, 108
VEL_COUNT = 8
TARGET_RMS = 0.06
K_MAX = 60


def _midi_to_hz(midi: int) -> float:
    return 440.0 * 2.0 ** ((midi - 69) / 12.0)


def _inharmonic_freq(k: int, f0: float, B: float) -> float:
    return k * f0 * math.sqrt(max(0.0, 1.0 + B * k * k))


# ---------------------------------------------------------------------------
# Quality auto-detection
# ---------------------------------------------------------------------------

def _auto_quality(note: dict) -> float:
    """Return quality score 0.0-1.0 for an extracted note."""
    partials = note.get("partials", [])
    if not partials:
        return 0.0
    p0 = partials[0]
    B     = note.get("B", 0.0)
    a1    = p0.get("a1", 1.0)
    tau1  = p0.get("tau1") or 0.0
    tau2  = p0.get("tau2") or 0.0
    beat  = p0.get("beat_hz", 0.0)
    mono  = p0.get("mono", True)

    score = 1.0
    if mono or a1 >= 0.999 or abs(tau1 - tau2) < 1e-4:
        score *= 0.4     # single-exp: usable but less informative
    if beat < 0.05:
        score *= 0.6
    if B <= 1e-10 or B > 0.05:
        score = 0.0
    return round(score, 2)


# ---------------------------------------------------------------------------
# Physics law fitting helpers
# ---------------------------------------------------------------------------

def _wls_linear(x: np.ndarray, y: np.ndarray,
                w: np.ndarray) -> tuple[float, float]:
    """Weighted least-squares linear fit: y = a + b*x. Returns (a, b)."""
    w = np.clip(w, 0.0, None)
    sw = w.sum()
    if sw < 1e-12:
        return float(np.mean(y)), 0.0
    xm = (w * x).sum() / sw
    ym = (w * y).sum() / sw
    denom = (w * (x - xm) ** 2).sum()
    b = (w * (x - xm) * (y - ym)).sum() / (denom + 1e-30)
    a = ym - b * xm
    return float(a), float(b)


def _smooth_1d(x: np.ndarray, y: np.ndarray, w: np.ndarray,
               x_out: np.ndarray) -> np.ndarray:
    """
    GP-smooth (if sklearn available) or WLS-linear prediction on x_out.
    Returns smoothed predictions.
    """
    mask = w > 0.05
    if mask.sum() < 2:
        return np.full(len(x_out), float(np.median(y)))

    xs, ys, ws = x[mask], y[mask], w[mask]

    if _HAS_SKLEARN and mask.sum() >= 4:
        kernel = ConstantKernel(1.0) * RBF(length_scale=12.0,
                                           length_scale_bounds=(5.0, 30.0)) \
               + WhiteKernel(noise_level=0.05, noise_level_bounds=(1e-5, 0.5))
        gp = GaussianProcessRegressor(kernel=kernel, n_restarts_optimizer=3,
                                      normalize_y=True)
        try:
            gp.fit(xs.reshape(-1, 1), ys, sample_weight=ws)
            return gp.predict(x_out.reshape(-1, 1))
        except Exception:
            pass

    # Fallback: weighted linear
    a, b = _wls_linear(xs, ys, ws)
    return a + b * x_out


def _smooth_2d(x1: np.ndarray, x2: np.ndarray, y: np.ndarray,
               w: np.ndarray, x1_out: np.ndarray,
               x2_out: np.ndarray) -> np.ndarray:
    """2D GP smooth (or bilinear) prediction. x1=midi, x2=vel. Returns y_out."""
    mask = w > 0.05
    if mask.sum() < 3:
        return np.full(len(x1_out), float(np.median(y)))

    xs  = np.stack([x1[mask], x2[mask]], axis=1)
    ys  = y[mask]
    ws  = w[mask]
    xout = np.stack([x1_out, x2_out], axis=1)

    if _HAS_SKLEARN and mask.sum() >= 6:
        kernel = ConstantKernel(1.0) * RBF(length_scale=[12.0, 2.0],
                                           length_scale_bounds=[(3.0, 30.0),
                                                                 (0.5, 5.0)]) \
               + WhiteKernel(noise_level=0.05)
        gp = GaussianProcessRegressor(kernel=kernel, n_restarts_optimizer=2,
                                      normalize_y=True)
        try:
            gp.fit(xs, ys, sample_weight=ws)
            return gp.predict(xout)
        except Exception:
            pass

    # Fallback: bilinear
    X = np.column_stack([np.ones(len(xs)), xs[:, 0], xs[:, 1]])
    try:
        c, *_ = np.linalg.lstsq(X * ws[:, None], ys * ws, rcond=None)
    except np.linalg.LinAlgError:
        c = [float(np.mean(ys)), 0.0, 0.0]
    Xout = np.column_stack([np.ones(len(xout)), xout[:, 0], xout[:, 1]])
    return Xout @ c


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class InstrumentDNA:
    """
    Fit physical laws from extracted params and generate complete soundbank.

    Attributes set after fit():
        _B_law       : (midi: float) -> log_B prediction
        _tau1_law    : (midi: float, vel: int) -> log_tau1_k1 prediction
        _tau_ratio   : (midi: float, k: int) -> log(tau_k / tau_k1) prediction
        _beat_law    : (midi: float) -> log_beat_hz prediction
        _a1_law      : (midi: float, vel: int, k: int) -> a1 prediction
        _A0_shape    : (midi: float, k: int) -> log(A0_k / A0_1) prediction
        _noise_law   : (midi: float, vel: int) -> {attack_tau, A_noise}
    """

    def __init__(self):
        self._fitted = False
        self._raw    = {}      # extracted samples dict
        self._qualities: dict[str, float] = {}

    # ------------------------------------------------------------------ fit

    def fit(self, params_path: str,
            anchors_path: Optional[str] = None) -> "InstrumentDNA":
        """
        Fit physics laws from extracted params JSON.

        Args:
            params_path:  Path to extracted params JSON (from ParamExtractor).
            anchors_path: Optional path to anchor quality JSON (from anchor_helper).
                          If None, qualities are auto-detected from extraction.
        """
        with open(params_path) as f:
            data = json.load(f)
        self._raw = data.get("notes", {})

        # ── Load or auto-detect qualities ──────────────────────────────────
        qualities: dict[str, float] = {}
        if anchors_path and os.path.exists(anchors_path):
            aq = json.load(open(anchors_path))
            for bank in aq.get("banks", []):
                for key, q in bank.get("notes", {}).items():
                    qualities[key] = float(q)
            print(f"  Loaded {len(qualities)} anchor quality scores from {anchors_path}")
        else:
            for key, note in self._raw.items():
                qualities[key] = _auto_quality(note)
            n_good = sum(1 for q in qualities.values() if q >= 0.5)
            print(f"  Auto-quality: {n_good}/{len(qualities)} notes with q>=0.5")

        self._qualities = qualities

        print("  Fitting physics laws …")
        self._fit_B_law()
        self._fit_tau1_law()
        self._fit_tau_ratio_law()
        self._fit_beat_law()
        self._fit_a1_law()
        self._fit_A0_shape()
        self._fit_noise_law()

        self._fitted = True
        print("  InstrumentDNA fit complete.")
        return self

    # ─── Physics law fits ────────────────────────────────────────────────────

    def _collect(self, param_fn, vel_filter=None):
        """
        Collect (midi, vel, value, quality) tuples from raw data.
        param_fn: (note_dict) -> float or None
        """
        rows = []
        for key, note in self._raw.items():
            if not note.get("partials"):
                continue
            q = self._qualities.get(key, 0.0)
            if q < 0.05:
                continue
            if vel_filter is not None and note.get("vel") != vel_filter:
                continue
            val = param_fn(note)
            if val is None or not np.isfinite(val):
                continue
            rows.append((float(note["midi"]), float(note["vel"]), float(val), float(q)))
        return np.array(rows) if rows else np.zeros((0, 4))

    def _fit_B_law(self):
        """log(B) = a + b*midi  [one B per MIDI, shared across vel]"""
        # Use vel=4 as reference; B is per-MIDI
        rows = {}
        for key, note in self._raw.items():
            midi = note.get("midi")
            B    = note.get("B", 0.0)
            q    = self._qualities.get(key, 0.0)
            if B > 1e-10 and B < 0.05 and q >= 0.3:
                if midi not in rows or q > rows[midi][1]:
                    rows[midi] = (math.log(B), q)

        if len(rows) < 3:
            self._B_pred = lambda midi: -10.0
            return

        midis = np.array(list(rows.keys()), dtype=float)
        logBs = np.array([rows[m][0] for m in rows])
        wts   = np.array([rows[m][1] for m in rows])
        all_midi = np.arange(MIDI_MIN, MIDI_MAX + 1, dtype=float)
        self._B_smooth = _smooth_1d(midis, logBs, wts, all_midi)
        # Store as lookup
        self._B_table = {int(m): float(b) for m, b in zip(all_midi, self._B_smooth)}
        print(f"    B law: {len(rows)} anchor points, "
              f"range [{np.exp(self._B_smooth.min()):.2e}, "
              f"{np.exp(self._B_smooth.max()):.2e}]")

    def _predict_B(self, midi: int) -> float:
        logB = self._B_table.get(int(midi), -9.0)
        return math.exp(logB)

    def _fit_tau1_law(self):
        """log(tau1_k1) smoothed over (midi, vel)."""
        rows = self._collect(lambda n: (
            math.log(n["partials"][0]["tau1"])
            if n["partials"] and n["partials"][0].get("tau1") and
               n["partials"][0]["tau1"] > 0.01 else None
        ))
        if len(rows) < 4:
            self._tau1_table = {}
            return

        midis, vels, log_t1, wts = rows[:, 0], rows[:, 1], rows[:, 2], rows[:, 3]
        # Build grid prediction for all (midi, vel) pairs
        all_midi = np.arange(MIDI_MIN, MIDI_MAX + 1, dtype=float)
        all_vel  = np.arange(VEL_COUNT, dtype=float)
        grid_m, grid_v = np.meshgrid(all_midi, all_vel)
        grid_m_f = grid_m.flatten()
        grid_v_f = grid_v.flatten()

        pred = _smooth_2d(midis, vels, log_t1, wts, grid_m_f, grid_v_f)
        self._tau1_table = {
            (int(m), int(v)): float(p)
            for m, v, p in zip(grid_m_f, grid_v_f, pred)
        }
        print(f"    tau1 law: {len(rows)} anchor points")

    def _predict_tau1_k1(self, midi: int, vel: int) -> float:
        log_t = self._tau1_table.get((int(midi), int(vel)), math.log(2.0))
        return math.exp(log_t)

    def _fit_tau_ratio_law(self):
        """
        log(tau1_k / tau1_k1) = -alpha * (k-1)
        Fit alpha per note, then smooth alpha(midi).
        """
        alphas_midi = []
        alphas_val  = []
        alphas_w    = []

        for key, note in self._raw.items():
            q = self._qualities.get(key, 0.0)
            if q < 0.3 or not note.get("partials"):
                continue
            parts = note["partials"]
            tau1_k1 = parts[0].get("tau1")
            if not tau1_k1 or tau1_k1 <= 0:
                continue
            ks, log_ratios = [], []
            for p in parts[1:min(len(parts), 16)]:
                tk = p.get("tau1")
                if tk and tk > 0.01:
                    ratio = tk / tau1_k1
                    if 0.05 < ratio < 20.0:
                        ks.append(p["k"] - 1)
                        log_ratios.append(math.log(ratio))
            if len(ks) < 3:
                continue
            ks_a = np.array(ks, dtype=float)
            lr_a = np.array(log_ratios)
            try:
                popt, _ = curve_fit(lambda k, alpha: -alpha * k,
                                    ks_a, lr_a, p0=[0.05],
                                    bounds=([0.0], [1.0]))
                alphas_midi.append(float(note["midi"]))
                alphas_val.append(float(popt[0]))
                alphas_w.append(q)
            except Exception:
                pass

        if len(alphas_midi) < 3:
            self._tau_alpha_default = 0.05
            self._tau_alpha_table   = {}
            return

        am = np.array(alphas_midi)
        av = np.array(alphas_val)
        aw = np.array(alphas_w)
        all_midi = np.arange(MIDI_MIN, MIDI_MAX + 1, dtype=float)
        smooth_alpha = _smooth_1d(am, av, aw, all_midi)
        smooth_alpha = np.clip(smooth_alpha, 0.0, 0.5)
        self._tau_alpha_table = {int(m): float(a) for m, a in zip(all_midi, smooth_alpha)}
        self._tau_alpha_default = float(np.median(av))
        print(f"    tau_ratio law: alpha range [{smooth_alpha.min():.3f}, {smooth_alpha.max():.3f}]")

    def _predict_tau_ratio(self, midi: int, k: int) -> float:
        """Return tau1_k / tau1_k1 predicted value."""
        alpha = self._tau_alpha_table.get(int(midi), self._tau_alpha_default)
        return math.exp(-alpha * (k - 1))

    def _fit_beat_law(self):
        """log(beat_hz) = a + b*log(f0)  for k=1."""
        rows = []
        for key, note in self._raw.items():
            q = self._qualities.get(key, 0.0)
            if q < 0.3 or not note.get("partials"):
                continue
            beat = note["partials"][0].get("beat_hz", 0.0)
            if beat > 0.05:
                rows.append((math.log(_midi_to_hz(note["midi"])),
                             math.log(beat), q))
        if len(rows) < 3:
            self._beat_table = {}
            return

        rows_a = np.array(rows)
        log_f, log_b, wts = rows_a[:, 0], rows_a[:, 1], rows_a[:, 2]
        all_midi = np.arange(MIDI_MIN, MIDI_MAX + 1, dtype=float)
        log_f_out = np.log([_midi_to_hz(int(m)) for m in all_midi])
        smooth = _smooth_1d(log_f, log_b, wts, log_f_out)
        self._beat_table = {int(m): float(b) for m, b in zip(all_midi, smooth)}
        print(f"    beat law: {len(rows)} anchor points, "
              f"range [{np.exp(smooth.min()):.3f}, {np.exp(smooth.max()):.3f}] Hz")

    def _predict_beat_hz(self, midi: int) -> float:
        log_b = self._beat_table.get(int(midi))
        if log_b is None:
            return 0.1
        return math.exp(log_b)

    def _fit_a1_law(self):
        """a1 for k=1: smooth surface over (midi, vel)."""
        rows = self._collect(lambda n: (
            n["partials"][0].get("a1")
            if n["partials"] and n["partials"][0].get("a1") is not None
               and not n["partials"][0].get("mono", True)
            else None
        ))
        if len(rows) < 4:
            self._a1_default = 0.5
            self._a1_table   = {}
            return

        midis, vels, a1s, wts = rows[:, 0], rows[:, 1], rows[:, 2], rows[:, 3]
        a1s = np.clip(a1s, 0.01, 0.99)
        all_midi = np.arange(MIDI_MIN, MIDI_MAX + 1, dtype=float)
        all_vel  = np.arange(VEL_COUNT, dtype=float)
        grid_m, grid_v = np.meshgrid(all_midi, all_vel)
        pred = _smooth_2d(midis, vels, a1s, wts,
                          grid_m.flatten(), grid_v.flatten())
        pred = np.clip(pred, 0.05, 0.95)
        self._a1_table = {
            (int(m), int(v)): float(p)
            for m, v, p in zip(grid_m.flatten(), grid_v.flatten(), pred)
        }
        self._a1_default = float(np.median(a1s))
        print(f"    a1 law: {len(rows)} anchor points, default={self._a1_default:.3f}")

    def _predict_a1(self, midi: int, vel: int) -> float:
        return self._a1_table.get((int(midi), int(vel)), self._a1_default)

    def _fit_A0_shape(self):
        """
        A0 spectral shape: log(A0_k / A0_1) as function of (k, midi).
        Simplified: fit slope per midi, smooth slope across midi.
        """
        slope_midi = []
        slope_val  = []
        slope_w    = []

        for key, note in self._raw.items():
            q = self._qualities.get(key, 0.0)
            if q < 0.3 or not note.get("partials"):
                continue
            parts = note["partials"]
            A0_k1 = parts[0].get("A0", 0.0)
            if A0_k1 <= 0:
                continue
            ks, log_ratios = [], []
            for p in parts[1:min(len(parts), 20)]:
                ak = p.get("A0", 0.0)
                if ak > 0:
                    ks.append(float(p["k"] - 1))
                    log_ratios.append(math.log(ak / A0_k1))
            if len(ks) < 3:
                continue
            ks_a = np.array(ks)
            lr_a = np.array(log_ratios)
            # Fit slope: log(A0_k/A0_1) = slope * (k-1)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                try:
                    popt, _ = curve_fit(lambda k, s: s * k, ks_a, lr_a,
                                        p0=[0.0], bounds=([-3.0], [3.0]))
                    slope_midi.append(float(note["midi"]))
                    slope_val.append(float(popt[0]))
                    slope_w.append(q)
                except Exception:
                    pass

        if len(slope_midi) < 2:
            self._A0_slope_table = {}
            self._A0_slope_default = 0.0
            return

        sm = np.array(slope_midi)
        sv = np.array(slope_val)
        sw = np.array(slope_w)
        all_midi = np.arange(MIDI_MIN, MIDI_MAX + 1, dtype=float)
        smooth = _smooth_1d(sm, sv, sw, all_midi)
        self._A0_slope_table = {int(m): float(s) for m, s in zip(all_midi, smooth)}
        self._A0_slope_default = float(np.median(sv))
        print(f"    A0 shape law: slope range [{smooth.min():.3f}, {smooth.max():.3f}]")

    def _predict_A0_ratio(self, midi: int, k: int) -> float:
        """Return A0_k / A0_1 predicted value."""
        slope = self._A0_slope_table.get(int(midi), self._A0_slope_default)
        return math.exp(slope * (k - 1))

    def _fit_noise_law(self):
        """Smooth attack_tau and A_noise across (midi, vel)."""
        rows_tau, rows_anoise = [], []
        for key, note in self._raw.items():
            q = self._qualities.get(key, 0.0)
            if q < 0.1:
                continue
            tau = note.get("attack_tau", 0.0)
            an  = note.get("A_noise", 0.0)
            if not tau and not an:
                continue
            midi = float(note["midi"])
            vel  = float(note["vel"])
            if tau > 0:
                rows_tau.append((midi, vel, math.log(tau), q))
            if an > 0:
                rows_anoise.append((midi, vel, math.log(an), q))

        def _table_from_rows(rows):
            if len(rows) < 2:
                return {}, 0.0
            r = np.array(rows)
            m, v, y, w = r[:, 0], r[:, 1], r[:, 2], r[:, 3]
            all_midi = np.arange(MIDI_MIN, MIDI_MAX + 1, dtype=float)
            all_vel  = np.arange(VEL_COUNT, dtype=float)
            gm, gv = np.meshgrid(all_midi, all_vel)
            pred = _smooth_2d(m, v, y, w, gm.flatten(), gv.flatten())
            tbl = {(int(mm), int(vv)): float(pp)
                   for mm, vv, pp in zip(gm.flatten(), gv.flatten(), pred)}
            return tbl, float(np.median(y))

        self._attack_tau_table, self._attack_tau_default = _table_from_rows(rows_tau)
        self._A_noise_table,    self._A_noise_default    = _table_from_rows(rows_anoise)

    def _predict_noise(self, midi: int, vel: int) -> tuple[float, float]:
        log_tau = self._attack_tau_table.get((int(midi), int(vel)),
                                             self._attack_tau_default)
        log_an  = self._A_noise_table.get((int(midi), int(vel)),
                                          self._A_noise_default)
        return math.exp(log_tau), math.exp(log_an)

    # ───────────────────────── Velocity enforcement ────────────────────────

    def _enforce_velocity(self, note_group: dict) -> dict:
        """
        For a group of 8 notes (same midi, vel 0-7):
        - A0_k1: monotonically increasing with vel
        - tau1_k1: monotonically decreasing with vel
        Returns updated note_group.
        """
        vels  = sorted(note_group.keys())
        if len(vels) < 2:
            return note_group

        if _HAS_SKLEARN:
            iso_up   = IsotonicRegression(increasing=True)
            iso_down = IsotonicRegression(increasing=False)
        else:
            iso_up = iso_down = None

        # A0 monotone increasing
        a0s = np.array([note_group[v]["partials"][0]["A0"] for v in vels])
        if iso_up is not None:
            a0s_fixed = iso_up.fit_transform(vels, a0s)
        else:
            a0s_fixed = np.maximum.accumulate(a0s)
        for i, v in enumerate(vels):
            note_group[v]["partials"][0]["A0"] = float(a0s_fixed[i])

        # tau1 monotone decreasing
        t1s = np.array([note_group[v]["partials"][0]["tau1"] for v in vels])
        if iso_down is not None:
            t1s_fixed = iso_down.fit_transform(vels, t1s)
        else:
            t1s_fixed = np.minimum.accumulate(t1s)
        for i, v in enumerate(vels):
            note_group[v]["partials"][0]["tau1"] = float(t1s_fixed[i])

        return note_group

    # ────────────────────────── Generation ────────────────────────────────

    def generate_note(self, midi: int, vel: int,
                      rng: np.random.Generator,
                      k_max: int = K_MAX) -> dict:
        """Generate one note dict from fitted laws."""
        assert self._fitted, "Call .fit() first"

        f0    = _midi_to_hz(midi)
        B     = self._predict_B(midi)
        tau1  = self._predict_tau1_k1(midi, vel)
        a1    = self._predict_a1(midi, vel)
        beat  = self._predict_beat_hz(midi)
        atk_tau, a_noise = self._predict_noise(midi, vel)

        # tau2 from a1 and tau1: for a1=0.5, tau2 ≈ 5*tau1; for a1=0.9, tau2 ≈ 2*tau1
        tau2 = tau1 * max(1.5, (1.0 - a1) * 15.0 + 1.5)

        # Determine K_valid
        nyquist = 24000.0
        K_valid = 0
        for k in range(1, k_max + 1):
            fk = _inharmonic_freq(k, f0, B)
            if fk >= nyquist * 0.95:
                break
            K_valid = k

        # Build partials
        A0_k1 = self._predict_A0_from_anchor(midi, vel) or 1.0
        partials = []
        for k in range(1, K_valid + 1):
            fk      = _inharmonic_freq(k, f0, B)
            tau1_k  = tau1 * self._predict_tau_ratio(midi, k)
            tau2_k  = tau2 * self._predict_tau_ratio(midi, k) * 0.8  # tau2 ratio slightly faster
            tau2_k  = max(tau2_k, tau1_k * 1.2)
            A0_k    = A0_k1 * self._predict_A0_ratio(midi, k)
            beat_k  = beat * max(0.5, 1.0 - 0.03 * (k - 1))  # slight rolloff with k
            phi_k   = float(rng.uniform(0, 2 * math.pi))

            partials.append({
                "k":          k,
                "f_hz":       float(fk),
                "A0":         float(max(A0_k, 1e-12)),
                "tau1":       float(max(tau1_k, 0.01)),
                "tau2":       float(max(tau2_k, tau1_k + 0.05)),
                "a1":         float(a1),
                "beat_hz":    float(beat_k),
                "phi":        phi_k,
            })

        phi_diff = float(rng.uniform(0, 2 * math.pi))

        return {
            "midi":       midi,
            "vel":        vel,
            "f0_hz":      float(f0),
            "B":          float(B),
            "K_valid":    K_valid,
            "attack_tau": float(atk_tau),
            "A_noise":    float(a_noise),
            "phi_diff":   float(phi_diff),
            "rms_gain":   1.0,   # calibrated later
            "eq_biquads": [{"b": [1.0, 0.0, 0.0], "a": [0.0, 0.0]}],  # neutral
            "partials":   partials,
            "_dna":       True,
        }

    def _predict_A0_from_anchor(self, midi: int, vel: int) -> Optional[float]:
        """Find nearest anchor note A0_k1 as absolute level reference."""
        key = f"m{midi:03d}_vel{vel}"
        note = self._raw.get(key)
        if note and note.get("partials") and self._qualities.get(key, 0) >= 0.3:
            return float(note["partials"][0].get("A0", 1.0))
        # Find nearest midi with good quality
        for delta in range(1, 12):
            for dm in [delta, -delta]:
                m2 = midi + dm
                k2 = f"m{m2:03d}_vel{vel}"
                n2 = self._raw.get(k2)
                if n2 and n2.get("partials") and self._qualities.get(k2, 0) >= 0.3:
                    return float(n2["partials"][0].get("A0", 1.0))
        return None

    def generate_bank(self, sr: int = 48000, k_max: int = K_MAX,
                      rng_seed: int = 42) -> dict:
        """Generate all 88×8 notes. Returns soundbank notes dict."""
        assert self._fitted
        rng   = np.random.default_rng(rng_seed)
        notes = {}

        # Generate raw notes
        for midi in range(MIDI_MIN, MIDI_MAX + 1):
            group = {}
            for vel in range(VEL_COUNT):
                note = self.generate_note(midi, vel, rng, k_max=k_max)
                group[vel] = note
            # Enforce velocity consistency
            group = self._enforce_velocity(group)
            for vel, note in group.items():
                key = f"m{midi:03d}_vel{vel}"
                notes[key] = note

        return notes

    # ─────────────────────────── Export ────────────────────────────────────

    def save_bank(self, output_path: str,
                  sr: int = 48000,
                  target_rms: float = TARGET_RMS,
                  duration_s: float = 3.0,
                  k_max: int = K_MAX,
                  rng_seed: int = 42,
                  calibrate_rms: bool = True):
        """
        Generate and export a complete soundbank JSON.

        Args:
            output_path:   Output path for the JSON file.
            calibrate_rms: If True, synthesize each note to compute rms_gain.
                           Requires training.modules.synthesizer. Slow but accurate.
        """
        assert self._fitted

        print(f"  Generating {(MIDI_MAX-MIDI_MIN+1)*VEL_COUNT} notes …")
        notes = self.generate_bank(sr=sr, k_max=k_max, rng_seed=rng_seed)

        if calibrate_rms:
            print(f"  Calibrating rms_gain (target_rms={target_rms}) …")
            notes = self._calibrate_rms(notes, sr, duration_s, target_rms)

        bank = {
            "source":    "dna:instrument_dna",
            "sr":        sr,
            "target_rms": target_rms,
            "vel_gamma":  0.7,
            "k_max":      k_max,
            "rng_seed":   rng_seed,
            "duration_s": duration_s,
            "n_notes":    len(notes),
            "notes":      notes,
        }

        os.makedirs(Path(output_path).parent, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(bank, f, indent=None, separators=(",", ":"))
        size_mb = os.path.getsize(output_path) / 1e6
        print(f"  Saved: {output_path}  ({size_mb:.1f} MB, {len(notes)} notes)")
        return bank

    def _calibrate_rms(self, notes: dict, sr: int,
                       duration_s: float, target_rms: float) -> dict:
        """Synthesize each note and set rms_gain to hit target_rms."""
        try:
            from training.modules.synthesizer import Synthesizer
            synth = Synthesizer(sr=sr)
        except ImportError:
            print("  [warn] synthesizer not available — rms_gain left at 1.0")
            return notes

        vel_gamma = 0.7
        updated = 0
        for key, note in notes.items():
            vel = note["vel"]
            vel_gain = ((vel + 1) / 8.0) ** vel_gamma
            try:
                audio = synth.render(note, midi=note["midi"], vel=note["vel"],
                                    duration=duration_s)
                rms   = float(np.sqrt(np.mean(audio ** 2)))
                if rms > 1e-10:
                    note["rms_gain"] = float((target_rms * vel_gain) / rms)
                    updated += 1
            except Exception:
                pass

        print(f"  rms_gain calibrated for {updated}/{len(notes)} notes")
        return notes


# ---------------------------------------------------------------------------
# CLI convenience
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse, sys

    parser = argparse.ArgumentParser(description="Instrument DNA: fit + generate soundbank")
    parser.add_argument("params",   help="Extracted params JSON (from ParamExtractor)")
    parser.add_argument("output",   help="Output soundbank JSON path")
    parser.add_argument("--anchors", default="", help="Anchor quality JSON (optional)")
    parser.add_argument("--sr",      type=int,   default=48000)
    parser.add_argument("--no-rms",  action="store_true", help="Skip rms_gain calibration")
    args = parser.parse_args()

    dna = InstrumentDNA()
    dna.fit(args.params, anchors_path=args.anchors or None)
    dna.save_bank(args.output, sr=args.sr, calibrate_rms=not args.no_rms)
