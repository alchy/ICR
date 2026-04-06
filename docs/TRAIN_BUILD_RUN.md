# ICR — Analysis, Build & Run

From WAV bank to synthesizer playback in three steps.
Module reference: [`docs/TRAINING_MODULES.md`](TRAINING_MODULES.md).

---

## 1. Overview

**ICR** extracts physical parameters (inharmonicity, decay, beating, noise,
spectral EQ) from WAV piano recordings and generates a JSON soundbank that
the PianoCore C++ synthesizer plays back in real-time.

```
WAV bank  -->  python run-training.py analyze  -->  soundbank JSON  -->  icrgui.exe
```

---

## 2. Prerequisites

### Python (3.12 recommended, use venv312)

```bash
pip install numpy scipy soundfile
```

| Package | Required |
|---------|----------|
| numpy | yes |
| scipy | yes |
| soundfile | yes |

### C++ Build

```bash
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release
```

Requires: CMake 3.16+, C++17 compiler. GLFW and Dear ImGui are fetched
automatically via FetchContent.

---

## 3. Analyze a WAV Bank

```bash
# Using venv312 (required for torch compatibility):
.venv312/Scripts/python.exe run-training.py analyze --bank C:/SoundBanks/IthacaPlayer/pl-grand

# Options:
#   --out soundbanks/my-piano.json    Custom output path
#   --workers 8                       Parallel extraction workers
#   --skip-eq                         Skip spectral EQ fitting (faster)
#   --skip-outliers                   Skip outlier detection
#   --sr-tag f44                      If WAVs are 44.1 kHz (default: f48)
```

**Pipeline:**
1. **Extract** — per-note partial analysis (frequency, amplitude, envelope, beating, noise)
2. **Filter** — remove statistical outliers across the keyboard
3. **EQ fit** — spectral envelope correction via biquad cascade
4. **Export** — assemble JSON with RMS calibration

Output: `soundbanks/{bank_name}.json`

### WAV File Naming Convention

```
m{midi:03d}-vel{idx}-{sr_tag}.wav
```
- `midi`: 021-108 (A0-C8)
- `vel idx`: 0-7 (pp to ff)
- `sr_tag`: f44 or f48

Example: `m060-vel4-f48.wav` = middle C, mezzo-forte, 48 kHz

---

## 4. Build

```bash
cmake -B build
cmake --build build --config Release
```

Produces:
- `build/bin/Release/icr.exe` — headless CLI (batch rendering, keyboard input)
- `build/bin/Release/icrgui.exe` — GUI with piano keyboard, MIDI, controls

---

## 5. Run

### GUI

```bash
./build/bin/Release/icrgui.exe --core PianoCore --params soundbanks/pl-grand.json
```

- Click piano keys or use A-K keyboard shortcuts (C4-B4)
- Connect MIDI controller via the port selector
- Spacebar toggles sustain pedal
- Adjust Mix, LFO, Limiter, BBE in the left panel
- Core params (beat_scale, noise_level, etc.) in the right panel

### Headless CLI

```bash
./build/bin/Release/icr.exe --core PianoCore --params soundbanks/pl-grand.json
```

Keyboard: A-K = C4-B4, space = sustain, Q = quit.

### Batch Rendering

```bash
./build/bin/Release/icr.exe --core PianoCore --params soundbanks/pl-grand.json \
    --batch batch_spec.json --batch-out output/
```

---

## 6. Troubleshooting

| Problem | Solution |
|---------|----------|
| `torch` import error | Use `.venv312/Scripts/python.exe` (Python 3.12) |
| No MIDI ports found | Check device manager, install MIDI driver |
| Build fails on Windows | Ensure Visual Studio 2019+ with C++ workload |
| `LINK: fatal error` | Close running icrgui.exe before rebuilding |
| Silent output | Check `--params` path, verify JSON has `notes` array |

---

## 7. Documentation

| Document | Content |
|----------|---------|
| [TRAINING_MODULES.md](TRAINING_MODULES.md) | Module-by-module reference |
| [JSON_SCHEMA.md](JSON_SCHEMA.md) | Soundbank JSON format specification |
| [ARCHITECTURE.md](ARCHITECTURE.md) | C++ engine architecture |
| [SYSEX_PROTOCOL.md](SYSEX_PROTOCOL.md) | MIDI SysEx protocol for live param updates |
| [PIANO_MODEL_FIXES.md](PIANO_MODEL_FIXES.md) | Critical synthesis model fixes with physics references |
