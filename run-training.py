"""
run-training.py  —  ICR training pipeline launcher
────────────────────────────────────────────────────
Run from anywhere (repo root, IDE, double-click):

    python run-training.py simple --bank C:/SoundBanks/IthacaPlayer/vv-rhodes
    python run-training.py icr-eval --bank C:/SoundBanks/IthacaPlayer/vv-rhodes
    python run-training.py smooth-icr-eval --bank C:/SoundBanks/IthacaPlayer/vv-rhodes

Subcommands
───────────
  simple              Extract -> filter -> EQ -> export soundbank (no NN)
  icr-eval            Extract -> filter -> EQ -> train NN (raw targets, ICR early-stop)
                      -> hybrid + spline-fix NN notes + pure-NN export
  smooth-icr-eval     Extract -> filter -> EQ -> spline-smooth measured params
                      -> train NN (smooth targets, ICR early-stop)
                      -> hybrid (raw measured + NN) + pure-NN export
  smooth-ext-icr-eval Same as smooth-icr-eval + measured notes extended to max partial
                      count before training (NN trains on complete harmonic targets)
  nn                  Extract -> filter -> EQ -> train NN (shared encoders) -> export
  full                Extract -> filter -> EQ -> train NN -> MRSTFT finetune -> export hybrid
  experimental        Like nn + MRSTFTFinetuner (legacy, slow Python proxy finetuner)

Key difference between icr-eval and smooth-icr-eval:
  icr-eval        NN trains on raw extracted params; spline_fix cleans NN output post-export
  smooth-icr-eval NN trains on spline-smoothed params; NN output is final (no second spline)

Output naming (--out optional)
───────────────────────────────
  soundbanks/params-{bank_name}-icr-eval.json
  soundbanks/params-{bank_name}-smooth-icr-eval.json
  soundbanks/params-{bank_name}-smooth-ext-icr-eval.json

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
    return str(REPO_ROOT / "soundbanks" / f"params-{name}-{cmd}.json")


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

    # ── smooth-icr-eval ──────────────────────────────────────────────────────
    smi = sub.add_parser(
        "smooth-icr-eval",
        help="Extract -> EQ -> spline-smooth measured params -> train NN (ICR eval) "
             "-> hybrid (raw measured + NN) + pure-NN export",
    )
    smi.add_argument("--bank",             required=True, help="WAV bank directory")
    smi.add_argument("--out",              default=None,
                     help="Output JSON (default: soundbanks/params-{bank}-smooth-icr-eval.json)")
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

    # ── smooth-ext-icr-eval ──────────────────────────────────────────────────
    sme = sub.add_parser(
        "smooth-ext-icr-eval",
        help="Like smooth-icr-eval but measured notes are extended to max partial "
             "count before training (NN trains on complete harmonic targets)",
    )
    sme.add_argument("--bank",         required=True, help="WAV bank directory")
    sme.add_argument("--out",          default=None,
                     help="Output JSON (default: soundbanks/params-{bank}-smooth-ext-icr-eval.json)")
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

    # ── icr-eval ─────────────────────────────────────────────────────────────
    icr = sub.add_parser(
        "icr-eval",
        help="Like experimental, but ICR C++ renderer drives eval/early-stop (no finetuner)",
    )
    icr.add_argument("--bank",         required=True, help="WAV bank directory")
    icr.add_argument("--out",          default=None,
                     help="Output soundbank JSON (default: soundbanks/params-{bank}-icr-eval.json)")
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

        elif args.cmd == "smooth-icr-eval":
            out_path = args.out or _default_out(args.bank, "smooth-icr-eval")
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

        elif args.cmd == "smooth-ext-icr-eval":
            out_path = args.out or _default_out(args.bank, "smooth-ext-icr-eval")
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

        elif args.cmd == "icr-eval":
            out_path = args.out or _default_out(args.bank, "icr-eval")
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
