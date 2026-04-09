# ICR — MIDI Command Reference

Complete table of MIDI messages accepted by the ICR engine.

## Standard MIDI Messages

### Note On / Off

| Status | Data | Description |
|--------|------|-------------|
| `0x90 ch` | `midi vel` | Note On (vel 1-127). Routed to active core `noteOn(midi, vel)` |
| `0x90 ch` | `midi 0` | Note Off (velocity 0 = note off) |
| `0x80 ch` | `midi vel` | Note Off. Routed to active core `noteOff(midi)` |

### Control Change (CC)

| Status | CC# | Range | Description |
|--------|-----|-------|-------------|
| `0xB0` | **64** | 0-127 | Sustain pedal. ≥64 = down (delays note-off), <64 = up (releases held notes) |
| `0xB0` | **7** | 0-127 | Channel volume → `setMasterGain()`. Square law: `(v/127)² × 2` |
| `0xB0` | **10** | 0-127 | Pan → `setMasterPan()`. 0=left, 64=center, 127=right |
| `0xB0` | **93** | 0-127 | LFO pan speed → `setPanSpeed()`. 0=off, 127=2 Hz |
| `0xB0` | **91** | 0-127 | LFO pan depth → `setPanDepth()`. 0=off, 127=100% |
| `0xB0` | **74** | 0-127 | Limiter threshold → `setLimiterThreshold()`. 0=-40 dB, 127=0 dB |

---

## SysEx Protocol

Frame format: `F0 7D 01 <cmd> <core_id> <data...> F7`

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

| Cmd | Name | Payload | Description |
|-----|------|---------|-------------|
| `0x70` | PING | (none) | Returns PONG: `F0 7D 01 71 F7` |
| `0x01` | SET_NOTE_PARAM | `midi vel param_id value(5)` | Set per-note parameter |
| `0x02` | SET_NOTE_PARTIAL | `midi vel k param_id value(5)` | Set per-partial parameter |
| `0x03` | SET_BANK | `chunk_idx(3) total(3) data...` | Upload JSON bank (chunked) |
| `0x10` | SET_MASTER | `param_id value(5)` | Set engine/core/DSP parameter |
| `0x72` | EXPORT_BANK | `path (ASCII)` | Export bank JSON to file path |

### Value Encoding (5 bytes)

Float values are encoded as 5 × 7-bit MIDI-safe bytes:
```
bytes[0..4] → 35-bit integer → reinterpret as IEEE 754 float32
```

---

## SET_NOTE_PARAM (0x01) — Per-Note Parameters

### Additive Core (core_id 0x01)

| Param ID | Key | Description |
|----------|-----|-------------|
| `0x01` | `f0_hz` | Fundamental frequency (Hz) |
| `0x02` | `B` | Inharmonicity coefficient |
| `0x03` | `attack_tau` | Attack transient decay (s) |
| `0x04` | `A_noise` | Attack noise amplitude |
| `0x05` | `rms_gain` | RMS output gain |
| `0x06` | `phi_diff` | Phase difference (stereo) |

### Physical Core (core_id 0x02)

Accepts any bank key via `setNoteParam(midi, vel, key, value)`:

| Key | Description |
|-----|-------------|
| `f0_hz` | Fundamental frequency |
| `B` | Inharmonicity coefficient |
| `gauge` | String thickness |
| `T60_fund` | Fundamental decay time (s) |
| `T60_nyq` | Nyquist decay time (s) |
| `exc_x0` | Hammer striking position |
| `n_disp_stages` | Dispersion allpass stages |
| `disp_coeff` | Per-stage allpass coefficient |
| `n_strings` | Unison strings (1-3) |
| `detune_cents` | String detuning (cents) |
| `K_hardening` | Velocity stiffness scaling (0-5) |
| `p_hardening` | Velocity exponent offset (0-1) |
| `output_scale` | Per-note output gain (0.01-0.5) |

Note: Physical core uses string key names (not param IDs) in its
`setNoteParam` implementation. SysEx param IDs 0x01-0x06 map to
additive core keys only.

---

## SET_NOTE_PARTIAL (0x02) — Per-Partial Parameters

### Additive Core only (core_id 0x01)

| Param ID | Key | Description |
|----------|-----|-------------|
| `0x10` | `f_hz` | Partial frequency (Hz) |
| `0x11` | `A0` | Partial amplitude |
| `0x12` | `tau1` | Fast decay time (s) |
| `0x13` | `tau2` | Slow decay time (s) |
| `0x14` | `a1` | Envelope mix ratio (fast/slow) |
| `0x15` | `beat_hz` | Beating frequency (Hz) |
| `0x16` | `phi` | Partial phase (rad) |

Physical core: N/A (no partial concept — returns false).

---

## SET_MASTER (0x10) — Global Parameters

### Core-Specific (param_id 0x01-0x07, routed to core's setParam)

| Param ID | Key | Description |
|----------|-----|-------------|
| `0x01` | `beat_scale` | Beating scale (additive) |
| `0x02` | `noise_level` | Attack noise level |
| `0x03` | `pan_spread` | Pan spread |
| `0x04` | `stereo_decorr` | Stereo decorrelation |
| `0x05` | `keyboard_spread` | Keyboard L/R spread (rad) |
| `0x06` | `eq_strength` | EQ strength (additive) |
| `0x07` | `rng_seed` | Random seed |

### Engine-Level (param_id 0x10-0x13)

| Param ID | Range | Description |
|----------|-------|-------------|
| `0x10` | 0-2.0 | Master gain |
| `0x11` | -1..1 | Master pan (0=center) |
| `0x12` | 0-2.0 | LFO pan speed (Hz) |
| `0x13` | 0-1.0 | LFO pan depth |

### DspChain (param_id 0x20-0x24)

| Param ID | Range | Description |
|----------|-------|-------------|
| `0x20` | 0-1.0 | Limiter threshold (0=-40dB, 1=0dB) |
| `0x21` | 0-1.0 | Limiter release (0=10ms, 1=2000ms) |
| `0x22` | 0-1.0 | Limiter enabled (≥0.5 = on) |
| `0x23` | 0-1.0 | BBE definition (0=off, 1=+12dB @ 5kHz) |
| `0x24` | 0-1.0 | BBE bass boost (0=off, 1=+10dB @ 180Hz) |

---

## SET_BANK (0x03) — Chunked Bank Upload

JSON bank split into SysEx-safe chunks (max ~240 bytes each).
Header per chunk: 3-byte chunk_idx + 3-byte total_chunks (7-bit encoding).

```
F0 7D 01 03 <core_id> <idx_hi> <idx_mid> <idx_lo> <tot_hi> <tot_mid> <tot_lo> <json_data...> F7
```

When all chunks received, calls `core->loadBankJson(json_string)`.

---

## EXPORT_BANK (0x72) — Bank Export

Payload is ASCII file path. Calls `core->exportBankJson(path)`.

```
F0 7D 01 72 <core_id> <path bytes...> F7
```

---

## MIDI Queue

- Lock-free SPSC ring buffer (512 events)
- Events queued from MIDI callback thread
- Drained at start of each audio processBlock (before synthesis)
- Overflow: logged as warning, event dropped
