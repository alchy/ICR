"""
training/pipeline_smooth_icr_eval.py
──────────────────────────────────────
Extract -> filter -> EQ -> spline-smooth measured params (+ optional extend partials)
-> train NN (ICR eval) -> export hybrid + pure-NN.

Difference from pipeline_icr_eval:
    - After extraction the measured params are smoothed with spline_fix
      (auto-anchors selected by extraction quality: K_valid × tau2/tau1 × a1).
    - Optionally the measured partials are extended to the maximum measured
      partial count before training (--extend-partials).  This gives the NN
      complete harmonic targets for every measured note so it naturally
      predicts full partial counts for unmeasured positions too.
    - The NN trains on these smoother, complete targets → better generalisation.
    - The hybrid bank preserves the RAW measured notes (ground truth) and uses
      the NN output directly for unmeasured positions — no second spline pass.

Replaces full-spline-icr-eval and b-spline-icr-eval (both were redundant after
the NoB refactor; extend_partials is now a flag here).

Output files:
    <out_path>                           final hybrid bank
    <stem>-pre-smooth.json               measured-only raw export (intermediate)
    <stem>-pre-smooth-spline.json        spline-smoothed measured (training targets)
    <stem>-pure-nn.json                  all 704 notes from NN (A/B comparison)

Call via run-training.py or import directly:
    from training.pipeline_smooth_icr_eval import run
    model, out_path = run(bank_dir, out_path, icr_exe="build/bin/Release/ICR.exe")
"""

from __future__ import annotations

import json
from pathlib import Path

from training.modules.extractor                 import ParamExtractor
from training.modules.structural_outlier_filter import StructuralOutlierFilter
from training.modules.eq_fitter                 import EQFitter
from training.modules.profile_trainer_exp       import ProfileTrainerEncExp
from training.modules.icr_evaluator             import ICRBatchEvaluator
from training.modules.exporter                  import SoundbankExporter


def _resolve_icr_exe(icr_exe: str) -> str:
    p = Path(icr_exe)
    if p.is_absolute():
        return str(p)
    repo_root = Path(__file__).parent.parent
    candidate = repo_root / icr_exe
    if candidate.exists():
        return str(candidate)
    return icr_exe


def run(
    bank_dir:        str,
    out_path:        str,
    epochs:          int   = 5000,
    workers:         int   = None,
    skip_outliers:   bool  = False,
    sr_tag:          str   = "f48",
    icr_exe:         str   = "build/bin/Release/ICR.exe",
    note_dur:        float = 3.0,
    icr_patience:    int   = 15,
    sr:              int   = 48000,
    auto_anchors:    int   = 12,
    extend_partials: bool  = False,
) -> tuple:
    """
    Smooth-ICR-eval pipeline:
    Extract -> filter -> EQ -> spline-smooth [+ extend partials] -> train NN
    (ICR early-stop) -> export hybrid + pure-NN.

    Args:
        bank_dir:        Directory with WAV files.
        out_path:        Output JSON soundbank path (final hybrid bank).
        epochs:          Max NN training epochs (early stop may exit sooner).
        workers:         Parallel worker count (None = auto).
        skip_outliers:   Skip structural outlier detection step.
        sr_tag:          Sample-rate tag suffix, e.g. "f44" or "f48".
        icr_exe:         Path to ICR.exe binary.
        note_dur:        Duration in seconds for each ICR-rendered eval note.
        icr_patience:    Early stop after this many evals without improvement.
        sr:              Sample rate for ICR rendering (must match sr_tag).
        auto_anchors:    Number of anchors auto-selected by extraction quality.
        extend_partials: Extend measured notes to max measured partial count
                         before training.  New partial values are filled by the
                         spline so the NN trains on complete harmonic targets.

    Returns:
        (model, out_path) -- trained model and path to final soundbank JSON.
    """
    out_path = str(out_path)
    stem     = Path(out_path).stem
    parent   = Path(out_path).parent
    pre_path = str(parent / (stem + "-pre-smooth.json"))
    spl_path = str(parent / (stem + "-pre-smooth-spline.json"))

    # ── Step 1: Extract + filter + EQ ────────────────────────────────────────
    print("\n[1/4] Extracting params...")
    params = ParamExtractor().extract_bank(bank_dir, workers, sr_tag=sr_tag)
    if not skip_outliers:
        params = StructuralOutlierFilter().filter(params)
    params = EQFitter().fit_bank(params, bank_dir, workers)

    # ── Step 2: Export raw measured bank + spline-smooth as training targets ──
    print(f"\n[2/4] Exporting raw measured bank -> {pre_path}")
    SoundbankExporter().from_params(params, pre_path)

    ep_label = "+ extend partials" if extend_partials else ""
    print(f"\n  Spline-smoothing measured params "
          f"(auto_anchors={auto_anchors}{', ' + ep_label if ep_label else ''})...")
    _spline_smooth_simple(pre_path, spl_path, auto_anchors, extend_partials)

    smooth_bank    = json.loads(Path(spl_path).read_text())
    duration_s     = float(smooth_bank.get("duration_s", 3.0))
    from tools.spline_fix import json_notes_to_samples
    smooth_samples = json_notes_to_samples(smooth_bank["notes"], duration_s)
    smooth_params  = {**params, "samples": smooth_samples}
    print(f"  Smooth params: {len(smooth_samples)} notes loaded as training targets")

    # ── Step 3: Train NN with ICR eval/early-stop ─────────────────────────────
    icr_exe_path = _resolve_icr_exe(icr_exe)
    if not Path(icr_exe_path).exists():
        raise FileNotFoundError(
            f"ICR.exe not found at '{icr_exe_path}'. "
            f"Build with CMake (target ICR) or specify --icr-exe."
        )

    print(f"\n[3/4] Training NN on smooth params (max {epochs} epochs)...")
    evaluator = ICRBatchEvaluator(
        icr_exe  = icr_exe_path,
        bank_dir = bank_dir,
        sr       = sr,
        note_dur = note_dur,
        sr_tag   = sr_tag,
    )
    try:
        model = ProfileTrainerEncExp().train(
            smooth_params,
            epochs        = epochs,
            icr_evaluator = evaluator,
            icr_patience  = icr_patience,
        )
    finally:
        evaluator.close()

    # ── Step 4: Export hybrid (raw measured + NN) + pure-NN ──────────────────
    # params (not smooth_params) → measured positions keep raw extracted values
    print(f"\n[4/4] Exporting hybrid + pure-NN banks...")
    SoundbankExporter().hybrid(model, params, out_path)

    pure_nn_path = out_path.replace(".json", "-pure-nn.json")
    print(f"\nExporting pure-NN bank -> {pure_nn_path}")
    SoundbankExporter().pure_nn(model, params, pure_nn_path)

    print(f"\nDone -> {out_path}")
    return model, out_path


# ── Internal helpers ──────────────────────────────────────────────────────────

def _spline_smooth_simple(
    file_in:         str,
    file_out:        str,
    auto_anchors:    int,
    extend_partials: bool = False,
) -> None:
    """Apply smooth-all + optional extend-partials to a measured-only bank."""
    from tools.spline_fix import apply_spline_fix_bank

    bank  = json.loads(Path(file_in).read_text())
    notes = bank["notes"]
    print(f"  Input: {len(notes)} measured notes")

    fixed_notes, stats = apply_spline_fix_bank(
        notes,
        smooth_all      = True,
        extend_partials = extend_partials,
        auto_anchors    = auto_anchors,
    )

    out_bank = {**{k: v for k, v in bank.items() if k != "notes"},
                "notes": fixed_notes}
    Path(file_out).parent.mkdir(parents=True, exist_ok=True)
    Path(file_out).write_text(json.dumps(out_bank, separators=(",", ":")))
    size_mb = Path(file_out).stat().st_size / 1e6
    print(f"  Written: {file_out}  ({size_mb:.1f} MB)")
