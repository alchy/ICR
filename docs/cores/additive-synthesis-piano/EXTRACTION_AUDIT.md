# Additive Core Extraction Audit — Middle Register Failure

**Branch:** `dev-additive-audit`
**Date:** 2026-04-10
**Symptom:** Middle register (MIDI 50-65) sounds hollow/"bottle", treble is excellent.

---

## Root Cause: Source Recording (pl-grand) Has Weak Harmonics

Cross-piano comparison at vel=4 — k5/k1 amplitude ratio:

| MIDI | pl-grand | ks-grand (CFX) | Factor |
|------|:--------:|:--------------:|:------:|
| 48 | 0.173 | 0.541 | 3× |
| 55 | **0.043** | **0.439** | **10×** |
| 60 | **0.055** | **0.441** | **8×** |
| 61 | **0.060** | **0.569** | **9×** |
| 65 | **0.023** | **0.156** | **7×** |
| 71 | 0.021 | 0.025 | 1.2× |

**Conclusion:** The extraction pipeline works correctly — ks-grand produces
rich harmonics in the middle register. The pl-grand source recording has
genuinely weak upper harmonics for MIDI 50-65 (likely microphone position,
lower actual velocity, or piano characteristics).

The extraction IS the measurement — the fix must compensate for deficient
source material.

---

## Detailed Findings

### 1. A0 Spectral Tilt Collapse (MIDI 50-65)

Actual A0 vs physics floor `sin(n*pi/8)/n` for pl-grand MIDI 55:

| k | A0 actual | A0 floor | Ratio |
|---|:---------:|:--------:|:-----:|
| 1 | 177.9 | 177.9 | 1.000 |
| 2 | 30.8 | 164.4 | 0.19 |
| 3 | 14.7 | 143.2 | 0.10 |
| 5 | 7.6 | 85.9 | 0.09 |
| 10 | 3.5 | 32.9 | 0.11 |
| 20 | 0.17 | 23.2 | 0.007 |

Harmonics k>=2 are 5-140× below the physics floor. The fundamental
dominates completely → "hollow/bottle" tone.

### 2. EQ Is NOT the Culprit

Average EQ gain in 2-6 kHz band for middle register: **-0.1 dB** (flat).
No over-correction happening.

### 3. Physics Floor Is Disabled

Current config: `RELAXED` (extraction_config.py) with:
- `physics_floor_enabled: False`
- `a1_blend_enabled: False`
- `beat_hz_fallback: 0.0`

The `STRICT` config would inject `sin(n*pi/8)/n` floor at 50% energy,
which would fill the missing harmonics.

### 4. tau1 Floor Hits

2/9 middle register notes hit the tau1 lower bound (0.05s vs configured
0.01s floor). These are notes where the decay fitter struggled.

---

## Recommendations

### Fix 1: Enable Physics Floor for pl-grand (PRIORITY 1)

Re-extract pl-grand with physics_floor_enabled=True. This injects the
hammer-strike spectral floor `sin(n*pi/8)/n` normalized to match k=1
amplitude. Only boosts — never cuts existing energy.

```bash
# In run-extract-additive.py, change config:
cfg = ExtractionConfig(
    physics_floor_enabled=True,
    physics_floor_scale=0.50,    # 50% of theoretical floor
    physics_floor_max_k=20,      # extend to k=20 (was 12)
)
```

**Expected impact:** Middle register harmonics boosted 5-50× for weak
partials. Should eliminate the hollow/bottle character.

### Fix 2: Adaptive Physics Floor (smarter)

Instead of a global on/off, detect per-note whether harmonics are
deficient and apply floor only where needed:

```python
# In exporter.py _build_note():
k5_k1_ratio = A0_k5 / A0_k1
if k5_k1_ratio < 0.10:  # deficient harmonics
    apply_physics_floor(partials, scale=0.50)
```

This preserves the high-quality treble extraction while fixing the
middle register.

### Fix 3: Use ks-grand as Reference Source

The ks-grand bank has consistently rich harmonics across all registers.
Consider using it as the default bank, or blending its spectral shape
into pl-grand for affected notes.

### Fix 4: Spectral Shape Borrowing from Good Neighbors

Already implemented (`_borrow_spectral_shape`), but only borrows across
velocity layers. Extend to borrow across MIDI notes — if MIDI 55 is
hollow but MIDI 48 and 68 are rich, interpolate the spectral shape.

---

## Extraction Pipeline Summary (for reference)

```
WAV → spectrum → peak detection → B/f0 fit
  → adaptive STFT → per-partial:
      onset envelope (A0) + main STFT (decay fit)
      → bi-exp: a1*exp(-t/tau1) + (1-a1)*exp(-t/tau2)
      → beating detection (modulation FFT)
  → damping law correction (optional)
  → noise analysis (harmonic subtraction)
  → outlier filter → EQ fitting → export
```

Config presets: RELAXED (current), STRICT (physics overrides), RAW (testing)

---

## Score Distribution (pl-grand listening test)

| Register | MIDI | Avg Score | Root Cause |
|----------|------|:---------:|------------|
| Deep bass | 21-30 | 0.44 | Weak gain, few velocity layers |
| Bass | 32-45 | 0.67 | Variable, mostly OK |
| **Low-mid** | **46-56** | **0.48** | **Hollow — weak harmonics in source** |
| **Middle** | **57-65** | **0.46** | **Hollow — weak harmonics in source** |
| Upper-mid | 66-77 | 0.52 | Improving |
| Upper | 78-90 | 0.69 | Good |
| **Treble** | **91-108** | **0.94** | **Excellent** |
