# ICR — TODO

Critical assessment informed by Chabassier, Chaigne & Joly (2012):
*"Modeling and simulation of a grand piano"* (INRIA RR-8181) and
project analysis.  Issues ordered by impact on sound quality.

---

## Implementation Plan

### Phase 1 — Extraction robustness (next)

| Step | What | Where | Physics basis |
|------|------|-------|---------------|
| 1a | Global damping law fit: R + eta*f^2 | extractor.py | Chabassier Eq.8: damping = R_u + eta_u*f^2 |
| 1b | Derive per-partial tau from damping law | extractor.py | Replace noisy independent fits with physics constraint |
| 1c | Noise harmonic subtraction: add sin() basis | extractor.py | Current cos()-only removes max 50% |
| 1d | Bi-exp fitter: 8 initializations + random restarts | extractor.py | Reduce local-optimum risk |
| 1e | Per-note fit quality metric | extractor.py | Residual energy / total energy → flag bad fits |

### Phase 2 — Validation and tools

| Step | What | Where |
|------|------|-------|
| 2a | Bank inspector: per-register statistics | tools/inspect_bank.py |
| 2b | Post-export quality report (spectral distance) | exporter.py + synthesizer.py |
| 2c | `--validate` flag on run-training.py | run-training.py |
| 2d | Unit tests for dsp_math.h + piano_math.h | tests/ |

### Phase 3 — Longitudinal string model

| Step | What | Where | Physics basis |
|------|------|-------|---------------|
| 3a | Extract non-harmonic peaks in first 10ms | extractor.py | Chabassier Eq.7: f_longi = l/(2L)*sqrt(E/rho) |
| 3b | Classify as longitudinal vs phantom vs noise | extractor.py | Longitudinal: f_longi series. Phantom: f_m+f_n |
| 3c | Add longitudinal partial synthesis in C++ | piano_core.cpp | Separate cosine sum, rapid decay, bass only |
| 3d | Export longitudinal params to JSON | exporter.py | New field: `longitudinal_partials[]` |

### Phase 4 — Hammer excitation model

The hammer-string interaction is the key to velocity-dependent timbre.
Currently we extract A0 per velocity layer independently, which fails at
low SNR.  The goal is to model the hammer physics directly, so velocity
produces a **physically correct** spectral change.

**Physics (Chabassier §2.2, Eq. 12-13):**

The hammer-string force is: `F(t) = K_H * [xi(t) - u(t)]_+^p + R_H * d/dt[...]`

Where:
- `K_H` = hammer stiffness (4e8 bass -> 2.3e11 treble, 3 orders of magnitude)
- `p` = nonlinear felt exponent (2.27 bass -> 3.0 treble)
- `xi(t)` = hammer position, `u(t)` = string displacement at contact point
- `R_H` = dissipation coefficient (hysteresis in felt compression)
- `[x]_+` = positive part (force only during contact)

The hammer velocity `v_H` (0.5 m/s pp -> 4.5 m/s ff) determines the
peak string displacement, and through the nonlinear `(*)^p` force law,
the spectral content of the excitation pulse.  Higher velocity = larger
displacement = more high-frequency energy in the force pulse.

**The spectral tilt is approximately:**
`A0(k, vel) ~ A0_ref(k) * (vel / vel_ref)^alpha(k)`

Where `alpha(k)` increases with k — high partials grow faster with velocity
than low partials.  This is the "brighter at louder" effect.

**Implementation steps:**

| Step | What | Where | Detail |
|------|------|-------|--------|
| 4a | Extract spectral tilt per velocity | extractor.py | For each MIDI note with >=3 vel layers: compute `slope = d(log A0)/d(log vel)` per partial k.  This gives `alpha(k)`. |
| 4b | Fit p exponent from alpha(k) curve | extractor.py | `alpha(k) ~ (p-1) * log(k)` approximately.  Fit p from the measured alpha values of well-extracted partials (k=1-8). |
| 4c | Store p, K_H per note in JSON | exporter.py | New fields: `hammer_p`, `hammer_K_H`.  Fall back to Chabassier table values if extraction fails. |
| 4d | C++ hammer pulse model | piano_math.h | Compute excitation spectrum from p and velocity: `E(k) ~ sinc(k*pi*x0/L)^2 * (v_H)^(2*(p-1))` where `x0` = striking position (~L/8). |
| 4e | Replace static A0 with A0 * E(k,vel) | piano_core.cpp | `A0_scaled = A0_ref * hammer_spectral_weight(k, velocity, p)` computed at noteOn. |
| 4f | Velocity-dependent noise_centroid | piano_core.cpp | `centroid = centroid_base + vel_factor * (vel/127)`.  Higher velocity = higher centroid = brighter attack noise. |

**Chabassier reference values for validation:**

| Note | p | K_H | M_H (g) | x0/L |
|------|---|-----|---------|------|
| D#1 | 2.4 | 4.0e8 | 12.0 | 0.129 |
| C2 | 2.27 | 2.0e9 | 10.2 | 0.125 |
| F3 | 2.4 | 1.0e9 | 9.0 | 0.120 |
| C#5 | 2.6 | 2.8e10 | 7.9 | 0.120 |
| G6 | 3.0 | 2.3e11 | 6.77 | 0.121 |

**Striking position x0/L:**
The hammer strikes at approximately 1/8 of the string length.  This creates
a spectral null at k = 8, 16, 24... (the "hammer harmonic").  This is a
well-known piano design feature and should be visible in extracted A0 data.

### Phase 5 — Soundboard and advanced

| Step | What | Where |
|------|------|-------|
| 5a | Extract soundboard IR (deconvolution) | tools/ |
| 5b | Post-synthesis convolution stage | dsp/ |
| 5c | Phantom partial extraction (f_m + f_n) | extractor.py |
| 5d | Sympathetic resonance (open string coupling) | piano_core.cpp |

---

## Critical Model Gaps

### 1. No hammer excitation model — velocity affects only loudness, not timbre

**Current:** Each velocity layer has its own A0 values extracted from recordings.
Lower velocity layers have weaker high partials (because the recording is
quieter → high partials fall below the analysis noise floor → extractor fits
tiny A0 values).  RMS normalization then amplifies everything, but the spectral
shape stays dark.  Result: pp sounds like ff with a low-pass filter, not like
a softer piano touch.

**Measured from pl-grand.json (MIDI 36 C2):**

| Partial k | vel=0 (dB re k=2) | vel=7 (dB re k=2) | Difference |
|---|---|---|---|
| k=5 | -13.7 dB | -6.5 dB | -7.3 dB |
| k=10 | -80.9 dB | -21.8 dB | -59.2 dB |
| k=20 | -67.5 dB | -50.1 dB | -17.4 dB |

Real piano: difference should be -3 to -6 dB.  Noise floor contamination.

**Physics (Chabassier §2.2):** The hammer-string force is `F = K_H * (xi - u)^p`
where the nonlinear exponent `p` varies from 1.5 (soft felt, bass) to 3.5
(hard felt, treble).  Higher velocity increases string amplitude, which through
the nonlinear `(*)^p` force law generates more high-frequency energy.  The
spectral change is **continuous and gradual**, not an abrupt layer switch.

**Mitigation (implemented):** Spectral shape borrowing from vel 5-7 average.
Velocity interpolation via `lerpNoteParams` with float velocity 0.0-7.0.

**Proper fix (Phase 4):** Model the nonlinear hammer force directly.

### 2. No longitudinal string vibration — missing metallic precursor

**Current:** Only transverse string motion modeled: `cos(2pi*f_k*t)`.

**Physics (Chabassier §2.1, Eq. 7):** Longitudinal wave speed in steel is
`c_longi = sqrt(E/rho) = 2914 m/s` for C2, vs transverse `c_trans = 209 m/s`.
The longitudinal wave arrives at the bridge **14x faster** than the transverse
wave, creating an audible metallic precursor — especially prominent in bass.

The longitudinal eigenfrequencies are `f_longi_l = l * f_longi_0` where
`f_longi_0 = 1/(2L) * sqrt(E/rho)`.  For C2 (L=1.6m): `f_longi_0 = 910 Hz`.
These are NOT harmonically related to the transverse fundamental (65.4 Hz).

**Additionally (Chabassier §4):** Geometrical nonlinearity couples transverse
and longitudinal modes, generating **phantom partials** at sum and difference
frequencies (e.g. f_3 + f_5).

### 3. No soundboard coupling model

**Current:** Soundboard effect captured only indirectly through spectral EQ
and the bi-exponential envelope.

**Physics (Chabassier §2.3-2.4):** The soundboard is an orthotropic plate
with ribs and bridges as heterogeneities.  The string-bridge coupling
transmits **both transverse and longitudinal** waves to the soundboard.
This is why the soundboard spectrum is "denser and richer than the strings"
(Chabassier §4).

### 4. Frequency-dependent damping not physics-constrained

**Current:** Each partial has independent tau1/tau2 from extraction.

**Physics (Chabassier §2.1, Eq. 8-9):** String damping is
`damping(f) = R_u + eta_u * f^2` — constant + quadratic.  Higher partials
decay quadratically faster.  Our per-partial extraction should capture this
but noise makes independent fits unreliable for high-k partials.

**Impact on extraction (Phase 1a-1b):**
- Fit R_u and eta_u globally from well-measured partials (k=1-8)
- Derive tau for k>8 from the physical law instead of noisy independent fits
- Reject extracted tau values that violate the quadratic trend by >2 sigma
- This eliminates the "tau=37s for k=20 at pp" problem entirely

### 5. Extraction pipeline: Chabassier-informed improvements

**5a. Onset analysis constrained by hammer contact time:**
Chabassier gives precise contact times: bass ~4 ms, treble <1 ms.
The onset STFT window should be at least 2x the contact time.  Our
frequency-scaled onset frame already satisfies this, but the contact
time provides a physical upper bound for `attack_tau` validation.

**5b. Harmonic subtraction in noise analysis:**
Current cos()-only basis removes at most 50% of harmonic energy.
Adding sin() basis gives complete harmonic subtraction, producing
a clean noise residual.

**5c. Bi-exp fitter robustness:**
Current 4 initializations risk local optima.  Increase to 8 with
random restarts.  Add per-note fit quality metric (residual energy
/ total energy) and flag notes below threshold.

**5d. Longitudinal peak detection (Phase 3a):**
After standard harmonic peak extraction, scan for non-harmonic peaks
in the first 10 ms of the spectrogram.  Classify by comparing
against predicted `f_longi = l/(2L)*sqrt(E/rho)` series.

**5e. Velocity-spectral-tilt extraction (Phase 4a):**
For each MIDI note with multiple velocity layers, compute
`spectral_tilt(vel) = slope of log(A0) vs log(k)`.  This measures
how brightness changes with velocity — currently lost to noise floor.

### 6. No quality gates

Pipeline has zero automatic validation.

- [ ] Post-export: synthesize reference notes, compute spectral distance
- [ ] Define quality thresholds, generate per-note quality report
- [ ] Add `--validate` flag to `run-training.py analyze`

---

## Operational Issues

### 7. No tests

Zero coverage.  `piano_math.h` and `dsp_math.h` are cleanly testable.

- [ ] `tests/test_dsp_math.cpp` — biquad, decay_coeff, RBJ shelves
- [ ] `tests/test_piano_math.cpp` — string models, envelope, allpass
- [ ] Python: feed known synthetic signal to extractor, verify parameters
- [ ] CMake test target (`ctest`)

### 8. Build / environment

- [ ] `requirements.txt` with pinned versions
- [ ] Evaluate removing torch dependency (unused DifferentiableRenderer)

### 9. Soundbank inspection tools

- [ ] `tools/inspect_bank.py` — per-register summary statistics
- [ ] `tools/compare_banks.py` — diff two soundbanks

---

## Quick Wins (remaining)

| # | Task | Effort | Impact |
|---|------|--------|--------|
| B | Verify tau(k) follows R+eta*k^2 law | 0.5 day | Validate extraction physics |
| C | Unit tests for math headers | 1 day | Catch regressions |
| D | Post-export quality report | 2 days | Detect bad extractions |
| E | Bank inspector tool | 0.5 day | Fast parameter debugging |

---

## Future: Hardware Velocity Resolution

MIDI velocity is 7-bit (1-127).  The C++ engine now maps velocity to a
**continuous float position 0.0-7.0** (`midiVelToFloat`) and interpolates
all parameters between adjacent layers.  This architecture is ready for
future hardware with **10+ bit velocity resolution** — the interpolation
naturally uses any fractional position without code changes.  Higher
velocity resolution = smoother dynamic transitions.

---

## Physics Reference (Chabassier et al. 2012)

### String parameters (Steinway D measurements)

| Note | L (m) | d (mm) | F (wrap) | T0 (N) | f0 (Hz) | p (hammer) | K_H (N/m^p) | M_H (g) |
|------|-------|--------|----------|--------|---------|------------|-------------|---------|
| D#1 | 1.945 | 1.48 | 5.73 | 1781 | 38.9 | 2.4 | 4.0e8 | 12.0 |
| C2 | 1.600 | 0.95 | 3.55 | 865 | 65.4 | 2.27 | 2.0e9 | 10.2 |
| F3 | 0.961 | 1.05 | 1.0 | 774 | 174.6 | 2.4 | 1.0e9 | 9.0 |
| C#5 | 0.326 | 0.92 | 1.0 | 684 | 555.6 | 2.6 | 2.8e10 | 7.9 |
| G6 | 0.124 | 0.79 | 1.0 | 587 | 1571 | 3.0 | 2.3e11 | 6.77 |

### Key physics

- Hammer stiffness K_H spans 3 orders of magnitude (4e8 bass - 2.3e11 treble)
- Nonlinear exponent p: 2.27 (bass) - 3.0 (treble); higher = brighter
- Hammer velocity: 0.5 m/s (pp) to 4.5 m/s (ff) — continuous
- Transverse wave speed: 209 m/s (C2)
- Longitudinal wave speed: 2914 m/s (C2) — 14x faster = precursor
- Damping: `R_u + eta_u * f^2` — constant + quadratic frequency term
- Hammer contact time: bass ~4 ms, middle ~2 ms, treble <1 ms

---

## Recently Completed

- [x] Extract DSP math into `dsp_math.h` + `piano_math.h`
- [x] Fix Schroeder allpass sign error (`-g*y` -> `+g*y`)
- [x] Fix GUI velocity fallback display bug
- [x] GUI refactor: PaddedPanel, sectionGap, grid layout
- [x] Onset STFT frame scaling for bass
- [x] Attack rise envelope (1-exp(-t/tau_rise))
- [x] Onset gate reduced 3ms -> 0.5ms, noise bypasses gate
- [x] attack_tau capped at 0.10s
- [x] eq_freq_min lowered 400Hz -> 80Hz
- [x] Noise filter upgraded 1-pole -> biquad bandpass
- [x] Peak frame detection fix for short onsets
- [x] Cleanup: removed 15 NN pipeline/module files + 5 docs
- [x] Simplified run-training.py to single `analyze` command
- [x] Spectral shape borrowing in exporter (vel 5-7 average -> vel 0-4)
- [x] Velocity interpolation: float 0.0-7.0, lerpNoteParams in C++
- [x] Revised TODO with Chabassier physics + extraction impact analysis
