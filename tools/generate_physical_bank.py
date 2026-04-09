"""
tools/generate_physical_bank.py
--------------------------------
Generate a JSON soundbank for PhysicalModelingPianoCore.

Per-note parameters are interpolated across the keyboard based on
the best values found during listening tests (Rounds 1-8).

Usage:
    python tools/generate_physical_bank.py
    python tools/generate_physical_bank.py --out soundbanks-physical/my-bank.json
"""

import json
import math
import os
import sys
from datetime import datetime


def lerp(a, b, t):
    """Linear interpolation: a at t=0, b at t=1."""
    return a + (b - a) * t


def generate_note_params(midi):
    """Generate physical model parameters for a single MIDI note.

    Parameters are interpolated across the keyboard based on listening
    test results. Three anchor points: bass (MIDI 36), middle (MIDI 60),
    treble (MIDI 84).
    """
    f0 = 440.0 * 2.0 ** ((midi - 69) / 12.0)
    t = (midi - 21) / 87.0  # 0..1 across keyboard

    # --- String parameters ---

    # Inharmonicity: wound bass strings are very stiff, treble less
    #   MIDI 36: B=1.5e-3 (heavy wound), MIDI 60: B=8e-4, MIDI 84: B=2e-4
    if midi <= 48:
        t_bass = (midi - 21) / 27.0  # 0..1 within bass range
        B = lerp(2e-3, 1e-3, t_bass)
    elif midi <= 72:
        t_mid = (midi - 48) / 24.0
        B = lerp(1e-3, 4e-4, t_mid)
    else:
        t_tre = (midi - 72) / 36.0
        B = lerp(4e-4, 5e-5, t_tre)

    # Gauge (string thickness): bass=3-4, middle=2, treble=1
    if midi <= 48:
        gauge = lerp(4.0, 2.5, (midi - 21) / 27.0)
    elif midi <= 72:
        gauge = lerp(2.5, 1.5, (midi - 48) / 24.0)
    else:
        gauge = lerp(1.5, 0.8, (midi - 72) / 36.0)

    # T60 fundamental: bass=12s, middle=5s, treble=1.5s
    T60_fund = lerp(12.0, 1.5, t)

    # T60 at Nyquist: controls spectral tilt
    # bass=0.3s, middle=0.3s, treble=0.2s (after gauge scaling in engine)
    T60_nyq = lerp(0.35, 0.15, t)

    # --- Excitation parameters ---

    # Strike position: 1/7 of string (slightly off 1/8 for richer spectrum)
    exc_x0 = 1.0 / 7.0

    # --- Multi-string ---

    if midi <= 27:
        n_strings = 1
    elif midi <= 48:
        n_strings = 2
    else:
        n_strings = 3

    # Detuning: bass=2 cents, treble=0.3 cents
    detune_cents = lerp(2.5, 0.3, t)

    # --- Dispersion (Teng-style) ---

    # ~4 stages per octave below 3 kHz, capped at 16
    if f0 > 3000.0:
        n_disp_stages = 0
    else:
        n_disp_stages = max(0, min(int(-math.log2(f0 / 3000.0) * 4.0), 16))
    disp_coeff = -0.30

    return {
        "midi": midi,
        "f0_hz": round(f0, 3),
        "B": round(B, 7),
        "gauge": round(gauge, 2),
        "T60_fund": round(T60_fund, 2),
        "T60_nyq": round(T60_nyq, 3),
        "exc_x0": round(exc_x0, 4),
        "n_strings": n_strings,
        "detune_cents": round(detune_cents, 2),
        "n_disp_stages": n_disp_stages,
        "disp_coeff": disp_coeff,
        "K_hardening": 1.5,
        "p_hardening": 0.3,
        "hammer_mass": 1.0,
        "string_mass": 1.0,
        "output_scale": 0.045,
        "bridge_refl": -1.0,
    }


def generate_bank(midi_from=21, midi_to=108):
    """Generate full soundbank."""
    bank = {
        "metadata": {
            "instrument_name": "steel-string-piano",
            "version": 1,
            "sr": 48000,
            "model": "PhysicalModelingPianoCore",
            "created": datetime.now().isoformat(timespec="seconds"),
            "description": "Teng-audit-2 corrected: bridge_refl=-1, disp_coeff=-0.30, no even-harmonic inject",
        },
        "notes": {}
    }

    for midi in range(midi_from, midi_to + 1):
        key = f"m{midi:03d}"
        bank["notes"][key] = generate_note_params(midi)

    return bank


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate PhysicalModelingPianoCore soundbank")
    parser.add_argument("--out", default=None, help="Output JSON path")
    parser.add_argument("--midi-from", type=int, default=21)
    parser.add_argument("--midi-to", type=int, default=108)
    args = parser.parse_args()

    bank = generate_bank(args.midi_from, args.midi_to)

    if args.out is None:
        ts = datetime.now().strftime("%m%d%H%M")
        os.makedirs("soundbanks-physical", exist_ok=True)
        out_path = f"soundbanks-physical/physical-piano-{ts}.json"
    else:
        out_path = args.out

    with open(out_path, "w") as f:
        json.dump(bank, f, indent=2)

    n = len(bank["notes"])
    size_kb = os.path.getsize(out_path) / 1024
    print(f"Generated {n} notes -> {out_path} ({size_kb:.1f} KB)")

    # Print sample params for key notes
    for midi in [36, 48, 60, 72, 84, 96]:
        p = bank["notes"][f"m{midi:03d}"]
        print(f"  MIDI {midi}: gauge={p['gauge']:.1f} B={p['B']:.1e} "
              f"T60f={p['T60_fund']:.1f}s T60n={p['T60_nyq']:.3f}s "
              f"disp={p['n_disp_stages']}×{p['disp_coeff']:.2f} "
              f"bridge={p['bridge_refl']:.1f} strings={p['n_strings']}")
