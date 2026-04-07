# ICR — TODO

Critical assessment informed by Chabassier, Chaigne & Joly (2012):
*"Modeling and simulation of a grand piano"* (INRIA RR-8181) and
project analysis.  Issues ordered by impact on sound quality.

---

## Implementation Plan

### Phase 1 — Extraction robustness (next)

Goal: make the extraction pipeline physics-aware so that parameters are
physically consistent even when individual partial measurements are noisy.

**Step 1a — Global damping law fit: R + eta*f^2**

Physics (Chabassier Eq.8): string damping in frequency domain is
`damping(f) = R_u + eta_u * f^2`.  Two constants per note.

Implementation in `extractor.py`:
- After per-partial tau1/tau2 extraction, compute effective damping rate
  for each partial: `rate_k = 1/tau1_k` (using fast component)
- Fit `rate(f) = R + eta * f^2` via least-squares on partials k=1..K
  where A0 > noise_floor (reliable partials only)
- Weight by A0 — louder partials have more reliable tau measurements
- Store R_u and eta_u in the note's output dict

**Step 1b — Derive per-partial tau from damping law**

For partials where the independent fit produced suspect values (tau > 10s
for k>8, or fit residual > 50% of signal energy):
- Replace tau1 with `1 / (R_u + eta_u * f_k^2)`
- Keep tau2 as max(tau1 * 1.5, independently_fitted_tau2) to preserve
  the aftersound component
- Flag these partials as `damping_derived: true` in output

This eliminates the core problem: vel=0 high partials with tau=37s
(noise-floor fits) get physically correct short tau values instead.

**Step 1c — Noise harmonic subtraction: add sin() basis**

Current `_analyze_noise` in extractor.py builds a least-squares matrix
with only cos(2*pi*f_k*t) columns.  This removes at most 50% of harmonic
energy because the phase component (sin) is untouched.

Fix: add sin(2*pi*f_k*t) columns to the regression matrix.  The residual
after subtraction will contain only inharmonic energy (true noise).

Implementation: in `_analyze_noise`, change the basis matrix from
`[cos(w1*t), cos(w2*t), ...]` to `[cos(w1*t), sin(w1*t), cos(w2*t), sin(w2*t), ...]`

**Step 1d — Bi-exp fitter: 8 initializations + random restarts**

Current `_fit_decay` uses 4 fixed initializations.  For notes where the
onset data is weak, the fitter may converge to a local minimum.

Fix: expand to 8 initializations:
- 4 existing deterministic starts
- 4 random starts: sample tau1 from log-uniform(0.01, 1.0),
  tau2 from log-uniform(0.5, 10.0), a1 from uniform(0.1, 0.9)
- Keep the best (lowest residual) across all 8

**Step 1e — Per-note fit quality metric**

After fitting, compute: `quality = 1 - (residual_energy / total_energy)`

Where `residual_energy = sum((signal - fitted_envelope)^2)` and
`total_energy = sum(signal^2)`.

- quality > 0.9: excellent fit
- quality 0.7-0.9: acceptable
- quality < 0.7: flag as suspect, log warning

Store in output as `fit_quality` per partial and per note (average).

### Phase 2 — Validation and tools

Goal: detect extraction failures automatically before they reach C++.

**Step 2a — Bank inspector** (`tools/inspect_bank.py`)

Command-line tool that reads a soundbank JSON and prints:
- Per-register (bass/middle/treble) statistics: partial count, A0 range,
  tau1/tau2 range, beat_hz range, noise params
- Notes with anomalous values (tau > 10s, A0 < 1e-6 for k<10, etc.)
- Spectral shape consistency across velocity layers
- Missing notes / velocity layers

Usage: `python tools/inspect_bank.py soundbanks/pl-grand.json`

**Step 2b — Post-export quality report**

After export, automatically synthesize 12 reference notes (one per octave,
mid-velocity) and compute spectral distance vs original WAV:
- MRSTFT distance (multi-resolution spectral loss)
- Envelope correlation (is the decay shape correct?)
- Attack onset match (first 20ms comparison)

Output: quality_report.txt with per-note scores and overall grade.

**Step 2c — `--validate` flag**

Add to `run-training.py analyze`:
```
python run-training.py analyze --bank ... --validate
```
Runs 2b automatically after export.  Exit code 1 if any note below threshold.

**Step 2d — Unit tests**

C++ tests (`tests/test_dsp_math.cpp`, `tests/test_piano_math.cpp`):
- Verify biquad against scipy.signal reference output
- Verify decay_coeff: decay^N = exp(-N/(tau*sr))
- Verify RBJ shelf coefficients against known values
- Verify string models: known phase → known output
- Verify allpass is unity gain: |H(e^jw)| = 1 for all w

Python test: generate a synthetic piano note with known parameters,
run extractor, verify extracted params match within tolerance.

### Phase 3 — Longitudinal string model

Goal: add the metallic precursor sound that gives bass piano notes their
characteristic "ping" attack.

**Physics (Chabassier §2.1, Eq. 7):**

Longitudinal eigenfrequencies: `f_longi_l = l * f_longi_0` where
`f_longi_0 = 1/(2L) * sqrt(E/rho)`.

For steel: E = 2.0e11 Pa, rho = 7850 kg/m^3 → sqrt(E/rho) = 5048 m/s.

| Note | L (m) | f_longi_0 (Hz) | f_trans_0 (Hz) | Ratio |
|------|-------|----------------|----------------|-------|
| C2 | 1.60 | 1578 | 65.4 | 24:1 |
| F3 | 0.96 | 2629 | 174.6 | 15:1 |
| C#5 | 0.33 | 7648 | 555.6 | 14:1 |

The longitudinal frequencies are NOT harmonically related to the transverse
fundamental — they appear as "extra" spectral peaks during the attack.

The longitudinal wave arrives at the bridge c_longi/c_trans = 14x faster
than the transverse wave, creating the precursor.

**Step 3a — Extract non-harmonic peaks**

In `extractor.py`, after standard harmonic peak extraction:
- Compute STFT of the first 10-20 ms only (attack window)
- Find peaks that are NOT near any harmonic k*f0
- Compare against predicted f_longi series for that note's string length
- String length L can be estimated from f0 and typical scaling curves

**Step 3b — Classify peaks**

Three categories:
- **Longitudinal:** peaks near f_longi_l = l*f_longi_0 (within ±2%)
- **Phantom:** peaks near f_m + f_n for known harmonics m, n
- **Noise/soundboard:** remaining non-harmonic peaks

Store classified peaks with frequency, amplitude, decay time.

**Step 3c — C++ longitudinal synthesis**

Add to `PianoVoice`:
```cpp
int n_longi_partials;
struct { float f_hz, A0, decay; } longi[8];  // max 8 longitudinal partials
```

In processBlock: separate cosine sum for longitudinal partials, rapid
exponential decay (tau ~ 5-20 ms), added to output before noise.

**Step 3d — Export to JSON**

New field in note dict:
```json
"longitudinal_partials": [
  {"l": 1, "f_hz": 1578, "A0": 0.02, "tau": 0.015},
  {"l": 2, "f_hz": 3156, "A0": 0.008, "tau": 0.010}
]
```

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

Goal: add the remaining perceptually significant phenomena that
differentiate a piano model from an organ-like additive synthesizer.

**Step 5a — Extract soundboard IR**

The soundboard filters all string vibrations before they reach the
listener.  Its impulse response (IR) encodes body resonances, wood
character, and radiation pattern.

Approach: deconvolve the known synthesized string signal from the
recorded signal.  The quotient in frequency domain is the soundboard
transfer function.  Average across several notes to reduce noise.

Output: `soundboard_ir.wav` (mono, ~100 ms, 48 kHz)

**Step 5b — Post-synthesis convolution**

Add a convolution stage in the DSP chain (after limiter, before output):
```cpp
class ConvolutionReverb {
    float* ir_;       // impulse response samples
    int    ir_len_;   // IR length
    float* buf_L_;    // overlap-save buffer
    float* buf_R_;
    void process(float* L, float* R, int n);
};
```
Use overlap-save FFT convolution for efficiency.  IR length ~4096
samples (85 ms at 48 kHz) is sufficient for soundboard character.

**Step 5c — Phantom partial extraction**

Phantom partials appear at `f_m + f_n` and `|f_m - f_n|` due to
geometrical nonlinearity (Chabassier §4).  Most prominent in bass
where string displacement is large relative to diameter.

After standard harmonic extraction, for each pair (m, n) where m,n <= 10:
- Check for spectral energy at f_m + f_n (±1% tolerance)
- If present and above noise floor, extract amplitude and decay
- Store as `phantom_partials: [{m, n, f_hz, A0, tau}]`

These can be synthesized as additional cosines in C++ — they have
rapid decay (similar to longitudinal modes).

**Step 5d — Sympathetic resonance**

When a note plays, undamped strings resonate sympathetically at
harmonically related frequencies.  This adds subtle richness.

Simplified model: when voice[m] is active, add low-level energy
(-40 dB) to voices whose f0 is an integer multiple of voice[m].f0.
This requires tracking open (undamped) strings and applying
a simple energy injection at noteOn.

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

**5f. Hammer-contact skip in decay fitting (IMPLEMENTED):**
Root cause of tau1=0.01 in middle register: onset prepend captures the
hammer contact peak (~first 5 ms), which is a transient impulse, not
string decay.  The bi-exp fitter interprets this as a fast component.
Fix: skip 5 ms post-peak before fitting, but preserve peak A0 value.
This makes the fitter see the actual string decay starting after the
hammer releases.

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

## Validation: pl-grand vs. Chabassier Steinway D

Both our sample bank (`pl-grand`) and Chabassier et al. (2012) use a
Steinway D grand piano, enabling direct parameter comparison.

### Frequency (tuning)

| Note | Chabassier f0 | Our f0 | Deviation |
|------|--------------|--------|-----------|
| D#1 (MIDI 27) | 38.9 Hz | 38.56 Hz | -0.9% (-15 cents) |
| C2 (MIDI 36) | 65.4 Hz | 65.12 Hz | -0.4% (-7 cents) |
| C#5 (MIDI 73) | 555.6 Hz | 553.93 Hz | -0.3% (-5 cents) |

Consistent: our piano tunes slightly flat (~A=438-439).

### Inharmonicity B

| Note | Chabassier (from phys. params) | Our extraction | Ratio |
|------|-------------------------------|----------------|-------|
| D#1 | ~3.5e-5 (Euler-Bernoulli core) | 5.3e-4 | 15x higher |
| C2 | ~3.6e-5 | 2.2e-4 | 6x higher |

Our B is measured from actual partial frequencies; Chabassier computes
from core-only stiffness.  Wound string wrapping adds effective stiffness
beyond the Euler-Bernoulli core model — the discrepancy is expected.
**Not a bug, but worth noting for future physical parameter modeling.**

### Prompt decay (tau1 for k=1)

| Note | Chabassier (~8 dB/s -> tau=1.1s) | Our tau1(k=1) | Status |
|------|----------------------------------|---------------|--------|
| D#1 | ~2.2 s | 0.66 s | Short (vel=4 ~mf, lower energy) |
| C2 | ~1.5 s | 0.46 s | Short (same reason) |
| C#5 | ~0.6 s | **0.01 s** | **BUG** — hammer-contact fit |

C#5 tau1=0.01 confirmed as hammer-contact fitting artifact.
D#1/C2 tau1 shorter than Chabassier — partially explained by different
dynamics (our vel=4 ~mf vs Chabassier 3-4.5 m/s ~f-ff).

### Partial count

| Note | Chabassier (simulation) | Our extraction |
|------|------------------------|----------------|
| D#1 | ~50-60 | 60 (max) |
| C2 | ~50-60 | 60 (max) |
| C#5 | ~20-30 | 29 |

Good agreement.

### Known discrepancies to investigate

| # | Issue | Severity | Notes |
|---|-------|----------|-------|
| 1 | tau1=0.01 for MIDI 49-90 | **Critical** | Hammer-contact skip fix pending (new analysis running) |
| 2 | noise_centroid = 1000 Hz everywhere | Medium | Floor masks real variation; bass should be lower, treble higher |
| 3 | stereo_width = 6.2 for C#5 | Medium | Abnormally high; EQ fitter stereo measurement issue |
| 4 | B 6-15x higher than Chabassier theory | Low | Expected for wound strings (measured vs. core-only theory) |
| 5 | tau1 shorter than Chabassier even after fix | Low | Different dynamics (mf vs ff); validate after re-analysis |

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
- [x] Bank inspector tool (`tools/inspect_bank.py`)
- [x] Hammer-contact skip: 5 ms post-peak skip before bi-exp fitting —
      fixes tau1=0.01 root cause (was fitting hammer impulse, not string decay)
- [x] Damping law correction: now also catches tau1 < 0.02 s (too short)
- [x] Bi-exp fitter tau1 lower bound raised to 0.03 s
