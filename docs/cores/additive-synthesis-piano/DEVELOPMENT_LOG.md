# ICR — Development Log

Chronological record of findings, fixes, and physics references.
For current priorities see [TODO.md](TODO.md).
For engine architecture see [ARCHITECTURE.md](../../engine/ARCHITECTURE.md).

---

## Physics References (Literature)

### Chabassier, Chaigne & Joly (2012) — INRIA RR-8181

*"Modeling and simulation of a grand piano"* — full physical model
of a Steinway D: nonlinear string, hammer interaction, soundboard,
air coupling.  Key parameters used for validation:

| Note | L (m) | d (mm) | F | T0 (N) | f0 (Hz) | p | K_H | M_H (g) |
|------|-------|--------|---|--------|---------|---|-----|---------|
| D#1 | 1.945 | 1.48 | 5.73 | 1781 | 38.9 | 2.4 | 4.0e8 | 12.0 |
| C2 | 1.600 | 0.95 | 3.55 | 865 | 65.4 | 2.27 | 2.0e9 | 10.2 |
| F3 | 0.961 | 1.05 | 1.0 | 774 | 174.6 | 2.4 | 1.0e9 | 9.0 |
| C#5 | 0.326 | 0.92 | 1.0 | 684 | 555.6 | 2.6 | 2.8e10 | 7.9 |
| G6 | 0.124 | 0.79 | 1.0 | 587 | 1571 | 3.0 | 2.3e11 | 6.77 |

Key physics:
- Hammer force: `F = K_H * (xi - u)^p` (nonlinear, velocity-dependent timbre)
- String damping: `R_u + eta_u * f^2` (constant + quadratic)
- Longitudinal wave: c=2914 m/s (14x transverse for C2), precursor
- Phantom partials at f_m + f_n from geometrical nonlinearity

### Cross-paper analysis (18 papers)

Implemented principles (confirmed across multiple sources):
- Inharmonicity f_k = k*f0*sqrt(1+B*k^2)
- Bi-exponential decay a1*exp(-t/tau1) + (1-a1)*exp(-t/tau2)
- Inter-string beating (1/2/3-string models)
- Frequency-dependent decay (per-partial tau)
- Per-note spectral EQ from recorded data
- Velocity layer interpolation
- Noise at attack (bandpass filtered, decaying)
- Stereo: per-string pan + M/S width + Schroeder allpass

Missing principles (priority order from literature):
1. Phantom/longitudinal partials (8/18 papers)
2. Velocity-dependent spectral shape (5/18 papers)
3. Soundboard modal transients (5/18 papers)
4. Physical hammer model (4/18 papers)
5. Pitch glide at attack (3/18 papers)

---

## Validation: pl-grand vs. Chabassier Steinway D

| Parameter | Chabassier | Our extraction | Status |
|-----------|-----------|----------------|--------|
| Tuning | A=440 | A=438-439 | OK (different tuning) |
| B (D#1) | ~3.5e-5 (core) | 5.3e-4 (measured) | Expected (wound strings) |
| tau1 (D#1) | ~2.2 s | 0.66 s | Short (mf vs ff) |
| tau1 (C#5) | ~0.6 s | 0.41 s | OK (was 0.01, fixed) |
| Partials | 50-60 (bass) | 60 | Good |

---

## Key Findings (Session 2026-04-06/07)

### Onset prepend vs. bi-exp fitting

Onset STFT captures hammer contact transient (~10-20ms). Bi-exp fitter
interprets this as very fast tau1 → 59% of middle register notes had
tau1=0.03 (lower bound).

MIDI 57 (on soundboard resonance): smooth decay from peak → tau1=0.03.
MIDI 59 (off resonance): spike + plateau → tau1=1.58.

**Fix:** onset for A0 peak only, bi-exp fit from main STFT data.
Bound-hit rate: 59% → 12%.

### Velocity spectral contamination

At low velocity, high partials below noise floor → extractor fits noise →
A0 artificially small → RMS normalization preserves dark spectrum →
pp sounds like ff + low-pass filter.

MIDI 36 vel=0 vs vel=7, k=10: 59 dB difference (should be 3-6 dB).

**Fix:** spectral shape borrowing from vel 5-7 average.

### Allpass sign regression

Original code: `y[n] = -g*x[n] + x[n-1] + g*y[n-1]` (correct allpass).
"Fix" changed to `-g*y[n-1]` (NOT allpass). Reverted.

### Soundboard transfer function

Pure additive synthesis has 20-85 dB less energy than original recording
in mid-frequency bands.  This energy comes from soundboard resonance,
room, and string-bridge coupling.

**Fix:** soundboard IR convolution (25ms, 0-4% mix).

### Stereo width explosion

EQ fitter computed `width = orig_S/orig_M / (syn_S/syn_M)`.
For centered notes, syn_S ≈ 0 → division explosion → width=6-8.

**Fix:** clamp to [0.2, 2.0] in extraction. Long-term: frequency-dependent
width (compare S/M only in bands where synthesis has stereo content).

---

## Listening Test Scores (pl-grand-04071611)

### By register

| Register | MIDI | Avg | Best | Worst |
|----------|------|-----|------|-------|
| Deep bass | 21-30 | 0.52 | 0.90 (21,26) | 0.23 (29) |
| Bass | 32-45 | 0.66 | 0.90 (38) | 0.40 (40) |
| Low-mid | 46-56 | 0.48 | 0.69 (49) | 0.34 (50) |
| Middle | 57-65 | 0.55 | 0.98 (62) | 0.30 (57) |
| Upper | 66-84 | 0.60 | 0.87 (84) | 0.30 (74) |
| Treble | 85-108 | 0.97 | 0.99 (91,96) | 0.89 (89) |

### Parameter correlations

| Symptom | Parameter | Notes |
|---------|-----------|-------|
| "Brinkava/praskla struna" | stereo_width > 4.0 | Fixed by clamp |
| "Dull/lahev" | Missing 1-2 kHz energy | Extraction issue for some notes |
| Weak/quiet | rms_gain < 0.0003 | Bass notes with few vel layers |
| Good treble | Few partials, high A_noise | Simple signal, strong attack |
| "Rezonujici" | width=2.0 (clamp) + fast tau1 | Over-bright synthesis |
