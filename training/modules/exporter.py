"""
training/modules/exporter.py
──────────────────────────────
Export PianoCore-ready JSON soundbanks.

Public API:
    exporter = SoundbankExporter()
    exporter.from_params(params, out_path)          # real extracted params only
    exporter.hybrid(model, params, out_path)        # NN fills gaps in real params
"""

import json
import math
import time
from pathlib import Path

import numpy as np
from scipy.signal import sosfilt

from training.modules.eq_fitter import _eq_to_biquads


# ─────────────────────────────────────────────────────────────────────────────
# Constants (match piano_core.cpp expectations)
# ─────────────────────────────────────────────────────────────────────────────

PIANO_MAX_PARTIALS = 60
PIANO_N_BIQUAD     = 5
VEL_GAMMA          = 0.7
TARGET_RMS_DEFAULT = 0.06
SR_DEFAULT         = 44_100
DURATION_DEFAULT   = 3.0
RNG_SEED_DEFAULT   = 0


# ─────────────────────────────────────────────────────────────────────────────
# SoundbankExporter
# ─────────────────────────────────────────────────────────────────────────────

class SoundbankExporter:
    """
    Write ICR soundbank JSON from extracted params or NN model.

    Usage:
        # Export only real extracted data
        SoundbankExporter().from_params(params, "soundbanks/out.json")

        # Export hybrid: real data where available, NN prediction for gaps
        SoundbankExporter().hybrid(model, params, "soundbanks/out.json")
    """

    def from_params(
        self,
        params:     dict,
        out_path:   str,
        sr:         int   = SR_DEFAULT,
        duration:   float = DURATION_DEFAULT,
        target_rms: float = TARGET_RMS_DEFAULT,
        rng_seed:   int   = RNG_SEED_DEFAULT,
    ) -> None:
        """
        Export a soundbank from real extracted physics params.

        Args:
            params:     Params dict (keys: samples, …) from ParamExtractor.
            out_path:   Output JSON path.
            sr:         Sample rate.
            duration:   Render duration for RMS calibration.
            target_rms: Target RMS level.
            rng_seed:   Base seed for random phase generation.
        """
        samples = params["notes"]
        out     = self._make_header("soundbank:params", sr, target_rms, duration, rng_seed,
                                    params.get("metadata"))

        n_total = len(samples)
        print(f"Exporting params bank: {n_total} notes "
              f"(computing rms_gain + biquad EQ per note) …", flush=True)
        t0     = time.monotonic()
        n_done = 0
        for midi in range(21, 109):
            for vel_idx in range(8):
                key = f"m{midi:03d}_vel{vel_idx}"
                if key not in samples:
                    continue
                note_data = self._build_note(samples[key], midi, vel_idx,
                                             sr, duration, target_rms, rng_seed)
                out["notes"][key] = note_data
                n_done += 1
                if n_done % 88 == 0:
                    pct = 100 * n_done // n_total
                    ela = time.monotonic() - t0
                    print(f"  {n_done}/{n_total} ({pct}%)  {ela:.1f}s", flush=True)

        self._write(out, out_path)

    def hybrid(
        self,
        model,
        params:     dict,
        out_path:   str,
        sr:         int   = SR_DEFAULT,
        duration:   float = DURATION_DEFAULT,
        target_rms: float = TARGET_RMS_DEFAULT,
        rng_seed:   int   = RNG_SEED_DEFAULT,
    ) -> None:
        """
        Export a hybrid soundbank: real params where available, NN otherwise.

        The NN predictions fill in any (midi, vel) slots not covered by the
        original sample bank, giving a complete 88×8 soundbank.

        Args:
            model:   Trained InstrumentProfile.
            params:  Params dict with real extracted samples.
            out_path: Output JSON path.
        """
        from training.modules.profile_trainer import build_dataset, generate_profile

        samples = params["notes"]
        measured = {k: v for k, v in samples.items()
                    if not v.get("is_interpolated")}

        # Build dataset only to get eq_freqs
        ds = build_dataset(measured)

        # Generate full 88×8 profile; measured samples are preserved verbatim.
        # Use generate_profile_exp for EncExp models (forward_dur requires vf).
        try:
            from training.modules.profile_trainer_exp import (
                InstrumentProfileEncExp, generate_profile_exp,
                build_dataset_exp,
            )
            if isinstance(model, InstrumentProfileEncExp):
                ds_exp = build_dataset_exp(measured)
                all_samples = generate_profile_exp(
                    model, ds_exp, midi_from=21, midi_to=108, sr=sr,
                    orig_samples=measured,
                )
            else:
                all_samples = generate_profile(
                    model, ds, midi_from=21, midi_to=108, sr=sr,
                    orig_samples=measured,
                )
        except ImportError:
            all_samples = generate_profile(
                model, ds, midi_from=21, midi_to=108, sr=sr,
                orig_samples=measured,
            )

        out = self._make_header("nn-hybrid:model", sr, target_rms, duration, rng_seed,
                                params.get("metadata"))

        n_measured = len(measured)
        n_total    = len(all_samples)
        n_nn       = n_total - n_measured
        print(f"Exporting hybrid bank: {n_total} notes "
              f"({n_measured} measured + {n_nn} NN-generated, "
              f"computing rms_gain + biquad EQ per note) …", flush=True)
        t0     = time.monotonic()
        n_done = 0
        for midi in range(21, 109):
            for vel_idx in range(8):
                key = f"m{midi:03d}_vel{vel_idx}"
                if key not in all_samples:
                    continue
                note_data = self._build_note(all_samples[key], midi, vel_idx,
                                             sr, duration, target_rms, rng_seed)
                out["notes"][key] = note_data
                n_done += 1
                if n_done % 88 == 0:
                    pct = 100 * n_done // n_total
                    ela = time.monotonic() - t0
                    print(f"  {n_done}/{n_total} ({pct}%)  {ela:.1f}s", flush=True)

        self._write(out, out_path)

    def pure_nn(
        self,
        model,
        params:     dict,
        out_path:   str,
        sr:         int   = SR_DEFAULT,
        duration:   float = DURATION_DEFAULT,
        target_rms: float = TARGET_RMS_DEFAULT,
        rng_seed:   int   = RNG_SEED_DEFAULT,
    ) -> None:
        """
        Export a pure-NN soundbank: all 704 notes from NN, none from extraction.

        Unlike hybrid(), measured notes are NOT preserved — the NN predicts
        every (midi, vel) slot uniformly.  Useful for A/B comparison: does
        the NN produce a smoother keyboard sweep when not interrupted by
        measured notes at their original (potentially noisy) values?

        Args:
            model:    Trained InstrumentProfile.
            params:   Params dict (used only for eq_freqs / B spline context).
            out_path: Output JSON path.
        """
        from training.modules.profile_trainer import build_dataset, generate_profile

        samples  = params["notes"]
        measured = {k: v for k, v in samples.items()
                    if not v.get("is_interpolated")}

        ds = build_dataset(measured)

        try:
            from training.modules.profile_trainer_exp import (
                InstrumentProfileEncExp, generate_profile_exp,
                build_dataset_exp,
            )
            if isinstance(model, InstrumentProfileEncExp):
                ds_exp      = build_dataset_exp(measured)
                all_samples = generate_profile_exp(
                    model, ds_exp, midi_from=21, midi_to=108, sr=sr,
                    orig_samples=None,   # ← no measured notes preserved
                )
            else:
                all_samples = generate_profile(
                    model, ds, midi_from=21, midi_to=108, sr=sr,
                    orig_samples=None,
                )
        except ImportError:
            all_samples = generate_profile(
                model, ds, midi_from=21, midi_to=108, sr=sr,
                orig_samples=None,
            )

        out    = self._make_header("nn-pure:model", sr, target_rms, duration, rng_seed,
                                   params.get("metadata"))
        n_total = len(all_samples)
        print(f"Exporting pure-NN bank: {n_total} notes "
              f"(all from NN, no measured notes preserved, "
              f"computing rms_gain + biquad EQ per note) …", flush=True)
        t0     = time.monotonic()
        n_done = 0
        for midi in range(21, 109):
            for vel_idx in range(8):
                key = f"m{midi:03d}_vel{vel_idx}"
                if key not in all_samples:
                    continue
                note_data = self._build_note(all_samples[key], midi, vel_idx,
                                             sr, duration, target_rms, rng_seed)
                out["notes"][key] = note_data
                n_done += 1
                if n_done % 88 == 0:
                    pct = 100 * n_done // n_total
                    ela = time.monotonic() - t0
                    print(f"  {n_done}/{n_total} ({pct}%)  {ela:.1f}s", flush=True)

        self._write(out, out_path)

    # ── Internal builders ─────────────────────────────────────────────────────

    def _make_header(
        self, source: str, sr: int, target_rms: float,
        duration: float, rng_seed: int,
        input_meta: dict = None,
    ) -> dict:
        meta = dict(input_meta or {})
        meta.update({
            "source":     source,
            "sr":         sr,
            "target_rms": target_rms,
            "vel_gamma":  VEL_GAMMA,
            "k_max":      PIANO_MAX_PARTIALS,
            "rng_seed":   rng_seed,
            "duration_s": duration,
        })
        return {
            "metadata": meta,
            "notes":    {},
        }

    def _build_note(
        self,
        sample:     dict,
        midi:       int,
        vel_idx:    int,
        sr:         int,
        duration:   float,
        target_rms: float,
        rng_seed:   int,
    ) -> dict:
        """Convert one sample entry into a piano_core_v2 note dict."""
        seed   = rng_seed + midi*256 + vel_idx
        rng    = np.random.default_rng(seed)

        partials_raw = sample.get("partials", [])
        K            = min(len(partials_raw), PIANO_MAX_PARTIALS)

        phi_diff = float(rng.uniform(0, 2*math.pi))
        phis     = rng.uniform(0, 2*math.pi, K).astype(np.float32)

        partials_out = [
            self._build_partial(partials_raw[ki], float(phis[ki]), ki)
            for ki in range(K)
        ]

        # Noise params (flat keys; cap attack_tau at tau1 of k=1 partial)
        attack_tau_raw  = float(sample.get("attack_tau", 0.05) or 0.05)
        A_noise         = float(sample.get("A_noise", 0.04) or 0.04)
        centroid_hz     = float(sample.get("noise_centroid_hz", 3000.0) or 3000.0)
        tau1_k1         = (partials_out[0]["tau1"] if partials_out else 3.0)
        attack_tau      = min(attack_tau_raw, tau1_k1)

        # EQ biquads (needed for rms_gain calibration)
        eq_biquads = self._fit_eq_biquads(sample, sr)

        # RMS gain calibration — renders partials + noise + EQ to get true output RMS
        rms_gain = self._compute_rms_gain(
            partials_out, phi_diff, attack_tau, A_noise, centroid_hz,
            eq_biquads, vel_idx, sr, duration, target_rms,
        )

        # Preserve editabe source data alongside baked values
        note: dict = {
            "midi":              midi,
            "vel":               vel_idx,
            "f0_hz":             float(sample.get("f0_hz", 440.0) or 440.0),
            "B":                 float(sample.get("B") or 0.0),
            "phi_diff":          phi_diff,
            "attack_tau":        attack_tau,
            "A_noise":           A_noise,
            "noise_centroid_hz": centroid_hz,
            "rms_gain":          rms_gain,
            "partials":          partials_out,
            "eq_biquads":        eq_biquads,
        }
        # spectral_eq: raw freq/gain curve used to compute eq_biquads;
        # stored so the editor can re-fit biquads after curve edits.
        spectral_eq = sample.get("spectral_eq")
        if spectral_eq:
            note["spectral_eq"] = spectral_eq
        if sample.get("is_interpolated"):
            note["is_interpolated"] = True
        return note

    def _build_partial(self, p: dict, phi: float, k_idx: int = 0) -> dict:
        """Sanitise and convert one raw partial dict."""
        beat = float(p.get("beat_hz", 0.0) or 0.0)
        if p.get("mono", False):
            beat = 0.0

        raw_tau1 = p.get("tau1")
        tau1 = max(float(raw_tau1) if raw_tau1 is not None else 0.5, 0.005)

        raw_tau2 = p.get("tau2")
        tau2 = float(raw_tau2) if raw_tau2 is not None else tau1
        tau2 = max(tau2, tau1)

        raw_a1 = p.get("a1")
        a1 = float(raw_a1) if raw_a1 is not None else 1.0
        if tau2 <= tau1*1.001:
            a1 = 1.0

        return {
            "k":       int(p.get("k", k_idx + 1)),
            "f_hz":    float(p["f_hz"]),
            "A0":      float(p["A0"]),
            "tau1":    tau1,
            "tau2":    tau2,
            "a1":      a1,
            "beat_hz": beat,
            "phi":     phi,
        }

    def _compute_rms_gain(
        self,
        partials:     list,
        phi_diff:     float,
        attack_tau:   float,
        A_noise:      float,
        centroid_hz:  float,
        eq_biquads:   list,
        vel_idx:      int,
        sr:           int,
        duration:     float,
        target_rms:   float,
    ) -> float:
        """Calibrate rms_gain so rendered note hits target_rms * vel_gain.

        Renders partials + noise (with centroid IIR filter) + EQ biquads,
        matching the C++ piano_core.cpp signal path exactly, so that the
        baked rms_gain produces the correct output level in the player.
        """
        vel_gain  = ((vel_idx+1)/8.0)**VEL_GAMMA
        audio     = _render_note_rms_ref(
            partials, phi_diff, attack_tau, A_noise, centroid_hz,
            eq_biquads, sr, duration,
        )
        rms = float(np.sqrt(np.mean(audio**2) + 1e-12))
        return ((target_rms * vel_gain) / rms) if rms > 1e-10 else 1.0

    def _fit_eq_biquads(self, sample: dict, sr: int) -> list:
        """Fit EQ biquads from spectral_eq data if present."""
        eq_data = sample.get("spectral_eq")
        if not eq_data:
            return []
        freqs_hz = eq_data.get("freqs_hz")
        gains_db = eq_data.get("gains_db")
        if not freqs_hz or not gains_db:
            return []
        try:
            return _eq_to_biquads(
                np.array(freqs_hz, dtype=np.float64),
                np.array(gains_db, dtype=np.float64),
                sr, n_sections=PIANO_N_BIQUAD,
            )
        except Exception:
            return []

    def _write(self, out: dict, out_path: str) -> None:
        print(f"\nTotal: {out['n_notes']} notes")
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(out, f, separators=(",", ":"))
        size_mb = Path(out_path).stat().st_size / 1e6
        print(f"Written: {out_path}  ({size_mb:.1f} MB)")


# ─────────────────────────────────────────────────────────────────────────────
# RMS calibration helper (replica of piano_core render for gain computation)
# ─────────────────────────────────────────────────────────────────────────────

def _render_note_rms_ref(
    partials:    list,
    phi_diff:    float,
    attack_tau:  float,
    A_noise:     float,
    centroid_hz: float,
    eq_biquads:  list,
    sr:          int,
    duration:    float,
) -> np.ndarray:
    """
    Render one note (rms_gain=1) matching the piano_core.cpp signal path:
      1. Partials — 2-string model, bi-exp envelope
      2. Attack noise — 1-pole IIR low-pass at centroid_hz (independent L+R averaged)
      3. Spectral EQ — biquad cascade (Direct Form II, same coeffs as C++)

    Result is mono (L+R average). Used exclusively to calibrate rms_gain so that
    the C++ player output hits target_rms * vel_gain after applying all three stages.
    """
    N      = int(duration * sr)
    inv_sr = np.float32(1.0) / np.float32(sr)
    t_idx  = np.arange(N, dtype=np.float32)
    t_f    = t_idx * inv_sr
    tpi2   = np.float32(2.0 * math.pi) * t_f
    audio  = np.zeros(N, dtype=np.float32)

    # 1. Partials (2-string model, matches C++ processBlock)
    for p in partials:
        tau1    = np.float32(p["tau1"])
        tau2    = np.float32(p["tau2"])
        a1      = np.float32(p["a1"])
        A0      = np.float32(p["A0"])
        f_hz    = np.float32(p["f_hz"])
        beat_hz = np.float32(p["beat_hz"])
        phi     = np.float32(p["phi"])

        df       = np.exp(np.float32(-1.0) / np.maximum(tau1 * np.float32(sr), np.float32(1.0)))
        ds       = np.exp(np.float32(-1.0) / np.maximum(tau2 * np.float32(sr), np.float32(1.0)))
        env_fast = np.power(df, t_idx)
        env_slow = np.power(ds, t_idx)
        env      = a1 * env_fast + (np.float32(1.0) - a1) * env_slow

        phase_c = tpi2 * f_hz + phi
        phase_b = tpi2 * (beat_hz * np.float32(0.5))
        s1      = np.cos(phase_c + phase_b)
        s2      = np.cos(phase_c - phase_b + np.float32(phi_diff))
        audio  += A0 * env * (s1 + s2) * np.float32(0.5)

    # 2. Attack noise — 1-pole IIR low-pass (centroid_hz), envelope-gated
    #    Matches C++ initVoice + processBlock noise section.
    #    Use deterministic seed so rms_gain is reproducible.
    rng      = np.random.default_rng(0)
    alp      = float(1.0 - math.exp(-2.0 * math.pi * min(centroid_hz, sr * 0.45) / sr))
    tau_n    = max(float(attack_tau), 1e-4)
    nenv     = np.exp(-t_f / np.float32(tau_n)).astype(np.float32)
    # Average of two independent channels (L+R) for mono estimate
    for _ in range(2):
        raw = rng.standard_normal(N).astype(np.float32)
        # Apply 1-pole IIR in-place
        y = 0.0
        for i in range(N):
            y = alp * float(raw[i]) + (1.0 - alp) * y
            raw[i] = np.float32(y)
        audio += np.float32(A_noise) * raw * nenv * np.float32(0.5)

    # 3. Spectral EQ biquad cascade (Direct Form II)
    #    Converts eq_biquads list → scipy sos array and applies to mono signal.
    if eq_biquads:
        sos = np.array(
            [[bq["b"][0], bq["b"][1], bq["b"][2], 1.0, bq["a"][0], bq["a"][1]]
             for bq in eq_biquads],
            dtype=np.float64,
        )
        audio = sosfilt(sos, audio.astype(np.float64)).astype(np.float32)

    return audio
