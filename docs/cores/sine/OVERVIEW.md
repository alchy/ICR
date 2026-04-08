# SineCore

Minimal reference implementation of the 3-layer Ithaca Core architecture.
Single sine oscillator per voice with onset/release ramps.

CLI: `--core SineCore` (no `--params` needed)

## Purpose

Validates the Ithaca Core 3-layer pattern (PatchManager -> VoiceManager -> Voice)
with the simplest possible synthesis.  Useful for:
- Testing MIDI input, audio output, GUI framework
- Verifying new engine features without core complexity
- Template for new core implementations

## Synthesis

- Single `sin(phase)` oscillator per voice
- Velocity-scaled amplitude: `(velocity / 127) * gain`
- Onset ramp: 3 ms linear (click prevention)
- Release ramp: 10 ms linear fade-out
- Mono -> stereo (identical channels)

## GUI Parameters

| Parameter | Range | Description |
|-----------|-------|-------------|
| `gain` | 0.0-2.0 | Output gain |
| `detune_cents` | -100-100 | Global pitch offset (cents) |

## Source Files

```
cores/sine/
    sine_core.h      SineVoice + SineVoiceManager + SinePatchManager + SineCore
    sine_core.cpp    Implementation (~250 lines)
```
