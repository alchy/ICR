"""
run-extract-additive.py  —  ICR soundbank analysis pipeline
────────────────────────────────────────────────────
Analyze a WAV soundbank and generate a JSON parameter file for ICR playback.

Usage:
    python run-extract-additive.py analyze --bank C:/SoundBanks/IthacaPlayer/pl-grand
    python run-extract-additive.py analyze --bank C:/SoundBanks/IthacaPlayer/pl-grand --out soundbanks-additive/my-piano.json
    python run-extract-additive.py analyze --bank C:/SoundBanks/IthacaPlayer/pl-grand --skip-eq --workers 8

Pipeline:  Extract partials → filter outliers → fit spectral EQ → export JSON

All console output is also written to:
    training-logs/run-analyze-{bank_name}-{timestamp}.log
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

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _start_tee(cmd: str, bank: str) -> _Tee:
    bank_name = Path(bank).name
    ts        = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path  = REPO_ROOT / "training-logs" / f"run-{cmd}-{bank_name}-{ts}.log"
    tee = _Tee(sys.stdout, log_path)
    sys.stdout = tee
    sys.stderr = tee
    print(f"Logging to: {log_path}")
    return tee


# ── Argument parser ───────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="run-extract-additive",
        description="ICR soundbank analysis pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = root.add_subparsers(dest="cmd", required=True)

    # ── analyze ──────────────────────────────────────────────────────────────
    analyze = sub.add_parser(
        "analyze",
        help="Extract → filter outliers → fit EQ → export soundbank JSON",
    )
    analyze.add_argument("--bank",    required=True,
                         help="WAV bank directory (e.g. C:/SoundBanks/IthacaPlayer/pl-grand)")
    analyze.add_argument("--out",     default=None,
                         help="Output JSON path (default: soundbanks-additive/{bank_name}.json)")
    analyze.add_argument("--workers", type=int, default=None,
                         help="Parallel workers (default: CPU count)")
    analyze.add_argument("--skip-eq", action="store_true",
                         help="Skip spectral EQ fitting (faster, no body resonance)")
    analyze.add_argument("--skip-outliers", action="store_true",
                         help="Skip structural outlier detection")
    analyze.add_argument("--sr-tag",  default="f48",
                         help="SR suffix in filenames: f44 or f48 (default: f48)")
    analyze.add_argument("--skip-ir", action="store_true",
                         help="Skip soundboard IR extraction")
    analyze.add_argument("--skip-physics-floor", action="store_true",
                         help="Skip physics floor correction (raw extraction only)")

    return root


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    args = _build_parser().parse_args()
    tee  = _start_tee(args.cmd, args.bank)

    try:
        bank_name = Path(args.bank).name
        ts        = datetime.now().strftime("%m%d%H%M")
        out_path  = args.out or str(REPO_ROOT / "soundbanks-additive" / f"{bank_name}-{ts}.json")

        print(f"Bank:   {args.bank}")
        print(f"Output: {out_path}")
        print(f"SR tag: {args.sr_tag}")
        print()

        from training.pipeline_simple import run
        out = run(
            bank_dir=args.bank,
            out_path=out_path,
            workers=args.workers,
            skip_eq=args.skip_eq,
            skip_outliers=args.skip_outliers,
            sr_tag=args.sr_tag,
            skip_physics_floor=args.skip_physics_floor,
        )
        print(f"\nSoundbank -> {out}")

        # Extract soundboard IR (convolution profile)
        if not args.skip_ir:
            ir_path = out_path.replace(".json", "-soundboard.wav")
            print(f"\nExtracting soundboard IR...")
            try:
                from tools.extract_soundboard_ir import extract_transfer_function
                import json as _json
                import numpy as _np
                import soundfile as _sf

                _bank = _json.load(open(out_path))
                from tools.extract_soundboard_ir import main as _ir_main
                import sys as _sys
                _orig_argv = _sys.argv
                _sys.argv = ["extract_soundboard_ir", out_path,
                             "--bank", args.bank, "--out", ir_path,
                             "--sr-tag", args.sr_tag]
                _ir_main()
                _sys.argv = _orig_argv
                print(f"Soundboard IR -> {ir_path}")
            except Exception as e:
                print(f"Soundboard IR extraction failed: {e}")
                ir_path = None
        else:
            ir_path = None

        print(f"\n{'='*60}")
        print(f"Soundbank:     {out}")
        if ir_path:
            print(f"Soundboard IR: {ir_path}")
        print(f"\nRun with:")
        ir_arg = f' --ir {ir_path}' if ir_path else ''
        print(f"  icrgui.exe --core AdditiveSynthesisPianoCore --params {out}{ir_arg}")

    finally:
        tee.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
