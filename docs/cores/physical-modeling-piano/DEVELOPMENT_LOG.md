# PhysicalModelingPianoCore -- Development Log

## 2026-04-08 -- v3: validated string model

### Listening test optimization (8 rounds)

Systematic A/B testing of string parameters on MIDI 36, 48, 60, 72, 84.
Each round generated 4-8 variants, user rated 0-9.

**Round 1:** Excitation rolloff (nylon vs steel)
- 1/k^2 = nylon, 1/k = brighter, 1/k^0.7 = best steel character
- Winner: `exc_rolloff=1.0, T60_nyq=0.08, B=4e-4`

**Round 2:** Brightness + odd/even emphasis
- odd_boost=1.5 has best metallic character
- Physical basis: rigid terminations + bridge coupling prefer odd modes
- Winner: `odd_boost=1.5, exc_rolloff=0.7, T60_nyq=0.15, B=4e-4`

**Round 3:** Refine across registers (MIDI 48, 60)
- m048: odd_boost=1.5 + B=6e-4 best
- m060: odd_boost=2.0 + rolloff=0.5 best

**Round 4:** Dispersion allpass cascade
- Single allpass (coeff > 0.3) creates buzz → use cascade of 3+ stages
- knee10 + slope3 + B=6e-4 best for MIDI 60
- Winner: `knee_k=10, knee_slope=3, n_disp_stages from B*N^2`

**Round 5:** Denser waveform (more harmonics)
- Flat excitation (rolloff=0.2) + gentle loss (T60_nyq=0.3) = richer
- But too "hairy" (high harmonics create jagged waveform)

**Round 6:** Two-stage rolloff (knee)
- Flat below knee_k, steep 1/k^slope above → smooth but rich
- knee_k=10 + slope=3.0-4.0 = best balance
- Winner: `rolloff=0.1, knee_k=10, knee_slope=3-4, B=8e-4`

**Round 7:** Push further
- knee_slope=4.0 smoother, B=1.2e-3 more metallic
- Higher knee (12-15) = more body but riskier artifacts
- Winner: `knee10_s4` for MIDI 60

**Round 8:** String gauge (thickness)
- gauge=2.0 for middle, 3.0+ for bass gives "hutnejsi" sound
- Gauge boosts fundamental, damps high harmonics
- Dispersion: min 3 stages or 0 (single stage = buzz)

### Key bugs fixed

1. **Half-frequency (128 Hz vs 262 Hz):** Dual-rail waveguide with sign
   inversion doubled the period. Fix: single-loop, no inversion.

2. **All-positive waveform:** Triangle initial conditions never crossed
   zero. Fix: remove DC offset from Fourier excitation.

3. **Only odd harmonics:** Half-period delay + sign inversion = square
   wave character. Fix: full-period delay + bipolar excitation.

4. **Saw artifacts:** Triangle shape persists through weak loss filter.
   Fix: Fourier series excitation (smooth from sample 0).

5. **Dispersion buzz:** Single allpass with coeff=-0.7 distorts. Fix:
   cascade of 3-16 stages with coeff=-0.15 each.

6. **Output clipping:** Impedance-derived output_scale=500x. Fix:
   empirical scaling (0.07-3.0 depending on iteration).

### Papers referenced
- Bank & Valimaki (2003) — Robust loss filter design (IEEE SPL)
- Teng (2012) — MSc thesis on piano synthesis (AMT)
- Smith (1992) — Digital waveguide models
- Chaigne & Askenfelt (1994) — Finite difference hammer model
- Van Duyne & Smith (1994) — Dispersion allpass cascade
- Chabassier, Chaigne & Joly (2012) — INRIA piano model

Papers location: `C:\Users\jindr\OneDrive\Osobni\LordAudio\IhtacaPapers\`

---

## 2026-04-08 -- v2: commuted synthesis + excitation experiments

- Removed internal soundboard mode bank (24 resonators)
- Rely on DspChain convolver with real soundboard IR
- Hammer FD excitation with impedance matching (Z=2.5)
- Auto-load IR from icr-config.json

---

## 2026-04-08 -- v1: initial waveguide

- Dual-rail digital waveguide
- Nonlinear hammer model (Chabassier)
- One-pole loss filter
- 24 soundboard modes
- Multiple bugs: wrong tuning, no sound, clipping, divergence
