# ICR — MIDI Command Reference

Complete table of MIDI messages accepted by the ICR engine.

## Standard MIDI Messages

### Note On / Off

| Status | Data | Physical | Additive | Sampler | Sine |
|--------|------|:--------:|:--------:|:-------:|:----:|
| `0x90` | `midi vel` | Note On | Note On | Note On | Note On |
| `0x90` | `midi 0` | Note Off | Note Off | Note Off | Note Off |
| `0x80` | `midi vel` | Note Off | Note Off | Note Off | Note Off |

### Control Change (CC)

| CC# | Description | Physical | Additive | Sampler | Sine |
|-----|-------------|:--------:|:--------:|:-------:|:----:|
| **64** | Sustain pedal (≥64 = down) | yes | yes | yes | yes |
| **7** | Channel volume → master gain | yes | yes | yes | yes |
| **10** | Pan (0=L, 64=C, 127=R) | yes | yes | yes | yes |
| **93** | LFO pan speed (0-2 Hz) | yes | yes | yes | yes |
| **91** | LFO pan depth (0-100%) | yes | yes | yes | yes |
| **74** | Limiter threshold (-40..0 dB) | yes | yes | yes | yes |

Note: CC 7/10/91/93/74 are engine-level — they work regardless of active core.

---

## SysEx Protocol

Frame: `F0 7D 01 <cmd> <core_id> <data...> F7`

### Core IDs

| ID | Core |
|----|------|
| `0x00` | Active core (default) |
| `0x01` | AdditiveSynthesisPianoCore |
| `0x02` | PhysicalModelingPianoCore |
| `0x03` | SamplerCore |
| `0x04` | SineCore |
| `0x7F` | Engine level (master/DspChain) |

### Commands

| Cmd | Name | Physical | Additive | Sampler | Sine |
|-----|------|:--------:|:--------:|:-------:|:----:|
| `0x70` | PING/PONG | yes | yes | yes | yes |
| `0x01` | SET_NOTE_PARAM | yes | yes | — | — |
| `0x02` | SET_NOTE_PARTIAL | — | yes | — | — |
| `0x03` | SET_BANK | yes | yes | — | — |
| `0x10` | SET_MASTER | yes | yes | yes | yes |
| `0x72` | EXPORT_BANK | yes | yes | — | — |

### Value Encoding (5 bytes)

Float values are encoded as 5 × 7-bit MIDI-safe bytes:
```
bytes[0..4] → 35-bit integer → reinterpret as IEEE 754 float32
```

---

## SET_NOTE_PARAM (0x01) — Per-Note Parameters

| Key | Physical | Additive | Description |
|-----|:--------:|:--------:|-------------|
| `f0_hz` | 0x01 | 0x01 | Fundamental frequency (Hz) |
| `B` | 0x02 | 0x02 | Inharmonicity coefficient |
| `gauge` | 0x10 | — | Retained for compatibility (no DSP effect) |
| `T60_fund` | 0x11 | — | Fundamental decay time (s) |
| `T60_nyq` | 0x12 | — | Nyquist decay time (s) |
| `exc_x0` | 0x13 | — | Hammer striking position (fraction) |
| `K_hardening` | 0x14 | — | Velocity stiffness scaling (0-5) |
| `p_hardening` | 0x15 | — | Velocity exponent offset (0-1) |
| `n_disp_stages` | 0x16 | — | Dispersion allpass stages (0-16) |
| `disp_coeff` | 0x17 | — | Per-stage allpass coefficient (Teng: -0.30) |
| `n_strings` | 0x18 | — | Unison strings (1-3) |
| `detune_cents` | 0x19 | — | String detuning (cents) |
| `hammer_mass` | 0x1A | — | Hammer mass scale (0.1-3.0) |
| `string_mass` | 0x1B | — | String mass scale (0.1-3.0) |
| `output_scale` | 0x1C | — | Per-note output gain (0.01-0.5) |
| `bridge_refl` | 0x1D | — | Bridge reflection (-1.0=rigid, default) |
| `attack_tau` | — | 0x03 | Attack transient decay (s) |
| `A_noise` | — | 0x04 | Attack noise amplitude |
| `rms_gain` | — | 0x05 | RMS output gain |
| `phi_diff` | — | 0x06 | Phase difference (stereo) |

Shared IDs (0x01-0x02) work for both cores. Physical-specific IDs
(0x10-0x1D) are silently ignored by additive core, and vice versa.

Sampler, Sine: `setNoteParam` not implemented (returns false).

---

## SET_NOTE_PARTIAL (0x02) — Per-Partial Parameters

| Key | Physical | Additive | Description |
|-----|:--------:|:--------:|-------------|
| `f_hz` (0x10) | — | yes | Partial frequency (Hz) |
| `A0` (0x11) | — | yes | Partial amplitude |
| `tau1` (0x12) | — | yes | Fast decay time (s) |
| `tau2` (0x13) | — | yes | Slow decay time (s) |
| `a1` (0x14) | — | yes | Envelope mix ratio |
| `beat_hz` (0x15) | — | yes | Beating frequency (Hz) |
| `phi` (0x16) | — | yes | Partial phase (rad) |

Physical, Sampler, Sine: N/A (no partial concept).

---

## SET_MASTER (0x10) — Global Parameters

### Core-Specific (param_id 0x01-0x07, routed to `setParam`)

| Param ID | Key | Physical | Additive | Sampler | Sine | Description |
|----------|-----|:--------:|:--------:|:-------:|:----:|-------------|
| `0x01` | `beat_scale` | — | yes | — | — | Beating scale |
| `0x02` | `noise_level` | — | yes | — | — | Attack noise level |
| `0x03` | `pan_spread` | — | yes | — | — | Pan spread |
| `0x04` | `stereo_decorr` | — | yes | — | — | Stereo decorrelation |
| `0x05` | `keyboard_spread` | yes | yes | yes | yes | Keyboard L/R spread (rad) |
| `0x06` | `eq_strength` | — | yes | — | — | EQ strength |
| `0x07` | `rng_seed` | — | yes | — | — | Random seed |

### Core-Specific `setParam` keys (full list per core)

| Key | Physical | Additive | Sampler | Sine | Description |
|-----|:--------:|:--------:|:-------:|:----:|-------------|
| `beat_scale` | — | yes | — | — | Beating amplitude scale (0-4) |
| `noise_level` | — | yes | — | — | Attack noise level (0-4) |
| `pan_spread` | — | yes | — | — | Per-partial pan spread (0-pi rad) |
| `stereo_decorr` | — | yes | — | — | Stereo decorrelation (0-2) |
| `keyboard_spread` | yes | yes | yes | yes | Stereo pan from note position (0-pi rad) |
| `eq_strength` | — | yes | — | — | Spectral EQ strength (0-1) |
| `rng_seed` | — | yes | — | — | Random seed (0-9999) |
| `brightness` | yes | — | — | — | Scales T60_nyq (0.1-4) |
| `stiffness_scale` | yes | — | — | — | Scales inharmonicity B (0.1-4) |
| `sustain_scale` | yes | — | — | — | Scales T60_fund (0.1-4) |
| `stereo_spread` | yes | — | — | — | Multi-string pan width (0-1) |
| `gauge_scale` | yes | — | — | — | Gauge scaling (0.5-4, no DSP effect) |
| `gain` | — | — | yes | yes | Output gain (0-2) |
| `detune_cents` | — | — | — | yes | Global detuning (cents) |
| `release_time` | — | — | yes | — | Release envelope (0.1-4s) |

### Engine-Level (param_id 0x10-0x13, always applied)

| Param ID | Range | Description |
|----------|-------|-------------|
| `0x10` | 0-2.0 | Master gain |
| `0x11` | -1..1 | Master pan (0=center) |
| `0x12` | 0-2.0 | LFO pan speed (Hz) |
| `0x13` | 0-1.0 | LFO pan depth |

### DspChain (param_id 0x20-0x26, always applied)

| Param ID | Range | Description |
|----------|-------|-------------|
| `0x20` | 0-1.0 | Limiter threshold (0=-40dB, 1=0dB) |
| `0x21` | 0-1.0 | Limiter release (0=10ms, 1=2000ms) |
| `0x22` | 0-1.0 | Limiter enabled (≥0.5 = on) |
| `0x23` | 0-1.0 | BBE definition (0=off, 1=+12dB @ 5kHz) |
| `0x24` | 0-1.0 | BBE bass boost (0=off, 1=+10dB @ 180Hz) |
| `0x25` | 0-1.0 | Convolver enabled (≥0.5 = on) |
| `0x26` | 0-1.0 | Convolver mix (0.0=dry, 1.0=full wet) |

---

## SET_BANK (0x03) — Chunked Bank Upload

| Feature | Physical | Additive | Sampler | Sine |
|---------|:--------:|:--------:|:-------:|:----:|
| `loadBankJson` | yes | yes | — | — |

JSON bank split into SysEx-safe chunks (max ~240 bytes each).
Header: 3-byte chunk_idx + 3-byte total_chunks (7-bit encoding).

```
F0 7D 01 03 <core_id> <idx_hi> <idx_mid> <idx_lo> <tot_hi> <tot_mid> <tot_lo> <json_data...> F7
```

---

## EXPORT_BANK (0x72)

| Feature | Physical | Additive | Sampler | Sine |
|---------|:--------:|:--------:|:-------:|:----:|
| `exportBankJson` | yes | yes | — | — |

Payload is ASCII file path:
```
F0 7D 01 72 <core_id> <path bytes...> F7
```

---

## MIDI Queue

- Lock-free SPSC ring buffer (512 events)
- Events queued from MIDI callback thread
- Drained at start of each audio processBlock (before synthesis)
- Overflow: logged as warning, event dropped

