"""
training/pipeline_v2.py
────────────────────────
Clean extraction pipeline (v2) with configurable thresholds.

Same 4-step flow as pipeline_simple, but applies ExtractionConfig
post-hoc to relax clamps that were baked into v1 modules.

The v1 modules (extractor, exporter) are used as-is, and their
overly-strict corrections are undone/relaxed in a post-processing
step controlled by ExtractionConfig.

Usage:
    from training.pipeline_v2 import run
    from training.extraction_config import RELAXED
    run(bank_dir, out_path, config=RELAXED)
"""

import math
from training.extraction_config import ExtractionConfig, RELAXED
from training.modules.extractor import ParamExtractor
from training.modules.structural_outlier_filter import StructuralOutlierFilter
from training.modules.eq_fitter import EQFitter
from training.modules.exporter import SoundbankExporter


def _relax_extraction(params: dict, cfg: ExtractionConfig) -> dict:
    """
    Post-process extraction results: undo v1 over-corrections.

    The v1 extractor applies:
      - tau1 floor at 50 ms
      - damping law override (replaces measured tau1 if deviation > 3×)
      - bi-exp acceptance ratio 1.3

    This function re-relaxes values that were force-corrected,
    restoring extraction fidelity where the v1 clamps were too aggressive.
    """
    if "samples" not in params:
        return params

    n_restored = 0

    for key, sample in params["samples"].items():
        partials = sample.get("partials", [])
        for p in partials:
            # Undo damping law override if config says disabled
            if not cfg.damping_law_enabled and p.get("damping_derived"):
                # The original measured tau1 was overwritten.
                # We can't recover it, but we can flag it as untrusted.
                # If raw_tau1 was saved, restore it.
                if "raw_tau1" in p:
                    p["tau1"] = p["raw_tau1"]
                    p["damping_derived"] = False
                    n_restored += 1

            # Relax tau1 floor (v1 used 50ms, v2 uses cfg.tau1_floor)
            # If tau1 was clamped to exactly 0.05 and cfg allows lower,
            # we can't recover the original, but we note the constraint.
            # (Real fix requires re-running extractor with lower floor.)

    if n_restored:
        print(f"  [v2] Restored {n_restored} damping-law-overridden tau1 values")

    return params


def run(bank_dir: str, out_path: str,
        workers: int = None,
        skip_eq: bool = False,
        sr_tag: str = "f48",
        config: ExtractionConfig = None) -> str:
    """
    v2 pipeline: Extract → relax → filter → EQ → export.

    All thresholds are controlled by `config` (ExtractionConfig).
    Default = RELAXED (trust extraction, minimal corrections).
    """
    if config is None:
        config = RELAXED

    # 1. Extract partials (uses v1 extractor internally)
    print("Step 1/4: Extracting partials from WAV files...")
    params = ParamExtractor().extract_bank(bank_dir, workers, sr_tag=sr_tag)

    # 1b. Relax v1 over-corrections based on config
    params = _relax_extraction(params, config)

    # 2. Outlier filter (optional, relaxed sigma)
    if config.outlier_enabled:
        print(f"Step 2/4: Outlier filter (sigma={config.outlier_sigma})...")
        params = StructuralOutlierFilter().filter(
            params, sigma=config.outlier_sigma)
    else:
        print("Step 2/4: Outlier filter SKIPPED")

    # 3. Spectral EQ fitting (optional)
    if not skip_eq:
        print("Step 3/4: Fitting spectral EQ...")
        params = EQFitter().fit_bank(params, bank_dir, workers)
    else:
        print("Step 3/4: EQ fitting SKIPPED")

    # 4. Export JSON bank (with relaxed constraints)
    print("Step 4/4: Exporting JSON bank...")
    SoundbankExporter().from_params(
        params, out_path,
        skip_physics_floor=not config.physics_floor_enabled,
    )

    return out_path
