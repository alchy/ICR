# ICR -- Build & Run

## Prerequisites

### C++ Build

```bash
cmake -B build
cmake --build build --config Release
```

Requires: CMake 3.16+, C++17 compiler (VS 2022, GCC, Clang).

Produces:
- `build/bin/Release/icr.exe` -- headless CLI
- `build/bin/Release/icrgui.exe` -- GUI with piano keyboard, MIDI, controls

### Python (3.12)

```bash
pip install numpy scipy soundfile
```

Required only for the training/extraction pipeline
(see [AdditiveSynthesisPianoCore docs](../cores/additive-synthesis-piano/TRAIN_BUILD_RUN.md)).

---

## Run

### List available cores

```bash
./build/bin/Release/icr.exe --list-cores
```

### GUI

```bash
./build/bin/Release/icrgui.exe \
    --core <CoreName> \
    --params <soundbank.json> \
    --ir <soundboard.wav>
```

**Controls:**
- Piano keyboard: click or A-K shortcuts (C4-B4)
- MIDI: connect via port selector
- Spacebar: sustain pedal
- Left panel: Mix, LFO Pan, Limiter, BBE, Soundboard IR (enable + mix slider)
- Right panel: Core params, last-note detail with per-partial diagnostics

### Headless CLI

```bash
./build/bin/Release/icr.exe \
    --core <CoreName> \
    --params <soundbank.json> \
    --ir <soundboard.wav>
```

### CLI Options

| Option | Description |
|--------|-------------|
| `--core <name>` | Synthesis core (default: SineCore) |
| `--params <path>` | Core parameter JSON (core-specific) |
| `--ir <path>` | Soundboard IR WAV file |
| `--config <path>` | SynthConfig JSON applied via setParam |
| `--core-param key=val` | Override a core parameter (repeatable) |
| `--port <N>` | MIDI input port index (default: 0) |
| `--midi-range-limit-from <N>` | Skip notes with MIDI < N on load |
| `--midi-range-limit-to <N>` | Skip notes with MIDI > N on load |
| `--render-batch <json>` | Offline batch render (no audio device) |
| `--out-dir <dir>` | Output directory for batch render |
| `--sr <hz>` | Sample rate for batch render (default: 48000) |
| `--list-cores` | Print available cores and exit |

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Build fails | Ensure VS 2019+ with C++ workload |
| `LINK: fatal error` | Close running icrgui.exe before rebuild |
| No MIDI ports | Check device manager, install MIDI driver |
| Silent output | Check `--params` path, verify JSON has `notes` array |
| Convolver clipping | Reduce mix slider (default 50% = 2% real mix) |
