"""
train-additive.py — Clean additive synthesis pipeline (v2)
──────────────────────────────────────────────────────────
Analyze WAV soundbank → extract partials → export JSON for C++ core.

Workflow:
    1. Analyze WAV files (FFT, peak tracking, envelope fitting)
    2. Optional: fit spectral EQ (soundboard body)
    3. Optional: extract soundboard IR (for convolver)
    4. Export JSON bank for AdditiveSynthesisPianoCore

Usage:
    # Basic (relaxed extraction, trust the measurements)
    python train-additive.py analyze --bank C:/SoundBanks/pl-grand

    # With strict v1 constraints (backward compatible)
    python train-additive.py analyze --bank C:/SoundBanks/pl-grand --strict

    # Skip optional steps
    python train-additive.py analyze --bank C:/SoundBanks/pl-grand --skip-eq --skip-ir

    # Custom output
    python train-additive.py analyze --bank C:/SoundBanks/pl-grand --out my-bank.json

Output: soundbanks-additive/{bank_name}-{timestamp}.json
Log:    training-logs/train-additive-{bank_name}-{timestamp}.log
"""

from __future__ import annotations

import argparse
import sys
import os
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).parent.resolve()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


class _Tee:
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

    def __getattr__(self, name):
        return getattr(self._stream, name)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="train-additive",
        description="Additive synthesis extraction pipeline (v2)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    analyze = sub.add_parser("analyze", help="Extract partials → export JSON bank")
    analyze.add_argument("--bank", required=True,
                         help="WAV bank directory")
    analyze.add_argument("--out", default=None,
                         help="Output JSON path")
    analyze.add_argument("--workers", type=int, default=None,
                         help="Parallel workers (default: CPU count)")
    analyze.add_argument("--skip-eq", action="store_true",
                         help="Skip spectral EQ fitting")
    analyze.add_argument("--skip-ir", action="store_true",
                         help="Skip soundboard IR extraction")
    analyze.add_argument("--sr-tag", default="f48",
                         help="SR suffix in filenames (default: f48)")
    analyze.add_argument("--strict", action="store_true",
                         help="Use v1 strict constraints (default: relaxed v2)")

    args = parser.parse_args()

    bank_name = Path(args.bank).name
    ts = datetime.now().strftime("%m%d%H%M")
    mode_suffix = "strict" if args.strict else "relaxed"
    out_path = args.out or str(
        REPO_ROOT / "soundbanks-additive" / f"{bank_name}-{ts}-{mode_suffix}.json")

    log_path = REPO_ROOT / "training-logs" / f"train-additive-{bank_name}-{ts}.log"
    tee = _Tee(sys.stdout, log_path)
    sys.stdout = tee
    sys.stderr = tee

    try:
        from training_additive.extraction_config import STRICT, RELAXED
        cfg = STRICT if args.strict else RELAXED

        print(f"Pipeline:  train-additive v2")
        print(f"Mode:      {'STRICT (v1 compat)' if args.strict else 'RELAXED (v2)'}")
        print(f"Bank:      {args.bank}")
        print(f"Output:    {out_path}")
        print(f"SR tag:    {args.sr_tag}")
        print(f"Config:    tau1_floor={cfg.tau1_floor}, "
              f"damping_law={'ON' if cfg.damping_law_enabled else 'OFF'}, "
              f"physics_floor={'ON' if cfg.physics_floor_enabled else 'OFF'}")
        print()

        from training_additive.pipeline_v2 import run
        result = run(
            bank_dir=args.bank,
            out_path=out_path,
            workers=args.workers,
            skip_eq=args.skip_eq,
            skip_ir=args.skip_ir,
            sr_tag=args.sr_tag,
            config=cfg,
        )

        print(f"\n{'='*60}")
        print(f"Soundbank:     {result['bank_path']}")
        for ir in result.get("ir_paths", []):
            print(f"Soundboard IR: {ir}")
        print(f"\nRun with:")
        ir_arg = f" --ir {result['ir_paths'][0]}" if result.get("ir_paths") else ""
        print(f"  icrgui --core AdditiveSynthesisPianoCore --params {result['bank_path']}{ir_arg}")

    finally:
        tee.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
