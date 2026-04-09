# ICR -- Ithaca Core Resonator

Real-time piano synthesizer with pluggable synthesis cores.  Supports both
analysis-resynthesis (additive) and physical modelling (waveguide) approaches.

## Synthesis Cores

| Core | Approach | Description |
|------|----------|-------------|
| **AdditiveSynthesisPianoCore** | Additive | 60-partial resynthesis from WAV analysis, bi-exp envelopes, spectral EQ, 1/2/3-string beating |
| **PhysicalModelingPianoCore** | Waveguide | Dual-rail waveguide (Teng/Smith) with Chaigne-Askenfelt hammer, multi-string stereo, soundboard IR |
| **SamplerCore** | Sample | WAV sample playback with velocity layers |
| **SineCore** | Reference | Single sine oscillator per voice, validates 3-layer architecture |

## Platform Support

| Platform | Audio | MIDI | GUI | Status |
|----------|-------|------|-----|--------|
| **Windows** (x86_64) | WASAPI | Windows MM | ImGui + GLFW + OpenGL3 | Primary |
| **macOS** (x86_64 / ARM) | CoreAudio | CoreMIDI | ImGui + GLFW + OpenGL3 | Supported |
| **Linux** (x86_64) | ALSA / JACK | ALSA MIDI | ImGui + GLFW + OpenGL3 | Supported |
| **Linux ARM** (Raspberry Pi) | ALSA | ALSA MIDI | Headless CLI | Supported |

All synthesis cores are 100% platform-independent (pure C++ math).
Platform I/O is handled by miniaudio (audio) and RtMidi (MIDI).

## Quick Start

```bash
cmake -B build
cmake --build build --config Release
```

```bash
# Physical modeling piano (no bank needed, physics defaults)
./build/bin/Release/icrgui --core PhysicalModelingPianoCore

# Headless CLI
./build/bin/Release/icr --core PhysicalModelingPianoCore

# List available cores
./build/bin/Release/icr --list-cores
```

## Documentation

| Section | Content |
|---------|---------|
| [Engine architecture](docs/engine/ARCHITECTURE.md) | Signal flow, threading, ISynthCore interface, 3-layer pattern |
| [Build & platforms](docs/engine/BUILD.md) | CMake options, cross-compile, platform details, troubleshooting |
| [SysEx protocol](docs/engine/SYSEX_PROTOCOL.md) | MIDI SysEx for live parameter editing |
| [Physical piano](docs/cores/physical-modeling-piano/OVERVIEW.md) | Dual-rail waveguide, Chaigne hammer, multi-string |
| [Physical piano notes](docs/cores/physical-modeling-piano/POZNATKY.md) | Allpass cascade, dispersion, velocity mapping |
| [Additive piano](docs/cores/additive-synthesis-piano/OVERVIEW.md) | Signal chain, WAV pipeline, JSON schema |
| [Sound editor](docs/tools/SOUND_EDITOR.md) | 3D soundbank editor with spline fitting |

## Repository Structure

```
engine/           C++ real-time engine (CoreEngine, ISynthCore, MIDI, miniaudio)
cores/
  additive_synthesis_piano/   Additive resynthesis piano
  physical_modeling_piano/    Dual-rail waveguide piano
  sampler/                    WAV sample playback
  sine/                       Reference sine oscillator
dsp/              Master bus DSP chain (convolver, BBE, limiter)
gui/              Dear ImGui frontend (core-agnostic)
tools-physical/   Python waveguide renderers (Teng v1/v2)
training/         Python analysis pipeline (for additive core)
soundbanks-additive/    Additive core parameter banks
soundbanks-physical/    Physical core parameter banks
soundbanks-soundboard/  Soundboard impulse response WAVs
soundbanks-sine/        SineCore dummy banks
docs/             Full documentation
third_party/      Vendored deps (nlohmann/json, RtMidi)
```

## Requirements

- **C++ build:** CMake >= 3.16, C++17 (VS 2022, GCC 9+, Clang 10+)
- **Python (optional):** Python 3.12, numpy, scipy, soundfile
