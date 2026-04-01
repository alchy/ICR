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
      --out   soundbanks/params-ks-grand.json \\
      [--workers 4] [--skip-eq]

  python train_pipeline.py full \\
      --bank      C:/SoundBanks/IthacaPlayer/ks-grand \\
      --out       soundbanks/params-ks-grand.json \\
      [--workers  4] \\
      [--epochs   1800] \\
      [--ft-epochs 200]
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
    simple.add_argument("--out",      required=True, help="Output soundbank JSON path")
    simple.add_argument("--workers",  type=int, default=None,
                        help="Parallel workers (default: CPU count)")
    simple.add_argument("--skip-eq",  action="store_true",
                        help="Skip spectral EQ step")

    # ── full ──────────────────────────────────────────────────────────────────
    full = sub.add_parser(
        "full",
        help="Extract → filter → EQ → train NN → finetune → export hybrid",
    )
    full.add_argument("--bank",       required=True, help="WAV bank directory")
    full.add_argument("--out",        required=True, help="Output soundbank JSON path")
    full.add_argument("--workers",    type=int, default=None,
                      help="Parallel workers (default: CPU count)")
    full.add_argument("--epochs",     type=int, default=1800,
                      help="NN training epochs (default: 1800)")
    full.add_argument("--ft-epochs",  type=int, default=200,
                      help="MRSTFT fine-tuning epochs (default: 200)")

    return root


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    args = _build_parser().parse_args()

    if args.cmd == "simple":
        from training.pipeline_simple import run
        out = run(
            bank_dir=args.bank,
            out_path=args.out,
            workers=args.workers,
            skip_eq=args.skip_eq,
        )
        print(f"\nDone → {out}")

    elif args.cmd == "full":
        from training.pipeline_full import run
        model, out = run(
            bank_dir=args.bank,
            out_path=args.out,
            epochs=args.epochs,
            ft_epochs=args.ft_epochs,
            workers=args.workers,
        )
        print(f"\nDone → {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
