# ICR Documentation

## Engine (shared)

| Document | Content |
|----------|---------|
| [engine/ARCHITECTURE.md](engine/ARCHITECTURE.md) | CoreEngine, ISynthCore interface, 3-layer Ithaca Core pattern, threading, GUI |
| [engine/BUILD.md](engine/BUILD.md) | C++ build, Python prerequisites, CLI options, troubleshooting |
| [engine/SYSEX_PROTOCOL.md](engine/SYSEX_PROTOCOL.md) | SysEx frame format, float encoding, engine-level commands |
| [engine/MAC_OS_CHANGES.md](engine/MAC_OS_CHANGES.md) | macOS porting notes |

## Cores

### AdditiveSynthesisPianoCore

| Document | Content |
|----------|---------|
| [OVERVIEW](cores/additive-synthesis-piano/OVERVIEW.md) | Synthesis features, signal chain, voice state |
| [TRAIN_BUILD_RUN](cores/additive-synthesis-piano/TRAIN_BUILD_RUN.md) | WAV analysis pipeline, run commands, diagnostic tools |
| [JSON_SCHEMA](cores/additive-synthesis-piano/JSON_SCHEMA.md) | Soundbank JSON format (note + partial keys, fallbacks) |
| [TRAINING_MODULES](cores/additive-synthesis-piano/TRAINING_MODULES.md) | Python extraction modules reference |
| [SYSEX_PARAMS](cores/additive-synthesis-piano/SYSEX_PARAMS.md) | SysEx parameter IDs for live editing |
| [TODO](cores/additive-synthesis-piano/TODO.md) | Priorities, implementation phases, known issues |
| [DEVELOPMENT_LOG](cores/additive-synthesis-piano/DEVELOPMENT_LOG.md) | Physics references, key findings, listening tests |

### PhysicalModelingPianoCore

| Document | Content |
|----------|---------|
| [OVERVIEW](cores/physical-modeling-piano/OVERVIEW.md) | Waveguide approach, GUI params, physics defaults, v0.1 status |
| [TODO](cores/physical-modeling-piano/TODO.md) | Roadmap: loss filter, soundboard modes, damper, coupling |
| [DEVELOPMENT_LOG](cores/physical-modeling-piano/DEVELOPMENT_LOG.md) | Implementation notes, bugs fixed, references |

### SineCore

| Document | Content |
|----------|---------|
| [OVERVIEW](cores/sine/OVERVIEW.md) | Reference sine implementation, 2 GUI params |

## Tools

| Document | Content |
|----------|---------|
| [SOUND_EDITOR](tools/SOUND_EDITOR.md) | 3D Three.js soundbank editor, spline model, REST API |
