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
| 0xF0 | PING              | Editor → Synth |
| 0xF1 | PONG              | Synth → Editor |

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
F0 7D 01  03  <chunk_hi> <chunk_lo>  <total_hi> <total_lo>  <data…>  F7
              └─────────────────────────────────────────────┘
              chunk index (2 bytes BE) + total chunks (2 bytes BE)
```

| Field         | Bytes | Description                           |
|---------------|-------|---------------------------------------|
| chunk index   | 2     | 0-based, big-endian                   |
| total chunks  | 2     | total number of chunks, big-endian    |
| data          | ≤240  | raw JSON bytes (UTF-8)                |

- Chunk size is 240 bytes max (safe SysEx limit).
- 2 ms inter-chunk delay to allow the synthesizer to buffer.
- Synth reassembles all chunks and applies the new bank atomically.

---

## 0x10 — SET_MASTER

Update a global synthesis parameter.

```
F0 7D 01  10  <param_id>  <v0 v1 v2 v3 v4>  F7
```

Uses the same Scalar Param IDs table as SET_NOTE_PARAM.
Master parameters affect all notes simultaneously (e.g. global `beat_scale`, `noise_level`).

---

## 0xF0 / 0xF1 — PING / PONG

```
F0 7D 01  F0  F7     # PING (editor → synth)
F0 7D 01  F1  F7     # PONG (synth → editor)
```

Used to verify MIDI connectivity before sending a bank.

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
    case 0xF0:  // PING → returns {F0 7D 01 F1 F7}
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
        case 0x02: return "B";          // NOTE: not runtime-settable in PianoCore
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

**SET_MASTER param IDs:** The protocol specifies the same ID table as SET_NOTE_PARAM,
mapping to `ISynthCore::setParam` string keys (e.g. `"beat_scale"`, `"noise_level"`).
However, the ID table only covers per-note keys (`f0_hz`, `B`, etc.) — none of which
are global `setParam` keys in `PianoCore`. SET_MASTER currently succeeds silently
only for keys that overlap (none at present). A dedicated master param ID table
should be defined in a future protocol revision.

**PONG:** Requires `MidiInput::openOutput(port_index)` to be called after
`MidiInput::open()`. If no output port is open, PING is processed but PONG is
dropped. Port index must be selected manually (no auto-pairing with the input port).
