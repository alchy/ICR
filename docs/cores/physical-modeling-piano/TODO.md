# PhysicalModelingPianoCore -- TODO

## Done (v3)

### String model
- [x] Single-loop Karplus-Strong waveguide (full-period delay, no sign inversion)
- [x] Bipolar Fourier excitation (DC-removed, all harmonics)
- [x] Two-stage spectral rolloff (knee_k + knee_slope)
- [x] Odd harmonic boost (metallic character)
- [x] String gauge (thickness → excitation shaping + HF damping)
- [x] One-pole loss filter from T60 (Smith/Bank design)
- [x] Allpass dispersion cascade (3-16 stages, buzz-free)
- [x] Fractional delay allpass (tuning)
- [x] 8 rounds of listening tests → optimized parameters

### Infrastructure
- [x] Physical soundbank JSON format (15 per-note params)
- [x] Bank generator (`tools/generate_physical_bank.py`)
- [x] Python test renderer (`tests/test_string.py`)
- [x] Soundboard IR auto-load from `icr-config.json`
- [x] Commuted synthesis (no internal soundboard modes, DspChain IR)
- [x] JSON schema documentation
- [x] C++ core rewritten from validated Python model (v0.3)
- [x] Per-note params loaded from JSON bank in `load()`
- [x] Dispersion delay compensation (correct tuning with allpass cascade)
- [x] `sound-editor` renamed to `sound-editor-additive`
- [x] `loadBankJson()` for SysEx SET_BANK support

### Findings from listening tests (R1-R8)
- Triangle excitation → saw artifacts (sharp edges persist)
- Fourier series excitation → clean from sample 0
- exc_rolloff 2.0 = nylon, 0.1 = steel
- odd_boost 1.5-2.0 = metallic character (physically correct)
- Loss filter pole 0.03 = too weak (sine), 0.4 = OK, 0.9 = too aggressive
- T60_nyq 40ms = too aggressive, 300ms = too gentle, 80-250ms = sweet spot
- Dispersion: single allpass → buzz, cascade of 3+ stages → clean
- gauge 2-3 for bass, 1.5 for mid = hutnejsi sound
- knee_k=10, knee_slope=3-4 = smooth waveform with rich body

---

## Current iteration: single string in C++ core

### P1. Port Python model to C++ — DONE
- [x] Rewrite `initVoice()` with Fourier excitation from bank params
- [x] Rewrite `process()` with single-loop KS (no sign inversion)
- [x] Load per-note params from physical bank JSON in `load()`
- [x] GUI scalers: brightness, stiffness, sustain, odd_emphasis, gauge, spread
- [x] Dispersion group delay compensation for correct tuning
- [ ] Global scalers not yet applied at noteOn (stored but unused)

### P2. SysEx for physical bank
- [ ] Define param IDs for core_id=0x02
- [ ] Implement `setNoteParam()` in C++
- [ ] Implement `loadBankJson()` for physical bank format
- [ ] Document in SYSEX_PARAMS.md

### P3. Physical bank editor
- [ ] `sound-editor-physical/` — standalone app
- [ ] Piano keyboard note selector
- [ ] Per-note parameter sliders (15 params)
- [ ] Global scalers (gauge, brightness, stiffness, sustain, detune)
- [ ] SysEx live preview
- [ ] MIDI audition
- [ ] Bank load/save
- [ ] `run-editor-physical.py` launcher

---

## Future iterations

### Multi-string beating
- [ ] 2-3 parallel waveguides per voice (from `n_strings`)
- [ ] Per-string detuning (from `detune_cents`)
- [ ] Independent noise per string
- [ ] Bridge coupling (optional: cross-string feedback)

### Hammer model
- [ ] Pre-computed hammer force signal (Chaigne/Askenfelt FD model)
- [ ] Velocity-dependent spectral shape (harder hit = brighter)
- [ ] Replace Fourier excitation with hammer output for more physical onset
- [ ] Hammer noise burst at attack

### Advanced physics
- [ ] Multi-pole loss filter (order 4-8 IIR from measured decay times)
- [ ] Physical damper model (increase loss on release, not linear fadeout)
- [ ] Sympathetic resonance (open strings vibrate when neighbors play)
- [ ] Longitudinal string modes (metallic "ping" in bass)
- [ ] Duplex scaling (string segments beyond bridge)

### Calibration from recordings
- [ ] Use additive extraction data (tau1/tau2, B, beat_hz) to calibrate
  physical bank parameters per note
- [ ] Spectral EQ post-waveguide from extracted eq_biquads
- [ ] Output level calibration from rms_gain
