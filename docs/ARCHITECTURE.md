# ICR — C++ Engine Architecture

## Overview

```
MIDI input
    │
    ▼
CoreEngine  ──────────────────────────────────────────────────────┐
│  MIDI queue (lock-free ring)                                     │
│  master gain / master pan (atomic<float>)                        │
│  LFO (panning modulation)                                        │
│                                                                  │
│   ISynthCore  (pluggable — selected at startup via --core)       │
│   ┌─────────────────────────────────────────────────────┐        │
│   │  PianoCore  /  SineCore  /  …                       │        │
│   │  voice pool · envelopes · oscillators · EQ · noise  │        │
│   └─────────────────────────────────────────────────────┘        │
│                                                                  │
│   DspChain                                                       │
│   ┌──────────────────────────────┐                               │
│   │  limiter → BBE exciter       │                               │
│   └──────────────────────────────┘                               │
│                                                                  │
│   miniaudio output device                                        │
└──────────────────────────────────────────────────────────────────┘
```

## Directory structure

```
engine/        CoreEngine, ISynthCore interface, SynthCoreRegistry,
               MidiInput, CoreLogger, miniaudio.h
cores/         Pluggable synth cores
  piano/         PianoCore — 2-string bi-exponential piano synth
  sine/          SineCore  — simple sine-wave test tone
dsp/           DspChain, limiter, BBE exciter
gui/           Dear ImGui frontend (ResonatorGUI)
third_party/   nlohmann/json, RtMidi
training/      Python training pipeline (extract → learn → export)
soundbanks/    Parameter JSON files (not in git — generate or copy)
docs/          Documentation
```

## Core interface: ISynthCore

`engine/i_synth_core.h` defines the contract every core must implement.

| Method | Thread | Notes |
|--------|--------|-------|
| `load(params_path, sr, logger)` | main | Load JSON params, allocate voices |
| `setSampleRate(sr)` | main | Call only when RT is stopped |
| `noteOn / noteOff / sustainPedal / allNotesOff` | RT | Called from CoreEngine after queue drain |
| `processBlock(out_l, out_r, n)` | RT | **No alloc, no lock, no IO** |
| `setParam / getParam` | GUI | Implementations use atomics |
| `describeParams()` | GUI | Returns slider metadata |
| `getVizState()` | GUI | Snapshot for visualization panel |
| `coreName / coreVersion / isLoaded` | any | Metadata |
| `setNoteParam(midi, vel, key, value)` | MIDI cb | Per-slot scalar update (SysEx 0x01) |
| `setNotePartialParam(midi, vel, k, key, value)` | MIDI cb | Per-partial update (SysEx 0x02) |
| `loadBankJson(json_str)` | MIDI cb | Full bank reload (SysEx 0x03) |

### Registration macro

Each core self-registers at static-init time — no central list to edit:

```cpp
// in cores/piano/piano_core.cpp
REGISTER_SYNTH_CORE("PianoCore", PianoCore)
```

`SynthCoreRegistry::instance().create("PianoCore")` instantiates on demand.

## CoreEngine

`engine/core_engine.h/.cpp`

- Owns the audio device (miniaudio callback).
- MIDI events arrive on the GUI/MIDI thread, are pushed into a lock-free ring
  buffer (`midi_q_`), and drained into the core at the top of each audio callback.
- Master gain, master pan, LFO speed/depth: `std::atomic<float>` — safe
  concurrent read from RT, write from GUI.
- `DspChain` applied to the mixed stereo output after `ISynthCore::processBlock`.

## PianoCore

`cores/piano/piano_core.cpp`

### Synthesis model

Each note spawns a voice with up to 60 partials × 2 strings:

```
partial k:
  f_k  = k · f0 · √(1 + B·k²)           inharmonic frequency
  A(t) = A0 · (a1·e^(-t/τ1) + (1-a1)·e^(-t/τ2))   bi-exponential envelope
  x_L  = A(t)·cos(2π·f_k·t + φ_L)
  x_R  = A(t)·cos(2π·f_k·t + φ_R)       φ_R = φ_L + φ_diff (per-partial random)
```

String 2 is detuned by `beat_hz` (≈0.3–3 Hz): produces the natural piano chorus.
`φ_diff` is drawn independently per partial at note-on → true stereo for every partial.

### Noise model

Filtered noise added during the attack transient:
```
σ²(t) = A_noise² · e^(-2t/τ_n)
rms normalised against partial stack to give consistent total level
```

### Spectral EQ

Per-(note, velocity) min-phase IIR biquad cascade, 5 sections, fitted from
LTASE (Long-Term Average Spectral Envelope) measurements:

```
fitting: WAV → FFT magnitude → cepstral minimum-phase → invfreqz lstsq → sos
runtime: Direct Form II, applied after Schroeder all-pass decorrelation
blend:   dry/wet controlled by eq_strength parameter (0 = off, 1 = full)
```

EQ frequency response is evaluated at 32 log-spaced frequencies in
`getVizState()` and displayed in the GUI's Spectral EQ column.

### Stereo pipeline

```
per-partial:  constant-power pan (keyboard position)
              φ_diff  → independent phase per partial
after sum:    Schroeder all-pass decorrelation (5 stages)
post:         5-section biquad EQ cascade (independent L/R state)
```

### Parameters (runtime-adjustable)

| Key | Default | Description |
|-----|---------|-------------|
| `beat_scale` | 1.0 | Scales all beat_hz values |
| `noise_level` | 1.0 | Noise amplitude multiplier |
| `keyboard_spread` | 0.8 | Stereo width across keyboard |
| `eq_strength` | 1.0 | EQ wet/dry blend |

## Threading model

```
GUI thread:   setParam / getParam / describeParams / getVizState
              MIDI callbacks → CoreEngine::pushMidiEvt (ring buffer write)

MIDI cb thread:  CoreEngine::handleSysEx
                 ├─ SET_NOTE_PARAM / SET_NOTE_PARTIAL → ISynthCore::setNoteParam/setNotePartialParam
                 │  (individual float writes; safe on x86 per codebase convention)
                 ├─ SET_BANK → ISynthCore::loadBankJson
                 │  (parse outside lock; memcpy under bank_mutex_; PianoCore::handleNoteOn
                 │   uses try_to_lock so the RT thread never blocks on a bank load)
                 ├─ SET_MASTER → ISynthCore::setParam
                 └─ PING → MidiInput::sendRaw (PONG via optional RtMidiOut)

RT thread:    CoreEngine audio callback
              ├─ drain MIDI ring → ISynthCore::noteOn/Off/etc.
              ├─ ISynthCore::processBlock
              └─ DspChain (limiter, BBE)
```

No locks on the RT path. MIDI ring buffer is sized 256 events; full buffer
drops incoming events gracefully. FTZ/DAZ denormal flush enabled at startup.

## DspChain

`dsp/dsp_chain.cpp`

Post-processing on the stereo mix:
- **Limiter**: peak limiter, configurable ceiling and release
- **BBE exciter**: high-frequency harmonic enhancement

Both stages are bypass-able at runtime.

## SysEx — Sound Editor integration

The ICR SysEx protocol (`docs/SYSEX_PROTOCOL.md`) is fully implemented on the
C++ side. The flow from Sound Editor to synthesis engine:

```
Sound Editor (Python)
  └─ sysex_bridge.py  ──MIDI loopback──▶  MidiInput::callback
                                               │
                                               ▼
                                         CoreEngine::handleSysEx
                                           ├─ 0x01 SET_NOTE_PARAM
                                           │    └▶ PianoCore::setNoteParam
                                           ├─ 0x02 SET_NOTE_PARTIAL
                                           │    └▶ PianoCore::setNotePartialParam
                                           ├─ 0x03 SET_BANK (chunked)
                                           │    └▶ PianoCore::loadBankJson
                                           ├─ 0x10 SET_MASTER
                                           │    └▶ ISynthCore::setParam
                                           └─ 0xF0 PING
                                                └▶ MidiInput::sendRaw (PONG)
```

### SET_BANK thread safety

`loadBankJson` parses the JSON string without holding any lock (potentially
several ms for a full soundbank), then performs a brief `memcpy` under
`bank_mutex_` to swap in the new `note_params_[128][8]`. `handleNoteOn` uses
`std::unique_lock(bank_mutex_, std::try_to_lock)` — if the lock is busy it
skips the noteOn rather than blocking the RT audio thread.

### PONG (optional)

To send PONG responses, call `MidiInput::openOutput(port_index)` after
`MidiInput::open()`. The output port is separate from the input port. If no
output port is open, PING is processed but PONG is silently dropped.

### `B` (inharmonicity)

SysEx param ID `0x02` (`B`) is defined in the protocol but is **not
runtime-settable** in `PianoCore`: inharmonicity is baked into per-partial
frequencies at export time (`analysis/export_piano_params.py`).
`setNoteParam(..., "B", ...)` returns `false`. To change inharmonicity,
re-export the soundbank and reload via `SET_BANK`.

## Build targets

| Target | Output | Description |
|--------|--------|-------------|
| `ICR` | `ICR.exe` | Headless CLI, real-time MIDI synthesis |
| `ICRGUI` | `ICRGUI.exe` | Dear ImGui frontend (GLFW + OpenGL3) |

Both targets link the same `engine/`, `cores/`, `dsp/` sources.
AVX2 + FMA enabled on x86-64 (MSVC `/arch:AVX2`, GCC/Clang `-mavx2 -mfma`).
