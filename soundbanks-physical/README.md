# Physical Modeling Soundbanks

JSON parameter banks for PhysicalModelingPianoCore.

## Schema

```json
{
  "metadata": {
    "instrument_name": "steel-string-piano",
    "version": 1,
    "sr": 48000,
    "created": "2026-04-08"
  },
  "defaults": {
    // Fallback values when a note doesn't have a specific override
  },
  "notes": {
    "m060": { ... per-note overrides ... },
    "m048": { ... },
    ...
  }
}
```

## Per-note parameters

| Key | Type | Range | Description |
|-----|------|-------|-------------|
| `f0_hz` | float | 27-4200 | Fundamental frequency |
| `B` | float | 0-0.01 | Inharmonicity (stiffness) |
| `T60_fund` | float | 0.5-20 | T60 of fundamental (seconds) |
| `T60_nyq` | float | 0.01-1.0 | T60 at Nyquist (seconds) |
| `gauge` | float | 0.5-5.0 | String thickness (1.0=normal) |
| `exc_rolloff` | float | 0-2.0 | Excitation spectral rolloff (0=flat, 2=triangle) |
| `odd_boost` | float | 1.0-3.0 | Odd harmonic emphasis |
| `knee_k` | int | 5-30 | Harmonic knee (flat below, steep above) |
| `knee_slope` | float | 1.0-5.0 | Rolloff slope above knee |
| `exc_x0` | float | 0.05-0.25 | Strike position (fraction of string) |
| `n_strings` | int | 1-3 | Number of unison strings |
| `detune_cents` | float | 0-5 | Detuning between strings (cents) |
| `n_harmonics` | int | 20-80 | Number of excitation harmonics |
| `n_disp_stages` | int | 0-16 | Dispersion allpass cascade stages |
| `disp_coeff` | float | -0.3-0 | Per-stage dispersion coefficient |
