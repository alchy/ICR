# ICR — TODO

Critical project assessment and planned improvements, ordered by impact.

---

## Structural Issues

### 1. Envelope-only model — no excitation model

The current synthesis is `A0 * envelope * cos(phase)` — a static spectral
shape per velocity layer.  Real piano has nonlinear hammer-string interaction:

- **Velocity-dependent spectral shape:** harder strike = brighter, not just louder.
  Currently A0 is fixed per velocity layer with no interpolation between layers.
- **Longitudinal precursor:** metallic "ping" in bass from longitudinal string
  vibration — not modeled at all.
- **Phantom partials:** non-harmonic frequency components from transverse-to-
  longitudinal coupling — not modeled.

**Steps:**
- [ ] Add velocity-weighted A0 interpolation between the 8 layers (short-term)
- [ ] Research extraction of longitudinal mode frequencies from WAV (medium-term)
- [ ] Evaluate adding phantom partial detection to extractor (long-term)

### 2. Fragile extraction pipeline

The entire sound quality depends on how well `extractor.py` decomposes
recordings into parameters.  Known weaknesses:

- **Noise harmonic subtraction** uses only `cos()` basis — removes at most 50%
  of harmonic content from the residual.  Noise estimates are contaminated.
- **Bi-exp fitter** has 4 initializations but no guarantee of global optimum.
  Some notes may get suboptimal tau1/tau2 splits.
- **Spectral EQ** fits the difference between model and recording — if the
  model is wrong, EQ compensates in the wrong direction.

**Steps:**
- [ ] Add `sin()` basis to harmonic subtraction in `_analyze_noise`
- [ ] Increase bi-exp fitter initializations to 8, add random restarts
- [ ] Add per-note fit quality metric (residual energy / total energy)
- [ ] Log warnings when fit quality is below threshold

### 3. No quality gates in pipeline

Pipeline `extract → filter → export` has zero automatic validation:

- No comparison of synthesized output vs original recording
- No spectral distance metric (MRSTFT or similar)
- No envelope match score
- Outlier filter catches statistical anomalies but not systematic errors
  (those affect entire registers equally and pass through undetected)

**Steps:**
- [ ] Add post-export validation: synthesize 10-15 reference notes, compute
      spectral distance vs original WAV
- [ ] Define quality thresholds: warn if any note exceeds threshold
- [ ] Add `--validate` flag to `run-training.py analyze`
- [ ] Generate quality report (per-note scores, worst offenders, register heatmap)

### 4. Heuristic stereo model

Stereo parameters are not physically calibrated:

- Constant-power pan from MIDI note: `center = π/4 + (midi-64.5)/87 * spread`
- Schroeder allpass decorrelation with hand-tuned coefficients
- M/S stereo width from EQ fitter

None of these reflect the actual piano: soundboard radiation pattern,
mic placement, bridge geometry — all ignored.

**Steps:**
- [ ] Extract L/R amplitude ratio per partial from stereo WAV recordings
- [ ] Fit decorrelation parameters from measured inter-channel phase differences
- [ ] Consider per-note stereo calibration instead of keyboard-wide formula

### 5. Missing soundboard / body response

Current model: partials + noise + EQ.  Real piano sound shaped by:

- **Soundboard impedance** — creates the double-decay (prompt/aftersound).
  Partially captured by bi-exp envelope, but coupling strength varies per note.
- **Sympathetic resonance** — open strings resonate with played notes.
  Not modeled (would require tracking all active voices + open strings).
- **Damper noise, pedal noise, key mechanism** — absent.

**Steps:**
- [ ] Extract soundboard IR from recordings (deconvolution of known excitation)
- [ ] Evaluate convolution reverb as post-processing stage (design exists in
      removed CONVOLUTION_REVERB.md — can be recovered from git history)
- [ ] Add simple sympathetic resonance: when a note plays, add low-level
      energy to harmonically related open strings (long-term)

---

## Operational Issues

### 6. No tests

Zero test coverage.  `piano_math.h` and `dsp_math.h` are now cleanly
separated and fully testable, but no tests exist.

**Steps:**
- [ ] Create `tests/test_dsp_math.cpp` — verify biquad, decay_coeff, RBJ
      shelves against known-good values (scipy.signal reference)
- [ ] Create `tests/test_piano_math.cpp` — verify string models, envelope,
      allpass, M/S width with hand-computed examples
- [ ] Add Python test for extractor: feed known synthetic signal, verify
      extracted parameters match within tolerance
- [ ] Add CMake test target (`ctest`)

### 7. Build depends on specific venv

Python 3.12 + torch.  No CI, no Docker, no pinned requirements.

**Steps:**
- [ ] Create `requirements.txt` with pinned versions
- [ ] Document venv312 setup in TRAIN_BUILD_RUN.md
- [ ] Consider removing torch dependency (only used by synthesizer for
      DifferentiableRenderer which is now unused)

### 8. Large opaque soundbank files

JSON with 704 notes × 60 partials × 8 parameters = multi-megabyte files.
No quick inspection tool beyond GUI (which shows only last played note).

**Steps:**
- [ ] Add `tools/inspect_bank.py` — print summary statistics per register
      (partial count, tau ranges, noise params, EQ shape)
- [ ] Add `tools/compare_banks.py` — diff two soundbanks, highlight changes
- [ ] Consider binary format for faster C++ loading (JSON parse is slow for
      large banks on embedded/low-power targets)

---

## Quick Wins (high impact, low effort)

| # | Task | Effort | Impact |
|---|------|--------|--------|
| A | Velocity interpolation between 8 layers | 1 day | Smooth dynamics, no staircase |
| B | Unit tests for math headers | 1 day | Catch regressions, document behavior |
| C | Post-export quality report | 2 days | Detect bad extractions automatically |
| D | Bank inspector tool | 0.5 day | Fast debugging of parameter issues |
| E | Remove torch dependency | 0.5 day | Simpler install, no venv312 requirement |

---

## Recently Completed

- [x] Extract DSP math into `dsp_math.h` + `piano_math.h` (refactor)
- [x] Fix Schroeder allpass sign error (`-g*y` → `+g*y`)
- [x] Fix GUI velocity fallback display bug
- [x] GUI refactor: PaddedPanel, sectionGap, grid layout
- [x] Onset STFT frame scaling for bass (extractor.py)
- [x] Attack rise envelope in C++ (1-exp(-t/tau_rise))
- [x] Onset gate reduced from 3ms to 0.5ms, noise bypasses gate
- [x] attack_tau hard-capped at 0.10s
- [x] eq_freq_min lowered from 400Hz to 80Hz
- [x] Noise filter upgraded from 1-pole to biquad bandpass
- [x] Peak frame detection without smoothing for short onsets
- [x] Cleanup: removed 15 obsolete NN pipeline/module files
- [x] Cleanup: removed 5 obsolete docs, rewrote TRAINING_MODULES + TRAIN_BUILD_RUN
- [x] Simplified run-training.py to single `analyze` command
