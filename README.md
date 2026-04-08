# ICR -- Ithaca Core Resonator

Real-time piano synthesizer with pluggable synthesis cores.  Supports both
analysis-resynthesis (additive) and physical modelling (waveguide) approaches.

## Synthesis Cores

| Core | Approach | Description |
|------|----------|-------------|
| **AdditiveSynthesisPianoCore** | Additive | 60-partial resynthesis from WAV analysis, bi-exp envelopes, spectral EQ, 1/2/3-string beating |
| **PhysicalModelingPianoCore** | Waveguide | Digital waveguide with nonlinear hammer, loss filter, dispersion, 24 soundboard modes |
| **SineCore** | Reference | Single sine oscillator per voice, validates 3-layer architecture |

## Quick start

### Build

```bash
cmake -B build
cmake --build build --config Release
```

Binaries: `build/bin/Release/icrgui.exe` (GUI), `build/bin/Release/icr.exe` (CLI)

### Run

```bash
# Additive piano (requires soundbank JSON from WAV analysis)
icrgui.exe --core AdditiveSynthesisPianoCore --params soundbanks/pl-grand.json --ir soundbanks/pl-grand-soundboard.wav

# Physical modeling piano (playable without params -- uses physics defaults)
icrgui.exe --core PhysicalModelingPianoCore

# List available cores
icr.exe --list-cores
```

### Analyze WAV bank (additive pipeline)

```bash
pip install numpy scipy soundfile
python run-training.py analyze --bank C:/SoundBanks/IthacaPlayer/pl-grand
```

See [additive piano docs](docs/cores/additive-synthesis-piano/TRAIN_BUILD_RUN.md) for details.

## Repository structure

```
engine/        C++ real-time engine (CoreEngine, ISynthCore, MIDI, miniaudio)
cores/
  additive_synthesis_piano/   Additive resynthesis piano core
  physical_modeling_piano/    Digital waveguide piano core
  sine/                       Reference sine oscillator
dsp/           Master bus DSP chain (convolver, BBE, limiter)
gui/           Dear ImGui frontend (core-agnostic)
training/      Python analysis/extraction pipeline (for additive core)
soundbanks/    Parameter JSON + soundboard IR files
sound-editor/  3D web-based soundbank editor (Three.js + FastAPI)
docs/          Documentation (see below)
third_party/   Vendored deps (nlohmann/json, RtMidi)
```

## Requirements

**C++ build:** CMake >= 3.16, C++17 (VS 2022 on Windows; GCC/Clang on Linux/macOS)
**Python (additive pipeline only):** Python 3.12, numpy, scipy, soundfile

## Documentation

Full index: [`docs/README.md`](docs/README.md)

| Section | Content |
|---------|---------|
| [Engine architecture](docs/engine/ARCHITECTURE.md) | CoreEngine, ISynthCore, 3-layer Ithaca Core pattern, threading |
| [Build & run](docs/engine/BUILD.md) | cmake, CLI options, troubleshooting |
| [SysEx protocol](docs/engine/SYSEX_PROTOCOL.md) | MIDI SysEx for live parameter editing |
| [Additive piano](docs/cores/additive-synthesis-piano/OVERVIEW.md) | Signal chain, WAV pipeline, JSON schema, training modules |
| [Physical modeling piano](docs/cores/physical-modeling-piano/OVERVIEW.md) | Waveguide model, physics defaults, v0.1 status |
| [Sound editor](docs/tools/SOUND_EDITOR.md) | 3D soundbank editor with spline fitting |
