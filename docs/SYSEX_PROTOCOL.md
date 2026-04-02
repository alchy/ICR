# ICR SysEx Protocol

SysEx communication between the ICR Sound Editor and the ICR synthesizer (ICRGUI / ICR.exe).

## Frame format

```
F0  7D  01  <cmd>  <data…>  F7
│   │   │   │
│   │   │   └─ command byte
│   │   └───── device ID (ICR = 0x01)
│   └───────── manufacturer ID (0x7D = non-commercial)
└───────────── SysEx start
```

All multi-byte values are encoded in **7-bit SysEx-safe bytes** (no byte ≥ 0x80 inside the frame).

---

## Commands

| cmd  | Name              | Direction      |
|------|-------------------|----------------|
| 0x01 | SET_NOTE_PARAM    | Editor → Synth |
| 0x02 | SET_NOTE_PARTIAL  | Editor → Synth |
| 0x03 | SET_BANK          | Editor → Synth |
| 0x10 | SET_MASTER        | Editor → Synth |
| 0x70 | PING              | Editor → Synth |
| 0x71 | PONG              | Synth → Editor |
| 0x72 | EXPORT_BANK       | Editor → Synth |

---

## Float32 encoding

A 32-bit float is packed as **5 × 7-bit bytes** (35 bits; 3 padding bits at MSB).

```
raw  = struct.pack(">f", value)        # big-endian IEEE 754
bits = int.from_bytes(raw, "big")      # 32-bit int
out  = [(bits >> (i * 7)) & 0x7F  for i in range(4, -1, -1)]   # 5 bytes
```

Decoding on the synth side (C++):

```cpp
uint32_t bits = 0;
for (int i = 4; i >= 0; --i)
    bits |= (uint32_t)(data[pos + (4 - i)] & 0x7F) << (i * 7);
float value;
memcpy(&value, &bits, 4);
```

---

## 0x01 — SET_NOTE_PARAM

Update one scalar parameter for a specific (midi, velocity) slot.

```
F0 7D 01  01  <midi>  <vel>  <param_id>  <v0 v1 v2 v3 v4>  F7
                                          └── float32, 5 bytes
```

| Field      | Bytes | Description              |
|------------|-------|--------------------------|
| `midi`     | 1     | MIDI note number (21–108)|
| `vel`      | 1     | Velocity layer (0–7)     |
| `param_id` | 1     | see Scalar Param IDs     |
| value      | 5     | float32, 7-bit encoded   |

### Scalar Param IDs

| id   | key          | Description              |
|------|--------------|--------------------------|
| 0x01 | `f0_hz`      | Fundamental frequency    |
| 0x02 | `B`          | Inharmonicity coefficient|
| 0x03 | `attack_tau` | Noise attack time const  |
| 0x04 | `A_noise`    | Noise amplitude          |
| 0x05 | `rms_gain`   | RMS output gain          |
| 0x06 | `phi_diff`   | Phase difference         |

---

## 0x02 — SET_NOTE_PARTIAL

Update one per-partial parameter for a specific (midi, velocity, partial index).

```
F0 7D 01  02  <midi>  <vel>  <k>  <param_id>  <v0 v1 v2 v3 v4>  F7
```

| Field      | Bytes | Description                      |
|------------|-------|----------------------------------|
| `midi`     | 1     | MIDI note (21–108)               |
| `vel`      | 1     | Velocity layer (0–7)             |
| `k`        | 1     | Partial index, 1-based (1..60)   |
| `param_id` | 1     | see Per-Partial Param IDs        |
| value      | 5     | float32, 7-bit encoded           |

### Per-Partial Param IDs

| id   | key       | Description                    |
|------|-----------|--------------------------------|
| 0x10 | `f_hz`    | Partial frequency (Hz)         |
| 0x11 | `A0`      | Initial amplitude              |
| 0x12 | `tau1`    | Fast decay time constant       |
| 0x13 | `tau2`    | Slow decay time constant       |
| 0x14 | `a1`      | Fast decay mixing coefficient  |
| 0x15 | `beat_hz` | String beating frequency (Hz)  |
| 0x16 | `phi`     | Initial phase                  |

---

## 0x03 — SET_BANK

Send the full soundbank as chunked JSON.

```
F0 7D 01  03  <ci2> <ci1> <ci0>  <tc2> <tc1> <tc0>  <data…>  F7
              └── chunk index ──┘  └── total chunks ──┘
              each value encoded as 3 × 7-bit bytes (big-endian, 21-bit range)
```

| Field         | Bytes | Description                                       |
|---------------|-------|---------------------------------------------------|
| chunk index   | 3     | 0-based; 7-bit bytes: `(idx>>14)&7F (idx>>7)&7F idx&7F` |
| total chunks  | 3     | same encoding; supports up to 2 M chunks (≈480 MB)|
| data          | ≤240  | raw JSON bytes (ASCII, all < 0x80)                |

- Chunk size is 240 bytes max (safe SysEx limit).
- 2 ms inter-chunk delay to allow the synthesizer to buffer.
- Synth reassembles all chunks and applies the new bank atomically.

---

## 0x10 — SET_MASTER

Update a global or engine-level parameter.

```
F0 7D 01  10  <param_id>  <v0 v1 v2 v3 v4>  F7
```

param_id uses its own table (separate from SET_NOTE_PARAM):

### ISynthCore global params (0x01–0x07)

| id   | key               | Range      | Description                    |
|------|-------------------|------------|--------------------------------|
| 0x01 | `beat_scale`      | 0.0–4.0    | Scales all beat_hz values      |
| 0x02 | `noise_level`     | 0.0–4.0    | Noise amplitude multiplier     |
| 0x03 | `pan_spread`      | 0.0–π rad  | Within-note string spread      |
| 0x04 | `stereo_decorr`   | 0.0–2.0    | Schroeder all-pass strength    |
| 0x05 | `keyboard_spread` | 0.0–π rad  | L–R spread across keyboard     |
| 0x06 | `eq_strength`     | 0.0–1.0    | Spectral EQ wet/dry blend      |
| 0x07 | `rng_seed`        | 0–9999     | Base RNG seed (applied at noteOn) |

### CoreEngine mix params (0x10–0x13)

| id   | key           | Range       | Description                   |
|------|---------------|-------------|-------------------------------|
| 0x10 | `master_gain` | 0.0–2.0     | Output gain (1.0 = unity)     |
| 0x11 | `master_pan`  | −1.0–+1.0   | Stereo pan (0 = centre)       |
| 0x12 | `lfo_speed`   | 0.0–2.0 Hz  | LFO panning rate              |
| 0x13 | `lfo_depth`   | 0.0–1.0     | LFO panning depth             |

### DspChain params (0x20–0x24) — normalised 0.0–1.0

| id   | key                  | 0.0         | 1.0       |
|------|----------------------|-------------|-----------|
| 0x20 | `limiter_threshold`  | −40 dB      | 0 dB      |
| 0x21 | `limiter_release`    | 10 ms       | 2000 ms   |
| 0x22 | `limiter_enabled`    | off         | on (≥0.5) |
| 0x23 | `bbe_definition`     | 0 dB shelf  | +12 dB    |
| 0x24 | `bbe_bass_boost`     | 0 dB shelf  | +10 dB    |

---

## 0x70 / 0x71 — PING / PONG

```
F0 7D 01  70  F7     # PING (editor → synth)
F0 7D 01  71  F7     # PONG (synth → editor)
```

Used to verify MIDI connectivity before sending a bank.

---

## 0xF2 — EXPORT_BANK

Ask ICR to serialize its current in-memory bank to a JSON file.

```
F0 7D 01  F2  <path bytes…>  F7
              └── ASCII file path on the ICR host machine
```

| Field | Bytes | Description                                  |
|-------|-------|----------------------------------------------|
| path  | 1–240 | Absolute file path, raw ASCII (no 0x80+ bytes) |

- ICR writes a JSON file at `path` in the same format as the soundbank loaded by `SET_BANK`.
- The file can be pulled back by the editor and compared with the original params JSON to validate the SysEx round-trip.
- No reply is sent; success/failure is visible in the ICR log.

### Python usage

```python
bridge.export_bank("C:/tmp/icr-exported.json")
```

---

## Python usage (SysExBridge)

```python
from sysex_bridge import SysExBridge

bridge = SysExBridge()
bridge.open("loopMIDI Port 1")

# Update one scalar param
bridge.set_note_param(midi=60, vel=3, param_key="rms_gain", value=0.12)

# Update one partial param
bridge.set_note_partial(midi=60, vel=3, k=1, param_key="tau1", value=0.35)

# Send full soundbank (chunked automatically)
import json
data = json.dumps(soundbank_dict).encode("utf-8")
bridge.set_bank(data)

bridge.close()
```

---

## C++ receiver — implemented (ICR side)

The protocol is fully implemented. Entry points:

| File | Symbol | Role |
|------|--------|------|
| `engine/midi_input.cpp` | `MidiInput::callback` | Strips F0/F7, calls `handleSysEx`, sends PONG |
| `engine/core_engine.cpp` | `CoreEngine::handleSysEx` | Dispatch + SET_BANK reassembly |
| `cores/piano/piano_core.cpp` | `PianoCore::setNoteParam` | Writes one scalar field |
| `cores/piano/piano_core.cpp` | `PianoCore::setNotePartialParam` | Writes one partial field |
| `cores/piano/piano_core.cpp` | `PianoCore::loadBankJson` | Full bank swap |
| `cores/piano/piano_core.cpp` | `PianoCore::exportBankJson` | Serialize bank to JSON file |

### Dispatch (CoreEngine::handleSysEx)

```cpp
// data: bytes AFTER F0, BEFORE F7
std::vector<uint8_t> CoreEngine::handleSysEx(const uint8_t* data, int len) {
    if (len < 3 || data[0] != 0x7D || data[1] != 0x01) return {};
    uint8_t cmd = data[2];
    const uint8_t* payload = data + 3;
    int payloadLen = len - 3;
    switch (cmd) {
    case 0x01:  // SET_NOTE_PARAM
    case 0x02:  // SET_NOTE_PARTIAL
    case 0x03:  // SET_BANK (chunk reassembly → loadBankJson when complete)
    case 0x10:  // SET_MASTER → core->setParam(key, value)
    case 0x70:  // PING → returns {F0 7D 01 71 F7}
    case 0x72:  // EXPORT_BANK → core->exportBankJson(path)
    }
    return {};
}
```

### Float decoding (core_engine.cpp)

```cpp
static float decodeSysExFloat(const uint8_t* b) {
    uint32_t bits = 0;
    for (int i = 0; i < 5; ++i)
        bits |= (uint32_t)(b[i] & 0x7F) << ((4 - i) * 7);
    float v;
    std::memcpy(&v, &bits, sizeof(v));
    return v;
}
```

### param_id → key mapping (core_engine.cpp)

```cpp
// Scalar note params (commands 0x01 and 0x10)
static const char* noteParamKey(uint8_t id) {
    switch (id) {
        case 0x01: return "f0_hz";
        case 0x02: return "B";          // recomputes all partial f_hz = k*f0*sqrt(1+B*k²)
        case 0x03: return "attack_tau";
        case 0x04: return "A_noise";
        case 0x05: return "rms_gain";
        case 0x06: return "phi_diff";
        default:   return nullptr;
    }
}

// Per-partial params (command 0x02)
static const char* partialParamKey(uint8_t id) {
    switch (id) {
        case 0x10: return "f_hz";
        case 0x11: return "A0";
        case 0x12: return "tau1";
        case 0x13: return "tau2";
        case 0x14: return "a1";
        case 0x15: return "beat_hz";
        case 0x16: return "phi";
        default:   return nullptr;
    }
}
```

### Notes and limitations

**`B` (inharmonicity, 0x02):** Defined in the protocol but `PianoCore::setNoteParam`
returns `false` for this key — inharmonicity is baked into partial frequencies at
export time. To change `B`, re-export the soundbank and push it via `SET_BANK`.

**SET_MASTER param ID ranges:** IDs 0x01–0x07 route to `ISynthCore::setParam`;
0x10–0x13 write directly to `CoreEngine` atomics; 0x20–0x24 write to `DspChain`
(normalised 0.0–1.0 → uint8 0–127). On the Python side, use `MASTER_PARAM_IDS`
dict in `sysex_bridge.py` — `set_master()` raises `ValueError` for unknown keys.

**PONG:** Requires `MidiInput::openOutput(port_index)` to be called after
`MidiInput::open()`. If no output port is open, PING is processed but PONG is
dropped. Port index must be selected manually (no auto-pairing with the input port).
