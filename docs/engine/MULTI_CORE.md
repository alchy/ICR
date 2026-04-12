# ICR -- Multi-Core Architecture

## Overview

ICR runs multiple synthesis cores in parallel.  Each core is an independent
ISynthCore instance with its own voice pool, parameters, and state.  MIDI
events are routed to the **active** core only, but **all** instantiated
cores continue to produce audio (so voices from a previously active core
decay naturally -- dozvuk).

```
                  +-- MIDI events --+
                  |                 |
                  v                 |
         [Active Core]             |  (receives noteOn/noteOff/sustain)
              |                    |
              v                    |
    +===================+          |
    | All Instantiated  |          |
    | Cores:            |          |
    |  - SamplerCore    |<---------+  (all get processBlock, audio summed)
    |  - AdditiveSynth  |
    |  - PhysicalModel  |
    |  - SineCore       |
    +===================+
              |
              v
        [Master Bus]
        (gain, LFO, DspChain)
              |
              v
        [Audio Output]
```

## Core Lifecycle

### Registration (static init, before main)

Each core self-registers via the `REGISTER_SYNTH_CORE` macro in its `.cpp`:

```cpp
// cores/sampler/sampler_core.cpp
REGISTER_SYNTH_CORE("SamplerCore", SamplerCore)
```

This adds a factory lambda to `SynthCoreRegistry`.  No central list to maintain.

### Instantiation (lazy, on first selection)

Cores are **not** instantiated at startup.  Only the initial core (from
`--core` or `icr-config.json` `default_core`) is created.  Other cores
are instantiated when the user selects them in the GUI for the first time.

```
User selects "PhysicalModelingPianoCore" in GUI
  -> Engine::switchCore("PhysicalModelingPianoCore", "")
     -> core not in cores_ map -> SynthCoreRegistry::create()
     -> core->load(params_path_from_config, sr, logger)
     -> stored in cores_ map, set as active
     -> old core remains in map, its voices dozvuk naturally
```

### Keep-alive

Once instantiated, a core stays in memory for the lifetime of the engine.
Switching back to a previously used core is instant (no reload).

### Destruction

All cores are destroyed when `Engine` is destroyed (application exit).

## icr-config.json

Engine configuration file, auto-loaded from the directory next to the
executable.  Defines per-core settings and the default core.

```json
{
  "log_file": "icr.log",
  "default_core": "SamplerCore",
  "cores": {
    "SamplerCore": {
      "params_path": "C:/SoundBanks/IthacaPlayer"
    },
    "AdditiveSynthesisPianoCore": {
      "params_path": "",
      "soundbank_dir": "soundbanks"
    },
    "PhysicalModelingPianoCore": {
      "params_path": ""
    },
    "SineCore": {
      "params_path": ""
    }
  }
}
```

### Config keys

| Key | Level | Description |
|-----|-------|-------------|
| `log_file` | top | Path to log file (relative to exe dir). Empty = no file log. |
| `default_core` | top | Core to load at startup if `--core` not specified |
| `cores` | top | Per-core configuration object |
| `params_path` | per-core | Path passed to `core->load()`. Meaning is core-specific. |
| `soundbank_dir` | per-core | Directory for soundbank JSON files (AdditiveSynthesis) |

### Priority

CLI flags always override config values:

```
--core           >  config.default_core       >  "SineCore"
--params         >  config.cores.X.params_path >  ""
--engine-config  >  auto-detect icr-config.json
```

## ISynthCore Interface

Every core implements this interface (`engine/i_synth_core.h`):

### Lifecycle

| Method | Thread | Description |
|--------|--------|-------------|
| `load(params_path, sr, logger)` | Main | Load parameters, initialize at sample rate |
| `setSampleRate(sr)` | Main | Change sample rate (only when audio stopped) |
| `isLoaded()` | Any | True after successful load() |
| `coreName()` | Any | Human-readable name (e.g. "SamplerCore") |
| `coreVersion()` | Any | Version string |

### MIDI (RT thread only)

| Method | Description |
|--------|-------------|
| `noteOn(midi, velocity)` | Trigger a note. velocity=0 treated as noteOff. |
| `noteOff(midi)` | Release a note (begins release phase / dozvuk) |
| `sustainPedal(down)` | Sustain pedal state. Delays noteOff until released. |
| `allNotesOff()` | Silence all voices immediately. |

### Audio (RT thread, zero-alloc)

| Method | Description |
|--------|-------------|
| `processBlock(out_l, out_r, n)` | **Additive** -- adds output to buffers (caller zeroes). Returns true if any voice active. |

### Parameters (GUI thread)

| Method | Description |
|--------|-------------|
| `setParam(key, value)` | Set a global parameter. Returns false if unknown. |
| `getParam(key, &out)` | Read a global parameter. Returns false if unknown. |
| `describeParams()` | Full parameter list with metadata (drives GUI sliders). |

### Per-note SysEx updates (MIDI callback thread)

| Method | Default | Description |
|--------|---------|-------------|
| `setNoteParam(midi, vel, key, value)` | `false` | Update one scalar field for a (midi, vel) slot |
| `setNotePartialParam(midi, vel, k, key, value)` | `false` | Update one per-partial field |
| `loadBankJson(json_str)` | `false` | Replace all parameters from JSON string |
| `exportBankJson(path)` | `false` | Serialize current bank to JSON file |

These methods have default implementations returning `false` -- cores that don't
support per-note editing (e.g. SineCore, PhysicalModelingPianoCore) don't need
to override them.

### Visualization (GUI thread, may allocate)

| Method | Description |
|--------|-------------|
| `getVizState()` | Snapshot of active voices, last note, partials, EQ for GUI display |

---

## Per-Core SysEx

The SysEx protocol ([SYSEX_PROTOCOL.md](SYSEX_PROTOCOL.md)) defines the
framing.  **Parameter IDs and semantics are core-specific** -- each core
defines its own SysEx vocabulary.

### Core addressing

SysEx messages can target a specific core via a **core ID byte** in the
protocol header.  This allows the Sound Editor to send parameters to a
non-active core (e.g. editing AdditiveSynthesisPianoCore while SamplerCore
is playing).

```
F0 7D 01 <cmd> <core_id> <data...> F7
                  |
                  +-- 0x00 = active core (default, backwards-compatible)
                      0x01 = AdditiveSynthesisPianoCore
                      0x02 = PhysicalModelingPianoCore
                      0x03 = SamplerCore
                      0x04 = SineCore
                      0x7F = engine-level (SET_MASTER with core_id=0x7F
                             targets Engine/DspChain, not any core)
```

When `core_id = 0x00`, the message is dispatched to the currently active
core (legacy behavior, matching the original protocol).

### Dispatch flow

```
MIDI SysEx frame arrives
  -> Engine::handleSysEx()
     1. Strip manufacturer header (0x7D 0x01)
     2. Read command byte and core_id
     3. Resolve target core:
        - core_id 0x00 -> active_core_
        - core_id 0x01-0x04 -> cores_[name] (if instantiated)
        - core_id 0x7F -> engine-level (master/dsp params)
     4. Dispatch to target:
        - 0x01: target->setNoteParam(midi, vel, key, value)
        - 0x02: target->setNotePartialParam(midi, vel, k, key, value)
        - 0x03: target->loadBankJson(chunked JSON)
        - 0x10: target->setParam(key, value) or engine atomics
        - 0x70: PING -> PONG
        - 0x72: target->exportBankJson(path)
```

### Core-specific param ID tables

Each core defines its own SysEx param IDs.  These are **not interchangeable**
-- sending an AdditiveSynthesisPianoCore param ID to SamplerCore will return
`false` (unknown key).

| Core | core_id | SysEx support | Documentation |
|------|---------|--------------|---------------|
| AdditiveSynthesisPianoCore | 0x01 | Full: note params, partial params, bank, export | [SYSEX_PARAMS.md](../cores/additive-synthesis-piano/SYSEX_PARAMS.md) |
| PhysicalModelingPianoCore | 0x02 | Planned: physical params (K_H, p, tau) | [TODO](../cores/physical-modeling-piano/TODO.md) |
| SamplerCore | 0x03 | Bank loading (SET_BANK), envelope params | [OVERVIEW](../cores/sampler/OVERVIEW.md) |
| SineCore | 0x04 | Minimal: gain, detune_cents via SET_MASTER | [OVERVIEW](../cores/sine/OVERVIEW.md) |

### Per-core SysEx examples

**AdditiveSynthesisPianoCore** -- update partial tau1 for MIDI 60, vel 3, partial 5:
```
F0 7D 01  02  01  3C  03  05  12  <float32>  F7
          |   |   |   |   |   |
          |   |   |   |   |   +-- param_id 0x12 = tau1
          |   |   |   |   +------ k = 5 (partial index)
          |   |   |   +---------- vel = 3
          |   |   +-------------- midi = 60 (0x3C)
          |   +------------------ core_id = 0x01 (AdditiveSynthesis)
          +---------------------- cmd = 0x02 (SET_NOTE_PARTIAL)
```

**SamplerCore** -- set envelope release time:
```
F0 7D 01  10  03  03  <float32>  F7
          |   |   |
          |   |   +-- param_id 0x03 = release_time
          |   +------ core_id = 0x03 (SamplerCore)
          +---------- cmd = 0x10 (SET_MASTER -> core setParam)
```

**Engine-level** -- set master gain (affects all cores):
```
F0 7D 01  10  7F  10  <float32>  F7
          |   |   |
          |   |   +-- param_id 0x10 = master_gain
          |   +------ core_id = 0x7F (engine-level)
          +---------- cmd = 0x10 (SET_MASTER)
```

### Adding SysEx to a new core

1. Choose a `core_id` byte (register in this document)
2. Override `setNoteParam()` / `setNotePartialParam()` in your core
3. Define param_id -> key mappings (can be in core .cpp or a shared table)
4. Document in `docs/cores/{your-core}/SYSEX_PARAMS.md` with:
   - Param ID table (id, key, range, description)
   - Wire format examples
   - Python usage examples
5. Override `loadBankJson()` / `exportBankJson()` if applicable

---

## Registered Cores

| Core | Registration string | params_path meaning | Bank selector |
|------|--------------------|--------------------|---------------|
| `SamplerCore` | `"SamplerCore"` | Base directory for WAV banks (e.g. `C:/SoundBanks/IthacaPlayer`) | GUI combo: subdirectories |
| `AdditiveSynthesisPianoCore` | `"AdditiveSynthesisPianoCore"` | Soundbank JSON path (or empty) | GUI combo: `soundbanks-additive/*.json` |
| `PhysicalModelingPianoCore` | `"PhysicalModelingPianoCore"` | Optional JSON with overrides (or empty = physics defaults) | -- |
| `SineCore` | `"SineCore"` | Ignored (no params needed) | -- |

---

## How to Add a New Core

### 1. Create files

```
cores/my_core/
    my_core.h       // Voice + VoiceManager + PatchManager + MyCore class
    my_core.cpp     // Implementation + REGISTER_SYNTH_CORE
```

### 2. Implement ISynthCore

Follow the 3-layer pattern:

```
MyCore (ISynthCore adapter)
  +-- MyPatchManager (MIDI -> native params)
        +-- MyVoiceManager (voice pool lifecycle)
              +-- MyVoice[128] (independent audio units)
```

Minimum required overrides:
- `load()`, `setSampleRate()`
- `noteOn()`, `noteOff()`, `sustainPedal()`, `allNotesOff()`
- `processBlock()` (RT-safe, additive output)
- `setParam()`, `getParam()`, `describeParams()`
- `getVizState()`
- `coreName()`, `coreVersion()`, `isLoaded()`

### 3. Register

In the `.cpp` file (NOT header):
```cpp
#include "engine/synth_core_registry.h"
REGISTER_SYNTH_CORE("MyCore", MyCore)
```

### 4. Include in build

Add to `CMakeLists.txt`:
```cmake
set(CORE_SOURCES
    ...
    cores/my_core/my_core.cpp
)
```

Add `#include` in `main.cpp` and `main_gui.cpp` to trigger static registration.

### 5. Configure

Add to `icr-config.json`:
```json
"MyCore": {
  "params_path": "/path/to/params"
}
```

### 6. Document

Create `docs/cores/my-core/OVERVIEW.md` with:
- Synthesis approach
- GUI parameters (from `describeParams()`)
- SysEx param IDs (if applicable)
- Source file paths

---

## Progressive Voice Gain (AGC)

Header: `dsp/agc.h` — standalone, header-only, no STL, RT-safe.
Portable to ARM MCUs and DSP chips.

### Problem

Voices sum additively.  With N active voices at full amplitude, peak
output = N x (voice amplitude).  20 voices can produce 20.0 peak — the
limiter would crush this to 1.0 causing massive pumping and distortion.

### Solution

AGC measures per-block RMS after all cores sum their output, then smoothly
adjusts gain to keep the signal near a target level.  It only attenuates
(never amplifies), preventing clipping without audible artifacts.

### Signal chain position

```
Cores sum -> AGC -> Master gain/LFO -> DspChain (convolver -> BBE -> limiter)
```

AGC runs **before** master gain, so the user's volume control is not affected.
The limiter remains as a last-resort safety net.

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `target_rms` | 0.15 (~-16 dB) | Desired output RMS level |
| `attack_ms` | 5 ms | Gain reduction speed (fast, catches transients) |
| `release_ms` | 200 ms | Gain recovery speed (slow, avoids pumping) |
| `gain_floor` | 0.05 | Minimum gain (prevents silence during very loud passages) |

### API (`dsp/agc.h`)

```cpp
#include "dsp/agc.h"

dsp::AgcState agc;
dsp::agc_init(agc, sample_rate);              // once at init
dsp::agc_process(agc, out_l, out_r, n);       // per block, in-place
dsp::agc_reset(agc);                          // reset to unity gain
```

`AgcState` is a plain struct (no vtable, no pointers) — can be memcpy'd,
serialized, or placed in shared memory for multi-core HW architectures.

### Per-core vs master bus

Currently one AGC instance runs on the **master bus** (in Engine).
For per-core AGC (e.g. when distributing cores to separate HW), each core
can include `dsp/agc.h` and maintain its own `AgcState`:

```cpp
// In your core's processBlock:
dsp::agc_process(my_agc_, out_l, out_r, n_samples);
```

---

## Velocity Mapping

Each core maps MIDI velocity (1-127) to amplitude differently.
The mapping should provide natural piano-like dynamic response.

| Core | Velocity curve | Description |
|------|---------------|-------------|
| SineCore | Linear `v/127` | Simple, direct |
| SamplerCore | Quadratic `(v/127)^2` + layer crossfade | Natural feel, two layers blended |
| AdditiveSynthesisPianoCore | Layer interpolation (8 layers, each with own `rms_gain`) | Velocity selects timbre AND amplitude via `lerpNoteParams` |
| PhysicalModelingPianoCore | Physics: `hammer_vel = 0.5 + 4.0 * (v/127)^1.5` m/s | Chabassier model, affects spectrum + energy |

### Guidelines for new cores

- Never use raw linear velocity — it sounds unnatural
- Quadratic `(v/127)^2` is a good minimum for dynamic response
- For piano-like instruments, velocity should affect **timbre** (spectral
  content, attack brightness) not just amplitude
- The AGC handles polyphonic level management — individual voices don't
  need to worry about total output level

---

## Threading Summary

| Thread | Who calls | What it does |
|--------|-----------|-------------|
| **RT (audio callback)** | miniaudio | `processBlock()` on ALL cores, `noteOn/Off` on active core |
| **MIDI callback** | RtMidi | Pushes events to lock-free queue (drained by RT thread) |
| **GUI thread** | ImGui/GLFW | `setParam()`, `getParam()`, `getVizState()`, `switchCore()` |
| **Bank loader** | SamplerCore | Background `std::thread` for async WAV loading |

### Thread safety rules

- `processBlock()`: zero-alloc, no locks, no I/O
- `setParam()`/`getParam()`: use `std::atomic<float>` for RT safety
- `loadBankJson()`: may lock `bank_mutex_` (RT thread uses `try_lock`)
- `switchCore()`: modifies `active_core_` pointer (atomic, GUI thread only)
- Bank loading: background thread, signals completion via `std::atomic<bool>`
