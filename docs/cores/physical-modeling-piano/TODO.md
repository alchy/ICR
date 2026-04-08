# PhysicalModelingPianoCore -- TODO

## Known Issues (v0.1)

- Sound is noticeably synthetic compared to real piano
- Hollow timbre lacking spectral richness
- Decay character doesn't fully match real piano bi-exponential shape
- No velocity-dependent timbral variation beyond hammer velocity

## Roadmap

### P1. Improve loss filter (higher order)
Current one-pole provides flat/Nyquist control only.  A 3-pole or biquad loss
filter would give finer frequency-dependent damping control, closer to the
measured `R + eta*f^2` Chabassier damping model.

### P2. More soundboard modes
24 modes is better than 8 but still coarse.  Target: 48-64 modes, possibly
with note-dependent mode selection (modes near f0 get higher coupling).

### P3. Physical damper model
Replace linear release fadeout with actual damper simulation: increase loss
filter coefficients when key is released, letting the string decay naturally.

### P4. Cross-string coupling at bridge
Currently strings are independent -- each sees only its own bridge reflection.
In reality, bridge force from all strings feeds back into each string.

### P5. Sympathetic resonance
Open (undamped) strings should resonate sympathetically when excited by
nearby notes.  Requires monitoring active string energy and coupling it
into matching harmonics of open strings.

### P6. Longitudinal string modes
Bass strings produce a metallic "ping" precursor from longitudinal vibration
(c_long = 14x c_transverse).  Could be added as a separate short delay line
or as injected partials at onset.

### P7. JSON parameter loading
Support a dedicated PhysicalModelingPianoCore JSON format with per-note
physical parameters (K_H, p, M_H, tau_fund, tau_high, impedance_ratio).
