# AdditiveSynthesisPianoCore

Full additive piano synthesis engine. Extracts physical parameters from WAV
recordings and resynthesizes in real-time using a 60-partial additive model with
bi-exponential envelopes, multi-string beating, velocity dynamics, spectral EQ,
and stereo imaging.

CLI: `--core AdditiveSynthesisPianoCore --params <soundbank.json>`

## Architecture

The core uses a three-layer architecture:

1. **AdditiveSynthesisPianoCore** -- top-level `ISynthCore` implementation. Owns
   `note_params_[128][8]` (8 velocity layers per MIDI note), GUI-settable atomic
   parameters, and delegates all MIDI/audio to the layers below.
2. **PianoPatchManager** -- translates MIDI noteOn velocity to native voice
   parameters. Performs velocity layer lookup with nearest-valid fallback,
   inter-layer interpolation (`lerpNoteParams`), forte normalization (rms_gain +
   spectral shape) and velocity dynamics curve. Handles sustain
   pedal with delayed noteOff.
3. **PianoVoiceManager** -- owns 128 `PianoVoice` slots (one per MIDI note).
   `initVoice()` prepares all per-voice state; `processBlock()` runs the
   sample-level DSP loop for all active voices.

Stateless DSP math lives in `additive_synthesis_piano_math.h` (namespace
`piano`), keeping the voice loop clean and the math independently testable.

## Signal Chain

```
Per-Voice:
  Partials (1-60, bi-exp envelope, 1/2/3-string model)
    |
    v
  Attack Rise Envelope (1 - exp(-t/rise_tau)) -- partials only
    |
    v
  (+) Noise (Gaussian, biquad bandpass, exp decay) -- bypasses rise env
    |
    v
  Schroeder Allpass Decorrelation (1st-order, L/R independent)
    |
    v
  Spectral EQ (up to 10 biquad sections, DF-II, -3 dB gain floor)
    |
    v
  M/S Stereo Width Correction (S *= stereo_width, M unchanged)
    |
    v
  Onset Ramp (0.5 ms) / Release Ramp (100 ms)
    |
    v
  Output (additive into bus)
```

## Velocity Model

MIDI velocity 1-127 maps to a continuous float 0.0-7.0 via `midiVelToFloat()`.
Two adjacent valid velocity layers are interpolated with `lerpNoteParams()`.

**Forte normalization:** The highest available velocity layer (typically vel=7)
is used as the reference for both `rms_gain` and spectral shape. Bank velocity
layers have inconsistent A0 ratios due to varying SNR across recordings. Forte
has the best SNR and most reliable spectral content.

- `rms_gain` is always taken from the forte reference layer.
- Spectral shape normalization: partials k>1 are adjusted so their ratio to k=1
  matches the forte layer's ratio. Only boosts -- never cuts below the current
  value.

**Velocity dynamics curve:** Since bank rms_gain normalizes layers to similar
loudness (for spectral accuracy), dynamics are re-introduced at noteOn:

```
vel_norm = midiVelToFloat(velocity) / 7.0   -- 0.0 to 1.0
vel_gain = pow(vel_norm, 1.5)               -- ~23 dB dynamic range
         = vel_norm * sqrt(vel_norm)         -- fast integer-free computation
```

`vel_gain` scales both `A0_scaled` (per-partial) and `A_noise_sc` (noise).

## String Models

String count is determined per-note from JSON `n_strings` (if present), else
from MIDI register:

| Register | MIDI range | Strings | Beat semantics |
|----------|-----------|---------|----------------|
| Bass | <= 27 | 1 | No beating |
| Tenor | 28-48 | 2 | f +/- beat_hz/2 |
| Treble | > 48 | 3 | f-beat_hz, f, f+beat_hz (symmetric) |

3-string model: outer strings use `phi` and `phi + phi_diff`; center string
uses a random phase `phi2` generated per noteOn from the voice RNG.

## Noise Model

Physics-based noise centroid and attack tau override bank values at noteOn:

```
phys_centroid = hammer_noise_centroid(midi)   -- 2000 Hz (bass) to 6000 Hz (treble)
phys_tau      = hammer_attack_tau(midi)       -- 5 ms (bass) to 1 ms (treble)
use_centroid  = max(bank_centroid, phys_centroid)   -- floor, never reduce
use_tau       = min(bank_tau, phys_tau)             -- ceiling, never lengthen
```

When centroid is raised significantly, noise amplitude is scaled down:
`noise_scale = sqrt(bank_centroid / use_centroid)` (approximately -3 dB per 2x
centroid rise, since higher-frequency noise is perceptually brighter).

Noise signal: Gaussian white noise, scaled by `A_noise * rms_gain * noise_level
* noise_scale * vel_gain`, with exponential decay `exp(-t/use_tau)`, filtered
through an RBJ bandpass biquad at `use_centroid` Hz, Q=1.5. Independent L/R
filter states.

## Spectral EQ

Up to 10 biquad sections in a Direct Form II cascade, applied per-voice to
stereo output. Coefficients are loaded from JSON `eq_biquads` (fitted from
`spectral_eq` frequency/gain curve by the Python exporter).

**Gain floor:** The EQ cascade enforces a -3 dB floor per sample. If the wet
signal would be below 0.7x the dry signal, it is clamped. This prevents
aggressive EQ from deficient source recordings from muffling the sound when
harmonic content has been corrected by forte normalization.

**eq_strength** (0-1) blends between bypass (0) and full EQ (1):
`output = dry * (1 - eq_strength) + wet * eq_strength`


## GUI Parameters

| Key | Default | Range | Category | Description |
|-----|---------|-------|----------|-------------|
| `beat_scale` | 1.0 | 0-4 | Timbre | Multiplier for all beat_hz values |
| `noise_level` | 1.0 | 0-4 | Timbre | Multiplier for noise amplitude |
| `eq_strength` | 0.5 | 0-1 | Timbre | EQ cascade dry/wet blend |
| `pan_spread` | 0.55 | 0-pi | Stereo | Angular spread between outer strings |
| `stereo_decorr` | 1.0 | 0-2 | Stereo | Schroeder allpass decorrelation amount |
| `keyboard_spread` | 0.60 | 0-pi | Stereo | L-R spread across keyboard (MIDI 21-108) |
| `rng_seed` | 0 | 0-9999 | Debug | Seed for per-voice PRNG (center string phase, noise) |

All parameters are `std::atomic<float>`, read at noteOn time (snapshot
semantics -- changing a parameter does not affect already-sounding notes).

## PianoVoice State

| State | Type | Description |
|---|---|---|
| `partials[60]` | struct | Per-partial: env_fast/slow, decay, A0_scaled, f_hz, beat_hz_h, phi |
| `noise_bpf` | BiquadCoeffs | Bandpass noise filter (centroid, Q=1.5) |
| `rise_coeff/env` | float | Attack rise envelope |
| `eq_coeffs/wL/wR` | array | EQ biquad cascade state |
| `gl1..gr3` | float | Constant-power pan gains (1/2/3 strings) |
| `ap_g_L/R, ap_x/y` | float | Schroeder allpass state |
| `stereo_width` | float | M/S correction factor |
| `eq_strength` | float | EQ blend (snapshot at noteOn) |

## Documentation

| Doc | Content |
|-----|---------|
| [JSON_SCHEMA.md](JSON_SCHEMA.md) | Soundbank JSON format (note-level + partial-level keys) |
| [TODO.md](TODO.md) | Priorities, implementation phases, known issues |
| [DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md) | Physics references, key findings, listening tests |
| [EXTRACTION_AUDIT.md](EXTRACTION_AUDIT.md) | Middle register failure analysis |

## Source Files

```
cores/additive_synthesis_piano/
    additive_synthesis_piano_core.h      Voice + VoiceManager + PatchManager + Core
    additive_synthesis_piano_core.cpp    Implementation (~995 lines)
    additive_synthesis_piano_math.h      DSP math (stateless, inline, namespace piano)
```
