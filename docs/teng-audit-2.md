# Teng Audit 2 -- Physical Piano Core vs. Teng 2012

**Branch:** `dev-teng-audit2`  
**Date:** 2026-04-09  
**Symptom:** Piano sounds like plucked nylon/mandolin, not struck steel.  
**Note:** Problem is NOT the convolution IR — it's the waveguide core itself.

---

## Reference: Teng 2012 MATLAB Architecture

```
Excitation: Chaigne FD hammer → F/(2*R0) → velocity input
Topology:   Dual delay line (DL1=M-i0, DL2=i0), observation at hammer point
Bridge:     H(z) = Hl * Hd^N_ap * Hfd  (single reflection filter)
Nut:        -1 (rigid)

Hl(z)  = gl * (1+al) / (1 + al*z^-1)     loss filter (gl NEGATIVE = includes -1 refl)
Hd(z)  = (ad + z^-1) / (1 + ad*z^-1)     dispersion allpass (cascade of N_ap)
Hfd(z) = (C + z^-1) / (1 + C*z^-1)       tuning allpass (fractional delay)

C4 parameters: gl=-0.997, al=-0.001, ad=-0.30, N_ap=16
```

## Our Architecture (physical_modeling_piano_math.h)

```
Excitation: Chaigne FD hammer → F/(2*R0) + noise + even harmonics
Topology:   Dual rail (upper/lower, length M each), observation at bridge
Bridge:     loss_filter → disp_cascade → tuning_AP → bridge_refl scalar
Nut:        -1 (rigid)

Loss:       y = g_dc * ((1-pole)*x + pole*y_prev)   → H(z) = g_dc*(1-b)/(1-g_dc*b*z^-1)
Disp:       allpass_tick with a_disp, N stages
Tuning:     allpass_tick with ap_a
bridge_refl: scalar, default -0.98

C4 parameters: g_dc≈0.996, b≈0.05, a_disp=-0.15, n_disp=11, bridge_refl=-0.98
```

---

## DEVIATION 1 (CRITICAL): bridge_refl=-0.98 causes uncompensated extra loss

### The Problem

The loss filter `compute_loss_filter()` computes `g_dc` to achieve the desired T60_fund
decay **on its own** (assuming it is the sole loss mechanism per round trip). But then
`bridge_refl = -0.98` multiplies the signal by an additional 0.98, adding **2% unaccounted
loss per round trip**.

### Quantified Impact (C4, MIDI 60)

```
f0=262 Hz, sr=48000, N_period=183 samples, T60_fund=7.29s (bank)

Loss filter g_dc = 10^(-3*183 / (7.29*48000)) = 0.99639

WITHOUT bridge_refl (intended):
  per-trip gain = 0.99639
  0.99639^N = 0.001 → N = 1908 trips → T60 = 1908/262 = 7.28s  ✓

WITH bridge_refl = -0.98 (actual):
  per-trip gain = 0.99639 × 0.98 = 0.97646
  0.97646^N = 0.001 → N = 290 trips → T60 = 290/262 = 1.11s  ✗

Actual T60 is 6.6× shorter than intended!
```

This applies to ALL notes across the entire keyboard.

### Why This Sounds Plucked

A T60 of ~1 second at DC is characteristic of plucked string instruments (guitar,
mandolin), not piano. Real piano sustain is 5-15 seconds. The rapid decay removes the
fundamental quality that distinguishes piano from plucked strings.

### Fix

Set `bridge_refl = -1.0` (rigid reflection, matches Teng). Let the loss filter handle
ALL amplitude decay. If asymmetric reflection is desired for timbre, compensate g_dc:

```cpp
// Compensate: g_dc_eff = g_dc_intended / |bridge_refl|
// so that g_dc_eff * |bridge_refl| = g_dc_intended
```

### Location

- `physical_modeling_piano_math.h:140` — dual_rail_init default
- `physical_modeling_piano_core.h:57` — PhysicsNoteParam default
- All bank JSON files: `"bridge_refl": -0.98`

---

## DEVIATION 2 (CRITICAL): Dispersion far too weak — zero above C5

### The Problem

| Parameter      | Teng C4     | Ours C4     | Ours C5 (MIDI 72) |
|----------------|-------------|-------------|---------------------|
| n_disp_stages  | 16          | 11          | **0**               |
| disp_coeff     | -0.30       | -0.15       | -0.15               |
| "strength"     | 16×0.30=4.8 | 11×0.15=1.65| **0**               |

Our dispersion is 3× weaker than Teng for C4, and **completely absent** for MIDI >= 72.

### Why It's Zero Above C5

From `populateDefaults()` (physical_modeling_piano_core.cpp:52-55):

```cpp
float N = sample_rate_ / np.f0_hz;
float beta = np.B * N * N;
int n_raw = (int)(beta * 0.5f);
np.n_disp_stages = (n_raw < 3) ? 0 : std::min(n_raw, 16);
```

For MIDI 72 (C5, f0=523Hz): N=91.8, B=0.0004, beta=3.37, n_raw=1 → **0 stages**.

The threshold `n_raw < 3` is too aggressive. Notes without dispersion have perfectly
harmonic partials — the acoustic signature of nylon/gut strings.

### Why This Sounds Like Nylon

Piano steel strings are stiff → pronounced inharmonicity (partials stretched sharp).
This is a defining timbral characteristic. Without it, partials stack perfectly like
a classical guitar or harp. The upper half of the keyboard (MIDI 72-108) has zero
inharmonicity in our model.

### Teng's Approach

Teng adjusts N_ap per frequency band (C7: 0-2 stages, C4: 16 stages) and uses
`ad=-0.30` (2× stronger coefficient). Even high notes get 2+ stages.

### Fix

1. Change `disp_coeff` from -0.15 to -0.30 (match Teng)
2. Lower the cutoff threshold: allow even 1 stage of dispersion
3. Recalculate n_disp_stages using Teng's approach (scale with B and f0, never
   fully zero out for notes < C7)

### Location

- `physical_modeling_piano_core.cpp:52-56` — n_disp_stages formula
- All bank JSON: `"disp_coeff": -0.15`

---

## DEVIATION 3 (MODERATE): Artificial even-harmonic injection in hammer force

### The Problem

After computing the Chaigne hammer force, `compute_force()` adds synthetic sinusoids:

```cpp
// physical_modeling_piano_math.h:464-467
float h2 = std::sin(TAU * 2.f * f0_note * t);
float h4 = std::sin(TAU * 4.f * f0_note * t) * 0.5f;
v_in[i] += (h2 + h4) * env * even_level;   // even_level = 0.12
```

### Why This Is Wrong

1. **False premise**: The comment says "rigid terminations favor odd modes" — this is
   **incorrect** for strings. A string fixed at both ends supports ALL integer harmonics
   (both odd and even). Only a pipe closed at one end favors odd harmonics.

2. **Not in Teng**: Teng's model has no spectral enrichment of the hammer force.
   The force signal naturally excites all harmonics when injected into the waveguide.

3. **Interference**: Adding pure sinusoids at exactly 2f0 and 4f0 to the force signal
   creates coherent tones that interfere with the waveguide's natural resonances,
   producing unnatural phasing/beating artifacts.

### Fix

Remove the even-harmonic injection entirely (lines 448-468). The waveguide + proper
loss filter will produce the correct harmonic spectrum naturally.

The broadband noise addition (lines 443-461) is acceptable as a subtle attack
brightness enhancement, though not in Teng's original model.

### Location

- `physical_modeling_piano_math.h:434-468` — spectral enrichment block

---

## DEVIATION 4 (MINOR): Loss filter parameter naming mismatch

### Analysis

Teng's filter: `H(z) = gl*(1+al)/(1+al*z^-1)` with al=-0.001

- gl is NEGATIVE (embeds the bridge -1 reflection sign)
- Pole at z = -al = +0.001 (nearly at origin → almost pure gain)

Our filter: `H(z) = g_dc*(1-b)/(1 - g_dc*b*z^-1)`

- g_dc is POSITIVE (sign comes from bridge_refl separately)
- Pole at z = g_dc*b ≈ 0.05 (50× further from origin → more aggressive LP)

The filter topology is equivalent, and our approach of deriving the pole from T60
values is arguably more principled than Teng's fixed al=-0.001. However, the comment
in code claims "Välimäki one-pole" but the form differs from Välimäki's published
formula: `H(z) = g*(1-a)/(1 - a*z^-1)`.

### Impact

Not a direct timbre issue (pole is still small). The real damage comes from
DEVIATION 1 (bridge_refl adding uncompensated loss).

---

## DEVIATION 5 (COSMETIC): Output tap at bridge vs. hammer point

Teng observes string velocity at the hammer contact point (sum of both rails at n0).
Our code outputs the upper rail at the bridge (`bridge_out = upper_at(M-1)`).

Both are valid physical observation points. Bridge output is more standard (represents
soundboard driving force). This is **not** a cause of the timbral problem.

---

## Summary: Root Causes of Nylon/Plucked Sound

| #  | Deviation             | Severity | Effect                                     |
|----|-----------------------|----------|--------------------------------------------|
| 1  | bridge_refl = -0.98   | CRITICAL | T60 is 5-7× shorter than intended          |
| 2  | Dispersion too weak/0 | CRITICAL | No inharmonicity above C5; 3× weak below   |
| 3  | Even-harmonic inject  | MODERATE | Unphysical artifacts in excitation          |
| 4  | Filter naming/form    | MINOR    | No direct impact (masked by #1)            |
| 5  | Output tap position   | COSMETIC | Not a timbre issue                         |

### Recommended Fix Priority

1. **Set `bridge_refl = -1.0`** everywhere (bank JSON + default). Immediately fixes
   the decay time to match intended T60 values.
2. **Change `disp_coeff` to -0.30** and lower the n_disp threshold so all notes
   get at least 1-2 stages of dispersion.
3. **Remove even-harmonic injection** from hammer force computation.
4. Regenerate all bank JSON files with corrected defaults.
