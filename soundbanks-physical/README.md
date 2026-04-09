# Physical Modeling Soundbanks

JSON parameter banks for the dual-rail waveguide piano synthesizer
(`tools-physical/generate_teng_v2.py`).

## Banks

| File | Description |
|---|---|
| `teng-v2-default.json` | Physics-based defaults for Chaigne hammer + dual-rail |

## Usage

```bash
# With bank
python tools-physical/generate_teng_v2.py \
    --bank soundbanks-physical/teng-v2-default.json \
    --midi 60 64 72 --vel 0.3 0.6 0.9

# Without bank (same physics defaults, built-in)
python tools-physical/generate_teng_v2.py \
    --midi 60 64 72 --vel 0.3 0.6 0.9
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
      "gauge": 1.6,
      "T60_fund": 7.3,
      "T60_nyq": 0.233,
      "exc_x0": 0.1429,
      "n_strings": 3,
      "detune_cents": 1.5,
      "n_disp_stages": 11,
      "disp_coeff": -0.15
    }
  }
}
```

## Per-note parameters

| Key | Type | Range | Description |
|-----|------|-------|-------------|
| `f0_hz` | float | 27-4200 | Fundamental frequency |
| `B` | float | 0-0.01 | Inharmonicity coefficient (string stiffness) |
| `gauge` | float | 0.5-3.0 | String thickness (HF damping multiplier) |
| `T60_fund` | float | 1.5-12 | Decay time of fundamental (seconds, 60 dB) |
| `T60_nyq` | float | 0.15-0.30 | Decay time at Nyquist (before gauge scaling) |
| `exc_x0` | float | 0.05-0.25 | Hammer striking position (fraction of string) |
| `n_strings` | int | 1-3 | Unison strings per note |
| `detune_cents` | float | 0.3-2.5 | Detuning between outer strings |
| `n_disp_stages` | int | 0-16 | Dispersion allpass stages (0 = harmonic) |
| `disp_coeff` | float | -0.3-0 | Per-stage allpass coefficient |

Note: v1 single-rail params (`exc_rolloff`, `odd_boost`, `knee_k`, `knee_slope`,
`n_harmonics`) are not used by the dual-rail renderer and are not in v2 banks.
