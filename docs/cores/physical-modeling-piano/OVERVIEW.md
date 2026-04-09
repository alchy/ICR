# PhysicalModelingPianoCore

Dual-rail digital waveguide piano (v1.0). Chaigne-Askenfelt FD hammer,
Teng 2012 string topology, commuted soundboard via external IR.

CLI: `--core PhysicalModelingPianoCore` (works without `--params` using physics defaults)

## Synthesis Approach

```
Chaigne FD Hammer ──→ F/(2R0) ──→ velocity injection at n0
                                        │
    ┌───────────── upper rail (nut→bridge) ──────────────┐
    │  nut (-1)                              bridge end   │
    │                                        │            │
    │  ← lower rail (bridge→nut) ←─── loss → disp → tune → ×bridge_refl
    └────────────────────────────────────────────────────┘
                                        │
                                   bridge output ──→ Σ strings ──→ IR convolution
```

- **Hammer**: Chaigne & Askenfelt (1994) finite-difference model.
  Nonlinear felt: `F = K|δ|^p`. 3 anchor points (C2/C4/C7),
  velocity-dependent K and p hardening. FD grid with Courant stability
  check — falls back to C4 params for MIDI 96+ (Teng §4.2).
- **String**: Dual delay lines (circular buffer, O(1) shift).
  Loss filter (Välimäki one-pole) + dispersion allpass cascade
  (Van Duyne & Smith 1994, coeff -0.30) + tuning allpass.
- **Bridge**: Rigid reflection (`bridge_refl = -1.0`). All decay
  handled by the loss filter (T60_fund, T60_nyq).
- **Nut**: Rigid reflection (-1).
- **Multi-string**: 1-3 per note, independently detuned. Produces
  beating and emergent two-stage decay.
- **Hammer noise**: Bandpass-filtered white noise burst (1.5-5 kHz).
- **Soundboard**: External IR convolution via DspChain (not in waveguide loop).

## GUI Parameters (setParam)

| Key | Group | Range | Description |
|-----|-------|-------|-------------|
| `brightness` | Timbre | 0.1-4.0 | Scales T60_nyq (HF decay) |
| `stiffness_scale` | Timbre | 0.1-4.0 | Scales inharmonicity B |
| `sustain_scale` | Timbre | 0.1-4.0 | Scales T60_fund |
| `gauge_scale` | String | 0.5-4.0 | Retained for compatibility (no DSP effect) |
| `keyboard_spread` | Stereo | 0.0-pi | L-R spread across keyboard |
| `stereo_spread` | Stereo | 0.0-1.0 | Multi-string pan width |

## Per-Note Parameters (setNoteParam / bank JSON)

See [JSON_SCHEMA.md](JSON_SCHEMA.md) for the full schema.

16 keys: `f0_hz`, `B`, `gauge`, `T60_fund`, `T60_nyq`, `exc_x0`,
`K_hardening`, `p_hardening`, `n_disp_stages`, `disp_coeff`,
`n_strings`, `detune_cents`, `hammer_mass`, `string_mass`,
`output_scale`, `bridge_refl`.

## Physics Defaults (Chaigne & Askenfelt 1994)

Interpolated from 3 anchor measurements:

| Parameter | C2 (MIDI 36) | C4 (MIDI 60) | C7 (MIDI 96) |
|-----------|:------------:|:------------:|:------------:|
| Ms (string) | 35 g | 3.93 g | 0.467 g |
| L (length) | 1.90 m | 0.62 m | 0.09 m |
| Mh (hammer) | 4.9 g | 2.97 g | 2.2 g |
| T (tension) | 750 N | 670 N | 750 N |
| K (stiffness) | 1e8 | 4.5e9 | 1e11 |
| p (exponent) | 2.3 | 2.5 | 3.0 |

## Source Files

```
cores/physical_modeling_piano/
    physical_modeling_piano_core.h      Voice + VoiceManager + PatchManager + Core
    physical_modeling_piano_core.cpp    Implementation
    physical_modeling_piano_math.h      DSP math (header-only, RT-safe)
```

## References

- Teng (2012): Piano Sounds Synthesis (MSc, Edinburgh)
- Chaigne & Askenfelt (1994): Numerical simulations of piano strings
- Smith (1992): Physical Modelling using Digital Waveguides
- Van Duyne & Smith (1994): Dispersion via allpass cascade
- Välimäki et al. (1996): Physical Modeling of Plucked String Instruments
- Bank (2000): Physics-Based Sound Synthesis of the Piano
