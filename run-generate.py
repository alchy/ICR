"""
run-generate.py  —  ICR WAV sample generator
─────────────────────────────────────────────
Generates WAV files from a soundbank JSON or trained model (.pt).

Usage
─────
  # Celá banka (MIDI 21–108, 8 velocity vrstev)
  python run-generate.py --source soundbanks/params-vv-rhodes-simple.json --full-bank

  # Jeden tón
  python run-generate.py --source soundbanks/params-vv-rhodes-simple.json \\
      --midi-note 64 --velocity 5

  # Rozsah not
  python run-generate.py --source soundbanks/params-vv-rhodes-simple.json \\
      --midi-range 48-72 --vel-count 4

  # S parametry syntézy
  python run-generate.py --source soundbanks/params-vv-rhodes-simple.json \\
      --full-bank --freq 48 --duration 4.0 --beat-scale 1.5 --eq-strength 0.8

Output
──────
  generated/{bank_name}/m{midi:03d}_vel{vel}.wav
  Override with --out-dir. Existující soubory jsou přepsány (výchozí chování).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ── Repo root on sys.path ─────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parent.resolve()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Windows: force UTF-8 output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sr_from_freq(freq: int) -> int:
    return {44: 44_100, 48: 48_000}[freq]


def _default_out_dir(source: str) -> str:
    name = Path(source).stem          # e.g. "params-vv-rhodes-simple"
    # strip leading "params-"
    if name.startswith("params-"):
        name = name[len("params-"):]
    # strip trailing pipeline/type suffix: -simple, -full, -nn, -ft
    for suffix in ("-simple", "-full", "-nn", "-ft"):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    return str(REPO_ROOT / "generated" / name)


def _load_source(source_path: str):
    path = Path(source_path)
    if not path.exists():
        raise FileNotFoundError(f"Source not found: {source_path}")
    if path.suffix.lower() == ".pt":
        from training.modules.profile_trainer import ProfileTrainer
        print(f"Loading model: {source_path}")
        return ProfileTrainer().load(source_path)
    if path.suffix.lower() == ".json":
        print(f"Loading params: {source_path}")
        with open(source_path, encoding="utf-8") as f:
            return json.load(f)
    raise ValueError(f"Unsupported source: {path.suffix}  (expected .pt or .json)")


def _parse_midi_range(s: str) -> tuple[int, int]:
    parts = s.split("-")
    if len(parts) != 2:
        raise ValueError(f"Invalid --midi-range '{s}' (expected lo-hi, e.g. 48-72)")
    return int(parts[0]), int(parts[1])


# ── Argument parser ───────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run-generate",
        description="Generate WAV files from a soundbank or trained model",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # ── Source & output ───────────────────────────────────────────────────────
    p.add_argument("--source", required=True,
                   help="Soundbank .json or model .pt file")
    p.add_argument("--out-dir", default=None,
                   help="Output directory (default: generated/{bank_name}/)")

    # ── Scope: co generovat ───────────────────────────────────────────────────
    scope = p.add_mutually_exclusive_group()
    scope.add_argument("--full-bank", action="store_true",
                       help="Generate full bank: MIDI 21–108, all velocity layers")
    scope.add_argument("--midi-note", type=int, metavar="MIDI",
                       help="Single MIDI note (requires --velocity)")
    scope.add_argument("--midi-range", default=None, metavar="LO-HI",
                       help="MIDI range, e.g. 48-72 (used with --vel-count)")

    p.add_argument("--velocity", type=int, metavar="VEL",
                   help="Velocity index 0–7 (used with --midi-note)")
    p.add_argument("--vel-count", type=int, default=8, metavar="N",
                   help="Velocity layers to render (default: 8)")

    # ── Audio ─────────────────────────────────────────────────────────────────
    p.add_argument("--freq", type=int, choices=[44, 48], default=48,
                   help="Sample rate: 44 = 44100 Hz, 48 = 48000 Hz (default: 48)")
    p.add_argument("--duration", type=float, default=3.0,
                   help="Render duration per note in seconds (default: 3.0)")

    # ── Synthesis parameters ──────────────────────────────────────────────────
    p.add_argument("--beat-scale",  type=float, default=1.0,
                   help="Beat frequency multiplier (default: 1.0)")
    p.add_argument("--noise-level", type=float, default=1.0,
                   help="Attack noise amplitude multiplier (default: 1.0)")
    p.add_argument("--eq-strength", type=float, default=1.0,
                   help="Spectral EQ blend 0–1 (default: 1.0)")
    p.add_argument("--target-rms",  type=float, default=0.06,
                   help="RMS normalisation target (default: 0.06)")

    return p


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    args   = _build_parser().parse_args()
    sr     = _sr_from_freq(args.freq)
    source = _load_source(args.source)

    out_dir = args.out_dir or _default_out_dir(args.source)
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    synth_kw = dict(
        sr          = sr,
        duration    = args.duration,
        beat_scale  = args.beat_scale,
        noise_level = args.noise_level,
        eq_strength = args.eq_strength,
        target_rms  = args.target_rms,
    )

    from training.modules.generator import SampleGenerator
    gen = SampleGenerator()

    # ── Single note ───────────────────────────────────────────────────────────
    if args.midi_note is not None:
        if args.velocity is None:
            print("ERROR: --midi-note requires --velocity", file=sys.stderr)
            return 1
        vel = args.velocity
        print(f"Generating note: MIDI {args.midi_note}  vel {vel}  → {out_dir}")
        audio    = gen.generate_note(source, midi=args.midi_note, vel=vel, **synth_kw)
        wav_file = Path(out_dir) / f"m{args.midi_note:03d}-vel{vel}-f{sr//1000}.wav"
        gen._write_wav(wav_file, audio, sr)
        print(f"Written: {wav_file}")
        return 0

    # ── Range or full bank ────────────────────────────────────────────────────
    if args.full_bank:
        midi_range = (21, 108)
        vel_count  = args.vel_count
    elif args.midi_range:
        midi_range = _parse_midi_range(args.midi_range)
        vel_count  = args.vel_count
    else:
        # no scope flag — default to full bank
        midi_range = (21, 108)
        vel_count  = args.vel_count

    print(f"Generating bank → {out_dir}")
    print(f"  MIDI: {midi_range[0]}–{midi_range[1]}  vel layers: {vel_count}")
    print(f"  sr={sr} Hz  duration={args.duration}s  "
          f"beat_scale={args.beat_scale}  noise_level={args.noise_level}  "
          f"eq_strength={args.eq_strength}")

    gen.generate_bank(source=source, out_dir=out_dir,
                      midi_range=midi_range, vel_count=vel_count, **synth_kw)
    return 0


if __name__ == "__main__":
    sys.exit(main())
