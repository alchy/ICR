"""
training/extraction_config.py
──────────────────────────────
Configurable thresholds for additive synthesis extraction pipeline.

Two presets:
  STRICT  — original v1 behavior (many clamps, physics overrides)
  RELAXED — v2 behavior (trust extraction, minimal corrections)

Usage:
    from training.extraction_config import ExtractionConfig, RELAXED, STRICT
    cfg = RELAXED  # or STRICT for backward compat
"""

from dataclasses import dataclass


@dataclass
class ExtractionConfig:
    """All tunable thresholds for the extraction pipeline."""

    # ── Bi-exponential decay fitting ──────────────────────────────────
    tau1_floor: float = 0.010          # minimum tau1 (s). v1=0.050
    tau1_max_factor: float = 0.5       # tau1_max = min(max_s, duration * factor)
    tau1_max_s: float = 60.0           # absolute tau1 ceiling (s). v1=20.0
    biexp_ratio_min: float = 1.05      # accept bi-exp if tau2/tau1 > this. v1=1.3
    biexp_improvement: float = 0.90    # accept if residual < mono * this. v1=0.85

    # ── Damping law override ──────────────────────────────────────────
    damping_law_enabled: bool = False  # apply physics tau1 correction. v1=True
    damping_law_sigma: float = 10.0    # replace if deviation > N×. v1=3.0
    damping_law_tau1_min: float = 0.01 # don't correct above this. v1=0.02
    damping_law_tau1_max: float = 30.0 # don't correct below this. v1=10.0

    # ── Physics floor (amplitude boost) ───────────────────────────────
    physics_floor_enabled: bool = False  # inject missing partials. v1=True
    physics_floor_scale: float = 0.25    # energy scale factor. v1=0.50
    physics_floor_max_k: int = 12        # apply to first N partials. v1=12
    a1_blend_enabled: bool = False       # blend a1 toward 0.73. v1=True
    a1_blend_factor: float = 0.10        # blend amount. v1=0.20

    # ── Attack / noise ────────────────────────────────────────────────
    attack_tau_max: float = 0.050      # max attack_tau (s). v1=0.010
    noise_amp_max: float = 2.0         # max A_noise. v1=1.0
    noise_centroid_use_floor: bool = False  # apply MIDI-dependent floor. v1=True

    # ── Beating ───────────────────────────────────────────────────────
    beat_hz_fallback: float = 0.0      # inject if undetected. v1=0.25. 0=off

    # ── Outlier filter ────────────────────────────────────────────────
    outlier_enabled: bool = True
    outlier_sigma: float = 5.0         # MAD-sigma threshold. v1=3.0

    # ── EQ fitting ────────────────────────────────────────────────────
    eq_sub_fundamental_clamp: bool = False  # clamp sub-f0 EQ to 0 dB. v1=True
    eq_stereo_width_range: tuple = (0.1, 3.0)  # v1=(0.2, 2.0)

    # ── Keyboard smoothing ────────────────────────────────────────────
    keyboard_smoothing_enabled: bool = True
    keyboard_smoothing_threshold: float = 0.7  # deviation to trigger. v1=0.5


# ── Presets ────────────────────────────────────────────────────────────

RELAXED = ExtractionConfig()  # defaults above = relaxed v2

STRICT = ExtractionConfig(
    tau1_floor=0.050,
    tau1_max_s=20.0,
    biexp_ratio_min=1.3,
    biexp_improvement=0.85,
    damping_law_enabled=True,
    damping_law_sigma=3.0,
    damping_law_tau1_min=0.02,
    damping_law_tau1_max=10.0,
    physics_floor_enabled=True,
    physics_floor_scale=0.50,
    a1_blend_enabled=True,
    a1_blend_factor=0.20,
    attack_tau_max=0.010,
    noise_amp_max=1.0,
    noise_centroid_use_floor=True,
    beat_hz_fallback=0.25,
    outlier_enabled=True,
    outlier_sigma=3.0,
    eq_sub_fundamental_clamp=True,
    eq_stereo_width_range=(0.2, 2.0),
    keyboard_smoothing_threshold=0.5,
)
