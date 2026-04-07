"""
tools/run_extraction.py
-----------------------
Run pipeline_simple extraction for a WAV bank.
Must be run as __main__ for Windows multiprocessing to work correctly.

Usage:
    python tools/run_extraction.py <bank_dir> <out_json> [workers]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def main():
    bank_dir = sys.argv[1] if len(sys.argv) > 1 else r"C:\SoundBanks\IthacaPlayer\pl-grand"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "soundbanks/pl-grand.json"
    workers  = int(sys.argv[3]) if len(sys.argv) > 3 else 6

    from training.pipeline_simple import run
    result = run(
        bank_dir=bank_dir,
        out_path=out_path,
        workers=workers,
        sr_tag="f48",
    )
    print(f"\nDone: {result}")


if __name__ == "__main__":
    main()
