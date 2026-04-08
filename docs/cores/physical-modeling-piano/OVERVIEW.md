# PhysicalModelingPianoCore

Digital waveguide piano synthesis engine (v0.1).  Models the physical energy
flow from hammer through string to soundboard, producing piano sound from
first principles rather than analysis-resynthesis.

CLI: `--core PhysicalModelingPianoCore` (works without `--params` using physics defaults)

## Synthesis Approach

```
Hammer --> String waveguide --> Bridge junction --> Soundboard --> Air
    ^           | (delay + loss + dispersion)          |
    +-----------+ (reflection back into string)        +--> Stereo output
```

- **Hammer**: Nonlinear felt model `F = K_H * max(0, xi-u)^p` (Chabassier)
- **String**: Two delay lines (right/left travelling waves) with allpass fractional delay
- **Loop filter**: One-pole frequency-dependent damping (models `R + eta*f^2`)
- **Dispersion**: Second-order allpass for inharmonicity (`f_k = k*f0*sqrt(1+B*k^2)`)
- **Bridge junction**: Kirchhoff scattering (`k_r` reflection + `k_t` transmission)
- **Soundboard**: 24 resonant modes (60 Hz - 8 kHz)
- **Hammer noise**: Bandpass-filtered Gaussian burst at attack (1.5-5 kHz centroid)
- **Multi-string**: 1/2/3 strings per note, independently detuned

## GUI Parameters

| Parameter | Group | Range | Description |
|-----------|-------|-------|-------------|
| `hammer_hardness` | Hammer | 0.1-4.0 | Scales K_H (brighter attack) |
| `brightness` | Timbre | 0.1-4.0 | Scales high-frequency decay (more = brighter) |
| `damping_scale` | Timbre | 0.1-4.0 | Scales all decay times |
| `soundboard_mix` | Timbre | 0.0-2.0 | Soundboard mode contribution |
| `detune_scale` | Strings | 0.0-4.0 | Scales inter-string detuning |
| `keyboard_spread` | Stereo | 0.0-pi | L-R spread across keyboard |

## Physics Defaults (Chabassier et al. 2012)

All parameters have MIDI-dependent defaults derived from Steinway D measurements:

| Parameter | MIDI 21 (A0) | MIDI 60 (C4) | MIDI 108 (C8) |
|-----------|-------------|-------------|--------------|
| K_H | 4e8 | 6.9e9 | 2.3e11 |
| p | 2.27 | 2.60 | 3.0 |
| M_H | 12 g | 9.6 g | 6.77 g |
| n_strings | 1 | 3 | 3 |
| tau_fund | 20 s | 11 s | 1 s |
| tau_high | 2 s | 1.2 s | 0.2 s |

## Current Status (v0.1)

Working but sound quality needs improvement:
- Attack has punch (hammer noise), but timbre is synthetic
- Decay envelope broadly correct but lacks bi-exponential character nuance
- Soundboard coloring present but coarse (24 modes vs 100+ real)
- No sympathetic resonance, no phantom/longitudinal partials

See [TODO.md](TODO.md) and [DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md) for roadmap.

## Source Files

```
cores/physical_modeling_piano/
    physical_modeling_piano_core.h      Voice + VoiceManager + PatchManager + Core
    physical_modeling_piano_core.cpp    Implementation (~640 lines)
    physical_modeling_piano_math.h      Waveguide math (delay, loss, hammer, soundboard)
```
