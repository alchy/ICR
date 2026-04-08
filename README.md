# ICR — Ithaca Core Resonator

Physics-based real-time piano synthesizer. Replaces WAV sample playback with parametric additive synthesis driven by parameters extracted from real piano recordings and refined via a neural network training pipeline.

## How it works

```
WAV recordings  →  extract physical params  →  NN training + fine-tuning
                →  export ICR JSON params   →  real-time synthesis (C++)
```

- **Synthesis**: 2-string bi-exponential additive synth per partial (60 partials × 128 voices)
- **Parameters**: per-note, per-velocity-layer (88 × 8 = 704 entries), fitted from real KS Grand recordings
- **Spectral EQ**: min-phase IIR biquad cascade (5 sections) fitted from LTASE measurements
- **Stereo**: constant-power pan + per-partial independent phase offset + Schroeder all-pass decorrelation

## Quick start

### Build

```bat
cmake -B build -G "Visual Studio 17 2022" -A x64
cmake --build build --config Release
```

Binaries: `build/bin/Release/ICRGUI.exe` (GUI), `build/bin/Release/ICR.exe` (CLI)

### Run

```bat
build\bin\Release\ICRGUI.exe --core AdditiveSynthesisPianoCore --params soundbanks\params-piano-soundbank.json
```

A bundled soundbank (`soundbanks/params-piano-soundbank.json`) is included in the repository.
It was fitted from KS Grand recordings and includes per-note spectral EQ.

### Train

```bash
pip install -r training/requirements.txt

# Simple (no NN, ~15 min)
python run-training.py simple --bank "C:/SoundBanks/IthacaPlayer/ks-grand"

# Full (NN + finetune, ~60 min)
python run-training.py full --bank "C:/SoundBanks/IthacaPlayer/ks-grand"
```

See `docs/TRAIN_BUILD_RUN.md` for the complete guide.

## Repository structure

```
engine/        C++ real-time engine (CoreEngine, ISynthCore, MIDI, miniaudio)
cores/         Pluggable synth cores (AdditiveSynthesisPianoCore, SineCore)
dsp/           DSP chain (limiter, BBE)
gui/           Dear ImGui frontend
third_party/   Vendored deps (nlohmann/json, RtMidi)
training/      Python training pipeline
soundbanks/    Parameter JSON files (not in git — generate or copy manually)
docs/          Documentation
```

## Requirements

**C++ build:** Visual Studio 2022 + CMake ≥ 3.16 (Windows); GCC/Clang on Linux/macOS  
**Python training:** Python 3.10+, PyTorch, see `training/requirements.txt`

## Documentation

See [`docs/README.md`](docs/README.md) for the full documentation index.

- [`docs/engine/ARCHITECTURE.md`](docs/engine/ARCHITECTURE.md) -- engine architecture, 3-layer Ithaca Core pattern
- [`docs/engine/BUILD.md`](docs/engine/BUILD.md) -- build instructions, CLI options
- [`docs/cores/additive-synthesis-piano/`](docs/cores/additive-synthesis-piano/OVERVIEW.md) -- additive piano: training pipeline, JSON schema, SysEx params
- [`docs/cores/physical-modeling-piano/`](docs/cores/physical-modeling-piano/OVERVIEW.md) -- waveguide piano: v0.1 status, TODO
- [`docs/tools/SOUND_EDITOR.md`](docs/tools/SOUND_EDITOR.md) -- 3D soundbank editor
