"""
training/pipeline_icr_eval.py
───────────────────────────────
Extract -> filter -> EQ -> train EncExp NN (ICR-MRSTFT eval/early-stop) -> export.

Difference from pipeline_experimental:
    - Uses ICR.exe --render-batch as the perceptual evaluation metric.
    - Early stopping driven by ICR-MRSTFT (ground-truth C++ synthesis).
    - MRSTFTFinetuner removed: the NN is exported directly after training.

Call via run-training.py or import directly:
    from training.pipeline_icr_eval import run
    model, out_path = run(bank_dir, out_path, icr_exe="build/bin/Release/ICR.exe")
"""

from __future__ import annotations

import os
from pathlib import Path

from training.modules.extractor                  import ParamExtractor
from training.modules.structural_outlier_filter  import StructuralOutlierFilter
from training.modules.eq_fitter                  import EQFitter
from training.modules.profile_trainer_exp        import ProfileTrainerEncExp
from training.modules.icr_evaluator              import ICRBatchEvaluator
from training.modules.exporter                   import SoundbankExporter


def _resolve_icr_exe(icr_exe: str) -> str:
    """Resolve ICR.exe path relative to repo root if not absolute."""
    p = Path(icr_exe)
    if p.is_absolute():
        return str(p)
    # Try relative to repo root (parent of training/)
    repo_root = Path(__file__).parent.parent
    candidate = repo_root / icr_exe
    if candidate.exists():
        return str(candidate)
    # Fall back to as-given (may be on PATH)
    return icr_exe


def run(
    bank_dir:     str,
    out_path:     str,
    epochs:       int   = 5000,
    workers:      int   = None,
    skip_outliers:bool  = False,
    sr_tag:       str   = "f48",
    icr_exe:      str   = "build/bin/Release/ICR.exe",
    note_dur:     float = 3.0,
    icr_patience: int   = 15,
    sr:           int   = 48000,
) -> tuple:
    """
    ICR-eval pipeline:
    Extract -> filter -> EQ -> train NN (ICR-MRSTFT early stop) -> export hybrid.

    Args:
        bank_dir:      Directory with WAV files.
        out_path:      Output JSON soundbank path.
        epochs:        Max NN training epochs (early stop may exit sooner).
        workers:       Parallel worker count (None = auto).
        skip_outliers: Skip structural outlier detection step.
        sr_tag:        Sample-rate tag suffix, e.g. "f44" or "f48".
        icr_exe:       Path to ICR.exe binary.
        note_dur:      Duration in seconds for each ICR-rendered eval note.
        icr_patience:  Early stop after this many evals without ICR-MRSTFT improvement.
        sr:            Sample rate for ICR rendering (should match sr_tag).

    Returns:
        (model, out_path) -- trained InstrumentProfileEncExp and path to soundbank JSON.
    """
    icr_exe_path = _resolve_icr_exe(icr_exe)
    if not Path(icr_exe_path).exists():
        raise FileNotFoundError(
            f"ICR.exe not found at '{icr_exe_path}'. "
            f"Build with CMake (target ICR) or specify --icr-exe."
        )

    params = ParamExtractor().extract_bank(bank_dir, workers, sr_tag=sr_tag)
    if not skip_outliers:
        params = StructuralOutlierFilter().filter(params)
    params = EQFitter().fit_bank(params, bank_dir, workers)

    evaluator = ICRBatchEvaluator(
        icr_exe  = icr_exe_path,
        bank_dir = bank_dir,
        sr       = sr,
        note_dur = note_dur,
        sr_tag   = sr_tag,
    )

    try:
        model = ProfileTrainerEncExp().train(
            params,
            epochs        = epochs,
            icr_evaluator = evaluator,
            icr_patience  = icr_patience,
        )
    finally:
        evaluator.close()

    SoundbankExporter().hybrid(model, params, out_path)
    return model, out_path
