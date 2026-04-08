# ICR SysEx Protocol

SysEx communication between the ICR Sound Editor and the ICR synthesizer.

Per-core parameter IDs are documented in each core's own docs:
- [AdditiveSynthesisPianoCore SysEx params](../cores/additive-synthesis-piano/SYSEX_PARAMS.md)
- [SamplerCore overview](../cores/sampler/OVERVIEW.md)

## Frame format

```
F0  7D  01  <cmd>  <core_id>  <data...>  F7
|   |   |   |      |
|   |   |   |      +-- target core (see Core ID table)
|   |   |   +--------- command byte
|   |   +------------- device ID (ICR = 0x01)
|   +----------------- manufacturer ID (0x7D = non-commercial)
+---------------------- SysEx start
```

All multi-byte values are encoded in **7-bit SysEx-safe bytes** (no byte >= 0x80 inside the frame).

Every command (except PING/PONG) includes a **core_id** byte that determines
which core instance receives the message.

---

## Core IDs

| core_id | Target |
|---------|--------|
| 0x00 | Active core (whichever is currently selected in GUI) |
| 0x01 | AdditiveSynthesisPianoCore |
| 0x02 | PhysicalModelingPianoCore |
| 0x03 | SamplerCore |
| 0x04 | SineCore |
| 0x7F | Engine-level (CoreEngine master mix + DspChain) |

If the targeted core is not yet instantiated, the message is silently ignored.

---

## Commands

| cmd  | Name              | Direction      | Scope |
|------|-------------------|----------------|-------|
| 0x01 | SET_NOTE_PARAM    | Editor -> Synth | Core-specific |
| 0x02 | SET_NOTE_PARTIAL  | Editor -> Synth | Core-specific |
| 0x03 | SET_BANK          | Editor -> Synth | Core-specific |
| 0x10 | SET_MASTER        | Editor -> Synth | Core (0x01-0x07) or Engine (0x10+) |
| 0x70 | PING              | Editor -> Synth | Engine (no core_id) |
| 0x71 | PONG              | Synth -> Editor | Engine |
| 0x72 | EXPORT_BANK       | Editor -> Synth | Core-specific |

---

## Float32 encoding

A 32-bit float is packed as **5 x 7-bit bytes** (35 bits; 3 padding bits at MSB).

```python
raw  = struct.pack(">f", value)
bits = int.from_bytes(raw, "big")
out  = [(bits >> (i * 7)) & 0x7F  for i in range(4, -1, -1)]
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

## 0x01 -- SET_NOTE_PARAM

```
F0 7D 01  01  <core_id>  <midi>  <vel>  <param_id>  <v0..v4>  F7
```

Update one scalar parameter for a specific (midi, velocity) slot.
The `param_id` -> key mapping is **core-specific** (see per-core docs).

---

## 0x02 -- SET_NOTE_PARTIAL

```
F0 7D 01  02  <core_id>  <midi>  <vel>  <k>  <param_id>  <v0..v4>  F7
```

Update one per-partial parameter.  `k` is 1-based partial index.

---

## 0x03 -- SET_BANK

```
F0 7D 01  03  <core_id>  <ci2><ci1><ci0>  <tc2><tc1><tc0>  <data...>  F7
```

Send the full soundbank as chunked JSON to the targeted core.

- Chunk index and total: 3 x 7-bit bytes each (21-bit range)
- Chunk size: 240 bytes max
- 2 ms inter-chunk delay
- Core reassembles and applies atomically via `loadBankJson()`

---

## 0x10 -- SET_MASTER

```
F0 7D 01  10  <core_id>  <param_id>  <v0..v4>  F7
```

### Routing by param_id

| param_id range | Target | Description |
|----------------|--------|-------------|
| 0x01-0x07 | `core->setParam()` on targeted core | Core-specific global params |
| 0x10-0x13 | CoreEngine atomics | Master mix (always engine, core_id ignored) |
| 0x20-0x24 | DspChain | Limiter + BBE (always engine, core_id ignored) |

### Core params (0x01-0x07) -- core-specific

These IDs are passed to `target->setParam(key, value)`.  The key mapping
depends on the core.  See per-core SysEx docs for tables.

### Engine params (0x10-0x13)

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
F0 7D 01  70  F7     # PING (no core_id)
F0 7D 01  71  F7     # PONG (reply)
```

Engine-level connectivity check.  No core_id byte (exception to the rule).

---

## 0x72 -- EXPORT_BANK

```
F0 7D 01  72  <core_id>  <path bytes...>  F7
```

Serialize the targeted core's in-memory bank to a JSON file at `path`.

---

## Examples

**Set partial tau1 on AdditiveSynthesisPianoCore (MIDI 60, vel 3, k=5):**
```
F0 7D 01  02  01  3C 03 05  12  <float32>  F7
          |   |   |  |  |   |
          cmd |   m  v  k  param_id=tau1
         core_id=0x01 (Additive)
```

**Set master gain (engine-level):**
```
F0 7D 01  10  7F  10  <float32>  F7
          |   |   |
          cmd |  param_id=master_gain
         core_id=0x7F (engine)
```

**Set hammer_hardness on PhysicalModelingPianoCore:**
```
F0 7D 01  10  02  01  <float32>  F7
          |   |   |
          cmd |  param_id=0x01 (core-specific: hammer_hardness)
         core_id=0x02 (PhysicalModeling)
```

---

## C++ dispatch (CoreEngine::handleSysEx)

```cpp
// 1. Parse header: cmd = data[2], core_id = data[3]
// 2. Resolve target:
//      0x00 -> active_core_
//      0x01-0x04 -> cores_[coreIdToName(id)]
//      0x7F -> engine-level (no core target)
// 3. Dispatch cmd to target->setNoteParam / loadBankJson / etc.
// 4. SET_MASTER: pid 0x01-0x07 -> target->setParam()
//               pid 0x10-0x24 -> engine/dsp atomics (always)
```
