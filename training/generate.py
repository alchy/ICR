"""
training/generate.py
──────────────────────
CLI for generating WAV sample banks from a trained model or params JSON.

Usage
─────
  # From trained NN model
  python generate.py \\
      --source  training/profile-ks-grand.pt \\
      --out-dir generated/ks-grand/ \\
      --midi-range 21-108 \\
      --vel-count 8

  # From extracted params JSON (no NN, uses real extracted data)
  python generate.py \\
      --source  soundbanks/params-ks-grand.json \\
      --out-dir generated/ks-grand-raw/

  # With custom synthesis settings
  python generate.py \\
      --source       training/profile-ks-grand.pt \\
      --out-dir      generated/ks-grand/ \\
      --beat-scale   1.5 \\
      --noise-level  0.8 \\
      --eq-strength  1.0 \\
      --duration     3.0
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = str(Path(__file__).parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Windows: force UTF-8 output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Argument parser
# ─────────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="generate",
        description="Generate WAV sample banks from a model or params JSON",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--source",      required=True,
                   help="InstrumentProfile .pt file OR params .json file")
    p.add_argument("--out-dir",     required=True,
                   help="Output directory for WAV files")
    p.add_argument("--midi-range",  default="21-108",
                   help="MIDI range as lo-hi (default: 21-108)")
    p.add_argument("--vel-count",   type=int, default=8,
                   help="Number of velocity layers (default: 8)")
    p.add_argument("--sr",          type=int, default=44_100,
                   help="Sample rate (default: 44100)")
    p.add_argument("--duration",    type=float, default=3.0,
                   help="Render duration per note in seconds (default: 3.0)")
    p.add_argument("--beat-scale",  type=float, default=1.0,
                   help="Beat frequency multiplier (default: 1.0)")
    p.add_argument("--noise-level", type=float, default=1.0,
                   help="Attack noise amplitude multiplier (default: 1.0)")
    p.add_argument("--eq-strength", type=float, default=1.0,
                   help="Spectral EQ blend 0–1 (default: 1.0)")
    p.add_argument("--target-rms",  type=float, default=0.06,
                   help="RMS normalisation target (default: 0.06)")
    return p


# ─────────────────────────────────────────────────────────────────────────────
# Source loading
# ─────────────────────────────────────────────────────────────────────────────

def _load_source(source_path: str):
    """
    Load source from a .pt model checkpoint or a .json params file.
    Returns either an InstrumentProfile or a params dict.
    """
    path = Path(source_path)
    if not path.exists():
        raise FileNotFoundError(f"Source not found: {source_path}")

    if path.suffix.lower() == ".pt":
        from training.modules.profile_trainer import ProfileTrainer
        print(f"Loading model: {source_path}")
        return ProfileTrainer().load(source_path)

    if path.suffix.lower() == ".json":
        print(f"Loading params: {source_path}")
        with open(source_path) as f:
            return json.load(f)

    raise ValueError(f"Unsupported source format: {path.suffix} "
                     f"(expected .pt or .json)")


def _parse_midi_range(midi_range: str) -> tuple:
    """Parse '21-108' → (21, 108)."""
    parts = midi_range.split("-")
    if len(parts) != 2:
        raise ValueError(f"Invalid --midi-range: '{midi_range}' (expected lo-hi)")
    return int(parts[0]), int(parts[1])


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    args = _build_parser().parse_args()

    source     = _load_source(args.source)
    midi_range = _parse_midi_range(args.midi_range)

    print(f"Generating bank → {args.out_dir}")
    print(f"  MIDI range:  {midi_range[0]}–{midi_range[1]}")
    print(f"  vel layers:  {args.vel_count}")
    print(f"  sr:          {args.sr} Hz")
    print(f"  duration:    {args.duration}s")
    print(f"  beat_scale:  {args.beat_scale}")
    print(f"  noise_level: {args.noise_level}")
    print(f"  eq_strength: {args.eq_strength}")

    from training.modules.generator import SampleGenerator
    SampleGenerator().generate_bank(
        source       = source,
        out_dir      = args.out_dir,
        midi_range   = midi_range,
        vel_count    = args.vel_count,
        sr           = args.sr,
        duration     = args.duration,
        beat_scale   = args.beat_scale,
        noise_level  = args.noise_level,
        eq_strength  = args.eq_strength,
        target_rms   = args.target_rms,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
