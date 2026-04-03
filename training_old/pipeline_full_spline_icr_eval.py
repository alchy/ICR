"""
training/pipeline_full_spline_icr_eval.py
──────────────────────────────────────────
Same as smooth-icr-eval but with partial extension enabled.

Workflow:
    Extract -> filter -> EQ
    -> spline-smooth measured params (auto-anchors)
    -> train NN (ICR early-stop) on smooth targets
    -> export hybrid
    -> spline-fix NN notes + extend NN notes to max measured partial count

The partial extension step fills in harmonics that the NN did not generate
but that are present in measured notes: each NN note is extended to the
maximum K_valid of its measured neighbours, and the new partial values
(tau1, tau2, A0, beat_hz) are filled by the spline fitted on measured data.

Output:
    <out_path>                        final bank (full partials, spline-fixed)
    <stem>-pre-smooth.json            measured-only export (intermediate)
    <stem>-pre-smooth-spline.json     spline-smoothed measured params (intermediate)
    <stem>-hybrid-raw.json            raw NN hybrid before spline fix (intermediate)

Usage:
    python run-training.py full-spline-icr-eval \\
        --bank C:/SoundBanks/IthacaPlayer/vv-rhodes
"""

from __future__ import annotations

from training.pipeline_smooth_icr_eval import run as _run_smooth


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
    Full-spline ICR-eval pipeline (smooth-icr-eval + partial extension).

    Identical to smooth-icr-eval with extend_partials=True.
    See pipeline_smooth_icr_eval.run() for full parameter docs.
    """
    return _run_smooth(
        bank_dir        = bank_dir,
        out_path        = out_path,
        epochs          = epochs,
        workers         = workers,
        skip_outliers   = skip_outliers,
        sr_tag          = sr_tag,
        icr_exe         = icr_exe,
        note_dur        = note_dur,
        icr_patience    = icr_patience,
        sr              = sr,
        auto_anchors    = auto_anchors,
        extend_partials = True,
    )
