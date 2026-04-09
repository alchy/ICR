# Physical Modeling Soundbanks

JSON parameter banks for the dual-rail waveguide piano synthesizer.

## Banks

| File | Description |
|---|---|
| `A-default.json` | Default physics (Teng-audit-2 corrected) |
| `teng-v2-default.json` | Original v2 bank (corrected) |
| `B-bright.json` ... `v12-concert-grand.json` | Timbre variants |

## Usage

```bash
# Real-time with MIDI:
build/bin/Release/icrgui.exe

# Offline batch render:
build/bin/Release/icr.exe \
    --core PhysicalModelingPianoCore \
    --params soundbanks-physical/A-default.json \
    --render-batch batch.json --out-dir output/
```

## Schema (v2)

```json
{
  "metadata": {
    "instrument_name": "steel-string-piano",
    "version": 2,
    "sr": 48000,
    "model": "teng-dual-rail-v2"
  },
  "notes": {
    "m060": {
      "midi": 60,
      "f0_hz": 261.626,
      "B": 0.0007,
      "gauge": 2.0,
      "T60_fund": 7.29,
      "T60_nyq": 0.26,
      "exc_x0": 0.1429,
      "n_strings": 3,
      "detune_cents": 1.51,
      "n_disp_stages": 14,
      "disp_coeff": -0.30,
      "K_hardening": 1.5,
      "p_hardening": 0.3,
      "hammer_mass": 1.0,
      "string_mass": 1.0,
      "output_scale": 0.045,
      "bridge_refl": -1.0
    }
  }
}
```

## Per-note parameters

| Key | Type | Range | Description |
|-----|------|-------|-------------|
| `f0_hz` | float | 27-4200 | Fundamental frequency |
| `B` | float | 0-0.01 | Inharmonicity coefficient (string stiffness) |
| `gauge` | float | 0.5-4.0 | Retained for compatibility (no DSP effect) |
| `T60_fund` | float | 1.5-12 | Decay time of fundamental (seconds, 60 dB) |
| `T60_nyq` | float | 0.15-0.35 | Decay time at Nyquist (controls spectral tilt) |
| `exc_x0` | float | 0.05-0.25 | Hammer striking position (fraction of string) |
| `n_strings` | int | 1-3 | Unison strings per note |
| `detune_cents` | float | 0.3-2.5 | Detuning between outer strings |
| `n_disp_stages` | int | 0-16 | Dispersion allpass stages (~4/octave below 3 kHz) |
| `disp_coeff` | float | -0.5-0 | Per-stage allpass coefficient (Teng: -0.30) |
| `K_hardening` | float | 0-5 | Velocity stiffness scaling |
| `p_hardening` | float | 0-1 | Velocity exponent offset |
| `hammer_mass` | float | 0.1-3 | Hammer mass scale (1.0 = Chaigne default) |
| `string_mass` | float | 0.1-3 | String mass scale (1.0 = Chaigne default) |
| `output_scale` | float | 0.01-0.5 | Per-note output gain |
| `bridge_refl` | float | -1..0 | Bridge reflection (-1.0 = rigid, default) |

## Generation

```bash
python tools/generate_physical_bank.py --out soundbanks-physical/my-bank.json
```

See [JSON_SCHEMA.md](../docs/cores/physical-modeling-piano/JSON_SCHEMA.md)
for full schema documentation.
