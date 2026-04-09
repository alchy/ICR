# ICR -- Build & Platforms

## Quick Build

```bash
cmake -B build
cmake --build build --config Release
```

Binaries:
- `build/bin/Release/icr` -- headless CLI
- `build/bin/Release/icrgui` -- GUI application

## CMake Options

| Option | Default | Description |
|--------|---------|-------------|
| `ICR_BUILD_GUI` | `ON` | Build `icrgui` (requires OpenGL + GLFW) |
| `ICR_BUILD_CLI` | `ON` | Build `icr` (headless) |
| `ICR_USE_AVX2` | `ON` | Enable AVX2/FMA on x86_64 |

## Platform-Specific Build

### Windows (x86_64)

```bash
cmake -B build
cmake --build build --config Release
```

Requirements: VS 2022 with C++ workload, CMake 3.16+.
Audio: WASAPI (via miniaudio). MIDI: Windows Multimedia API (via RtMidi).

### macOS (x86_64 / Apple Silicon)

```bash
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
```

Requirements: Xcode command line tools, CMake 3.16+.
Audio: CoreAudio. MIDI: CoreMIDI.
Frameworks linked automatically: CoreAudio, AudioToolbox, CoreFoundation, CoreMIDI.

### Linux (x86_64)

```bash
# Install dependencies
sudo apt install cmake build-essential libasound2-dev libgl-dev libglfw3-dev

cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
```

Audio: ALSA (default) or JACK (auto-detected via pkg-config).
MIDI: ALSA MIDI.

### Raspberry Pi (Linux ARM, headless)

```bash
# Install dependencies
sudo apt install cmake build-essential libasound2-dev

# Build CLI only (no GUI, no AVX2)
cmake -B build -DCMAKE_BUILD_TYPE=Release \
      -DICR_BUILD_GUI=OFF -DICR_USE_AVX2=OFF
cmake --build build
```

Audio: ALSA. MIDI: ALSA MIDI.
No GUI dependencies needed (no OpenGL/GLFW).
AVX2 disabled (ARM has NEON, not AVX).

### CI / Headless Server

```bash
cmake -B build -DCMAKE_BUILD_TYPE=Release -DICR_BUILD_GUI=OFF
cmake --build build
```

Useful for batch rendering and automated testing without display.

## Dependencies

### C++ (auto-fetched by CMake)

| Library | Version | Purpose | Source |
|---------|---------|---------|--------|
| miniaudio | header-only | Cross-platform audio I/O | Vendored in `engine/` |
| RtMidi | header-only | Cross-platform MIDI I/O | Vendored in `third_party/` |
| nlohmann/json | header-only | JSON parsing | Vendored in `third_party/` |
| GLFW | 3.4 | Window management (GUI only) | FetchContent |
| Dear ImGui | 1.91.9 | Immediate-mode GUI (GUI only) | FetchContent |
| OpenGL | 3.0+ | Rendering (GUI only) | System |

### Python (optional, additive pipeline only)

```bash
pip install numpy scipy soundfile
```

## Platform Abstraction Layers

```
┌─────────────────────────────────────────────────┐
│  ISynthCore implementations (pure C++ math)     │  ← 100% portable
│  DspChain (convolver, BBE, limiter)             │
├─────────────────────────────────────────────────┤
│  CoreEngine (RT loop, MIDI queue, master bus)   │  ← portable (std::atomic)
├─────────────────────────────────────────────────┤
│  miniaudio          │  RtMidi         │ GLFW    │  ← platform abstraction
│  (audio device)     │  (MIDI ports)   │ (window)│
├─────────────────────┼─────────────────┼─────────┤
│  WASAPI/CoreAudio/  │  WinMM/CoreMIDI/│ Win32/  │  ← platform-specific
│  ALSA/JACK          │  ALSA           │ Cocoa/X │     (handled by libs)
└─────────────────────┴─────────────────┴─────────┘
```

No platform `#ifdef` in synthesis cores, DSP chain, or engine logic.
Platform-specific code is isolated to terminal keyboard input
(`main.cpp`: Windows `conio.h` vs POSIX `termios`).

## Run

### GUI

```bash
icrgui --core PhysicalModelingPianoCore
icrgui --core AdditiveSynthesisPianoCore --params soundbanks-additive/bank.json
```

### Headless CLI

```bash
icr --core PhysicalModelingPianoCore
```

Keyboard fallback: `a-k` = C4-C5, `z` = sustain, `q` = quit.

### Batch Render (offline, no audio device)

```bash
icr --core PhysicalModelingPianoCore \
    --render-batch batch.json --out-dir output/ --sr 48000
```

Batch JSON format:
```json
[
  {"midi": 60, "vel_idx": 3, "duration_s": 2.5},
  {"midi": 72, "vel_idx": 7, "duration_s": 2.5}
]
```

Output: `m060-v03-f48.wav` (stereo 16-bit PCM).

### CLI Options

| Option | Description |
|--------|-------------|
| `--core <name>` | Synthesis core (default: SineCore) |
| `--params <path>` | Core parameter JSON |
| `--config <path>` | Engine config JSON |
| `--core-param key=val` | Override core parameter (repeatable) |
| `--port <N>` | MIDI input port index (default: 0) |
| `--render-batch <json>` | Offline batch render |
| `--out-dir <dir>` | Output directory for batch render |
| `--sr <hz>` | Sample rate (default: 48000) |
| `--list-cores` | Print available cores and exit |

## Configuration

`icr-config.json` (copied next to executable by CMake):

```json
{
  "log_file": "icr.log",
  "default_core": "SineCore",
  "cores": {
    "PhysicalModelingPianoCore": {
      "params_path": "soundbanks-physical/teng-v2-default.json",
      "soundbank_dir": "soundbanks-physical",
      "ir_path": "soundbanks-soundboard/pl-grand-04072006-soundboard.wav",
      "master_gain": "100",
      "limiter_enabled": "64",
      "bbe_definition": "30",
      "bbe_bass_boost": "20"
    }
  }
}
```

Per-core keys: `params_path`, `soundbank_dir`, `ir_path`, `master_gain`,
`master_pan`, `lfo_speed`, `lfo_depth`, `limiter_threshold`,
`limiter_release`, `limiter_enabled`, `bbe_definition`, `bbe_bass_boost`.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Build fails | Ensure C++17 compiler (VS 2022+, GCC 9+, Clang 10+) |
| `LINK: fatal error` | Close running icrgui before rebuild |
| No MIDI ports | Check device manager, install MIDI driver |
| Silent output | Check `--params` path, verify JSON has `notes` key |
| Convolver clipping | Reduce mix slider (default 50% = 2% real mix) |
| RPi: no GUI | Build with `-DICR_BUILD_GUI=OFF` |
| RPi: slow | Reduce polyphony, use shorter IR, or headless mode |
