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

## C++ receiver skeleton (ICR side)

```cpp
void ICRSynth::handleSysEx(const uint8_t* data, int len) {
    // data starts AFTER F0, ends BEFORE F7
    if (len < 3) return;
    if (data[0] != 0x7D || data[1] != 0x01) return;  // not ICR

    uint8_t cmd = data[2];
    const uint8_t* payload = data + 3;
    int payloadLen = len - 3;

    switch (cmd) {
    case 0x01:  // SET_NOTE_PARAM
        if (payloadLen < 8) return;
        handleSetNoteParam(payload);
        break;
    case 0x02:  // SET_NOTE_PARTIAL
        if (payloadLen < 9) return;
        handleSetNotePartial(payload);
        break;
    case 0x03:  // SET_BANK
        handleBankChunk(payload, payloadLen);
        break;
    case 0x10:  // SET_MASTER
        if (payloadLen < 6) return;
        handleSetMaster(payload);
        break;
    case 0xF0:  // PING → reply with PONG
        sendPong();
        break;
    }
}

float decodeSysExFloat(const uint8_t* b) {
    uint32_t bits = 0;
    for (int i = 0; i < 5; ++i)
        bits |= (uint32_t)(b[i] & 0x7F) << ((4 - i) * 7);
    float v; memcpy(&v, &bits, 4);
    return v;
}
```
