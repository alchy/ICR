# ICR — TODO

Critical assessment informed by Chabassier, Chaigne & Joly (2012):
*"Modeling and simulation of a grand piano"* (INRIA RR-8181),
listening tests, and bank inspector analysis.

---

## Current Priority: Next Steps

### P1. Automated quality report (Phase 2b)

**Why first:** We iterate by subjective listening — slow and inconsistent.
An automated spectral distance metric vs original WAV would give objective
scores per note, identify worst offenders, and measure improvement between
iterations without full listening sessions.

**Steps:**
- [ ] Render 12-15 reference notes (one per octave, mid-velocity) via Python
- [ ] Compute spectral distance vs original WAV (MRSTFT or log-spectral)
- [ ] Compute envelope correlation (decay shape match)
- [ ] Output: `quality_report.txt` with per-note scores and overall grade
- [ ] Add `--validate` flag to `run-extract-additive.py analyze`

### P2. Bass noise model (A_noise too low, centroid too uniform)

**Problem from listening test:**
- MIDI 21 (score 0.89): "good texture but weak hammer attack noise"
- Bass A_noise = 0.33 vs treble A_noise = 0.98 — 3x weaker
- noise_centroid = 1000 Hz everywhere below MIDI 91 (floor masks variation)
- Real piano: bass hammer is heavier → stronger "thwack", not weaker

**Root cause:** Harmonic leakage in noise residual.  Even with sin()+cos()
basis, the least-squares subtraction is imperfect for bass (many overlapping
harmonics in 0-5 kHz band).  The residual energy is dominated by harmonic
tails, not noise, so `A_noise` and `centroid_hz` are contaminated.

**Steps:**
- [ ] Improve noise isolation: bandpass noise residual to 2-8 kHz before
      measuring A_noise and centroid (hammer noise lives above harmonics)
- [ ] Remove centroid floor (1000 Hz) — let extraction measure naturally,
      but validate against Chabassier hammer contact physics
- [ ] Consider velocity-dependent centroid: harder hammer = higher centroid

### P3. "Hollow/bottle" tones in MIDI 50-65 range

**Problem from listening test:**
- MIDI 53 (score 0.25): "dutý ton, bez výrazných vyšších harmonických"
- MIDI 55 (score 0.21): "symptom hraní na láhev"
- MIDI 61 (score 0.34): "dutý, chybí průraznost"

**Possible causes (need investigation):**
- Spectral EQ may be cutting upper harmonics (EQ fitted to compensate
  for model deficiency, creating over-correction)
- Onset/decay separation may lose A0 accuracy for some notes (main STFT
  peak may be lower than true onset peak if hammer transient is very short)
- These notes may need more partials with significant energy above k=20

**Steps:**
- [ ] Use bank inspector to compare A0 spectral tilt of good vs bad notes
- [ ] Check if EQ biquads have high-frequency cuts for these notes
- [ ] Compare A0(k) between affected notes and their neighbors

### P4. Weak bass notes (MIDI 25-30)

**Problem from listening test:**
- MIDI 25 (score 0.30), MIDI 27 (score 0.24): "slabý, obly, bez jasných harmonických"
- rms_gain = 0.00020-0.00022 (lowest in entire bank)

**Root cause:** These are the deepest wound bass strings (f0 = 31-39 Hz).
Few velocity layers available.  Low rms_gain means either A0 extraction
underestimated the partials, or RMS calibration overcompensated.

**Steps:**
- [ ] Verify with quality report (P1) — spectral distance vs original
- [ ] Check if spectral shape borrowing is working for these notes
- [ ] Consider per-register rms_gain normalization

---

## Implementation Plan (Phases)

### Phase 1 — Extraction robustness (MOSTLY DONE)

| Step | Status | What |
|------|--------|------|
| 1a | DONE | Global damping law fit R + eta*f^2 |
| 1b | DONE | Derive per-partial tau from damping law |
| 1c | DONE | Noise harmonic subtraction: sin() + cos() basis |
| 1d | DONE | Bi-exp fitter: 8 initializations + random restarts |
| 1e | DONE | Per-note fit_quality metric (propagated to JSON + GUI) |
| 1f | DONE | Onset/decay separation (A0 from onset, fit from main STFT) |
| 1g | DONE | Spectral shape borrowing (vel 5-7 avg → vel 0-4) |
| 1h | DONE | Hammer-contact skip (10ms post-peak) |

**Remaining:** tau1 still hits 0.05 lower bound for ~12% of middle register
notes (MIDI 61, 62, 65).  These may be genuine fast-coupling notes
(soundboard resonance) — needs quality report (P1) to confirm.

### Phase 2 — Validation and tools (PARTIALLY DONE)

| Step | Status | What |
|------|--------|------|
| 2a | DONE | Bank inspector (`tools/inspect_bank.py`) |
| 2b | **NEXT** | Post-export quality report (spectral distance vs WAV) |
| 2c | TODO | `--validate` flag on run-extract-additive.py |
| 2d | TODO | Unit tests for dsp_math.h + additive_synthesis_piano_math.h |

### Phase 3 — Longitudinal string model (TODO)

Goal: metallic precursor "ping" in bass.  Details in previous version.

| Step | What |
|------|------|
| 3a | Extract non-harmonic peaks in first 10-20 ms |
| 3b | Classify: longitudinal (f_longi series) vs phantom (f_m+f_n) vs noise |
| 3c | C++ longitudinal partial synthesis (cosine sum, rapid decay) |
| 3d | Export longitudinal_partials[] to JSON |

### Phase 4 — Hammer excitation model (TODO)

Goal: velocity-dependent timbre via nonlinear hammer force F = K_H*(xi-u)^p.

| Step | What |
|------|------|
| 4a | Extract spectral tilt per velocity |
| 4b | Fit p exponent per note from multi-vel data |
| 4c | Store hammer_p, hammer_K_H per note in JSON |
| 4d | C++ hammer_spectral_weight(k, velocity, p) at noteOn |
| 4e | Replace static A0 with A0 * E(k,vel) |
| 4f | Velocity-dependent noise_centroid |

Chabassier reference: p = 2.27 (bass) to 3.0 (treble), K_H spans 3 orders
of magnitude, hammer strikes at x0/L ≈ 1/8 (null at k=8,16,24).

### Phase 5 — Soundboard and advanced (TODO)

| Step | What |
|------|------|
| 5a | Extract soundboard IR (deconvolution) |
| 5b | Post-synthesis convolution stage |
| 5c | Phantom partial extraction (f_m + f_n) |
| 5d | Sympathetic resonance (open string coupling) |

### Phase 6 — Stereo width extraction reform (TODO)

**Current fix:** stereo_width clamped to [0.2, 2.0] in eq_fitter.py.

**Root cause:** Python synthesis is nearly mono for centered notes
(symmetric pan → S ≈ 0) → width = orig_S/syn_S explodes.
Adding decorrelation to Python renderer would NOT help — real stereo
comes from soundboard radiation, room acoustics, mic placement.

**Long-term fix:** frequency-dependent width — compare S/M only in bands
where synthesis has actual stereo content (beating frequencies, pan offset).
Bands where synthesis is mono → width = 1.0 (ignore).

Deferred to after Phase 4 (hammer model changes spectral content).

---

## Listening Test Findings (latest: pl-grand-04071523)

### Score distribution by register

| Register | MIDI range | Avg score | Best | Worst | Pattern |
|----------|-----------|-----------|------|-------|---------|
| Deep bass | 21-30 | 0.44 | 0.89 (21) | 0.23 (29,30) | Weak gain, missing harmonics |
| Bass | 32-45 | 0.67 | 0.80 (41) | 0.20 (39) | Variable; 39 "chrastivý" |
| Low-mid | 46-56 | 0.48 | 0.68 (52) | 0.25 (53) | "Dutý" tones begin |
| Middle | 57-65 | 0.46 | 0.76 (60) | 0.34 (61) | "Hollow/bottle" problem |
| Upper-mid | 66-77 | 0.52 | 0.79 (71) | 0.30 (77) | "Břinkavý" from stereo_width |
| Upper | 78-90 | 0.69 | 0.98 (88) | 0.40 (89) | Improving toward treble |
| Treble | 91-108 | 0.94 | 0.99 (92,95) | 0.79 (98) | Excellent |

### Key parameter correlations

| Symptom | Parameter | Notes |
|---------|-----------|-------|
| "Břinkavý/praskla struna" | stereo_width > 4.0 | MIDI 65 (8.0), 71 (7.13), 74 (4.18) — FIXED by clamp |
| "Dutý/lahev" | Unknown | MIDI 53, 55, 61 — needs investigation (P3) |
| Weak/quiet | rms_gain < 0.0003 | MIDI 25, 27 — lowest in bank |
| "Chrastivý" | Unknown | MIDI 39 — isolated, possible extraction artifact |
| Good bass | tau1 0.3-1.0, a1 0.4-0.6 | MIDI 32, 40, 41, 46 |
| Good treble | Few partials, high A_noise | MIDI 88-95 — simple signal, strong noise attack |

---

## Operational Issues

### Tests
- [ ] `tests/test_dsp_math.cpp` — biquad, decay_coeff, RBJ shelves, allpass unity gain
- [ ] `tests/test_piano_math.cpp` — string models, envelope, rise
- [ ] Python: synthetic signal → extractor → verify parameters
- [ ] CMake test target (`ctest`)

### Build / environment
- [ ] `requirements.txt` with pinned versions
- [ ] Evaluate removing torch dependency (unused DifferentiableRenderer)

### Tools
- [x] `tools/inspect_bank.py` — per-register summary + note detail
- [ ] `tools/compare_banks.py` — diff two soundbanks

---

## Hardware Velocity Resolution

MIDI velocity is 7-bit (1-127).  C++ maps to continuous float 0.0-7.0
(`midiVelToFloat`) and interpolates all parameters via `lerpNoteParams`.
Architecture ready for 10+ bit hardware velocity.

---

## Validation: pl-grand vs. Chabassier Steinway D

| Parameter | Chabassier | Our extraction | Status |
|-----------|-----------|----------------|--------|
| Tuning | A=440 | A≈438-439 (-5 to -15 cents) | OK (different tuning) |
| B (D#1) | ~3.5e-5 (core theory) | 5.3e-4 (measured) | Expected (wound strings) |
| B (C2) | ~3.6e-5 | 2.2e-4 | Expected |
| tau1 (D#1) | ~2.2 s | 0.66 s | Short (mf vs ff dynamics) |
| tau1 (C2) | ~1.5 s | 0.46 s | Short (same) |
| tau1 (C#5) | ~0.6 s | 0.41 s | OK (was 0.01, fixed) |
| Partials (bass) | 50-60 | 60 | Good |
| Partials (treble) | 20-30 | 29 | Good |

---

## Physics Reference (Chabassier et al. 2012)

### String parameters (Steinway D)

| Note | L (m) | d (mm) | F | T0 (N) | f0 (Hz) | p | K_H | M_H (g) |
|------|-------|--------|---|--------|---------|---|-----|---------|
| D#1 | 1.945 | 1.48 | 5.73 | 1781 | 38.9 | 2.4 | 4.0e8 | 12.0 |
| C2 | 1.600 | 0.95 | 3.55 | 865 | 65.4 | 2.27 | 2.0e9 | 10.2 |
| F3 | 0.961 | 1.05 | 1.0 | 774 | 174.6 | 2.4 | 1.0e9 | 9.0 |
| C#5 | 0.326 | 0.92 | 1.0 | 684 | 555.6 | 2.6 | 2.8e10 | 7.9 |
| G6 | 0.124 | 0.79 | 1.0 | 587 | 1571 | 3.0 | 2.3e11 | 6.77 |

### Key physics
- Hammer stiffness K_H: 4e8 (bass) to 2.3e11 (treble)
- Nonlinear exponent p: 2.27 (bass) to 3.0 (treble)
- Hammer velocity: 0.5 m/s (pp) to 4.5 m/s (ff)
- Wave speed: transverse 209 m/s, longitudinal 2914 m/s (C2)
- Damping: R_u + eta_u * f^2
- Hammer contact: bass ~4 ms, middle ~2 ms, treble <1 ms

### Onset prepend vs. bi-exp fitting — root cause analysis

Onset prepend captures hammer contact transient (~10-20ms), which the
bi-exp fitter interprets as very fast tau1.  MIDI 57 (smooth decay from
peak → tau1=0.03) vs MIDI 59 (spike + plateau → tau1=1.58) showed
the mechanism is soundboard coupling at resonance.

**Fix:** onset for A0 peak only, bi-exp fit from main STFT data.
Reduced bound-hit rate from 59% to 12% of middle register.

---

## Recently Completed

- [x] DSP math refactor (`dsp_math.h` + `additive_synthesis_piano_math.h`)
- [x] Allpass sign fix (reverted incorrect "fix" — `+g*y[n-1]` is correct)
- [x] GUI: velocity fallback display, PaddedPanel, fit_quality columns
- [x] Onset STFT frame scaling for bass
- [x] Attack rise envelope (1-exp(-t/tau_rise)), noise bypasses onset gate
- [x] Noise filter: 1-pole → biquad bandpass (Q=1.5)
- [x] Python/C++ noise model match (both use biquad BP for RMS calibration)
- [x] attack_tau capped at 0.10s, eq_freq_min lowered to 80Hz
- [x] Onset/decay separation (A0 from onset, fit from main STFT)
- [x] Spectral shape borrowing (vel 5-7 avg, extends to max ref length)
- [x] Velocity interpolation: float 0.0-7.0, lerpNoteParams with max(K) + fade
- [x] Damping law correction R+eta*f^2 (catches tau1 < 0.02 and > 10s)
- [x] Bi-exp fitter: 8 inits, tau1 floor 0.05s, fit_quality metric
- [x] stereo_width clamped to [0.2, 2.0] in EQ fitter extraction
- [x] Bank inspector tool
- [x] Timestamped soundbank output (MMDDHHmm suffix)
- [x] Cleanup: removed 15 NN files, 5 docs, simplified run-extract-additive.py
