# tools-physical/ — Dual-rail waveguide piano synthesizer

Offline renderer for the physical modeling piano core, based on the
dual-rail digital waveguide approach (Teng 2012 / Smith 1992).

## Quick start

```bash
# Render anchor notes from a soundbank
python tools-physical/generate_teng.py \
    --bank soundbanks-physical/physical-piano-04081305.json

# Specific notes, multiple velocities
python tools-physical/generate_teng.py \
    --bank soundbanks-physical/physical-piano-04081305.json \
    --midi 48 60 72 --vel 0.3 0.6 0.9

# A/B dispersion test
python tools-physical/generate_teng.py \
    --bank soundbanks-physical/physical-piano-04081305.json \
    --midi 64 --disp-stages 0 6 12 23
```

Output: `tmp_audio/teng/m060-v06-f262.wav`

Format: `m{MIDI}-v{velocity*10}-f{freq_hz}[-d{disp_stages}].wav`


## Architecture

```
generate_teng.py
│
├── render_note()               Public API — orchestrates multi-string + stereo
│   └── _dual_rail_string()     One string: dual-rail waveguide sample loop
│       ├── one_pole_lp()       Loss filter (Välimäki one-pole)
│       └── allpass_frac()      Allpass for tuning & dispersion
│
├── write_wav_stereo()          16-bit PCM stereo WAV writer
├── write_wav_mono()            16-bit PCM mono WAV writer
└── load_bank()                 Soundbank JSON loader
```


## How it works

### Single-rail vs dual-rail

The original `tests/test_string.py` uses a **single-rail** waveguide — one
circular delay line where the signal loops around. It works, but requires
computing an explicit Fourier series for the initial excitation (with
parameters like `odd_boost`, `knee_k`, `knee_slope`, `exc_rolloff` that must
be hand-tuned per listening test).

The **dual-rail** approach uses two parallel delay lines representing
physically traveling waves:

```
         n0 (hammer)
          |
  [Nut]   v                           [Bridge]
    |   ←---lower----[+]----upper--->    |
   -1   --->lower----[+]----upper---←   [H]  → output
                                         |
    H = loss_filter × dispersion × tuning × (-1)
```

- **upper rail**: right-traveling wave (nut → bridge), length M = N/2
- **lower rail**: left-traveling wave (bridge → nut), length M = N/2
- **hammer force** (half-sine pulse) injected at physical position `x0`
  on both rails simultaneously

### Why dual-rail sounds better

1. **Natural comb filter** — the hammer creates two wave fronts. The direct
   path (hammer → bridge) and the reflected path (hammer → nut → bridge)
   arrive at different times. The path difference = 2×n0 samples, creating
   notches at harmonics k = 1/x0, 2/x0, ... (e.g., k=7, 14, 21 for x0=1/7).
   No Fourier coefficients needed — physics does it.

2. **Realistic attack** — the hammer pulse shape directly controls the
   spectral content. Shorter contact = brighter. No `odd_boost` / `knee_k`
   / `knee_slope` parameters to hand-tune.

3. **Multi-string beating** — 2-3 slightly detuned strings create natural
   beating and two-stage decay (fast initial decay from in-phase vertical
   vibration, slow tail from out-of-phase horizontal). This is a defining
   characteristic of piano sound.

4. **Stereo** — detuned strings are panned across L/R with configurable
   spread, giving natural stereo width from the physical separation of
   strings on the bridge.

### Signal flow (per sample)

```python
for n in range(n_samples):
    output[n] = upper[bridge]           # 1. read bridge output

    x = upper[bridge]                    # 2. bridge reflection chain:
    x = loss_filter(x)                   #    - one-pole LPF (decay)
    x = dispersion_cascade(x)            #    - allpass cascade (stiffness)
    x = tuning_allpass(x)                #    - fractional delay (tuning)

    nut_ref = -lower[nut]                # 3. nut reflection: rigid (-1)

    shift upper →                        # 4. propagate waves
    upper[nut] = nut_ref
    shift lower ←
    lower[bridge] = -x

    if n < hammer_duration:              # 5. inject hammer force
        upper[n0] += force[n]
        lower[n0] += force[n]
```

### Delay compensation

Every filter in the feedback loop adds group delay that would detune f0.
The delay line length is shortened to compensate:

```
N_compensated = SR/f0 - filter_delay - dispersion_delay
M = N_compensated / 2     (each rail = half period)
```

Where:
- `filter_delay = pole / (1 - pole²)` — loss filter group delay at DC
- `dispersion_delay = n_stages × (1 - a) / (1 + a)` — allpass cascade at DC

The tuning allpass handles the fractional part.


## Dispersion (allpass cascade)

Real piano strings have stiffness that stretches upper partials:

```
f_k = k × f0 × sqrt(1 + B × k²)
```

This is simulated by a cascade of first-order allpass filters with
coefficient `a = -0.15`. Each stage adds frequency-dependent delay:

```
Group delay per stage:
  DC (ω=0):      τ = (1-a)/(1+a) = 1.353 samples
  Nyquist (ω=π): τ = (1+a)/(1-a) = 0.739 samples
```

Higher harmonics "see" a shorter loop → oscillate faster → stretched partials.

| Stages | DC delay | Nyq delay | Character            |
|--------|----------|-----------|----------------------|
| 0      | 0        | 0         | pure string, smooth  |
| 6      | 8.1      | 4.4       | mild stretch, piano  |
| 12     | 16.2     | 8.9       | strong, bell-like    |
| 23     | 31.1     | 17.0      | extreme, metallic    |

Number of stages is computed from inharmonicity: `n = min(16, B × N² × 0.5)`.


## Parameters

### From soundbank JSON (per note)

| Parameter       | Typical range   | Description                              |
|-----------------|-----------------|------------------------------------------|
| `T60_fund`      | 1.5 - 12.0 s    | Fundamental decay time (60 dB)           |
| `T60_nyq`       | 0.15 - 0.35 s   | Nyquist decay time (before gauge scale)  |
| `B`             | 5e-5 - 2e-3     | Inharmonicity coefficient                |
| `gauge`         | 0.8 - 4.0       | String thickness (HF damping multiplier) |
| `exc_x0`        | 0.1429 (1/7)    | Hammer striking position                 |
| `n_strings`     | 1, 2, or 3      | Strings per note (bass=1-2, mid/treble=3)|
| `detune_cents`  | 0.3 - 2.5       | Detuning between outer strings           |
| `n_disp_stages` | 0 - 16          | Dispersion allpass stages (0=auto from B)|

### CLI parameters

| Flag              | Default          | Description                           |
|-------------------|------------------|---------------------------------------|
| `--bank`          | (required)       | Path to soundbank JSON                |
| `--midi`          | 36 48 60 72 84   | MIDI notes to render                  |
| `--vel`           | 0.6              | Velocity values (0.0-1.0)             |
| `--duration`      | 2.5              | Note duration in seconds              |
| `--output-dir`    | tmp_audio/teng   | Output directory                      |
| `--stereo-spread` | 0.3              | Stereo width (0=mono, 1=full pan)     |
| `--disp-stages`   | (from bank)      | Override dispersion (one file per val) |
| `--mono`          | false            | Mono output (1 string, no detuning)   |


## Example soundbank note entry

```json
{
  "m060": {
    "midi": 60,
    "f0_hz": 261.626,
    "B": 0.0007,
    "gauge": 2.0,
    "T60_fund": 5.84,
    "T60_nyq": 0.253,
    "exc_x0": 0.1429,
    "n_strings": 3,
    "detune_cents": 1.5,
    "n_disp_stages": 11,
    "disp_coeff": -0.15
  }
}
```


## References

- Teng W.J. (2012), "Piano Sounds Synthesis with an emphasis on the
  modeling of the hammer and the piano wire", MSc thesis, University of
  Edinburgh
- Smith J.O. (1992), "Physical Modeling Using Digital Waveguides",
  Computer Music Journal 16(4)
- Van Duyne S.A. & Smith J.O. (1994), "A Simplified Approach to Modeling
  Dispersion Caused by Stiffness in Strings and Plates", ICMC
- Välimäki V. et al. (1996), "Physical Modeling of Plucked String
  Instruments with Application to Real-Time Sound Synthesis", JAES
- Bank B. (2000), "Physics-Based Sound Synthesis of the Piano",
  MSc thesis, Budapest University of Technology
