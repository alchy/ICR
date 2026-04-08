# PhysicalModelingPianoCore -- Development Log

## 2026-04-08 -- Initial implementation (v0.1)

### Architecture
- Digital waveguide model with 3-layer Ithaca Core pattern
- Delay lines with allpass fractional delay for accurate tuning
- Nonlinear hammer model (Chabassier: F = K_H * max(0, xi-u)^p)
- One-pole loss filter for frequency-dependent damping
- Second-order allpass dispersion filter for inharmonicity
- 24 soundboard resonant modes (60 Hz - 8 kHz)
- Bandpass hammer noise at attack (1.5-5 kHz, velocity-scaled)

### Bugs fixed during integration
1. **Hammer never contacts string**: `hammer_tick` tested compression before
   advancing position -- `xi=0, u=0` -> `compression=0` -> immediate bounce.
   Fix: symplectic Euler (advance position first, then test compression).

2. **Injection scale ~0**: Original `F * dt * 0.5` gives ~2e-5 scaling.
   Fix: impedance-matched injection `F / (2*Z)` where `Z ~ 2*f0`.

3. **Soundboard divergence**: `output_scale` applied to both direct string
   and soundboard output -> total >1.0 -> clipping.  Fix: `output_scale`
   on direct string only, soundboard has own gain structure.

4. **Fast decay**: `impedance_ratio=0.01` -> 2% amplitude loss per round-trip
   -> effective tau ~0.2s.  Fix: reduced 10x to 0.0002-0.002.

### Physics references used
- Chabassier, Chaigne & Joly (2012) -- INRIA RR-8181 (hammer parameters)
- Smith (1993) -- Digital Waveguides
- Bank & Sujbert (2005) -- Longitudinal vibrations
- Valimaki et al. (2006) -- Commuted waveguide synthesis
