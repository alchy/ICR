# ICR — TODO

Critical assessment informed by Chabassier, Chaigne & Joly (2012):
*"Modeling and simulation of a grand piano"* (INRIA RR-8181) and
project analysis.  Issues ordered by impact on sound quality.

---

## Critical Model Gaps

### 1. No hammer excitation model — velocity affects only loudness, not timbre

**Current:** Each velocity layer has its own A0 values extracted from recordings.
Lower velocity layers have weaker high partials (because the recording is
quieter → high partials fall below the analysis noise floor → extractor fits
tiny A0 values).  RMS normalization then amplifies everything, but the spectral
shape stays dark.  Result: pp sounds like ff with a low-pass filter, not like
a softer piano touch.

**Physics (Chabassier §2.2):** The hammer-string force is `F = K_H · (ξ - u)^p`
where the nonlinear exponent `p` varies from 1.5 (soft felt, bass) to 3.5
(hard felt, treble).  Higher velocity increases string amplitude, which through
the nonlinear `(·)^p` force law generates more high-frequency energy.  The
spectral change is **continuous and gradual**, not an abrupt layer switch.

**Chabassier Table 4:** Hammer velocities 0.5 m/s (p) to 4.5 m/s (ff) produce
ADR (amplitude-to-diameter ratio) from 0.12 to 4.05.  The nonlinearity means
the spectral brightness scales **nonlinearly** with velocity — not as a
simple amplitude multiplier.

**Steps:**
- [ ] Velocity interpolation: interpolate A0 values between velocity layers
      instead of nearest-match (immediate fix, moderate impact)
- [ ] Extract spectral tilt per velocity: measure how A0(k)/A0(1) changes
      with velocity and fit a velocity-dependent spectral tilt curve
- [ ] Consider velocity-dependent `noise_centroid_hz` (harder hammer =
      higher centroid — brighter attack noise)
- [ ] Long-term: model the nonlinear hammer force directly (p parameter)

### 2. No longitudinal string vibration — missing metallic precursor

**Current:** Only transverse string motion modeled: `cos(2π·f_k·t)`.

**Physics (Chabassier §2.1, Eq. 7):** Longitudinal wave speed in steel is
`c_longi = √(E/ρ) ≈ 2914 m/s` for C2, vs transverse `c_trans ≈ 209 m/s`.
The longitudinal wave arrives at the bridge **14× faster** than the transverse
wave, creating an audible metallic precursor — especially prominent in bass.

The longitudinal eigenfrequencies are `f_longi_ℓ = ℓ · f_longi_0` where
`f_longi_0 = 1/(2L) · √(E/ρ)`.  For C2 (L=1.6m): `f_longi_0 ≈ 910 Hz`.
These are NOT harmonically related to the transverse fundamental (65.4 Hz).

**Additionally (Chabassier §4):** Geometrical nonlinearity couples transverse
and longitudinal modes, generating **phantom partials** at sum and difference
frequencies (e.g. f_3 + f_5).  These are clearly visible in real piano spectra
and are "perceptually significant" according to the paper.

**Steps:**
- [ ] Extract longitudinal mode frequencies from recordings (they appear as
      spectral peaks at non-harmonic frequencies in the first ~10ms)
- [ ] Add optional longitudinal partial synthesis in C++ (separate cosine
      sum with different frequency series, rapid decay)
- [ ] Evaluate phantom partial extraction (sum/difference frequencies)

### 3. No soundboard coupling model

**Current:** Soundboard effect captured only indirectly through spectral EQ
and the bi-exponential envelope.

**Physics (Chabassier §2.3-2.4):** The soundboard is an orthotropic plate
with ribs and bridges as heterogeneities.  Its modal structure determines:
- Which partials are efficiently radiated (impedance matching)
- The double-decay (prompt sound from symmetric modes, aftersound from
  antisymmetric modes — Weinreich mechanism)
- Low-frequency modes that appear in the spectrum during attack (clearly
  visible in simulations, Fig. 10)
- Energy transfer between strings via the bridge

The string-bridge coupling transmits **both transverse and longitudinal**
waves to the soundboard.  This is why the soundboard spectrum is "denser
and richer than the strings" (Chabassier §4).

**Steps:**
- [ ] Extract soundboard IR from recordings (impulse deconvolution from
      known string excitation)
- [ ] Evaluate post-synthesis convolution with soundboard IR
- [ ] Consider per-register soundboard coupling coefficients

### 4. Frequency-dependent damping not per-partial

**Current:** Each partial has its own tau1/tau2 from extraction — this does
capture frequency-dependent damping indirectly.

**Physics (Chabassier §2.1, Eq. 8-9):** String damping is modeled as
`2ρA·R_u · ∂u/∂t − 2T_0·η_u · ∂³u/(∂t·∂x²)`.  In frequency domain this
gives damping = `R_u + η_u · f²` — a constant term plus a **quadratic**
frequency-dependent term.  This means higher partials decay quadratically
faster.

**Assessment:** Our per-partial bi-exp extraction should capture this
naturally (higher-k partials will have shorter tau values).  But if the
extraction is noisy, the quadratic relationship is not enforced.

**Steps:**
- [ ] Verify extracted tau1(k) follows approximately R + η·k² pattern
- [ ] Consider fitting the R and η constants globally per note, then
      deriving per-partial tau from the physical law (more robust than
      per-partial independent fitting)

### 5. Fragile extraction pipeline

**Noise harmonic subtraction** uses only `cos()` basis — removes at most 50%
of harmonic content.  **Bi-exp fitter** has 4 initializations but no global
optimum guarantee.  **Spectral EQ** compensates model errors bidirectionally.

**Steps:**
- [ ] Add `sin()` basis to harmonic subtraction in `_analyze_noise`
- [ ] Increase bi-exp fitter initializations to 8 with random restarts
- [ ] Add per-note fit quality metric (residual energy / total energy)

### 6. No quality gates

Pipeline has zero automatic validation of output quality.

**Steps:**
- [ ] Post-export: synthesize 10-15 reference notes, compute spectral
      distance vs original WAV
- [ ] Define quality thresholds, generate per-note quality report
- [ ] Add `--validate` flag to `run-training.py analyze`

---

## Operational Issues

### 7. No tests

Zero coverage.  `piano_math.h` and `dsp_math.h` are cleanly testable.

**Steps:**
- [ ] `tests/test_dsp_math.cpp` — biquad, decay_coeff, RBJ shelves
- [ ] `tests/test_piano_math.cpp` — string models, envelope, allpass
- [ ] Python: feed known synthetic signal to extractor, verify parameters
- [ ] CMake test target (`ctest`)

### 8. Build / environment

Python 3.12 + torch required.  No CI, no Docker.

**Steps:**
- [ ] `requirements.txt` with pinned versions
- [ ] Evaluate removing torch dependency (only used by DifferentiableRenderer
      which is now unused)

### 9. Soundbank inspection tools

JSON with 704 notes × 60 partials = opaque multi-MB files.

**Steps:**
- [ ] `tools/inspect_bank.py` — per-register summary statistics
- [ ] `tools/compare_banks.py` — diff two soundbanks

---

## Quick Wins (remaining)

| # | Task | Effort | Impact |
|---|------|--------|--------|
| B | Verify tau(k) follows R+η·k² law | 0.5 day | Validate extraction physics |
| C | Unit tests for math headers | 1 day | Catch regressions |
| D | Post-export quality report | 2 days | Detect bad extractions |
| E | Bank inspector tool | 0.5 day | Fast parameter debugging |

## Future: Hardware Velocity Resolution

MIDI velocity is 7-bit (1-127).  The C++ engine now maps velocity to a
**continuous float position 0.0-7.0** (`midiVelToFloat`) and interpolates
all parameters between adjacent layers.  This architecture is ready for
future hardware with **10+ bit velocity resolution** — the interpolation
naturally uses any fractional position without code changes.  Higher
velocity resolution → smoother dynamic transitions.

---

## Physics Reference (Chabassier et al. 2012)

### String parameters used in their simulations

| Note | L (m) | d (mm) | F (wrap) | T₀ (N) | f₀ (Hz) | p (hammer) | K_H (N/m^p) | M_H (g) |
|------|-------|--------|----------|--------|---------|------------|-------------|---------|
| D#1 | 1.945 | 1.48 | 5.73 | 1781 | 38.9 | 2.4 | 4.0e8 | 12.0 |
| C2 | 1.600 | 0.95 | 3.55 | 865 | 65.4 | 2.27 | 2.0e9 | 10.2 |
| F3 | 0.961 | 1.05 | 1.0 | 774 | 174.6 | 2.4 | 1.0e9 | 9.0 |
| C#5 | 0.326 | 0.92 | 1.0 | 684 | 555.6 | 2.6 | 2.8e10 | 7.9 |
| G6 | 0.124 | 0.79 | 1.0 | 587 | 1571 | 3.0 | 2.3e11 | 6.77 |

Key observations:
- Hammer stiffness K_H spans **3 orders of magnitude** (4e8 bass → 2.3e11 treble)
- Nonlinear exponent p increases from 2.27 (bass) to 3.0 (treble)
- Wrap factor F = 5.73 for bass, 1.0 for plain steel (treble)
- Hammer mass decreases 12g → 6.8g bass → treble
- Hammer velocity 0.5 m/s (p) to 4.5 m/s (ff) — continuous, not 8 discrete layers

### Wave speeds (C2 string)
- Transverse: 209 m/s
- Longitudinal: 2914 m/s (14× faster — explains precursor)

### Damping model
- `damping(f) = R_u + η_u · f²` — constant + quadratic frequency term

---

## Recently Completed

- [x] Extract DSP math into `dsp_math.h` + `piano_math.h`
- [x] Fix Schroeder allpass sign error (`-g*y` → `+g*y`)
- [x] Fix GUI velocity fallback display bug
- [x] GUI refactor: PaddedPanel, sectionGap, grid layout
- [x] Onset STFT frame scaling for bass
- [x] Attack rise envelope (1-exp(-t/tau_rise))
- [x] Onset gate reduced 3ms → 0.5ms, noise bypasses gate
- [x] attack_tau capped at 0.10s
- [x] eq_freq_min lowered 400Hz → 80Hz
- [x] Noise filter upgraded 1-pole → biquad bandpass
- [x] Peak frame detection fix for short onsets
- [x] Cleanup: removed 15 NN pipeline/module files + 5 docs
- [x] Simplified run-training.py to single `analyze` command
- [x] **Spectral shape borrowing** in exporter: average A0(k)/A0(1) from vel
      layers 5-7, apply to vel 0-4 (fixes noise-floor contamination that made
      pp sound like ff + low-pass filter)
- [x] **Velocity interpolation** in C++: MIDI velocity mapped to continuous
      float 0.0-7.0 (`midiVelToFloat`), all parameters (A0, tau1, tau2, a1,
      beat_hz, noise, EQ coefficients) linearly interpolated between two
      bounding layers via `lerpNoteParams`.  Ready for 10+ bit HW velocity.
- [x] Revised TODO with Chabassier et al. (2012) physics reference
