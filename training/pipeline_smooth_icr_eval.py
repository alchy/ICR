"""
training/pipeline_smooth_icr_eval.py
──────────────────────────────────────
Extract -> filter -> EQ -> spline-smooth -> [ICR round-trip] -> train NN
(ICR eval) -> export hybrid + pure-NN.

Pipeline variants (controlled by flags):
    default              spline-smooth measured params as NN targets
    --extend-partials    extend measured to max partial count before training
    --icr-round-trip     run ICR round-trip after spline-smooth:
                           smooth_params → ICR render → re-extract → params_rt
                                       → spline-smooth params_rt → training targets
                         NN trains on smooth params_rt — converges to what ICR actually
                         produces, not what the extractor measured from real piano.

Why round-trip matters
──────────────────────
The extractor measures raw piano recordings. ICR synthesis has its own transfer
function — the same params fed to ICR produce a signal that, when re-extracted,
gives slightly different values (systematic offset per parameter). Training on
params_rt corrects for this offset: the NN learns what ICR needs as input to
reproduce the intended sound.

Why a second spline pass after round-trip
─────────────────────────────────────────
Re-extracting from ICR-rendered audio introduces the same per-note estimation noise
as extracting from real piano audio (windowed FFT variance, DF resolution limits,
bi-exp fitting uncertainty). Without a second spline pass these noisy per-note values
become training targets directly. The systematic ICR transfer-function correction is
smooth across MIDI notes and survives spline fitting; the per-note noise does not.

Why full-duration renders
─────────────────────────
Each note is rendered for its full natural duration (duration_s from smooth_params).
Short fixed renders (e.g. 3 s) limit:
  - DF estimation: frequency resolution = 1/duration; slow beats (< 0.3 Hz) in the
    bass register are invisible in a 3 s window.
  - tau2 estimation: bi-exp fitting uses t[-1]*0.9 as upper bound; 3 s caps tau2 at
    2.7 s, too short for bass notes where tau2 can reach 8–15 s.

EQ handling in round-trip
─────────────────────────
spectral_eq is excluded from the round-trip (neutral EQ used for rendering).
EQ will be handled as a separate training branch in a future refactor.

Output files:
    <out_path>                           final hybrid bank
    <stem>-pre-smooth.json               measured-only raw export (intermediate)
    <stem>-pre-smooth-spline.json        spline-smoothed measured (training targets)
    <stem>-pre-smooth-rt.json            raw round-trip targets before second spline (if --icr-round-trip)
    <stem>-pre-smooth-rt-spl.json        spline-smoothed round-trip targets (training targets, if --icr-round-trip)
    <stem>-pure-nn.json                  all 704 notes from NN (A/B comparison)

Call via run-training.py or import directly:
    from training.pipeline_smooth_icr_eval import run
    model, out_path = run(bank_dir, out_path, icr_exe="build/bin/Release/icr.exe")
    model, out_path = run(bank_dir, out_path, icr_exe=..., icr_round_trip=True)
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
    icr_exe:         str   = "build/bin/Release/icr.exe",
    note_dur:        float = 3.0,
    icr_patience:    int   = 15,
    sr:              int   = 48000,
    auto_anchors:    int   = 12,
    extend_partials: bool  = False,
    icr_round_trip:  bool  = False,
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
        icr_round_trip:  After spline-smooth, render all measured notes through
                         ICR (at full natural duration per note), re-extract params,
                         apply a second spline pass to remove per-note extraction
                         noise, and use the smoothed round-trip params as training
                         targets.  Corrects for systematic ICR synthesis offsets —
                         NN converges to what ICR actually produces.

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
    smooth_params  = {**params, "notes": smooth_samples}
    print(f"  Smooth params: {len(smooth_samples)} notes loaded as training targets")

    # ── Step 2b (optional): ICR round-trip correction ─────────────────────────
    if icr_round_trip:
        icr_exe_path = _resolve_icr_exe(icr_exe)
        if not Path(icr_exe_path).exists():
            raise FileNotFoundError(
                f"ICR.exe not found at '{icr_exe_path}'. "
                f"Required for --icr-round-trip."
            )
        from training.modules.icr_round_trip import ICRRoundTripProcessor
        rt_path    = str(parent / (stem + "-pre-smooth-rt.json"))
        smooth_params = ICRRoundTripProcessor(
            icr_exe  = icr_exe_path,
            sr       = sr,
            sr_tag   = sr_tag,
            note_dur = note_dur,
        ).process(smooth_params, workers=workers)
        # Save round-trip params for inspection
        rt_bank = {**smooth_bank, "notes": smooth_params["notes"]}
        Path(rt_path).write_text(json.dumps(rt_bank, separators=(",", ":")))
        print(f"  Round-trip targets written: {rt_path}")

        # Second spline pass: remove per-note extraction noise from round-trip
        # targets while preserving the systematic ICR transfer-function correction
        # (which is smooth across MIDI notes and survives spline fitting).
        rt_spl_path = str(parent / (stem + "-pre-smooth-rt-spl.json"))
        print(f"  Spline-smoothing round-trip targets (auto_anchors={auto_anchors})...")
        _spline_smooth_simple(rt_path, rt_spl_path, auto_anchors, extend_partials=False)
        rt_spl_bank    = json.loads(Path(rt_spl_path).read_text())
        smooth_samples = json_notes_to_samples(rt_spl_bank["notes"], duration_s)
        smooth_params  = {**params, "notes": smooth_samples}
        print(f"  Smooth round-trip params: {len(smooth_samples)} notes as training targets")

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

    pure_nn_path = str(Path(out_path).parent / (Path(out_path).stem.replace("-hybrid", "") + "-nn.json"))
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
