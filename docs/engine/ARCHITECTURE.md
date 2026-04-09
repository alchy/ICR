# ICR -- C++ Engine Architecture

## Overview

ICR is a pluggable real-time synthesizer engine written in C++17.  Multiple
synthesis cores can be registered and selected at runtime.  The engine provides
audio I/O, MIDI input, master bus processing, and a core-agnostic GUI.

The architecture follows the **Ithaca Core 3-layer pattern**
(Voice, VoiceManager, PatchManager), described below.

## Available Cores

| Core | Type | Description |
|------|------|-------------|
| [AdditiveSynthesisPianoCore](../cores/additive-synthesis-piano/OVERVIEW.md) | Additive | 60-partial analysis-resynthesis piano, bi-exp envelopes, spectral EQ |
| [PhysicalModelingPianoCore](../cores/physical-modeling-piano/OVERVIEW.md) | Waveguide | Dual-rail waveguide (Teng/Smith), Chaigne-Askenfelt hammer, multi-string stereo |
| [SamplerCore](../cores/sampler/OVERVIEW.md) | Sample | WAV sample playback with velocity layers and banks |
| [SineCore](../cores/sine/OVERVIEW.md) | Reference | Single sine oscillator per voice, reference implementation |

## Signal Flow

```
┌─────────────────────────────────────────────────────────────────┐
│ CoreEngine::audioCallback (miniaudio, ~5.3 ms latency @ 256)   │
└─────────────────────────────────────────────────────────────────┘
                           |
┌─────────────────────────────────────────────────────────────────┐
│ CoreEngine::processBlock (RT thread)                            │
│                                                                 │
│ 1. Drain MIDI queue (512-event lock-free SPSC ring)            │
│    └─ Route to active core: noteOn/Off/sustainPedal            │
│                                                                 │
│ 2. Zero output buffers                                         │
│                                                                 │
│ 3. For each instantiated core:                                 │
│    └─ core->processBlock(L, R, 256)                            │
│       Physical: 3 strings x dual-rail waveguide per voice      │
│       Additive: 60 partials x envelope + sine per voice        │
│                                                                 │
│ 4. AGC (progressive voice gain, never amplifies)               │
│                                                                 │
│ 5. Master gain + LFO panning (per-sample sine modulation)      │
│                                                                 │
│ 6. DspChain (master bus effects):                              │
│    Convolver (soundboard IR) → BBE → Limiter                   │
│                                                                 │
│ 7. Peak metering (exponential decay)                           │
└─────────────────────────────────────────────────────────────────┘
                           |
                   Audio device output
```

## Architecture Layers

```
┌─────────────────────────────────────────────────┐
│  ISynthCore implementations                     │  Pure C++ math
│  (SineCore, AdditiveSynthesis, PhysicalModeling) │  Zero platform deps
│  DspChain (convolver, BBE, limiter)             │  No allocation in RT
├─────────────────────────────────────────────────┤
│  CoreEngine                                     │  RT loop, MIDI queue
│  (std::atomic params, lock-free SPSC ring)      │  std::filesystem I/O
├─────────────────────────────────────────────────┤
│  miniaudio       │  RtMidi        │ ImGui/GLFW  │  Platform abstraction
│  (audio device)  │  (MIDI ports)  │ (GUI)       │  (optional)
├──────────────────┼────────────────┼─────────────┤
│  WASAPI/CoreAudio│  WinMM/CoreMIDI│ Win32/Cocoa │  OS-specific
│  ALSA/JACK       │  ALSA          │ X11         │  (handled by libs)
└──────────────────┴────────────────┴─────────────┘
```

## Three-Layer Core Architecture (Ithaca Core)

Every ISynthCore implements the same 3-layer pattern:

### PatchManager (MIDI → native float translation)

System entry point. Receives MIDI events and translates to native
parametrization. Handles sustain pedal with delayed note-offs.

| Method | Description |
|--------|-------------|
| `noteOn(midi, velocity, vm, ...)` | Translate MIDI → native params, delegate to VoiceManager |
| `noteOff(midi, vm, sr)` | Sustain-aware release |
| `sustainPedal(down, vm, sr)` | Queue/release delayed note-offs |
| `allNotesOff(vm, sr)` | Release all voices |

### VoiceManager (voice pool lifecycle)

128 voice slots, one per MIDI note. No MIDI awareness.

| Method | Description |
|--------|-------------|
| `processBlock(L, R, n)` | Iterate active voices → Voice::process |
| `initVoice(midi, ...)` | Set up voice with native parameters |
| `releaseVoice(midi, sr)` | Begin release phase |
| `voice(midi)` | Access for visualization |

### Voice (independent DSP unit)

Owns all per-voice state. Produces stereo audio. Can be distributed
to separate HW (FPGA, DSP chip). No MIDI awareness, no global state.

| Method | Description |
|--------|-------------|
| `process(L, R, n)` | Produces stereo audio, returns false when done |

## Threading Model

| Thread | Components | Safety |
|--------|------------|--------|
| **RT (audio)** | processBlock, voice synthesis, AGC, master, DSP chain | Zero alloc, lock-free, no I/O |
| **MIDI callback** | pushMidiEvt → SPSC ring buffer | Atomic write pointer |
| **GUI** | setParam/getParam, getVizState, loadBankJson | Atomic reads, try_to_lock for bank |

**Communication:**
- RT ↔ GUI: `std::atomic<float>` (relaxed ordering)
- MIDI → RT: lock-free SPSC ring (512 events, acquire/release ordering)
- Bank load: `std::mutex` with `try_to_lock` from RT (never blocks audio)

## Core ↔ GUI Interface

GUI is fully core-agnostic. The right panel is generated dynamically
from the core's declared parameters:

| Interface | Direction | Description |
|-----------|-----------|-------------|
| `describeParams()` | Core → GUI | Declares sliders: key, label, group, min/max, unit |
| `getVizState()` | Core → GUI | Snapshot: active voices, last note details |
| `setParam(key, val)` | GUI → Core | Parameter change (atomic, RT-safe) |
| `coreName()` | Core → GUI | Display name |

New cores only implement `describeParams()` and `getVizState()` — GUI
automatically shows corresponding controls with no code changes.

## Per-Core Soundbank Selector

GUI provides a generic bank dropdown for any core with `soundbank_dir`
configured in `icr-config.json`. The selector discovers `.json` files
via `std::filesystem` and calls `core->loadBankJson()` for hot-reload.

No `dynamic_cast` — uses `engine.core()->coreName()` and `loadBankJson()`
from the ISynthCore interface.

## Per-Core DSP Defaults

Each core can define default DSP chain settings in `icr-config.json`:
- `master_gain`, `master_pan`, `lfo_speed`, `lfo_depth`
- `limiter_threshold`, `limiter_release`, `limiter_enabled`
- `bbe_definition`, `bbe_bass_boost`

Applied on engine init and core switch via `applyDspDefaults()`.
GUI sliders sync automatically.

## Voice Memory Footprint

| Core | Per Voice | 128 Voices | Dominant |
|------|-----------|------------|----------|
| Physical (dual-rail) | 52 KB | 6.7 MB | Circular buffers (2×2048 floats × 3 strings) |
| Additive | 5.7 KB | 730 KB | Partial array (60 × 44 bytes) |
| Sine | <1 KB | ~100 KB | Phase accumulator only |

## Performance Estimates (48 kHz, 256-sample block)

| Component | Cost per sample | Notes |
|-----------|-----------------|-------|
| Physical voice (3 strings) | ~180 ops | Dual-rail tick + loss + dispersion |
| Additive voice (60 partials) | ~1200 ops | Sin + envelope per partial |
| Convolver (4800-sample IR) | ~4800 mults | On master bus, independent of polyphony |
| AGC + Master + LFO | negligible | Per-sample gain application |
| BBE + Limiter | negligible | 2 biquads + peak detector |

Typical 10-note chord: ~2000 ops/sample (physical) or ~12000 ops/sample
(additive). Well within single-core budget at 1+ GHz.

## SysEx Integration

Both additive and physical cores support runtime parameter editing via
MIDI SysEx. See [SysEx Protocol](SYSEX_PROTOCOL.md).

| Feature | Additive | Physical |
|---------|----------|----------|
| Bank upload (SET_BANK) | Full | Full |
| Bank export (EXPORT_BANK) | Full | Full |
| Per-note update (SET_NOTE_PARAM) | Full (7 keys) | Full (10 keys) |
| Per-partial update | Full (7 keys) | N/A |

## File Structure

```
engine/
    core_engine.h/cpp           CoreEngine (audio, MIDI, master bus)
    i_synth_core.h              ISynthCore interface + viz structs
    synth_core_registry.h       Factory pattern (REGISTER_SYNTH_CORE macro)
    midi_input.h/cpp            RtMidi wrapper
cores/
    sine/                       SineCore (reference)
    additive_synthesis_piano/   AdditiveSynthesisPianoCore
    physical_modeling_piano/    PhysicalModelingPianoCore (dual-rail v1.0)
    sampler/                    SamplerCore
dsp/
    agc.h                       Progressive voice gain
    dsp_math.h                  Shared DSP primitives (biquad, RBJ)
    dsp_chain.h/cpp             Master bus: Convolver → BBE → Limiter
    limiter/                    Peak limiter
    bbe/                        BBE Sonic Maximizer
    convolver/                  Soundboard IR convolution (with SR resample)
gui/
    resonator_gui.h/cpp         ImGui real-time GUI (core-agnostic)
```
