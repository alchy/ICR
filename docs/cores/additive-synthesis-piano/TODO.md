# ICR -- TODO

Critical assessment informed by Chabassier, Chaigne & Joly (2012):
*"Modeling and simulation of a grand piano"* (INRIA RR-8181),
listening tests, and bank inspector analysis.

---

## Current Priority: Next Steps

### P1. Automated quality report (Phase 2b)

**Why first:** We iterate by subjective listening -- slow and inconsistent.
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
- Bass A_noise = 0.33 vs treble A_noise = 0.98 -- 3x weaker
- Real piano: bass hammer is heavier -> stronger "thwack", not weaker

**Root cause:** Harmonic leakage in noise residual. Even with sin()+cos()
basis, the least-squares subtraction is imperfect for bass. The residual
energy is dominated by harmonic tails, not noise, so A_noise and centroid_hz
are contaminated.

**Mitigation (DONE):** Physics-based noise centroid/tau override
(`hammer_noise_centroid`, `hammer_attack_tau` in math.h) provides a floor
for centroid and ceiling for tau at noteOn. This ensures minimum metallic
brightness even when extracted values are contaminated. Noise amplitude is
scaled down proportionally when centroid is raised (`sqrt(bank/phys)` ratio).

**Remaining:**
- [ ] Improve noise isolation: bandpass noise residual to 2-8 kHz before
      measuring A_noise and centroid (hammer noise lives above harmonics)

### P3. "Hollow/bottle" tones in MIDI 50-65 range

**Problem from listening test:**
- MIDI 53 (score 0.25): "duty ton, bez vyraznych vyssich harmonickych"
- MIDI 55 (score 0.21): "symptom hrani na lahev"
- MIDI 61 (score 0.34): "duty, chybi pruraznost"

**Root cause identified:** Source recording (pl-grand) has genuinely weak
upper harmonics for MIDI 50-65. Cross-piano comparison shows ks-grand (CFX)
has 7-10x richer k5/k1 ratios. The extraction IS the measurement -- see
[EXTRACTION_AUDIT.md](EXTRACTION_AUDIT.md) for details.

**Mitigations applied:**
- Forte spectral shape normalization (DONE) -- higher-velocity layers
  boost weak partials in lower-velocity layers
- Hybrid bank generation (DONE) -- `tools/synthesize_hybrid_bank.py`
  borrows spectral shape from ks-grand for deficient pl-grand notes

**Remaining:**
- [ ] Physics floor extraction option (re-extract with physics_floor_enabled)
- [ ] Cross-note spectral shape borrowing (borrow from MIDI neighbors)

### P4. Weak bass notes (MIDI 25-30)

**Problem from listening test:**
- MIDI 25 (score 0.30), MIDI 27 (score 0.24): "slaby, obly, bez jasnych harmonickych"
- rms_gain = 0.00020-0.00022 (lowest in entire bank)

**Steps:**
- [ ] Verify with quality report (P1) -- spectral distance vs original
- [ ] Check if spectral shape borrowing is working for these notes
- [ ] Consider per-register rms_gain normalization

---

## Implementation Plan (Phases)

### Phase 1 -- Extraction robustness (DONE)

| Step | Status | What |
|------|--------|------|
| 1a | DONE | Global damping law fit R + eta*f^2 |
| 1b | DONE | Derive per-partial tau from damping law |
| 1c | DONE | Noise harmonic subtraction: sin() + cos() basis |
| 1d | DONE | Bi-exp fitter: 8 initializations + random restarts |
| 1e | DONE | Per-note fit_quality metric (propagated to JSON + GUI) |
| 1f | DONE | Onset/decay separation (A0 from onset, fit from main STFT) |
| 1g | DONE | Spectral shape borrowing (vel 5-7 avg -> vel 0-4) |
| 1h | DONE | Hammer-contact skip (10ms post-peak) |

**Note:** tau1 still hits 0.05 lower bound for ~12% of middle register
notes (MIDI 61, 62, 65). These may be genuine fast-coupling notes
(soundboard resonance) -- needs quality report (P1) to confirm.

### Phase 2 -- Validation and tools (PARTIALLY DONE)

| Step | Status | What |
|------|--------|------|
| 2a | DONE | Bank inspector (`tools/inspect_bank.py`) |
| 2b | **NEXT** | Post-export quality report (spectral distance vs WAV) |
| 2c | TODO | `--validate` flag on run-extract-additive.py |
| 2d | TODO | Unit tests for dsp_math.h + additive_synthesis_piano_math.h |
| 2e | DONE | Hybrid bank synthesis (`tools/synthesize_hybrid_bank.py`) |

### Phase 3 -- Velocity dynamics and normalization (DONE)

| Step | Status | What |
|------|--------|------|
| 3a | DONE | vel_gain = pow(vel_norm, 1.5) curve (~23 dB range) |
| 3b | DONE | Forte rms_gain reference (always use highest valid vel layer) |
| 3c | DONE | Forte spectral shape normalization (A0 ratios from forte) |
| 3d | REVERTED | Body parameter -- removed, sounds better without |
| 3e | DONE | EQ gain floor (-3 dB, prevents over-muffling) |
| 3f | DONE | Physics-based noise centroid/tau override (metallic attack) |
| 3g | IMPL/OFF | vel_spectral_weight -- implemented in math.h but not called in core (forte normalization handles the need) |

### Phase 4 -- Longitudinal string model (TODO)

Goal: metallic precursor "ping" in bass.

| Step | What |
|------|------|
| 4a | Extract non-harmonic peaks in first 10-20 ms |
| 4b | Classify: longitudinal (f_longi series) vs phantom (f_m+f_n) vs noise |
| 4c | C++ longitudinal partial synthesis (cosine sum, rapid decay) |
| 4d | Export longitudinal_partials[] to JSON |

### Phase 5 -- Hammer excitation model (TODO)

Goal: velocity-dependent timbre via nonlinear hammer force F = K_H*(xi-u)^p.

| Step | What |
|------|------|
| 5a | Extract spectral tilt per velocity |
| 5b | Fit p exponent per note from multi-vel data |
| 5c | Store hammer_p, hammer_K_H per note in JSON |
| 5d | C++ hammer_spectral_weight(k, velocity, p) at noteOn |
| 5e | Replace static A0 with A0 * E(k,vel) |

Chabassier reference: p = 2.27 (bass) to 3.0 (treble), K_H spans 3 orders
of magnitude, hammer strikes at x0/L ~ 1/8 (null at k=8,16,24).

### Phase 6 -- Soundboard and advanced (TODO)

| Step | What |
|------|------|
| 6a | Extract soundboard IR (deconvolution) |
| 6b | Post-synthesis convolution stage |
| 6c | Phantom partial extraction (f_m + f_n) |
| 6d | Sympathetic resonance (open string coupling) |

### Phase 7 -- Stereo width extraction reform (TODO)

**Current fix:** stereo_width clamped to [0.2, 2.0] in eq_fitter.py.

**Root cause:** Python synthesis is nearly mono for centered notes
(symmetric pan -> S ~ 0) -> width = orig_S/syn_S explodes. Real stereo
comes from soundboard radiation, room acoustics, mic placement.

**Long-term fix:** frequency-dependent width -- compare S/M only in bands
where synthesis has actual stereo content (beating frequencies, pan offset).
Bands where synthesis is mono -> width = 1.0 (ignore).

Deferred to after Phase 5 (hammer model changes spectral content).

---

## Listening Test Findings (latest: pl-grand-04071523)

### Score distribution by register

| Register | MIDI range | Avg score | Best | Worst | Pattern |
|----------|-----------|-----------|------|-------|---------|
| Deep bass | 21-30 | 0.44 | 0.89 (21) | 0.23 (29,30) | Weak gain, missing harmonics |
| Bass | 32-45 | 0.67 | 0.80 (41) | 0.20 (39) | Variable; 39 "chrastivy" |
| Low-mid | 46-56 | 0.48 | 0.68 (52) | 0.25 (53) | "Duty" tones begin |
| Middle | 57-65 | 0.46 | 0.76 (60) | 0.34 (61) | "Hollow/bottle" problem |
| Upper-mid | 66-77 | 0.52 | 0.79 (71) | 0.30 (77) | "Brinkava" from stereo_width |
| Upper | 78-90 | 0.69 | 0.98 (88) | 0.40 (89) | Improving toward treble |
| Treble | 91-108 | 0.94 | 0.99 (92,95) | 0.79 (98) | Excellent |

### Key parameter correlations

| Symptom | Parameter | Notes |
|---------|-----------|-------|
| "Brinkava/praskla struna" | stereo_width > 4.0 | MIDI 65 (8.0), 71 (7.13), 74 (4.18) -- FIXED by clamp |
| "Duty/lahev" | Weak source harmonics | MIDI 53, 55, 61 -- root cause: pl-grand recording |
| Weak/quiet | rms_gain < 0.0003 | MIDI 25, 27 -- lowest in bank |
| "Chrastivy" | Unknown | MIDI 39 -- isolated, possible extraction artifact |
| Good bass | tau1 0.3-1.0, a1 0.4-0.6 | MIDI 32, 40, 41, 46 |
| Good treble | Few partials, high A_noise | MIDI 88-95 -- simple signal, strong noise attack |

---

## Sound Editor -- Future Features

### SE1. Note Compare & Correct (Source -> Destination)

Interactive tool for fixing problematic notes by referencing good ones.
Per-parameter comparison with deviation % and correction sliders.

- [ ] Backend: `/editor/compare` endpoint
- [ ] Backend: `/editor/correct` endpoint
- [ ] Frontend: source/dest selectors, deviation table, correction sliders

### SE2. MIDI Audition (Note Playback via Loopback)

Send MIDI noteOn/noteOff from the editor to ICR for instant listening.

- [ ] Backend: `/midi/audition` endpoint
- [ ] Frontend: click-to-play on note cards

---

## Operational Issues

### Tests
- [ ] `tests/test_dsp_math.cpp` -- biquad, decay_coeff, RBJ shelves, allpass unity gain
- [ ] `tests/test_piano_math.cpp` -- string models, envelope, rise
- [ ] Python: synthetic signal -> extractor -> verify parameters
- [ ] CMake test target (`ctest`)

### Build / environment
- [ ] `requirements.txt` with pinned versions
- [ ] Evaluate removing torch dependency (unused DifferentiableRenderer)

### Tools
- [x] `tools/inspect_bank.py` -- per-register summary + note detail
- [x] `tools/synthesize_hybrid_bank.py` -- hybrid bank from pl-grand + ks-grand
- [ ] `tools/compare_banks.py` -- diff two soundbanks

---

## Hardware Velocity Resolution

MIDI velocity is 7-bit (1-127). C++ maps to continuous float 0.0-7.0
(`midiVelToFloat`) and interpolates all parameters via `lerpNoteParams`.
Architecture ready for 10+ bit hardware velocity.

---

## Validation: pl-grand vs. Chabassier Steinway D

| Parameter | Chabassier | Our extraction | Status |
|-----------|-----------|----------------|--------|
| Tuning | A=440 | A~438-439 (-5 to -15 cents) | OK (different tuning) |
| B (D#1) | ~3.5e-5 (core theory) | 5.3e-4 (measured) | Expected (wound strings) |
| B (C2) | ~3.6e-5 | 2.2e-4 | Expected |
| tau1 (D#1) | ~2.2 s | 0.66 s | Short (mf vs ff dynamics) |
| tau1 (C2) | ~1.5 s | 0.46 s | Short (same) |
| tau1 (C#5) | ~0.6 s | 0.41 s | OK (was 0.01, fixed) |
| Partials (bass) | 50-60 | 60 | Good |
| Partials (treble) | 20-30 | 29 | Good |

---

## Recently Completed

- [x] Velocity dynamics curve: vel_gain = pow(vel_norm, 1.5), ~23 dB range
- [x] Forte rms_gain reference (always from highest valid velocity layer)
- [x] Forte spectral shape normalization (A0 ratios from forte, only boost)
- [x] Physics-based noise centroid/tau override (metallic hammer attack)
- [x] EQ gain floor (-3 dB, prevents aggressive EQ from muffling)
- [x] Body parameter (implemented then reverted -- better without)
- [x] Hybrid bank synthesis tool (`tools/synthesize_hybrid_bank.py`)
- [x] DSP math refactor (`dsp_math.h` + `additive_synthesis_piano_math.h`)
- [x] Allpass sign fix (reverted incorrect "fix" -- `+g*y[n-1]` is correct)
- [x] GUI: velocity fallback display, PaddedPanel, fit_quality columns
- [x] Onset STFT frame scaling for bass
- [x] Attack rise envelope (1-exp(-t/tau_rise)), noise bypasses onset gate
- [x] Noise filter: 1-pole -> biquad bandpass (Q=1.5)
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
