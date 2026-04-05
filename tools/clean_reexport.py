"""
tools/clean_reexport.py
────────────────────────
Re-export soundbank using SoundbankExporter with all code fixes applied.

Reads an existing soundbank (which has spectral_eq per note from a previous
EQFitter run), applies:
  1. Sub-fundamental EQ clamping (exporter._fit_eq_biquads)
  2. noise_centroid_hz floor at 1000 Hz (exporter._build_note)
  3. Recomputes rms_gain for all notes

Does NOT re-run extraction or LTASE (uses stored spectral_eq).

Usage:
    python tools/clean_reexport.py [input.json] [output.json]
    python tools/clean_reexport.py  # defaults: soundbanks/pl-grand.json -> soundbanks/pl-grand.json
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from training.modules.exporter import SoundbankExporter


def main():
    bank_in  = sys.argv[1] if len(sys.argv) > 1 else "soundbanks/pl-grand.json"
    bank_out = sys.argv[2] if len(sys.argv) > 2 else bank_in

    print(f"Reading:  {bank_in}")
    with open(bank_in) as f:
        old_bank = json.load(f)

    meta = old_bank["metadata"]
    sr         = meta.get("sr", 44100)
    duration   = meta.get("duration_s", 3.0)
    target_rms = meta.get("target_rms", 0.06)
    rng_seed   = meta.get("rng_seed", 0)

    print(f"sr={sr}  duration={duration}s  target_rms={target_rms}  rng_seed={rng_seed}")

    # Wrap in params-style dict so from_params() can iterate
    params = {"notes": old_bank["notes"], "metadata": meta}

    SoundbankExporter().from_params(
        params,
        bank_out,
        sr=sr,
        duration=duration,
        target_rms=target_rms,
        rng_seed=rng_seed,
    )
    print(f"Written:  {bank_out}")


if __name__ == "__main__":
    main()
