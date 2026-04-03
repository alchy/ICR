"""
training/pipeline_smooth_icr_eval.py
──────────────────────────────────────
Extract -> filter -> EQ -> spline-smooth measured params -> train NN (ICR eval)
-> export hybrid -> spline-fix NN notes.

Difference from pipeline_icr_eval:
    - After extraction the measured params are smoothed with spline_fix
      (auto-anchors selected by extraction quality: K_valid × tau2/tau1 × a1).
    - The NN trains on these smoother targets → better generalisation to
      unmeasured MIDI positions.
    - After hybrid export a second spline_fix pass replaces NN-generated
      notes with spline values derived from the smooth measured data.

Output files:
    <out_path>                        final hybrid + spline-fixed bank
    <stem>-pre-smooth.json            measured-only simple export (intermediate)
    <stem>-pre-smooth-spline.json     spline-smoothed measured params (intermediate)

Call via run-training.py or import directly:
    from training.pipeline_smooth_icr_eval import run
    model, out_path = run(bank_dir, out_path, icr_exe="build/bin/Release/ICR.exe")
"""

from __future__ import annotations

import json
import sys
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
    bank_dir:      str,
    out_path:      str,
    epochs:        int   = 5000,
    workers:       int   = None,
    skip_outliers: bool  = False,
    sr_tag:        str   = "f48",
    icr_exe:       str   = "build/bin/Release/ICR.exe",
    note_dur:      float = 3.0,
    icr_patience:  int   = 15,
    sr:            int   = 48000,
    auto_anchors:  int   = 12,
) -> tuple:
    """
    Smooth-ICR-eval pipeline:
    Extract -> filter -> EQ -> spline-smooth -> train NN (ICR early-stop)
    -> export hybrid -> spline-fix NN notes.

    Args:
        bank_dir:      Directory with WAV files.
        out_path:      Output JSON soundbank path (final bank).
        epochs:        Max NN training epochs (early stop may exit sooner).
        workers:       Parallel worker count (None = auto).
        skip_outliers: Skip structural outlier detection step.
        sr_tag:        Sample-rate tag suffix, e.g. "f44" or "f48".
        icr_exe:       Path to ICR.exe binary.
        note_dur:      Duration in seconds for each ICR-rendered eval note.
        icr_patience:  Early stop after this many evals without improvement.
        sr:            Sample rate for ICR rendering (must match sr_tag).
        auto_anchors:  Number of anchors auto-selected by extraction quality.

    Returns:
        (model, out_path) -- trained model and path to final soundbank JSON.
    """
    # ── Paths for intermediate files ──────────────────────────────────────────
    out_path  = str(out_path)
    stem      = Path(out_path).stem
    parent    = Path(out_path).parent
    pre_path  = str(parent / (stem + "-pre-smooth.json"))
    spl_path  = str(parent / (stem + "-pre-smooth-spline.json"))

    # ── Step 1: Extract + filter + EQ ────────────────────────────────────────
    print("\n[1/5] Extracting params...")
    params = ParamExtractor().extract_bank(bank_dir, workers, sr_tag=sr_tag)
    if not skip_outliers:
        params = StructuralOutlierFilter().filter(params)
    params = EQFitter().fit_bank(params, bank_dir, workers)

    # ── Step 2: Export measured-only simple bank ──────────────────────────────
    print(f"\n[2/5] Exporting simple bank -> {pre_path}")
    SoundbankExporter().from_params(params, pre_path)

    # ── Step 3: Spline-smooth measured params ─────────────────────────────────
    print(f"\n[3/5] Spline-smoothing measured params (auto_anchors={auto_anchors})...")
    _spline_smooth_simple(pre_path, spl_path, auto_anchors)

    # Load smooth bank and replace params["samples"] with smoothed values
    smooth_bank    = json.loads(Path(spl_path).read_text())
    duration_s     = float(smooth_bank.get("duration_s", 3.0))
    from tools.spline_fix import json_notes_to_samples
    smooth_samples = json_notes_to_samples(smooth_bank["notes"], duration_s)
    smooth_params  = {**params, "samples": smooth_samples}
    n_smooth       = len(smooth_samples)
    print(f"  Smooth params: {n_smooth} notes loaded as training targets")

    # ── Step 4: Train NN with ICR eval/early-stop ─────────────────────────────
    icr_exe_path = _resolve_icr_exe(icr_exe)
    if not Path(icr_exe_path).exists():
        raise FileNotFoundError(
            f"ICR.exe not found at '{icr_exe_path}'. "
            f"Build with CMake (target ICR) or specify --icr-exe."
        )

    print(f"\n[4/5] Training NN on smooth params (max {epochs} epochs)...")
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

    # ── Step 5: Export hybrid + spline-fix NN notes ───────────────────────────
    print(f"\n[5/5] Exporting hybrid + fixing NN notes...")
    hybrid_path = out_path.replace(".json", "-hybrid-raw.json")
    SoundbankExporter().hybrid(model, smooth_params, hybrid_path)

    # Spline-fix: replace NN-generated notes with spline from smooth measured
    _spline_fix_hybrid(hybrid_path, out_path, pre_path, auto_anchors)

    print(f"\nDone -> {out_path}")
    return model, out_path


# ── Internal helpers ──────────────────────────────────────────────────────────

def _spline_smooth_simple(
    file_in:      str,
    file_out:     str,
    auto_anchors: int,
) -> None:
    """Apply smooth-all + auto-anchors to a measured-only simple bank."""
    from tools.spline_fix import apply_spline_fix_bank

    bank  = json.loads(Path(file_in).read_text())
    notes = bank["notes"]
    print(f"  Input: {len(notes)} measured notes")

    fixed_notes, stats = apply_spline_fix_bank(
        notes,
        smooth_all    = True,
        auto_anchors  = auto_anchors,
    )

    out_bank = {**{k: v for k, v in bank.items() if k != "notes"},
                "notes": fixed_notes}
    Path(file_out).parent.mkdir(parents=True, exist_ok=True)
    Path(file_out).write_text(json.dumps(out_bank, separators=(",", ":")))
    size_mb = Path(file_out).stat().st_size / 1e6
    print(f"  Written: {file_out}  ({size_mb:.1f} MB)")


def _spline_fix_hybrid(
    hybrid_path:  str,
    out_path:     str,
    ref_path:     str,
    auto_anchors: int,
) -> None:
    """Fix NN-generated notes in hybrid bank using measured-only spline."""
    from tools.spline_fix import apply_spline_fix_bank

    hybrid_bank = json.loads(Path(hybrid_path).read_text())
    ref_keys    = set(json.loads(Path(ref_path).read_text())["notes"].keys())
    notes       = hybrid_bank["notes"]
    print(f"  Hybrid: {len(notes)} notes  |  measured ref: {len(ref_keys)}")

    fixed_notes, stats = apply_spline_fix_bank(
        notes,
        fix_interpolated = True,
        ref_keys         = ref_keys,
        auto_anchors     = auto_anchors,
    )

    out_bank = {**{k: v for k, v in hybrid_bank.items() if k != "notes"},
                "notes": fixed_notes}
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(out_bank, separators=(",", ":")))
    size_mb = Path(out_path).stat().st_size / 1e6
    print(f"  Written: {out_path}  ({size_mb:.1f} MB)")
