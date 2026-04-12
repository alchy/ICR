# ICR Engine — API Reference

C++17 modular synthesizer engine with pluggable synthesis cores,
real-time audio processing, MIDI control, and SysEx protocol support.

## Initialization Sequence

Proper initialization from program start through audio processing:

1. **Logger** (`engine/logger.h`)
   ```cpp
   Logger logger(stdout, stdout);   // file_out, rt_out
   ```

2. **AppConfig** — parse CLI arguments (`engine/app_config.h`)
   ```cpp
   AppConfig cfg;
   int rc = cfg.parse(argc, argv, /*gui_mode=*/false);
   if (rc != 0) return (rc == 2) ? 0 : 1;
   ```

3. **Engine** — create and initialize (`engine/engine.h`)
   ```cpp
   auto engine = std::make_unique<Engine>();
   cfg.initEngine(*engine, logger);   // loads config, core, IR, params
   ```

4. **Start audio**
   ```cpp
   engine->start();   // opens audio device, begins RT callback
   ```

5. **MIDI events**
   ```cpp
   engine->noteOn(60, 100);    // C4, velocity 100
   engine->noteOff(60);
   engine->sustainPedal(127);  // pedal down (>=64)
   ```

6. **Shutdown**
   ```cpp
   engine->stop();
   // Engine destructor handles cleanup
   ```

## Complete Playback Example

```cpp
#include "engine/app_config.h"
#include "engine/midi_input.h"

int main() {
    ICR_ENABLE_FTZ();

    Logger logger(stdout, stdout);
    Engine engine;

    // Load config + initialize core
    engine.loadEngineConfig("icr-config.json", logger);
    engine.initialize("AdditiveSynthesisPianoCore", "", "", logger);

    // Set master mix parameters (MIDI 0-127)
    engine.setMasterGain(100, logger);   // ~78% gain
    engine.setMasterPan(64);             // center
    engine.setPanSpeed(0);               // no LFO
    engine.setPanDepth(0);

    // Start audio device
    engine.start();

    // Play a note
    engine.noteOn(60, 100);    // C4, forte
    // ... wait ...
    engine.noteOff(60);

    // Change block size at runtime (any positive integer)
    engine.setBlockSize(512);  // ~10.7 ms latency @ 48kHz

    // Switch to a different synthesis core (lazy instantiation)
    engine.switchCore("PhysicalModelingPianoCore", "");

    engine.stop();
    return 0;
}
```

## Engine Class

Central coordinator — owns synthesis cores, MIDI queue, and delegates to
MasterBus, DspChain, AudioDevice, SysExHandler, and EngineConfig modules.

### Lifecycle

| Method | Parameters | Description | Return |
|--------|------------|-------------|--------|
| `Engine()` | — | Create engine instance | — |
| `~Engine()` | — | Stop audio, free resources | — |
| `loadEngineConfig(path, logger)` | `path`: JSON path, `logger` | Load per-core config (params_path, DSP defaults) | bool |
| `initialize(core, params, config, logger, from, to)` | Core name, paths, MIDI range | Instantiate core, load parameters, allocate buffers | bool |
| `start()` | — | Open audio device, begin RT callback | bool |
| `stop()` | — | Stop audio device (blocks until RT thread exits) | void |
| `switchCore(name, params)` | Core name, params path | Switch active core (lazy instantiation, no audio gap) | bool |
| `setBlockSize(size)` | Any positive int | Change block size at runtime (restarts audio if running) | bool |
| `isRunning()` | — | Check if audio device is active | bool |
| `isInitialized()` | — | Check if core is loaded and ready | bool |

### MIDI Control (thread-safe, lock-free)

| Method | Parameters | Description |
|--------|------------|-------------|
| `noteOn(midi, velocity)` | 0-127, 0-127 | Trigger note on active core |
| `noteOff(midi)` | 0-127 | Release note |
| `sustainPedal(val)` | 0-127 (>=64 = down) | Sustain pedal CC64 |
| `allNotesOff()` | — | Silence all voices immediately |

### Master Mix (MIDI 0-127 setters)

| Method | MIDI Range | Physical Range | Description |
|--------|-----------|----------------|-------------|
| `setMasterGain(val, logger)` | 0-127 | 0.0–2.0 (square law) | Master output volume |
| `setMasterPan(val)` | 0-127 | L–C–R (64 = center) | Stereo panorama |
| `setPanSpeed(val)` | 0-127 | 0.0–2.0 Hz | LFO panning speed |
| `setPanDepth(val)` | 0-127 | 0.0–1.0 | LFO panning depth |

### DSP Chain

| Method | MIDI Range | Description |
|--------|-----------|-------------|
| `setLimiterThreshold(val)` | 0-127 | Brick-wall limiter threshold |
| `setLimiterRelease(val)` | 0-127 | Limiter release time |
| `setLimiterEnabled(val)` | 0/127 | Limiter on/off |
| `setBBEDefinition(val)` | 0-127 | BBE high-frequency definition |
| `setBBEBassBoost(val)` | 0-127 | BBE low-frequency boost |

### Accessors

| Method | Return | Description |
|--------|--------|-------------|
| `core()` | `ISynthCore*` | Active synthesis core |
| `activeCoreName()` | `const string&` | Name of active core |
| `coreByName(name)` | `ISynthCore*` | Access specific core (nullptr if not loaded) |
| `masterBus()` | `MasterBus&` | Direct access to master bus module |
| `getDspChain()` | `DspChain*` | DSP chain (limiter, BBE, convolver) |
| `config()` | `EngineConfig&` | Engine config (per-core JSON settings) |
| `getLogger()` | `Logger&` | Logger instance |
| `sampleRate()` | int | Current sample rate (Hz) |
| `blockSize()` | int | Current audio block size (samples) |
| `activeVoices()` | int | Number of currently sounding voices |
| `getOutputPeakLin()` | float | Peak output level (0.0–1.0+) |

## Module Reference

### MasterBus (`engine/master_bus.h`, header-only)

Post-core master gain, stereo pan, and LFO panning.
Thread-safe atomics for GUI/MIDI writes, RT-safe `process()`.

| Method | Description |
|--------|-------------|
| `setGainMidi(uint8_t)` | MIDI 0-127 → square-law gain 0..2 |
| `setPanMidi(uint8_t)` | MIDI 0-127, 64 = center |
| `setGain(float)` | Direct gain 0..2 (SysEx) |
| `setPan(float l, float r)` | Direct L/R coefficients |
| `setLfoSpeed(float hz)` | LFO frequency 0..2 Hz |
| `setLfoDepth(float d)` | LFO depth 0..1 |
| `process(L, R, n, sr)` | Apply gain + LFO to audio buffers (RT-safe) |

### EngineConfig (`engine/engine_config.h/cpp`)

Per-core JSON configuration persistence.

| Method | Description |
|--------|-------------|
| `load(path, logger)` | Parse icr-config.json |
| `save(logger)` | Write current state to JSON |
| `value(core, key)` | Get per-core config value |
| `setValue(core, key, val)` | Set per-core config value |
| `defaultCoreName()` | Default core from config |
| `logFilePath()` | Log file path from config |

### AudioDevice (`engine/audio_device.h/cpp`)

miniaudio playback device wrapper.

| Method | Description |
|--------|-------------|
| `start(cb, userdata, sr, bs)` | Open and start audio device |
| `stop()` | Stop and uninit device |
| `setBlockSize(int)` | Change block size (stop → reinit → start) |
| `setSampleRate(int)` | Change sample rate (stop → reinit → start) |
| `isRunning()` | Check playback state |
| `sampleRate()` / `blockSize()` | Current device parameters |

### SysExHandler (`engine/sysex_handler.h/cpp`)

ICR SysEx protocol parser and dispatcher.

| Command | ID | Description |
|---------|-----|-------------|
| PING | 0x70 | Returns PONG (0x71) |
| SET_NOTE_PARAM | 0x01 | Per-note scalar parameter |
| SET_NOTE_PARTIAL | 0x02 | Per-partial parameter |
| SET_BANK | 0x03 | Chunked JSON bank replace |
| SET_MASTER | 0x10 | Engine/core/DSP global parameters |
| EXPORT_BANK | 0x72 | Export bank JSON to file |

### BatchRenderer (`engine/batch_renderer.h/cpp`)

Offline render: JSON spec → stereo 16-bit WAV files.  No audio device needed.

```cpp
int n = renderBatch(*engine.core(), logger, "batch.json", "exports/", 48000);
```

### AppConfig (`engine/app_config.h/cpp`)

Shared CLI argument parsing for both `icr` and `icrgui` targets.

| Method | Description |
|--------|-------------|
| `parse(argc, argv, gui_mode)` | Parse CLI args (0=ok, 1=error, 2=early exit) |
| `initEngine(engine, logger)` | Common init: config → core → IR → params |

## Block Size and Latency

Block size determines the audio processing granularity and output latency.
Smaller values = lower latency, higher CPU load.

| Block Size | Latency @ 48kHz | Latency @ 44.1kHz | Use Case |
|------------|-----------------|---------------------|----------|
| 64 | 1.3 ms | 1.5 ms | Ultra-low latency, high CPU |
| 128 | 2.7 ms | 2.9 ms | Low latency |
| 256 | 5.3 ms | 5.8 ms | Default, good balance |
| 512 | 10.7 ms | 11.6 ms | Moderate latency, low CPU |
| 1024 | 21.3 ms | 23.2 ms | Batch processing, lowest CPU |

The `setBlockSize()` method accepts any positive integer — not limited to
powers of 2.  This is important for VST/JUCE integration where the host
controls the block size and may request non-standard values.

## Threading Model

- **RT thread** (audio callback): `processBlock()`, `MasterBus::process()`,
  `DspChain::process()`.  No alloc, no lock, no IO.
- **MIDI callback thread**: `SysExHandler::handle()`, `MidiInput::callback()`.
  Enqueues events via lock-free SPSC ring buffer.
- **GUI thread**: `setMasterGain()`, `setParam()`, `getVizState()`.
  Writes atomics, reads snapshots.
- **Main thread**: `initialize()`, `start()`, `stop()`, `switchCore()`.
  Not concurrent with RT thread (Engine handles sequencing).

## Per-Core Parameter Serialization

Each synthesis core has its own set of parameters that sound best with
different settings — a piano core needs different gain, DSP, and timbre
than a sine oscillator.  ICR persists all parameters per-core in
`icr-config.json` so switching between cores restores the last-used settings.

### What is serialized

| Category | Parameters | Storage key |
|----------|-----------|-------------|
| **Master bus** | gain, pan, LFO speed, LFO depth | `master_gain`, `master_pan`, `lfo_speed`, `lfo_depth` |
| **Limiter** | threshold, release, enabled | `limiter_threshold`, `limiter_release`, `limiter_enabled` |
| **BBE** | definition, bass boost | `bbe_definition`, `bbe_bass_boost` |
| **Convolver** | enabled, mix | `convolver_enabled`, `convolver_mix` |
| **Core-specific** | All params from `describeParams()` | `cp_<key>` (e.g. `cp_beat_scale`) |

DSP bus parameters are stored as MIDI integers (0-127).
Core-specific parameters are stored as float strings with the `cp_` prefix.

### When parameters are saved / loaded

| Event | Action |
|-------|--------|
| **Core switch (GUI)** | Save outgoing core → `saveCoreParams(old)`, load incoming → `loadCoreParams(new)` |
| **GUI exit** | Save active core → `saveCoreParams(active)`, then `saveConfig()` to disk |
| **Engine startup** | `applyDspDefaults()` restores DSP bus from config |

### icr-config.json structure

```json
{
  "default_core": "AdditiveSynthesisPianoCore",
  "log_file": "icr.log",
  "cores": {
    "AdditiveSynthesisPianoCore": {
      "params_path": "soundbanks-additive/ks-grand.json",
      "ir_path": "soundbanks-soundboard/grand-ir.wav",
      "master_gain": "51",
      "master_pan": "64",
      "limiter_enabled": "0",
      "cp_beat_scale": "1.0",
      "cp_noise_level": "0.5",
      "cp_keyboard_spread": "0.8"
    },
    "PhysicalModelingPianoCore": {
      "params_path": "soundbanks-physical/D-grand.json",
      "master_gain": "37",
      "cp_brightness": "1.2",
      "cp_stiffness_scale": "1.0"
    }
  }
}
```

### Auto-discovery of new parameters

Core-specific parameters are serialized via `ISynthCore::describeParams()`.
When a new parameter is added to a core's `describeParams()` implementation,
it is automatically included in serialization — no GUI or config code changes
needed.  On first load, missing `cp_` keys are simply skipped (core uses its
compiled default).

### API

```cpp
// Save all params (DSP + core-specific) for a core to config
engine.saveCoreParams("AdditiveSynthesisPianoCore");

// Load all params from config and apply to engine + core
engine.loadCoreParams("AdditiveSynthesisPianoCore");

// Persist to disk
engine.saveConfig();
```

### Core-specific parameters by core

**AdditiveSynthesisPianoCore:**

| Key | Group | Range | Description |
|-----|-------|-------|-------------|
| `beat_scale` | Timbre | 0.0–4.0 | Multi-string beating intensity |
| `noise_level` | Timbre | 0.0–4.0 | Noise floor level |
| `pan_spread` | Stereo | 0.0–π | Per-string pan angle (radians) |
| `stereo_decorr` | Stereo | 0.0–2.0 | Stereo decorrelation amount |
| `keyboard_spread` | Stereo | 0.0–π | Low-to-high pan spread |
| `eq_strength` | Timbre | 0.0–1.0 | Spectral EQ curve intensity |
| `rng_seed` | Debug | 0–9999 | Random seed for phase init |

**PhysicalModelingPianoCore:**

| Key | Group | Range | Description |
|-----|-------|-------|-------------|
| `brightness` | Timbre | 0.1–4.0 | High-frequency damping scale |
| `stiffness_scale` | Timbre | 0.1–4.0 | String stiffness multiplier |
| `sustain_scale` | Timbre | 0.1–4.0 | Decay time multiplier |
| `keyboard_spread` | Stereo | 0.0–π | Low-to-high pan spread |
| `stereo_spread` | Stereo | 0.0–1.0 | Multi-string stereo width |

**SamplerCore:**

| Key | Group | Range | Description |
|-----|-------|-------|-------------|
| `gain` | Output | 0.0–2.0 | Sample playback gain |
| `keyboard_spread` | Stereo | 0.0–π | Low-to-high pan spread |
| `release_time` | Envelope | 0.1–4.0 | Release envelope duration (s) |

**SineCore:**

| Key | Group | Range | Description |
|-----|-------|-------|-------------|
| `gain` | Output | 0.0–2.0 | Oscillator output gain |
| `detune_cents` | Tuning | -100–100 | Pitch offset in cents |
| `keyboard_spread` | Stereo | 0.0–π | Low-to-high pan spread |

## Architecture

```
┌───────────────────────────────────────────────────────────┐
│ AudioDevice (miniaudio wrapper)                           │
│   audioCallback() → Engine::processBlock()                │
├───────────────────────────────────────────────────────────┤
│ Engine                                                    │
│   MIDI queue (lock-free SPSC) → drain → active core      │
│   Multi-core: all cores produce audio (dozvuk tails)      │
│   → AGC → MasterBus → DspChain → interleave → output     │
├───────────────────────────────────────────────────────────┤
│ ISynthCore implementations                                │
│   AdditiveSynthesisPianoCore  │  PhysicalModelingPianoCore│
│   SamplerCore                 │  SineCore                 │
├───────────────────────────────────────────────────────────┤
│ Support modules                                           │
│   EngineConfig │ SysExHandler │ BatchRenderer │ AppConfig │
└───────────────────────────────────────────────────────────┘
```
