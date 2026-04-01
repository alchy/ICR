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
build\bin\Release\ICRGUI.exe --core PianoCore --params soundbanks\params-ks-grand-ft.json
```

### Train (full pipeline)

```bash
pip install -r training/requirements.txt

python training/train_pipeline.py \
    --bank "C:/SoundBanks/IthacaPlayer/ks-grand" \
    --finetune
```

See `docs/TRAIN_BUILD_RUN.md` for the complete guide.

## Repository structure

```
engine/        C++ real-time engine (CoreEngine, ISynthCore, MIDI, miniaudio)
cores/         Pluggable synth cores (PianoCore, SineCore)
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

- [`docs/TRAIN_BUILD_RUN.md`](docs/TRAIN_BUILD_RUN.md) — full training pipeline, build, and run guide
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — C++ engine architecture
- [`docs/ANALYSIS.md`](docs/ANALYSIS.md) — acoustic physics reference (papers)
