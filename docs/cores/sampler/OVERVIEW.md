# SamplerCore

WAV sample playback engine.  Discovers and loads sample banks from disk,
plays back with velocity layer crossfade, per-voice envelope, and stereo
panning.

CLI: `--core SamplerCore` (auto-loads first bank from configured directory)

## Sample Bank Format

Banks are subdirectories of the configured `params_path` (default:
`C:\SoundBanks\IthacaPlayer` on Windows).  Each subdirectory containing
at least one matching WAV file is a selectable bank.

### WAV file naming

```
m{midi:03d}-vel{idx}-f{sr_tag}.wav
```

| Field | Range | Example |
|-------|-------|---------|
| `midi` | 000-127 | m060 = Middle C |
| `vel idx` | 0-7 | vel0 = pp, vel7 = ff |
| `sr_tag` | f44, f48 | Sample rate indicator |

### Directory structure

```
C:/SoundBanks/IthacaPlayer/
  ks-grand/                    <- bank "ks-grand"
    m021-vel0-f48.wav
    m021-vel1-f48.wav
    ...
    m108-vel7-f48.wav
  pl-grand/                    <- bank "pl-grand"
    m021-vel0-f48.wav
    ...
  vv-rhodes/                   <- bank "vv-rhodes"
    ...
```

## Features

- **Async bank loading** -- WAV files load in background thread; GUI shows
  "Loading..." indicator; combo disabled during load
- **Velocity crossfade** -- continuous interpolation between two adjacent
  velocity layers based on MIDI velocity (not just layer selection + gain)
- **Click-free retrigger** -- damping buffer captures previous voice output
  and crossfades (~21 ms) when the same note is retriggered
- **Per-voice envelope** -- onset ramp (3 ms anti-click) + release fadeout
  (200 ms default, configurable)
- **Constant-power stereo** -- MIDI-dependent panning across keyboard

## GUI Parameters

| Parameter | Group | Range | Description |
|-----------|-------|-------|-------------|
| `gain` | Output | 0.0-2.0 | Master output gain |
| `keyboard_spread` | Stereo | 0.0-pi | L-R pan spread across keyboard |
| `release_time` | Envelope | 0.1-4.0 | Release time multiplier |

## Bank Switching

In GUI: select bank from "Bank:" combo (visible when SamplerCore is active).
Switching loads the new bank asynchronously.  Notes currently playing continue
from the previous bank until they finish.

## SysEx Support

| Command | Support | Description |
|---------|---------|-------------|
| SET_MASTER (0x10) | `gain`, `keyboard_spread`, `release_time` | Global params via `setParam()` |
| SET_BANK (0x03) | Not yet | Future: bank switch by name via SysEx |
| SET_NOTE_PARAM (0x01) | Not supported | Samples are read-only (not parametric) |
| SET_NOTE_PARTIAL (0x02) | Not supported | No partial model |

Core ID for SysEx addressing: **0x03**

## MIDI Controls

| MIDI Event | Handler | Description |
|------------|---------|-------------|
| Note On (vel 1-127) | `noteOn` | Selects velocity layer pair, starts playback with crossfade |
| Note On (vel 0) | `noteOff` | Treated as note-off |
| Note Off | `noteOff` | Begins release envelope (or deferred if sustain active) |
| CC 64 (Sustain) | `sustainPedal` | >= 64: hold notes, < 64: release all held notes |

## Source Files

```
cores/sampler/
    sampler_core.h       Voice + VoiceManager + PatchManager + SamplerCore
    sampler_core.cpp     Implementation
    wav_loader.h         Minimal WAV file loader (PCM16, PCM24, Float32)
```
