"""
training/modules/exporter.py
──────────────────────────────
Export AdditiveSynthesisPianoCore-ready JSON soundbanks.

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

from training_additive.modules.eq_fitter import _eq_to_biquads


# ─────────────────────────────────────────────────────────────────────────────
# Constants (match additive_synthesis_piano_core.cpp expectations)
# ─────────────────────────────────────────────────────────────────────────────

PIANO_MAX_PARTIALS = 60
PIANO_N_BIQUAD     = 10
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
        SoundbankExporter().from_params(params, "soundbanks-additive/out.json")

        # Export hybrid: real data where available, NN prediction for gaps
        SoundbankExporter().hybrid(model, params, "soundbanks-additive/out.json")
    """

    def from_params(
        self,
        params:     dict,
        out_path:   str,
        sr:         int   = SR_DEFAULT,
        duration:   float = DURATION_DEFAULT,
        target_rms: float = TARGET_RMS_DEFAULT,
        rng_seed:   int   = RNG_SEED_DEFAULT,
        skip_physics_floor: bool = False,
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

        # Spectral shape borrowing: fix noise-floor contamination in low velocity layers.
        # Average the spectral shape A0(k)/A0(1) from vel layers 5-7 (forte/fortissimo),
        # then apply to vel 0-4 while preserving each layer's overall amplitude.
        # This is physically motivated: real piano spectral tilt changes only subtly
        # with velocity (Chabassier 2012: nonlinear hammer exponent p causes gradual,
        # not drastic, spectral change).
        self._borrow_spectral_shape(samples)
        if not skip_physics_floor:
            self._apply_exploration_recipe(samples)

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
        from training_additive.modules.profile_trainer import build_dataset, generate_profile

        samples = params["notes"]
        measured = {k: v for k, v in samples.items()
                    if not v.get("is_interpolated")}

        # Build dataset only to get eq_freqs
        ds = build_dataset(measured)

        # Generate full 88×8 profile; measured samples are preserved verbatim.
        # Use generate_profile_exp for EncExp models (forward_dur requires vf).
        try:
            from training_additive.modules.profile_trainer_exp import (
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
        from training_additive.modules.profile_trainer import build_dataset, generate_profile

        samples  = params["notes"]
        measured = {k: v for k, v in samples.items()
                    if not v.get("is_interpolated")}

        ds = build_dataset(measured)

        try:
            from training_additive.modules.profile_trainer_exp import (
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

    # ── Keyboard smoothing ────────────────────────────────────────────────────

    @staticmethod
    def _smooth_across_keyboard(samples: dict) -> None:
        """
        Smooth per-partial parameters across the keyboard to reduce
        extraction artifacts (e.g. tau1 jumping 9x between neighbors).

        Real piano parameters change gradually across MIDI range.
        A 5-note weighted median filter smooths outliers while preserving
        genuine register transitions (wound→plain string crossover).

        Smoothed params: tau1, tau2 (per partial k=1..8), A_noise, attack_tau.
        NOT smoothed: A0 (spectral shape), f_hz, beat_hz, phi (note-specific).
        """
        from scipy.ndimage import median_filter

        # Group by velocity layer
        by_vel: dict[int, dict[int, dict]] = {}
        for key, sample in samples.items():
            if not key.startswith("m"):
                continue
            try:
                midi = int(key[1:4])
                vel  = int(key.split("vel")[1])
            except (ValueError, IndexError):
                continue
            by_vel.setdefault(vel, {})[midi] = sample

        n_smoothed = 0
        for vel, midi_map in by_vel.items():
            midis = sorted(midi_map.keys())
            if len(midis) < 5:
                continue

            # Smooth note-level params
            for param_name in ["attack_tau", "A_noise"]:
                vals = np.array([float(midi_map[m].get(param_name, 0) or 0)
                                 for m in midis])
                if vals.max() < 1e-12:
                    continue
                # Median filter (size=5 = 2 neighbors each side)
                smoothed = median_filter(vals, size=5, mode='nearest')
                for i, m in enumerate(midis):
                    if abs(vals[i] - smoothed[i]) / (vals[i] + 1e-12) > 0.3:
                        midi_map[m][param_name] = float(smoothed[i])
                        n_smoothed += 1

            # Smooth per-partial tau1, tau2 for k=1..8
            for ki in range(8):
                tau1_vals = []
                tau2_vals = []
                valid_midis = []
                for m in midis:
                    parts = midi_map[m].get("partials", [])
                    if ki < len(parts) and parts[ki].get("tau1"):
                        tau1_vals.append(float(parts[ki]["tau1"]))
                        tau2_vals.append(float(parts[ki].get("tau2") or parts[ki]["tau1"]))
                        valid_midis.append(m)

                if len(tau1_vals) < 5:
                    continue

                tau1_arr = np.array(tau1_vals)
                tau2_arr = np.array(tau2_vals)

                # Log-domain median filter (tau values span orders of magnitude)
                log_tau1 = np.log(np.maximum(tau1_arr, 1e-6))
                log_tau2 = np.log(np.maximum(tau2_arr, 1e-6))
                sm_log1 = median_filter(log_tau1, size=5, mode='nearest')
                sm_log2 = median_filter(log_tau2, size=5, mode='nearest')
                sm_tau1 = np.exp(sm_log1)
                sm_tau2 = np.exp(sm_log2)

                for i, m in enumerate(valid_midis):
                    parts = midi_map[m]["partials"]
                    # Only correct if deviation > 50% from smoothed
                    if abs(tau1_arr[i] - sm_tau1[i]) / (tau1_arr[i] + 1e-6) > 0.5:
                        parts[ki]["tau1"] = float(max(sm_tau1[i], 0.010))
                        n_smoothed += 1
                    if abs(tau2_arr[i] - sm_tau2[i]) / (tau2_arr[i] + 1e-6) > 0.5:
                        parts[ki]["tau2"] = float(max(sm_tau2[i], parts[ki]["tau1"]))
                        n_smoothed += 1

        if n_smoothed > 0:
            print(f"Keyboard smoothing: corrected {n_smoothed} parameters "
                  f"across {len(by_vel)} velocity layers", flush=True)

    # ── Physics floor — fill missing partials to physical minimum ────────────

    @staticmethod
    def _apply_exploration_recipe(samples: dict) -> None:
        """Fill gaps in extracted partial amplitudes to physical floor.

        Piano string struck at x0/L ≈ 1/8 produces:
            A(n) = sin(n * π * x0/L) / n

        This is the MINIMUM spectral profile any properly struck piano
        string should have.  If extraction found less energy in a partial,
        it's an extraction failure — boost it to the floor.  Never CUT
        partials that are already above the floor (extraction got it right).

        Energy conservation: total energy in all partials is constrained
        by hammer kinetic energy E = ½Mv².  RMS calibration (applied AFTER
        this step) automatically compensates the overall level, so we only
        need to fix the DISTRIBUTION, not the absolute values.

        Also: a1 blend toward 0.73, beating floor, attack sharpening.
        """
        X0_L = 1.0 / 8.0   # standard piano striking position

        def physics_floor(n):
            """Ideal partial amplitude for harmonic n (energy-normalized)."""
            return abs(math.sin(n * math.pi * X0_L)) / n

        n_floor_applied = 0
        n_partials_boosted = 0
        n_modified = 0

        for key, sample in samples.items():
            if not key.startswith("m"):
                continue
            try:
                midi = int(key[1:4])
            except (ValueError, IndexError):
                continue

            parts = sample.get("partials", [])
            if len(parts) < 4:
                continue

            # Compute physics floor scaled to this note's total energy.
            # The floor has same total energy as extracted — only reshapes.
            extracted_energy = sum(p.get("A0", 0)**2 for p in parts[:12])
            if extracted_energy < 1e-20:
                continue

            floor_profile = [physics_floor(k + 1) for k in range(min(12, len(parts)))]
            floor_energy = sum(a**2 for a in floor_profile)
            if floor_energy < 1e-20:
                continue

            # Scale floor to 50% of extracted energy — conservative floor
            scale = math.sqrt(extracted_energy / floor_energy) * 0.5

            note_boosted = False
            for ki in range(min(12, len(parts))):
                p = parts[ki]
                k = p.get("k", ki + 1)
                current_A0 = p.get("A0", 0)
                floor_A0 = physics_floor(k) * scale

                # Boost only — never cut
                if current_A0 < floor_A0:
                    p["A0"] = floor_A0
                    n_partials_boosted += 1
                    note_boosted = True

            if note_boosted:
                n_floor_applied += 1

            # a1: blend 20% toward 0.73 (more aftersound sustain)
            for p in parts[:10]:
                p["a1"] = 0.8 * p.get("a1", 0.8) + 0.2 * 0.73

            # Beating: ensure minimum 0.25 Hz if missing
            for p in parts[:8]:
                if p.get("beat_hz", 0) < 0.1:
                    p["beat_hz"] = 0.25

            # Attack: cap at 10ms (sharper hammer)
            sample["attack_tau"] = min(sample.get("attack_tau", 0.03), 0.010)

            n_modified += 1

        if n_modified > 0:
            print(f"Physics floor: {n_floor_applied}/{n_modified} notes had partials "
                  f"boosted ({n_partials_boosted} total partials, "
                  f"floor=sin(n*pi/8)/n at 50% energy)",
                  flush=True)

    # ── Spectral shape borrowing ─────────────────────────────────────────────

    @staticmethod
    def _borrow_spectral_shape(samples: dict) -> None:
        """
        Fix noise-floor contamination in low-velocity layers.

        For quiet recordings (vel 0-4), high partials often fall below the
        analysis noise floor, producing artificially small A0 values — the
        note sounds like ff with a low-pass filter instead of a softer touch.

        Fix: compute reference spectral shape A0(k)/A0(1) averaged from
        vel layers 5, 6, 7 (forte range — best SNR).  Apply this shape to
        vel 0-4 while preserving each layer's own A0(1) (overall loudness).

        Only partials where the reference shape has a HIGHER relative level
        than the target are corrected — we never darken a layer.
        """
        # Group samples by MIDI note
        by_midi: dict[int, dict[int, dict]] = {}
        for key, sample in samples.items():
            if not key.startswith("m"):
                continue
            try:
                midi = int(key[1:4])
                vel  = int(key.split("vel")[1])
            except (ValueError, IndexError):
                continue
            by_midi.setdefault(midi, {})[vel] = sample

        n_fixed = 0
        for midi, vel_map in by_midi.items():
            # Collect reference layers (vel 5, 6, 7)
            ref_layers = [vel_map[v] for v in (5, 6, 7) if v in vel_map]
            if not ref_layers:
                continue

            # Compute average spectral shape: A0(k) / A0(k=1) across ref layers
            # Use the minimum partial count across references
            ref_shapes = []
            for ref in ref_layers:
                parts = ref.get("partials", [])
                if len(parts) < 2:
                    continue
                a0_1 = float(parts[0].get("A0", 1e-12) or 1e-12)
                if a0_1 < 1e-12:
                    continue
                shape = [float(p.get("A0", 0) or 0) / a0_1 for p in parts]
                ref_shapes.append(shape)

            if not ref_shapes:
                continue

            # Average shapes (extend to longest — use available data per k)
            max_k = max(len(s) for s in ref_shapes)
            avg_shape = [0.0] * max_k
            counts    = [0] * max_k
            for s in ref_shapes:
                for ki in range(len(s)):
                    avg_shape[ki] += s[ki]
                    counts[ki] += 1
            avg_shape = [v / max(c, 1) for v, c in zip(avg_shape, counts)]

            # Apply to vel 0-4
            for vel_idx in range(5):
                if vel_idx not in vel_map:
                    continue
                sample = vel_map[vel_idx]
                parts  = sample.get("partials", [])
                if len(parts) < 2:
                    continue

                a0_1 = float(parts[0].get("A0", 1e-12) or 1e-12)
                if a0_1 < 1e-12:
                    continue

                for ki in range(min(len(parts), max_k)):
                    current_a0 = float(parts[ki].get("A0", 0) or 0)
                    target_a0  = a0_1 * avg_shape[ki]
                    # Only correct upward — never darken a layer
                    if target_a0 > current_a0:
                        parts[ki]["A0"] = target_a0
                        n_fixed += 1

        if n_fixed > 0:
            print(f"Spectral shape borrowing: corrected {n_fixed} partial A0 values "
                  f"across {len(by_midi)} MIDI notes", flush=True)

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
        """Convert one sample entry into a additive_synthesis_piano_core_v2 note dict."""
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
        A_noise         = min(float(sample.get("A_noise", 0.04) or 0.04), 1.0)
        # noise_centroid_hz: MIDI-dependent floor instead of fixed 1000 Hz.
        # Bass notes need higher floor (harmonics are low → residual centroid
        # is dominated by harmonic leakage).  Middle/treble notes should use
        # their extracted centroid — blind test showed fixed 1000 Hz floor
        # correlates with bad scores (r=+0.33).
        centroid_hz_raw = float(sample.get("noise_centroid_hz", 3000.0) or 3000.0)
        # Floor: ~1200 Hz for bass (MIDI 21), ~400 Hz for treble (MIDI 108)
        centroid_floor  = max(400.0, 1200.0 - (midi - 21) * 800.0 / 87.0)
        centroid_hz     = max(centroid_hz_raw, centroid_floor)
        tau1_k1         = (partials_out[0]["tau1"] if partials_out else 3.0)
        # Hard cap at 0.10 s — real hammer noise never exceeds ~50 ms.
        attack_tau      = min(attack_tau_raw, tau1_k1, 0.10)

        # EQ biquads (needed for rms_gain calibration)
        eq_biquads = self._fit_eq_biquads(sample, sr)

        # RMS gain calibration — renders partials + noise + EQ to get true output RMS
        rms_gain = self._compute_rms_gain(
            partials_out, phi_diff, attack_tau, A_noise, centroid_hz,
            eq_biquads, vel_idx, midi, sr, duration, target_rms,
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
            # stereo_width: extracted from eq_fitter measurement as flat key for C++.
            # Represents orig_S/orig_M divided by syn_S/syn_M — how much wider/narrower
            # the original recording is vs the synthesized output.
            # C++ applies M/S post-EQ: M unchanged, S scaled by this factor.
            # rms_gain calibration is unaffected: (L'+R')/2 = M is invariant to M/S.
            w = float(spectral_eq.get("stereo_width_factor", 1.0) or 1.0)
            note["stereo_width"] = round(w, 4)
        if sample.get("is_interpolated"):
            note["is_interpolated"] = True

        # Per-note synthesis overrides (C++ falls back to midi-based defaults if absent)
        # n_strings: acoustic string count for this MIDI note
        note["n_strings"] = 1 if midi <= 27 else (2 if midi <= 48 else 3)
        # rise_tau: attack rise time in seconds
        # Chabassier: bass ~4ms, middle ~2ms, treble <1ms (hammer contact time)
        rise_ms = 4.0 - (midi - 21) / (108 - 21) * 3.8  # 4.0ms -> 0.2ms
        note["rise_tau"] = round(max(rise_ms * 0.001, 0.0002), 6)

        return note

    def _build_partial(self, p: dict, phi: float, k_idx: int = 0) -> dict:
        """Sanitise and convert one raw partial dict."""
        beat = float(p.get("beat_hz", 0.0) or 0.0)
        if p.get("mono", False):
            beat = 0.0

        raw_tau1 = p.get("tau1")
        tau1 = max(float(raw_tau1) if raw_tau1 is not None else 0.5, 0.010)

        raw_tau2 = p.get("tau2")
        tau2 = float(raw_tau2) if raw_tau2 is not None else tau1
        tau2 = max(tau2, tau1)

        raw_a1 = p.get("a1")
        a1 = float(raw_a1) if raw_a1 is not None else 1.0
        if tau2 <= tau1*1.001:
            a1 = 1.0

        out = {
            "k":       int(p.get("k", k_idx + 1)),
            "f_hz":    float(p["f_hz"]),
            "A0":      float(p["A0"]),
            "tau1":    tau1,
            "tau2":    tau2,
            "a1":      a1,
            "beat_hz": beat,
            "phi":     phi,
        }
        # Extraction diagnostics (optional, for GUI display)
        fq = p.get("fit_quality")
        if fq is not None:
            out["fit_quality"] = round(float(fq), 4)
        if p.get("damping_derived"):
            out["damping_derived"] = True
        return out

    def _compute_rms_gain(
        self,
        partials:     list,
        phi_diff:     float,
        attack_tau:   float,
        A_noise:      float,
        centroid_hz:  float,
        eq_biquads:   list,
        vel_idx:      int,
        midi:         int,
        sr:           int,
        duration:     float,
        target_rms:   float,
    ) -> float:
        """Calibrate rms_gain so rendered note hits target_rms * vel_gain.

        Renders partials + noise (with centroid IIR filter) + EQ biquads,
        matching the C++ additive_synthesis_piano_core.cpp signal path exactly (1/2/3-string model
        depending on MIDI), so that rms_gain produces correct output level.
        """
        vel_gain  = ((vel_idx+1)/8.0)**VEL_GAMMA
        audio     = _render_note_rms_ref(
            partials, phi_diff, attack_tau, A_noise, centroid_hz,
            eq_biquads, midi, sr, duration,
        )
        rms = float(np.sqrt(np.mean(audio**2) + 1e-12))
        return ((target_rms * vel_gain) / rms) if rms > 1e-10 else 1.0

    def _fit_eq_biquads(self, sample: dict, sr: int) -> list:
        """Fit EQ biquads from spectral_eq data if present.

        Sub-fundamental clamping: gains are clipped to ≤ 0 dB below f0*0.8.
        This prevents the EQ from boosting room-tone / sympathetic-resonance
        content at frequencies where no harmonics exist, which would inflate
        the rms_gain calibration and make the harmonic content quieter.
        """
        eq_data = sample.get("spectral_eq")
        if not eq_data:
            return []
        freqs_hz = eq_data.get("freqs_hz")
        gains_db = eq_data.get("gains_db")
        if not freqs_hz or not gains_db:
            return []
        try:
            f_arr = np.array(freqs_hz, dtype=np.float64)
            g_arr = np.array(gains_db, dtype=np.float64)
            # Clamp boosts below the fundamental to avoid fitting sub-harmonic
            # room-tone compensation (real cause of HF brilliance deficit).
            f0_hz = float(sample.get("f0_hz", 0.0))
            if f0_hz > 100.0:
                sub_mask = f_arr < f0_hz * 0.8
                g_arr[sub_mask] = np.minimum(g_arr[sub_mask], 0.0)
            return _eq_to_biquads(f_arr, g_arr, sr, n_sections=PIANO_N_BIQUAD)
        except Exception:
            return []

    def _write(self, out: dict, out_path: str) -> None:
        print(f"\nTotal: {len(out['notes'])} notes")
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(out, f, separators=(",", ":"))
        size_mb = Path(out_path).stat().st_size / 1e6
        print(f"Written: {out_path}  ({size_mb:.1f} MB)")


# ─────────────────────────────────────────────────────────────────────────────
# RMS calibration helper (replica of additive_synthesis_piano_core render for gain computation)
# ─────────────────────────────────────────────────────────────────────────────

def _render_note_rms_ref(
    partials:    list,
    phi_diff:    float,
    attack_tau:  float,
    A_noise:     float,
    centroid_hz: float,
    eq_biquads:  list,
    midi:        int,
    sr:          int,
    duration:    float,
) -> np.ndarray:
    """
    Render one note (rms_gain=1) matching the additive_synthesis_piano_core.cpp signal path:
      1. Partials — 1/2/3-string model depending on MIDI, bi-exp envelope
      2. Attack noise — biquad bandpass at centroid_hz Q=1.5 (matches C++ exactly)
      3. Spectral EQ — biquad cascade (Direct Form II, same coeffs as C++)

    Result is mono (L+R average). Used exclusively to calibrate rms_gain so that
    the C++ player output hits target_rms * vel_gain after applying all three stages.

    String model (matches C++ initVoice/processBlock):
      MIDI ≤ 27: 1-string  s = cos(f*t + phi)
      MIDI 28-48: 2-string  s1=cos((f+b/2)*t+phi),  s2=cos((f-b/2)*t+phi+phi_diff)
      MIDI > 48:  3-string  s1=cos((f-b)*t+phi),  s2=cos(f*t+phi2),  s3=cos((f+b)*t+phi+phi_diff)
      where b = beat_hz (full detuning for 3-string, half-detuning for 2-string).
    """
    n_strings = 1 if midi <= 27 else (2 if midi <= 48 else 3)

    N      = int(duration * sr)
    inv_sr = np.float32(1.0) / np.float32(sr)
    t_idx  = np.arange(N, dtype=np.float32)
    t_f    = t_idx * inv_sr
    tpi2   = np.float32(2.0 * math.pi) * t_f
    audio  = np.zeros(N, dtype=np.float32)

    # 1. Partials — string model matches C++ processBlock
    rng_p = np.random.default_rng(1)   # deterministic seed for reproducible rms_gain
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

        if n_strings == 1:
            audio += A0 * env * np.cos(phase_c)
        elif n_strings == 2:
            phase_b = tpi2 * (beat_hz * np.float32(0.5))
            s1 = np.cos(phase_c + phase_b)
            s2 = np.cos(phase_c - phase_b + np.float32(phi_diff))
            audio += A0 * env * (s1 + s2) * np.float32(0.5)
        else:  # 3-string: f-beat, f, f+beat
            phi2    = np.float32(rng_p.uniform(0, 2*math.pi))
            phase_b = tpi2 * beat_hz   # full detuning
            s1 = np.cos(phase_c - phase_b)
            s2 = np.cos(tpi2 * f_hz + phi2)
            s3 = np.cos(phase_c + phase_b + np.float32(phi_diff))
            audio += A0 * env * (s1 + s2 + s3) / np.float32(3.0)

    # 2. Attack noise — biquad bandpass (centroid_hz, Q=1.5), envelope-gated
    #    Matches C++ initVoice + processBlock noise section exactly:
    #      v.noise_bpf = dsp::rbj_bandpass(centroid_hz, 1.5f, sr)
    #      noise = biquad_tick(white * A_noise * noise_env, bpf, state)
    #    Use deterministic seed so rms_gain is reproducible.
    rng      = np.random.default_rng(0)
    tau_n    = max(float(attack_tau), 1e-4)
    nenv     = np.exp(-t_f / np.float32(tau_n)).astype(np.float32)

    # RBJ bandpass coefficients (matches dsp::rbj_bandpass in dsp_math.h)
    Q  = 1.5
    fc = min(float(centroid_hz), sr * 0.45)
    w0 = 2.0 * math.pi * fc / sr
    alpha_bpf = math.sin(w0) / (2.0 * Q)
    a0 = 1.0 + alpha_bpf
    bp_b = [alpha_bpf / a0, 0.0, -alpha_bpf / a0]
    bp_a = [1.0, -2.0 * math.cos(w0) / a0, (1.0 - alpha_bpf) / a0]
    # scipy sos format: [b0, b1, b2, a0=1, a1, a2]
    noise_sos = np.array([[bp_b[0], bp_b[1], bp_b[2], 1.0, bp_a[1], bp_a[2]]])

    # Average of two independent channels (L+R) for mono estimate
    for _ in range(2):
        raw = rng.standard_normal(N).astype(np.float32)
        filtered = sosfilt(noise_sos, raw.astype(np.float64)).astype(np.float32)
        audio += np.float32(A_noise) * filtered * nenv * np.float32(0.5)

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
