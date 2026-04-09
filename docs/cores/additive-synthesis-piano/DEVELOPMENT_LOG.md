# ICR -- Development Log

Chronological record of findings, fixes, and physics references.
For current priorities see [TODO.md](TODO.md).
For engine architecture see [ARCHITECTURE.md](../../engine/ARCHITECTURE.md).

---

## Physics References (Literature)

### Chabassier, Chaigne & Joly (2012) -- INRIA RR-8181

*"Modeling and simulation of a grand piano"* -- full physical model
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
- Velocity layer interpolation with forte normalization
- Noise at attack (bandpass filtered, decaying, physics-based centroid/tau)
- Stereo: per-string pan + M/S width + Schroeder allpass
- Velocity dynamics curve (vel_gain = pow(vel_norm, 1.5))

Missing principles (priority order from literature):
1. Phantom/longitudinal partials (8/18 papers)
2. Soundboard modal transients (5/18 papers)
3. Physical hammer model (4/18 papers)
4. Pitch glide at attack (3/18 papers)

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

## Session 2026-04-09/10 -- Velocity Dynamics, Forte Normalization, Metallic Attack

### Velocity dynamics curve

Bank velocity layers are RMS-normalized to similar loudness (required for
accurate spectral extraction). This removes natural dynamics. Re-introduced
at noteOn with:

```
vel_norm = midiVelToFloat(velocity) / 7.0   // 0.0 to 1.0
vel_gain = pow(vel_norm, 1.5)               // ~23 dB dynamic range
```

`vel_gain` multiplies both `A0_scaled` (per-partial) and `A_noise_sc` (noise
amplitude). The 1.5 exponent was chosen by listening -- gives natural piano
dynamics without excessive pp/ff contrast.

### Forte rms_gain and spectral shape normalization

**Problem:** Bank velocity layers have inconsistent A0 ratios extracted from
different recordings with varying SNR. Lower velocity layers (pp/mp) have
poor high-partial accuracy because harmonics are below noise floor.

**Fix:** At noteOn, always use the forte (highest valid) velocity layer as
reference for:
1. `rms_gain` -- taken directly from forte layer
2. Spectral shape -- for each partial k>1, compute ratio `A0(k)/A0(k=1)`
   from forte layer and apply to the interpolated layer. Only boost, never
   cut below current value.

This means velocity layer interpolation still works for `A0(k=1)` magnitude
and decay parameters, but spectral tilt is anchored to the most reliable
measurement.

### Physics-based noise centroid and attack tau

**Problem:** Extracted noise centroid often too low (2-3 kHz for all registers)
due to harmonic leakage in noise residual. Bass hammer attack sounds like
soft thud instead of metallic hit.

**Fix:** `hammer_noise_centroid(midi)` and `hammer_attack_tau(midi)` in
`additive_synthesis_piano_math.h` provide physics-based floors/ceilings:
- Centroid: 2000 Hz (MIDI 21) to 6000 Hz (MIDI 108)
- Attack tau: 5 ms (bass) to 1 ms (treble)
- At noteOn: `use_centroid = max(bank, physics)`, `use_tau = min(bank, physics)`
- Noise amplitude scaled by `sqrt(bank_centroid / use_centroid)` when centroid
  is raised, compensating for perceptual brightness increase.

### EQ gain floor (-3 dB)

**Problem:** EQ cascade from deficient source recordings could cut signal
by >6 dB, muffling notes where forte normalization had already
corrected the harmonic content.

**Fix:** In `eq_cascade_stereo()`, if wet signal is below 0.7x dry signal
(per sample, per channel), clamp to 0.7x. This prevents more than 3 dB of
attenuation from the EQ stage while still allowing spectral shaping.

### Body parameter (reverted)

Low-harmonic boost for warmth/fullness. At noteOn, partials k=1..8 are
boosted: `A0[ki] *= 1 + body * (1 - ki/8)`.

Default 0.15 gives k=1 +1.3 dB, k=4 +0.7 dB, k=8 +0 dB.

### vel_spectral_weight (implemented, disabled)

Function `vel_spectral_weight(k, vel_norm, slope)` in math.h models
velocity-dependent spectral tilt (soft hammer = less HF). Implemented and
tested but NOT called in the core -- forte spectral shape normalization
handles the same need more robustly. Kept for potential future use.

### Hybrid bank synthesis tool

`tools/synthesize_hybrid_bank.py` creates a hybrid bank from pl-grand
(excellent treble) and ks-grand (rich middle register). Per-note strategy:
1. Start with pl-grand as base
2. Measure k5/k1 amplitude ratio
3. If below threshold, borrow spectral shape from ks-grand
4. If both weak, apply physics floor sin(n*pi/8)/n
5. Preserve pl-grand decay and noise parameters
6. Recalibrate rms_gain

---

## Session 2026-04-06/07 -- Onset/Decay Separation, Extraction Fixes

### Onset prepend vs. bi-exp fitting

Onset STFT captures hammer contact transient (~10-20ms). Bi-exp fitter
interprets this as very fast tau1 -- 59% of middle register notes had
tau1=0.03 (lower bound).

MIDI 57 (on soundboard resonance): smooth decay from peak -> tau1=0.03.
MIDI 59 (off resonance): spike + plateau -> tau1=1.58.

**Fix:** onset for A0 peak only, bi-exp fit from main STFT data.
Bound-hit rate: 59% -> 12%.

### Velocity spectral contamination

At low velocity, high partials below noise floor -> extractor fits noise ->
A0 artificially small -> RMS normalization preserves dark spectrum ->
pp sounds like ff + low-pass filter.

MIDI 36 vel=0 vs vel=7, k=10: 59 dB difference (should be 3-6 dB).

**Fix:** spectral shape borrowing from vel 5-7 average.

### Allpass sign regression

Original code: `y[n] = -g*x[n] + x[n-1] + g*y[n-1]` (correct allpass).
"Fix" changed to `-g*y[n-1]` (NOT allpass). Reverted.

### Soundboard transfer function

Pure additive synthesis has 20-85 dB less energy than original recording
in mid-frequency bands. This energy comes from soundboard resonance,
room, and string-bridge coupling.

**Fix:** soundboard IR convolution (25ms, 0-4% mix).

### Stereo width explosion

EQ fitter computed `width = orig_S/orig_M / (syn_S/syn_M)`.
For centered notes, syn_S ~ 0 -> division explosion -> width=6-8.

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
| "Dull/lahev" | Missing 1-2 kHz energy | Source recording issue |
| Weak/quiet | rms_gain < 0.0003 | Bass notes with few vel layers |
| Good treble | Few partials, high A_noise | Simple signal, strong attack |
| "Rezonujici" | width=2.0 (clamp) + fast tau1 | Over-bright synthesis |
