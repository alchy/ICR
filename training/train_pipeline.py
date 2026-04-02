"""
training/train_pipeline.py
───────────────────────────
CLI for the ICR training pipelines.

Subcommands
───────────
  simple   Extract → filter → EQ → export soundbank (no NN)
  full     Extract → filter → EQ → train NN → finetune → export hybrid

Usage
─────
  python train_pipeline.py simple \\
      --bank  C:/SoundBanks/IthacaPlayer/ks-grand \\
      [--out  soundbanks/params-ks-grand-simple.json] \\
      [--workers 4] [--skip-eq]

  python train_pipeline.py full \\
      --bank      C:/SoundBanks/IthacaPlayer/ks-grand \\
      [--out      soundbanks/params-ks-grand-full.json] \\
      [--workers  4] \\
      [--epochs   3000] \\
      [--ft-epochs 200]

Output naming
─────────────
  If --out is omitted the output path is derived automatically:
      soundbanks/params-{bank_name}-{simple|full}.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure ICR root is on sys.path so `training.*` imports work when the
# script is invoked directly (python training/train_pipeline.py ...)
_ROOT = str(Path(__file__).parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Windows: force UTF-8 output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _default_out(bank: str, cmd: str) -> str:
    name = Path(bank).name          # e.g. "vv-rhodes"
    return f"soundbanks/params-{name}-{cmd}.json"


# ─────────────────────────────────────────────────────────────────────────────
# Argument parsers
# ─────────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="train_pipeline",
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
    simple.add_argument("--bank",     required=True, help="WAV bank directory")
    simple.add_argument("--out",      default=None,
                        help="Output soundbank JSON path "
                             "(default: soundbanks/params-{bank}-simple.json)")
    simple.add_argument("--workers",  type=int, default=None,
                        help="Parallel workers (default: CPU count)")
    simple.add_argument("--skip-eq",  action="store_true",
                        help="Skip spectral EQ step")
    simple.add_argument("--skip-outliers-detection", action="store_true",
                        help="Skip structural outlier detection step")
    simple.add_argument("--sr-tag",   default="f48",
                        help="SR suffix in filenames: f44 or f48 (default: f48, fallback f44)")

    # ── full ──────────────────────────────────────────────────────────────────
    full = sub.add_parser(
        "full",
        help="Extract → filter → EQ → train NN → finetune → export hybrid",
    )
    full.add_argument("--bank",       required=True, help="WAV bank directory")
    full.add_argument("--out",        default=None,
                      help="Output soundbank JSON path "
                           "(default: soundbanks/params-{bank}-full.json)")
    full.add_argument("--workers",    type=int, default=None,
                      help="Parallel workers (default: CPU count)")
    full.add_argument("--epochs",     type=int, default=3000,
                      help="NN training epochs (default: 3000)")
    full.add_argument("--ft-epochs",  type=int, default=200,
                      help="MRSTFT fine-tuning epochs (default: 200)")
    full.add_argument("--skip-outliers-detection", action="store_true",
                      help="Skip structural outlier detection step")
    full.add_argument("--sr-tag",     default="f48",
                      help="SR suffix in filenames: f44 or f48 (default: f48, fallback f44)")

    return root


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    args = _build_parser().parse_args()

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
        print(f"\nDone → {out}")

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
        print(f"\nDone → {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
