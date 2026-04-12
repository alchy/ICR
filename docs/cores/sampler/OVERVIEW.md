# SamplerCore

WAV sample playback engine.  Discovers and loads sample banks from disk,
plays back with velocity layer crossfade, per-voice envelope, and stereo
panning.

CLI: `--core SamplerCore` (auto-loads first bank from configured directory)

## Voice Pool Architecture (v2.0)

SamplerCore uses a **voice pool** with configurable size (default 32 voices).
Voices are allocated from a free-list on each noteOn and released back when
the release envelope completes.

This design supports:
- **Sustain pedal polyphony** — held notes remain in the pool while new notes
  allocate separate voices.  No dropout when retriggering the same pitch.
- **Note stealing** — when the pool is full, the quietest releasing voice is
  stolen first.  If no voices are releasing, the quietest active voice is taken.
- **Multiple voices per pitch** — the same MIDI note can sound multiple times
  simultaneously (e.g. rapid retrigger with sustain pedal).

### Pool sizing

| Pool Size | Use Case |
|-----------|----------|
| 16 | Minimal, solo performance |
| 32 | Default, typical piano playing with sustain |
| 64 | Dense passages, heavy pedal use |
| 128 | Maximum (future: sympathetic resonance) |

Configured in `icr-config.json`:
```json
{
  "voice_pool_size": 32
}
```

### Note stealing priority

When the voice pool is exhausted:

1. **Releasing voices** — steal the one with lowest envelope level
2. **Active voices** — if no releasing voices, steal the quietest overall
3. **Damping buffer** — stolen voice's last ~21ms captured for click-free crossfade

### Voice lifecycle

```
noteOn(60, 100)
  → allocVoice() finds free slot (or steals quietest)
  → voice.active = true, position = 0
  → onset ramp: 0 → 1 over 3 ms

noteOff(60)
  ├─ sustain OFF → releaseNote(60): all voices with midi==60 start release
  │                release ramp: 1 → 0 over release_time
  │                voice.active = false when rel_gain <= 0
  │
  └─ sustain ON  → delayed_offs_[60] = true (voice keeps playing)
                   when pedal released → releaseNote(60) for all pending
```

## Sample Bank Format

Banks are subdirectories of the configured `params_path` (default:
`soundbanks-sampler`).  Each subdirectory containing at least one matching
WAV file is a selectable bank.

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
soundbanks-sampler/
  ks-grand/                    <- bank "ks-grand"
    m021-vel0-f48.wav
    m021-vel1-f48.wav
    ...
    m108-vel7-f48.wav
  pl-grand/                    <- bank "pl-grand"
    ...
```

## Features

- **Voice pool** (v2.0) -- configurable pool size, free-list allocation,
  note stealing (quietest-first), sustain-safe polyphony
- **Async bank loading** -- WAV files load in background thread; GUI shows
  "Loading..." indicator; combo disabled during load
- **Velocity crossfade** -- continuous interpolation between two adjacent
  velocity layers based on MIDI velocity (not just layer selection + gain)
- **Click-free retrigger** -- damping buffer captures previous voice output
  and crossfades (~21 ms) when a voice is stolen or retriggered
- **Per-voice envelope** -- onset ramp (3 ms anti-click) + release fadeout
  (configurable, default 1.0s multiplier)
- **Constant-power stereo** -- MIDI-dependent panning across keyboard

## GUI Parameters

| Parameter | Group | Range | Description |
|-----------|-------|-------|-------------|
| `gain` | Output | 0.0-2.0 | Master output gain |
| `keyboard_spread` | Stereo | 0.0-pi | L-R pan spread across keyboard |
| `release_time` | Envelope | 0.1-4.0 | Release time multiplier (seconds) |

## Bank Switching

In GUI: select bank from "Soundbank:" combo (visible when SamplerCore is
active).  Switching loads the new bank asynchronously.  Notes currently
playing continue from the previous bank until they finish.

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
| Note On (vel 1-127) | `noteOn` | Allocates voice from pool, starts playback |
| Note On (vel 0) | `noteOff` | Treated as note-off |
| Note Off | `noteOff` | Releases all voices on that pitch (or defers if sustain) |
| CC 64 (Sustain) | `sustainPedal` | >= 64: defer noteOffs, < 64: release all deferred |

## Source Files

```
cores/sampler/
    sampler_core.h       Voice + VoiceManager (pool) + PatchManager + SamplerCore
    sampler_core.cpp     Implementation
    wav_loader.h         Minimal WAV file loader (PCM16, PCM24, Float32)
```
