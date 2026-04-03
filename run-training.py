"""
run-training.py  —  ICR training pipeline launcher
────────────────────────────────────────────────────
Run from anywhere (repo root, IDE, double-click):

    python run-training.py simple --bank C:/SoundBanks/IthacaPlayer/vv-rhodes
    python run-training.py raw-nn-icreval --bank C:/SoundBanks/IthacaPlayer/vv-rhodes
    python run-training.py spl-nn-icreval --bank C:/SoundBanks/IthacaPlayer/vv-rhodes

Naming convention: <target-prep>-nn-<icr-role>
  target-prep:  raw | spl (spline) | spl-ext (spline+extend) | spl-icrtarget (spline+round-trip)
  icr-role:     icreval (ICR drives early-stop only)
                icrtarget (ICR generates training targets via round-trip)

Active workflows
────────────────
  raw-nn-icreval          Extract -> EQ -> train NN (raw targets) -> ICR early-stop
                          -> hybrid + spline-fix NN notes + pure-NN export
  spl-nn-icreval          Extract -> EQ -> spline-smooth -> train NN (smooth targets)
                          -> ICR early-stop -> hybrid (raw measured + NN) + pure-NN export
  spl-ext-nn-icreval      Same as spl-nn-icreval + extend measured notes to max partial count
                          before training (NN trains on complete harmonic targets)
  spl-icrtarget-nn-icreval      spl-nn-icreval + ICR round-trip: smooth params → ICR render
                                → re-extract → training targets (NN converges to what ICR
                                actually produces, not what extractor measured from real piano)
  spl-ext-icrtarget-nn-icreval  spl-ext-nn-icreval + ICR round-trip: extend partials +
                                spline-smooth → ICR render → re-extract → training targets

Legacy workflows
────────────────
  simple                  Extract -> filter -> EQ -> export (no NN)
  nn                      Extract -> filter -> EQ -> NN (shared encoders) -> export
  full                    Extract -> filter -> EQ -> NN -> MRSTFT finetune -> export hybrid
  experimental            Like nn + MRSTFTFinetuner (legacy, slow Python proxy finetuner)

Output naming (--out sets hybrid path; nn path derived automatically)
───────────────────────────────────────────────────────────────────
  soundbanks/{bank_name}-raw-nn-icreval-hybrid.json      ← raw measured + NN interpolated
  soundbanks/{bank_name}-raw-nn-icreval-nn.json          ← pure NN (all 704 notes)
  soundbanks/{bank_name}-spl-nn-icreval-hybrid.json
  soundbanks/{bank_name}-spl-nn-icreval-nn.json
  soundbanks/{bank_name}-spl-ext-nn-icreval-hybrid.json
  soundbanks/{bank_name}-spl-ext-nn-icreval-nn.json
  soundbanks/{bank_name}-spl-icrtarget-nn-icreval-hybrid.json
  soundbanks/{bank_name}-spl-icrtarget-nn-icreval-nn.json
  soundbanks/{bank_name}-spl-ext-icrtarget-nn-icreval-hybrid.json
  soundbanks/{bank_name}-spl-ext-icrtarget-nn-icreval-nn.json

All console output is also written to:
  training-logs/run-{cmd}-{bank_name}-{timestamp}.log
"""

from __future__ import annotations

import argparse
import sys
import os
from datetime import datetime
from pathlib import Path

# ── Repo root on sys.path ─────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parent.resolve()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Windows: force UTF-8 output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ── Tee logging ───────────────────────────────────────────────────────────────

class _Tee:
    """Write to both the original stream and a log file."""

    def __init__(self, stream, log_path: Path):
        self._stream = stream
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(log_path, "w", encoding="utf-8", buffering=1)

    def write(self, data):
        self._stream.write(data)
        self._file.write(data)

    def flush(self):
        self._stream.flush()
        self._file.flush()

    def close(self):
        self._file.close()

    # Proxy everything else (isatty, fileno, …) to the original stream
    def __getattr__(self, name):
        return getattr(self._stream, name)


def _start_tee(cmd: str, bank: str) -> _Tee | None:
    bank_name = Path(bank).name
    ts        = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path  = REPO_ROOT / "training-logs" / f"run-{cmd}-{bank_name}-{ts}.log"
    tee = _Tee(sys.stdout, log_path)
    sys.stdout = tee
    sys.stderr = tee
    print(f"Logging to: {log_path}")
    return tee


# ── Helpers ───────────────────────────────────────────────────────────────────

def _default_out(bank: str, cmd: str) -> str:
    name = Path(bank).name
    return str(REPO_ROOT / "soundbanks" / f"{name}-{cmd}-hybrid.json")


# ── Argument parser ───────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="run-training",
        description="IthacaCoreResonator training pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = root.add_subparsers(dest="cmd", required=True)

    # ── simple ────────────────────────────────────────────────────────────────
    simple = sub.add_parser(
        "simple",
        help="Extract → filter → EQ → export (no NN training)",
    )
    simple.add_argument("--bank",    required=True, help="WAV bank directory")
    simple.add_argument("--out",     default=None,
                        help="Output soundbank JSON (default: soundbanks/params-{bank}-simple.json)")
    simple.add_argument("--workers", type=int, default=None,
                        help="Parallel workers (default: CPU count)")
    simple.add_argument("--skip-eq", action="store_true",
                        help="Skip spectral EQ step")
    simple.add_argument("--skip-outliers-detection", action="store_true",
                        help="Skip structural outlier detection step")
    simple.add_argument("--sr-tag",  default="f48",
                        help="SR suffix in filenames: f44 or f48 (default: f48)")

    # ── full ──────────────────────────────────────────────────────────────────
    full = sub.add_parser(
        "full",
        help="Extract → filter → EQ → train NN → finetune → export hybrid",
    )
    full.add_argument("--bank",      required=True, help="WAV bank directory")
    full.add_argument("--out",       default=None,
                      help="Output soundbank JSON (default: soundbanks/params-{bank}-full.json)")
    full.add_argument("--workers",   type=int, default=None,
                      help="Parallel workers (default: CPU count)")
    full.add_argument("--epochs",    type=int, default=3000,
                      help="NN training epochs (default: 3000)")
    full.add_argument("--ft-epochs", type=int, default=200,
                      help="MRSTFT fine-tuning epochs (default: 200)")
    full.add_argument("--skip-outliers-detection", action="store_true",
                      help="Skip structural outlier detection step")
    full.add_argument("--sr-tag",    default="f48",
                      help="SR suffix in filenames: f44 or f48 (default: f48)")

    # ── nn ────────────────────────────────────────────────────────────────────
    nn = sub.add_parser(
        "nn",
        help="Extract -> EQ -> NN (shared encoders, vel on all nets) -> export (no finetuner)",
    )
    nn.add_argument("--bank",      required=True, help="WAV bank directory")
    nn.add_argument("--out",       default=None,
                    help="Output soundbank JSON (default: soundbanks/params-{bank}-nn.json)")
    nn.add_argument("--workers",   type=int, default=None,
                    help="Parallel workers (default: CPU count)")
    nn.add_argument("--epochs",    type=int, default=10000,
                    help="NN training epochs (default: 10000)")
    nn.add_argument("--skip-outliers-detection", action="store_true",
                    help="Skip structural outlier detection step")
    nn.add_argument("--sr-tag",    default="f48",
                    help="SR suffix in filenames: f44 or f48 (default: f48)")

    # ── experimental (legacy: nn + MRSTFTFinetuner) ───────────────────────────
    exp = sub.add_parser(
        "experimental",
        help="Like nn, but followed by MRSTFTFinetuner (Python proxy MRSTFT, slow)",
    )
    exp.add_argument("--bank",      required=True, help="WAV bank directory")
    exp.add_argument("--out",       default=None,
                     help="Output soundbank JSON (default: soundbanks/params-{bank}-experimental.json)")
    exp.add_argument("--workers",   type=int, default=None,
                     help="Parallel workers (default: CPU count)")
    exp.add_argument("--epochs",    type=int, default=10000,
                     help="NN training epochs (default: 10000)")
    exp.add_argument("--ft-epochs", type=int, default=200,
                     help="MRSTFT fine-tuning epochs (default: 200)")
    exp.add_argument("--skip-outliers-detection", action="store_true",
                     help="Skip structural outlier detection step")
    exp.add_argument("--sr-tag",    default="f48",
                     help="SR suffix in filenames: f44 or f48 (default: f48)")

    # ── spl-nn-icreval ───────────────────────────────────────────────────────
    smi = sub.add_parser(
        "spl-nn-icreval",
        help="spline-smooth measured params -> train NN (smooth targets, ICR early-stop) "
             "-> hybrid (raw measured + NN) + pure-NN export",
    )
    smi.add_argument("--bank",             required=True, help="WAV bank directory")
    smi.add_argument("--out",              default=None,
                     help="Output JSON (default: soundbanks/params-{bank}-spl-nn-icreval.json)")
    smi.add_argument("--workers",          type=int, default=None,
                     help="Parallel workers (default: CPU count)")
    smi.add_argument("--epochs",           type=int, default=5000,
                     help="Max NN epochs (default: 5000, early stop may exit sooner)")
    smi.add_argument("--icr-exe",          default="build/bin/Release/ICR.exe",
                     help="Path to ICR.exe (default: build/bin/Release/ICR.exe)")
    smi.add_argument("--note-dur",         type=float, default=3.0,
                     help="ICR render duration per note in seconds (default: 3.0)")
    smi.add_argument("--icr-patience",     type=int, default=15,
                     help="Early stop after N evals without improvement (default: 15)")
    smi.add_argument("--auto-anchors",     type=int, default=12,
                     help="Auto-anchor count for spline smoothing (default: 12)")
    smi.add_argument("--extend-partials",  action="store_true",
                     help="Extend measured notes to max partial count before training "
                          "(NN trains on complete harmonic targets)")
    smi.add_argument("--skip-outliers-detection", action="store_true",
                     help="Skip structural outlier detection step")
    smi.add_argument("--sr-tag",           default="f48",
                     help="SR suffix in filenames: f44 or f48 (default: f48)")

    # ── spl-icrtarget-nn-icreval ─────────────────────────────────────────────
    srt = sub.add_parser(
        "spl-icrtarget-nn-icreval",
        help="spl-nn-icreval + ICR round-trip: smooth params → ICR render → "
             "re-extract → training targets (NN learns ICR's actual transfer function)",
    )
    srt.add_argument("--bank",         required=True, help="WAV bank directory")
    srt.add_argument("--out",          default=None,
                     help="Output JSON (default: soundbanks/params-{bank}-spl-icrtarget-nn-icreval.json)")
    srt.add_argument("--workers",      type=int, default=None,
                     help="Parallel workers (default: CPU count)")
    srt.add_argument("--epochs",       type=int, default=5000,
                     help="Max NN epochs (default: 5000, early stop may exit sooner)")
    srt.add_argument("--icr-exe",      default="build/bin/Release/ICR.exe",
                     help="Path to ICR.exe (default: build/bin/Release/ICR.exe)")
    srt.add_argument("--note-dur",     type=float, default=3.0,
                     help="ICR render duration per note in seconds (default: 3.0)")
    srt.add_argument("--icr-patience", type=int, default=15,
                     help="Early stop after N evals without improvement (default: 15)")
    srt.add_argument("--auto-anchors", type=int, default=12,
                     help="Auto-anchor count for spline smoothing (default: 12)")
    srt.add_argument("--skip-outliers-detection", action="store_true",
                     help="Skip structural outlier detection step")
    srt.add_argument("--sr-tag",       default="f48",
                     help="SR suffix in filenames: f44 or f48 (default: f48)")

    # ── spl-ext-nn-icreval ───────────────────────────────────────────────────
    sme = sub.add_parser(
        "spl-ext-nn-icreval",
        help="Like spl-nn-icreval but measured notes are extended to max partial "
             "count before training (NN trains on complete harmonic targets)",
    )
    sme.add_argument("--bank",         required=True, help="WAV bank directory")
    sme.add_argument("--out",          default=None,
                     help="Output JSON (default: soundbanks/params-{bank}-spl-ext-nn-icreval.json)")
    sme.add_argument("--workers",      type=int, default=None,
                     help="Parallel workers (default: CPU count)")
    sme.add_argument("--epochs",       type=int, default=5000,
                     help="Max NN epochs (default: 5000, early stop may exit sooner)")
    sme.add_argument("--icr-exe",      default="build/bin/Release/ICR.exe",
                     help="Path to ICR.exe (default: build/bin/Release/ICR.exe)")
    sme.add_argument("--note-dur",     type=float, default=3.0,
                     help="ICR render duration per note in seconds (default: 3.0)")
    sme.add_argument("--icr-patience", type=int, default=15,
                     help="Early stop after N evals without improvement (default: 15)")
    sme.add_argument("--auto-anchors", type=int, default=12,
                     help="Auto-anchor count for spline smoothing (default: 12)")
    sme.add_argument("--skip-outliers-detection", action="store_true",
                     help="Skip structural outlier detection step")
    sme.add_argument("--sr-tag",       default="f48",
                     help="SR suffix in filenames: f44 or f48 (default: f48)")

    # ── spl-ext-icrtarget-nn-icreval ────────────────────────────────────────
    sxrt = sub.add_parser(
        "spl-ext-icrtarget-nn-icreval",
        help="spl-ext-nn-icreval + ICR round-trip: extend partials + spline-smooth "
             "→ ICR render → re-extract → training targets",
    )
    sxrt.add_argument("--bank",         required=True, help="WAV bank directory")
    sxrt.add_argument("--out",          default=None,
                      help="Output JSON (default: soundbanks/params-{bank}-spl-ext-icrtarget-nn-icreval.json)")
    sxrt.add_argument("--workers",      type=int, default=None,
                      help="Parallel workers (default: CPU count)")
    sxrt.add_argument("--epochs",       type=int, default=5000,
                      help="Max NN epochs (default: 5000, early stop may exit sooner)")
    sxrt.add_argument("--icr-exe",      default="build/bin/Release/ICR.exe",
                      help="Path to ICR.exe (default: build/bin/Release/ICR.exe)")
    sxrt.add_argument("--note-dur",     type=float, default=3.0,
                      help="ICR render duration per note in seconds (default: 3.0)")
    sxrt.add_argument("--icr-patience", type=int, default=15,
                      help="Early stop after N evals without improvement (default: 15)")
    sxrt.add_argument("--auto-anchors", type=int, default=12,
                      help="Auto-anchor count for spline smoothing (default: 12)")
    sxrt.add_argument("--skip-outliers-detection", action="store_true",
                      help="Skip structural outlier detection step")
    sxrt.add_argument("--sr-tag",       default="f48",
                      help="SR suffix in filenames: f44 or f48 (default: f48)")

    # ── raw-nn-icreval ───────────────────────────────────────────────────────
    icr = sub.add_parser(
        "raw-nn-icreval",
        help="train NN on raw extracted params, ICR C++ renderer drives early-stop "
             "(no finetuner); spline_fix cleans NN output post-export",
    )
    icr.add_argument("--bank",         required=True, help="WAV bank directory")
    icr.add_argument("--out",          default=None,
                     help="Output soundbank JSON (default: soundbanks/params-{bank}-raw-nn-icreval.json)")
    icr.add_argument("--workers",      type=int, default=None,
                     help="Parallel workers (default: CPU count)")
    icr.add_argument("--epochs",       type=int, default=5000,
                     help="Max NN training epochs (default: 5000, early stop may exit sooner)")
    icr.add_argument("--icr-exe",      default="build/bin/Release/ICR.exe",
                     help="Path to ICR.exe (default: build/bin/Release/ICR.exe)")
    icr.add_argument("--note-dur",     type=float, default=3.0,
                     help="ICR render duration per note in seconds (default: 3.0)")
    icr.add_argument("--icr-patience", type=int, default=15,
                     help="Early stop after N evals without ICR-MRSTFT improvement (default: 15)")
    icr.add_argument("--skip-outliers-detection", action="store_true",
                     help="Skip structural outlier detection step")
    icr.add_argument("--sr-tag",       default="f48",
                     help="SR suffix in filenames: f44 or f48 (default: f48)")

    return root


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    args = _build_parser().parse_args()
    tee  = _start_tee(args.cmd, args.bank)

    try:
        if args.cmd == "simple":
            out_path = args.out or _default_out(args.bank, "simple")
            from training.pipeline_simple import run
            out = run(
                bank_dir=args.bank,
                out_path=out_path,
                workers=args.workers,
                skip_eq=args.skip_eq,
                skip_outliers=args.skip_outliers_detection,
                sr_tag=args.sr_tag,
            )
            print(f"\nDone -> {out}")

        elif args.cmd == "full":
            out_path = args.out or _default_out(args.bank, "full")
            from training.pipeline_full import run
            model, out = run(
                bank_dir=args.bank,
                out_path=out_path,
                epochs=args.epochs,
                ft_epochs=args.ft_epochs,
                workers=args.workers,
                skip_outliers=args.skip_outliers_detection,
                sr_tag=args.sr_tag,
            )
            print(f"\nDone -> {out}")

        elif args.cmd == "nn":
            out_path = args.out or _default_out(args.bank, "nn")
            from training.pipeline_nn import run
            model, out = run(
                bank_dir=args.bank,
                out_path=out_path,
                epochs=args.epochs,
                workers=args.workers,
                skip_outliers=args.skip_outliers_detection,
                sr_tag=args.sr_tag,
            )
            print(f"\nDone -> {out}")

        elif args.cmd == "experimental":
            out_path = args.out or _default_out(args.bank, "experimental")
            from training.pipeline_experimental import run
            model, out = run(
                bank_dir=args.bank,
                out_path=out_path,
                epochs=args.epochs,
                ft_epochs=args.ft_epochs,
                workers=args.workers,
                skip_outliers=args.skip_outliers_detection,
                sr_tag=args.sr_tag,
            )
            print(f"\nDone -> {out}")

        elif args.cmd == "spl-nn-icreval":
            out_path = args.out or _default_out(args.bank, "spl-nn-icreval")
            from training.pipeline_smooth_icr_eval import run
            model, out = run(
                bank_dir        = args.bank,
                out_path        = out_path,
                epochs          = args.epochs,
                workers         = args.workers,
                skip_outliers   = args.skip_outliers_detection,
                sr_tag          = args.sr_tag,
                icr_exe         = args.icr_exe,
                note_dur        = args.note_dur,
                icr_patience    = args.icr_patience,
                auto_anchors    = args.auto_anchors,
                extend_partials = args.extend_partials,
            )
            print(f"\nDone -> {out}")

        elif args.cmd == "spl-icrtarget-nn-icreval":
            out_path = args.out or _default_out(args.bank, "spl-icrtarget-nn-icreval")
            from training.pipeline_smooth_icr_eval import run
            model, out = run(
                bank_dir        = args.bank,
                out_path        = out_path,
                epochs          = args.epochs,
                workers         = args.workers,
                skip_outliers   = args.skip_outliers_detection,
                sr_tag          = args.sr_tag,
                icr_exe         = args.icr_exe,
                note_dur        = args.note_dur,
                icr_patience    = args.icr_patience,
                auto_anchors    = args.auto_anchors,
                icr_round_trip  = True,
            )
            print(f"\nDone -> {out}")

        elif args.cmd == "spl-ext-nn-icreval":
            out_path = args.out or _default_out(args.bank, "spl-ext-nn-icreval")
            from training.pipeline_smooth_icr_eval import run
            model, out = run(
                bank_dir        = args.bank,
                out_path        = out_path,
                epochs          = args.epochs,
                workers         = args.workers,
                skip_outliers   = args.skip_outliers_detection,
                sr_tag          = args.sr_tag,
                icr_exe         = args.icr_exe,
                note_dur        = args.note_dur,
                icr_patience    = args.icr_patience,
                auto_anchors    = args.auto_anchors,
                extend_partials = True,
            )
            print(f"\nDone -> {out}")

        elif args.cmd == "spl-ext-icrtarget-nn-icreval":
            out_path = args.out or _default_out(args.bank, "spl-ext-icrtarget-nn-icreval")
            from training.pipeline_smooth_icr_eval import run
            model, out = run(
                bank_dir        = args.bank,
                out_path        = out_path,
                epochs          = args.epochs,
                workers         = args.workers,
                skip_outliers   = args.skip_outliers_detection,
                sr_tag          = args.sr_tag,
                icr_exe         = args.icr_exe,
                note_dur        = args.note_dur,
                icr_patience    = args.icr_patience,
                auto_anchors    = args.auto_anchors,
                extend_partials = True,
                icr_round_trip  = True,
            )
            print(f"\nDone -> {out}")

        elif args.cmd == "raw-nn-icreval":
            out_path = args.out or _default_out(args.bank, "raw-nn-icreval")
            from training.pipeline_icr_eval import run
            model, out = run(
                bank_dir      = args.bank,
                out_path      = out_path,
                epochs        = args.epochs,
                workers       = args.workers,
                skip_outliers = args.skip_outliers_detection,
                sr_tag        = args.sr_tag,
                icr_exe       = args.icr_exe,
                note_dur      = args.note_dur,
                icr_patience  = args.icr_patience,
            )
            print(f"\nDone -> {out}")

    finally:
        tee.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
