# Session Summary — 2026-04-06/07

## What was done

### 1. DSP Math Refactor
- Extracted all synthesis math into `dsp/dsp_math.h` (shared: biquad, RBJ shelves,
  decay_coeff, gain envelope) and `cores/piano/piano_math.h` (piano-specific: string
  models, envelope, allpass, M/S, pan, ramps)
- Eliminated duplicated biquad code between BBE and piano EQ
- `PianoBiquadCoeffs` aliased to `dsp::BiquadCoeffs`

### 2. Bug Fixes Found During Refactor
- **Schroeder allpass sign** — identified sign error in feedback term (audit pending
  re-verification: current form may still not be true allpass)
- **GUI velocity fallback** — `getVizState()` was recomputing vel_idx without
  fallback logic, causing empty partials table for some notes
- **Onset gate** reduced from 3ms to 0.5ms; noise now bypasses onset gate entirely

### 3. Piano Model Fixes (Chabassier-informed)
- **Attack rise envelope** `1-exp(-t/tau_rise)`: 4ms bass → 0.2ms treble
- **Noise filter** upgraded from 1-pole LPF to biquad bandpass (Q=1.5)
- **attack_tau** capped at 0.10s (was 1.0s)
- **eq_freq_min** lowered from 400Hz to 80Hz

### 4. Extraction Pipeline Overhaul
- **Onset STFT frame scaling** with frequency (bass gets 4096 vs 256 for treble)
- **Sin() basis** added to noise harmonic subtraction (complete removal vs 50%)
- **8 initializations** in bi-exp fitter (was 4) + random restarts
- **Damping law** R+eta*f^2 global fit → corrects suspect tau values
- **fit_quality** metric per partial (propagated through JSON to GUI)
- **Spectral shape borrowing** from vel 5-7 average → fixes noise-floor
  contamination at low velocity
- **Hammer-contact separation**: onset data used ONLY for A0 peak; bi-exp fit
  runs from main STFT data exclusively (the key breakthrough)

### 5. C++ Velocity Interpolation
- MIDI velocity mapped to continuous float 0.0-7.0
- `lerpNoteParams` interpolates all parameters between adjacent layers
- Architecture ready for 10+ bit hardware velocity

### 6. GUI Improvements
- PaddedPanel RAII container, sectionGap() template, uniform spacing
- Velocity fallback notification (fixed-height, no layout shift)
- Partials table: +2 columns (Q = fit quality color-coded, D = damping derived)
- Window enlarged to 1500x920

### 7. Cleanup
- Removed 15 obsolete NN pipeline/module files
- Removed 5 obsolete docs
- Simplified `run-training.py` to single `analyze` command
- Bank inspector tool (`tools/inspect_bank.py`)

---

## Key Findings

### Onset prepend vs. bi-exp fitting (root cause of "gummy" middle register)
The onset prepend was capturing the hammer contact transient (~first 10-20ms),
which the bi-exp fitter interpreted as a very fast tau1 component.  This caused
59% of middle register notes to have tau1=0.03s (lower bound), meaning 60-80%
of energy disappeared in 30ms → "gummy/muffled" sound.

**MIDI 57 vs 59 comparison** revealed the mechanism: MIDI 57 (on soundboard
resonance) has smooth continuous decay from peak; MIDI 59 (off resonance) has
spike + plateau.  Both are real physics, but onset data in the fit makes the
fitter model the spike as tau1, not the sustained decay.

**Fix**: onset for A0 only, fit from main STFT → tau1 improved from 0.03 to
0.1-4.2s across middle register (bound hit rate: 59% → 12%).

### Velocity-dependent spectral contamination
At low velocity, high partials fall below the analysis noise floor → extractor
fits noise, not signal → A0 values are artificially small → RMS normalization
preserves the dark spectral shape → pp sounds like ff + low-pass filter.

**Measured**: MIDI 36 vel=0 vs vel=7, k=10: 59 dB difference (should be 3-6 dB).

**Fix**: spectral shape borrowing from vel 5-7 average.

### Steinway D validation (pl-grand vs Chabassier)
- Tuning: -0.3 to -0.9% (A≈438-439) — consistent
- Inharmonicity B: 6-15x higher than Chabassier theory (expected for wound strings)
- Partial count: good agreement (60 bass, 29 treble)
- tau1 now in physically correct range after onset/decay separation

---

## Current State — Listening Test Results (latest build)

### What sounds good (score >= 0.7)
- **Treble (MIDI 98-107)**: clean, balanced, correct dynamics
- **MIDI 100**: 0.9 — reference quality
- **MIDI 62**: 0.79 — first good note descending from treble
- **MIDI 46**: 0.78 — faithful bass, good attack and color
- **MIDI 32**: 0.75 — good bass shape and dynamics (slightly "heavy" body)
- **MIDI 52**: 0.6 — decent but lacks clarity

### What sounds bad (score < 0.5)
- **MIDI 77**: 0.30 — too much noise, bad color → **stereo_width = 6.85**
- **MIDI 65**: 0.45 — similar → **stereo_width = 6.12**
- **MIDI 57**: 0.34 — hollow, wrong color, like EQ artifact
- **MIDI 55**: 0.21 — "bottle blowing" begins, loss of clarity
- **MIDI 50**: 0.10 — complete color loss, hollow

### Identified correlations
1. **stereo_width > 2.0** directly correlates with low scores (MIDI 65, 77)
   — S component amplified 6x, causing noise/degradation.
   Fix should be in extraction (EQ fitter stereo measurement), not player clamping.

2. **A_noise decreases bass→treble** (0.33 → 0.98) — opposite of expected.
   Bass hammer noise should be at least as strong as treble.  Low A_noise in
   bass/middle explains weak attack character.

3. **noise_centroid = 1000 Hz everywhere** below MIDI 91 — the floor masks
   real variation.  Treble should have 2-5 kHz centroid.

4. **RMS calibration mismatch**: Python uses 1-pole LPF for noise, C++ uses
   biquad bandpass — systematically different noise energy → wrong rms_gain.

---

## Open Issues (priority order)

### Critical (affects all notes)

**1. Allpass filter is NOT unity-gain**
The audit revealed that the current implementation:
```cpp
y[n] = -g*x[n] + x[n-1] - g*y[n-1]   // piano_math.h:126
```
yields `H(z) = (-g + z^-1) / (1 + g*z^-1)`.  This is NOT an allpass filter
(`|H(e^jw)| != 1`).  The correct Schroeder first-order allpass requires:
```
H(z) = (-g + z^-1) / (1 - g*z^-1)     // correct: note MINUS in denominator
```
which gives the difference equation:
```
y[n] = -g*x[n] + x[n-1] + g*y[n-1]    // note PLUS on feedback term
```
Our original code had `+g*y[n-1]` (correct), our "fix" changed it to
`-g*y[n-1]` (incorrect).  **The fix broke a working allpass into a
frequency-dependent filter.**  Must revert the sign change.

**2. Python/C++ noise model mismatch → wrong rms_gain for ALL notes**
Python RMS calibration renderer (`exporter.py:632-647`) uses 1-pole LPF:
```python
y = alpha * x + (1-alpha) * y    # low-pass, passes everything below centroid
```
C++ playback (`piano_core.cpp:474-477`) uses biquad bandpass:
```cpp
dsp::biquad_tick(noise, v.noise_bpf, v.noise_bpf_L)  // bandpass, rejects both low and high
```
1-pole LPF has broader bandwidth → higher noise energy → Python overestimates
noise RMS → `rms_gain` is calibrated too low → C++ output is systematically
quieter than target.  The `_iir_rms` correction in exporter (lines 410-414)
compounds this: it adjusts for 1-pole characteristics, not bandpass.

**Fix:** Python renderer must use the same biquad bandpass as C++.

### High (affects specific notes)

**3. stereo_width unclamped — values up to 6.85 (FIXED in extraction)**
Root cause: `rms(syn_S)` near zero (synthesis nearly mono) → division
explosion → width_factor 6-8.  Clamped to [0.2, 2.0] in eq_fitter.py.
Real piano S/M ratio is 0.3-1.5; values >2.0 are extraction artifacts.

Second listening test confirmed: MIDI 65 (width=8.0, score=0.44),
MIDI 71 (width=7.13), MIDI 74 (width=4.18, score=0.24) — all "břinkavý",
"praskla struna" character directly caused by extreme stereo width.

**4. lerpNoteParams drops partials when layer counts differ (FIXED)**
Changed `min(K)` to `max(K)` with A0 fade for single-layer partials.

### Medium

**5. A_noise too low for bass** (0.33 vs 0.98 treble)
Bass hammer noise should be at least as strong as treble.  Low A_noise in
bass/middle explains weak attack character.  Likely caused by harmonic
leakage in noise residual despite sin() basis addition.

**6. noise_centroid = 1000 Hz everywhere** below MIDI 91
Floor masks real per-note variation.  Treble hammers are harder → higher
centroid (2-5 kHz).  Bass hammers are softer → lower centroid but still
above 800 Hz.

**7. MIDI 55 "bottle blowing"** — not explained by stereo_width or tau1;
may be EQ artifact or phase coherence issue

---

## Output Naming
Soundbank outputs now have timestamp suffix: `pl-grand-04071430.json`
(MMDDHHmm format) to preserve good iterations.
