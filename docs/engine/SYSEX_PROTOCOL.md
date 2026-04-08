# ICR SysEx Protocol

SysEx communication between the ICR Sound Editor and the ICR synthesizer (ICRGUI / ICR.exe).

Per-core parameter IDs are documented in each core's own docs:
- [AdditiveSynthesisPianoCore SysEx params](../cores/additive-synthesis-piano/SYSEX_PARAMS.md)

## Frame format

```
F0  7D  01  <cmd>  <data...>  F7
|   |   |   |
|   |   |   +-- command byte
|   |   +------ device ID (ICR = 0x01)
|   +---------- manufacturer ID (0x7D = non-commercial)
+-------------- SysEx start
```

All multi-byte values are encoded in **7-bit SysEx-safe bytes** (no byte >= 0x80 inside the frame).

---

## Commands

| cmd  | Name              | Direction      | Scope |
|------|-------------------|----------------|-------|
| 0x01 | SET_NOTE_PARAM    | Editor -> Synth | Core-specific |
| 0x02 | SET_NOTE_PARTIAL  | Editor -> Synth | Core-specific |
| 0x03 | SET_BANK          | Editor -> Synth | Core-specific |
| 0x10 | SET_MASTER        | Editor -> Synth | Engine + Core |
| 0x70 | PING              | Editor -> Synth | Engine |
| 0x71 | PONG              | Synth -> Editor | Engine |
| 0x72 | EXPORT_BANK       | Editor -> Synth | Engine |

---

## Float32 encoding

A 32-bit float is packed as **5 x 7-bit bytes** (35 bits; 3 padding bits at MSB).

```python
raw  = struct.pack(">f", value)        # big-endian IEEE 754
bits = int.from_bytes(raw, "big")      # 32-bit int
out  = [(bits >> (i * 7)) & 0x7F  for i in range(4, -1, -1)]   # 5 bytes
```

C++ decoding:

```cpp
uint32_t bits = 0;
for (int i = 0; i < 5; ++i)
    bits |= (uint32_t)(b[i] & 0x7F) << ((4 - i) * 7);
float v;
std::memcpy(&v, &bits, sizeof(v));
```

---

## 0x03 -- SET_BANK

Send the full soundbank as chunked JSON.

```
F0 7D 01  03  <ci2> <ci1> <ci0>  <tc2> <tc1> <tc0>  <data...>  F7
              +-- chunk index --+  +-- total chunks --+
              each value encoded as 3 x 7-bit bytes (big-endian, 21-bit range)
```

- Chunk size: 240 bytes max.
- 2 ms inter-chunk delay.
- Synth reassembles all chunks and applies the new bank atomically.

---

## 0x10 -- SET_MASTER

Update a global or engine-level parameter.

```
F0 7D 01  10  <param_id>  <v0 v1 v2 v3 v4>  F7
```

### ISynthCore global params (0x01-0x07)

Routed to `ISynthCore::setParam`. Param IDs and semantics depend on the active core.

### CoreEngine mix params (0x10-0x13)

| id   | key           | Range       | Description                   |
|------|---------------|-------------|-------------------------------|
| 0x10 | `master_gain` | 0.0-2.0     | Output gain (1.0 = unity)     |
| 0x11 | `master_pan`  | -1.0-+1.0   | Stereo pan (0 = centre)       |
| 0x12 | `lfo_speed`   | 0.0-2.0 Hz  | LFO panning rate              |
| 0x13 | `lfo_depth`   | 0.0-1.0     | LFO panning depth             |

### DspChain params (0x20-0x24) -- normalised 0.0-1.0

| id   | key                  | 0.0         | 1.0       |
|------|----------------------|-------------|-----------|
| 0x20 | `limiter_threshold`  | -40 dB      | 0 dB      |
| 0x21 | `limiter_release`    | 10 ms       | 2000 ms   |
| 0x22 | `limiter_enabled`    | off         | on (>=0.5)|
| 0x23 | `bbe_definition`     | 0 dB shelf  | +12 dB    |
| 0x24 | `bbe_bass_boost`     | 0 dB shelf  | +10 dB    |

---

## 0x70 / 0x71 -- PING / PONG

```
F0 7D 01  70  F7     # PING (editor -> synth)
F0 7D 01  71  F7     # PONG (synth -> editor)
```

Used to verify MIDI connectivity before sending a bank.

**PONG** requires `MidiInput::openOutput(port_index)` to be called after `open()`.

---

## 0x72 -- EXPORT_BANK

Ask ICR to serialize its current in-memory bank to a JSON file.

```
F0 7D 01  F2  <path bytes...>  F7
```

---

## C++ dispatch (CoreEngine::handleSysEx)

```cpp
switch (cmd) {
case 0x01:  // SET_NOTE_PARAM   -> core->setNoteParam(midi, vel, key, value)
case 0x02:  // SET_NOTE_PARTIAL -> core->setNotePartialParam(midi, vel, k, key, value)
case 0x03:  // SET_BANK         -> chunk reassembly -> core->loadBankJson()
case 0x10:  // SET_MASTER       -> core->setParam() or engine/dsp atomics
case 0x70:  // PING             -> returns PONG
case 0x72:  // EXPORT_BANK      -> core->exportBankJson(path)
}
```
