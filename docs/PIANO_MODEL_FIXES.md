# Piano Model Critical Fixes

## Overview

Critical analysis of the additive piano synthesis model identified 7 issues
causing audible defects: **slow attack in bass**, **muffled middle register**,
while **treble (octaves 7-8) sounded correct**.  All fixes are informed by
piano acoustics literature (Askenfelt, Weinreich, Chaigne, Smith/CCRMA).

---

## Fix 1: Onset STFT Frame Scaling for Bass (extractor.py)

**Problem:** The onset high-resolution STFT used a fixed 256-sample frame
(~5.3 ms at 48 kHz, frequency resolution 187 Hz).  For bass notes (f0 = 27-55 Hz),
the fundamental falls into FFT bin 0 (DC) and cannot be resolved.  The onset
prepend returns garbage or is skipped entirely, so the bi-exponential fitter
never sees the fast attack transient.

**Fix:** Frame size now scales with partial frequency:
```python
min_frame   = max(256, int(4 * sr / max(fk, 20.0)))
frame_exp   = max(8, min(14, round(math.log2(min_frame))))
ONSET_FRAME = 1 << frame_exp
ONSET_HOP   = max(32, ONSET_FRAME // 4)
```

For A1 (55 Hz): ONSET_FRAME = 4096 (resolution 11.7 Hz, bin index ~5).
For C7 (2093 Hz): ONSET_FRAME = 256 (unchanged).

Also increased `search_bins` from 1 to 2 for more robust bin matching.

---

## Fix 2: Attack Rise Phase in C++ Envelope Model (piano_math.h, piano_core.cpp)

**Problem:** Both `env_fast` and `env_slow` initialize to 1.0 — the envelope
only decays from t=0.  Real piano strings take 1-5 ms to reach peak amplitude
after hammer contact (longer for bass wound strings, shorter for treble).

**Physics:** Hammer-string contact duration:
- Bass: ~4 ms (heavy wound strings)
- Middle: ~2 ms (plain steel)  
- Treble: <1 ms (short stiff strings)

**Fix:** Added exponential rise envelope `1 - exp(-t / rise_tau)` applied to
partials only (noise bypasses it — noise IS the attack transient):
```cpp
// piano_math.h
inline float rise_tau_from_midi(int midi) {
    float t = (midi - 21) / 87.f;           // 0..1 across keyboard
    float rise_ms = 4.0f - t * 3.8f;        // 4.0 ms (bass) → 0.2 ms (treble)
    return rise_ms * 0.001f;
}
```

The rise coefficient is precomputed at noteOn as `exp(-1/(rise_tau * sr))`.

---

## Fix 3: Onset Gate Reduced + Noise Bypass (piano_core.h, piano_core.cpp)

**Problem:** The 3 ms linear onset gate suppressed the first 3 ms of all
output — exactly the time window containing the hammer-knock character.
For bass at 27.5 Hz (period 36 ms), the gate is 1/12 of a cycle, removing
the percussive "thunk".  For treble at 4000 Hz, the gate spans 12 cycles
and is imperceptible.

**Fix:**
1. `PIANO_ONSET_MS` reduced from 3.0 to **0.5 ms** (minimal click prevention)
2. Noise now bypasses the rise envelope — the synthesis loop computes
   partials and noise separately, applies rise only to partials, then sums

---

## Fix 4: Hard Cap attack_tau at 0.10 s (extractor.py, exporter.py)

**Problem:** The noise envelope `exp(-t/attack_tau)` decay time was fitted
with bounds [0.003, 1.0] seconds.  For bass notes, harmonic leakage in the
noise residual dominated the fit, producing `attack_tau = 0.3-0.8 s` when
real hammer noise decays in 20-50 ms.

**Physics:** Piano hammer noise consists of:
1. Hammer felt impact (broadband, <5 ms)
2. Longitudinal precursor (metallic ping, ~10-20 ms)
3. Key mechanism noise (<30 ms)

None of these exceeds ~50 ms.  Values above 100 ms indicate harmonic leakage.

**Fix:**
- extractor.py: `bounds=([0.003], [0.10])` (was `[1.0]`)
- exporter.py: `attack_tau = min(attack_tau_raw, tau1_k1, 0.10)`

---

## Fix 5: EQ freq_min Lowered to 80 Hz (synthesizer.py)

**Problem:** The spectral EQ correction zeroed gains below 200 Hz and faded
200-400 Hz.  For middle C (261 Hz), the fundamental got no EQ correction.
For bass notes, the first 7+ harmonics were unaffected.  Soundboard body
resonances that give warmth and presence were not applied to the bass/middle
register.

**Fix:** Default `eq_freq_min` changed from 400 Hz to **80 Hz**.
Sub-fundamental boost is already prevented by clamping in `_fit_eq_biquads`.

---

## Fix 6: Noise Filter Upgraded to Biquad Bandpass (dsp_math.h, piano_core)

**Problem:** The 1-pole low-pass filter (-6 dB/octave) at `noise_centroid_hz`
could not create the bandpass spectral shape of real hammer noise.  It passed
all energy below the centroid, adding "muddiness" to bass notes instead of
a percussive "thwack".

**Physics:** Real hammer noise peaks at 1-4 kHz (depending on hammer hardness
and note register) and rolls off both above and below.

**Fix:** Replaced 1-pole IIR with RBJ constant-skirt-gain bandpass biquad:
```cpp
v.noise_bpf = dsp::rbj_bandpass(noise_centroid_hz, 1.5f, sr);
```
Q = 1.5 gives a bandwidth of centroid/1.5 Hz, providing natural roll-off
on both sides without the low-frequency mud.

Added `dsp::rbj_bandpass()` to `dsp_math.h` (RBJ Audio EQ Cookbook formula).

---

## Fix 7: Peak Frame Detection Without Smoothing (extractor.py)

**Problem:** `_find_peak_frame()` applied 3-sample boxcar smoothing before
argmax.  For bass notes where the onset prepend contributes only 3-8 frames,
this shifted the detected peak away from the true attack maximum.  The
bi-exponential fitter then started from a later point, making `tau1` longer.

**Fix:** Skip smoothing for short sequences (<=6 frames), use raw argmax:
```python
if len(amps) <= 6:
    return int(np.argmax(amps))
```

---

## Impact Summary

| Register | Before | After |
|----------|--------|-------|
| Bass (MIDI 21-40) | Slow attack, no percussive character | Fast hammer-knock from noise + proper rise time |
| Middle (MIDI 48-72) | Muffled, lacking brightness | EQ correction active, bandpass noise adds clarity |
| Treble (MIDI 84-108) | Already correct | Unchanged (all fixes degrade gracefully to treble behavior) |

## Reference Values (Piano Physics)

| Parameter | Bass (A0) | Middle (C4) | Treble (C7) |
|-----------|-----------|-------------|-------------|
| f0 | 27.5 Hz | 261.6 Hz | 2093 Hz |
| Hammer contact | ~4 ms | ~2 ms | <1 ms |
| B (inharmonicity) | ~0.0003 | ~0.0005 | ~0.01 |
| Prompt decay tau | ~2.2 s | ~1.1 s | ~0.3 s |
| Real noise decay | ~20-50 ms | ~10-30 ms | ~5-15 ms |

Sources: Askenfelt & Jansson (KTH), Weinreich (U. Michigan), Chaigne &
Askenfelt (numerical simulations), Smith (CCRMA/Stanford), Conklin.
