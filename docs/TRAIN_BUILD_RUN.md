# ICR — Analysis, Build & Run

From WAV bank to synthesizer playback.
Module reference: [`TRAINING_MODULES.md`](TRAINING_MODULES.md).

---

## 1. Overview

**ICR** extracts physical parameters from WAV piano recordings, generates
a JSON soundbank and a soundboard impulse response, then plays them back
through a real-time additive synthesizer with soundboard convolution.

```
WAV bank  -->  analyze  -->  soundbank.json + soundboard.wav  -->  icrgui.exe
```

---

## 2. Prerequisites

### Python (3.12 — use .venv312)

```bash
pip install numpy scipy soundfile
```

### C++ Build

```bash
cmake -B build
cmake --build build --config Release
```

Requires: CMake 3.16+, C++17 compiler.

---

## 3. Analyze a WAV Bank

```bash
.venv312/Scripts/python.exe run-training.py analyze --bank C:/SoundBanks/IthacaPlayer/pl-grand
```

**Pipeline:**
1. **Extract** — per-note partial analysis (frequency, amplitude, bi-exp envelope, beating, noise)
2. **Filter** — remove statistical outliers across the keyboard
3. **EQ fit** — spectral envelope correction via 10-section biquad cascade
4. **Export** — assemble JSON with RMS calibration (biquad BP noise model matching C++)
5. **Soundboard IR** — extract body resonance by deconvolving synthesis from recordings

**Output:**
```
soundbanks/pl-grand-04071830.json              ← soundbank parameters
soundbanks/pl-grand-04071830-soundboard.wav    ← soundboard impulse response (25ms)
```

**Options:**
```
--out soundbanks/my-piano.json    Custom output path
--workers 8                       Parallel extraction workers
--skip-eq                         Skip spectral EQ fitting
--skip-outliers                   Skip outlier detection
--skip-ir                         Skip soundboard IR extraction
--sr-tag f44                      If WAVs are 44.1 kHz (default: f48)
```

### WAV File Naming

```
m{midi:03d}-vel{idx}-{sr_tag}.wav
```
- `midi`: 021-108 (A0-C8)
- `vel idx`: 0-7 (pp to ff)
- `sr_tag`: f44 or f48

---

## 4. Build

```bash
cmake -B build
cmake --build build --config Release
```

Produces:
- `build/bin/Release/icr.exe` — headless CLI
- `build/bin/Release/icrgui.exe` — GUI with piano keyboard, MIDI, controls

---

## 5. Run

### GUI

```bash
./build/bin/Release/icrgui.exe \
    --core PianoCore \
    --params soundbanks/pl-grand-04071830.json \
    --ir soundbanks/pl-grand-04071830-soundboard.wav
```

**Controls:**
- Piano keyboard: click or A-K shortcuts (C4-B4)
- MIDI: connect via port selector
- Spacebar: sustain pedal
- Left panel: Mix, LFO Pan, Limiter, BBE, **Soundboard IR** (enable + mix slider)
- Right panel: Core params, last-note detail with per-partial diagnostics

### Headless CLI

```bash
./build/bin/Release/icr.exe \
    --core PianoCore \
    --params soundbanks/pl-grand.json \
    --ir soundbanks/pl-grand-soundboard.wav
```

### Without Soundboard IR

Omit `--ir` to run with dry additive synthesis + EQ only.

---

## 6. Signal Chain

```
Per-voice (PianoCore):
  Partials (1/2/3-string model, bi-exp envelope)
    × Attack rise envelope (1-exp(-t/tau_rise))
  + Noise (biquad bandpass at centroid_hz, Q=1.5)
  → Allpass decorrelation (Schroeder)
  → Spectral EQ (10-section biquad cascade, DF-II)
  → M/S stereo width correction

Master bus (DspChain):
  → Soundboard IR convolution (25ms, 0-4% mix)
  → BBE Sonic Maximizer (high shelf + low shelf)
  → Peak Limiter (attack 1ms, variable release)
  → Master gain + LFO pan
```

---

## 7. Diagnostic Tools

```bash
# Inspect soundbank parameters per register
python tools/inspect_bank.py soundbanks/pl-grand.json

# Inspect specific note
python tools/inspect_bank.py soundbanks/pl-grand.json --midi 57 --vel 4

# Quality report (compare synthesis vs original WAV)
python tools/quality_report.py soundbanks/pl-grand.json \
    --bank C:/SoundBanks/IthacaPlayer/pl-grand

# Extract soundboard IR separately
python tools/extract_soundboard_ir.py soundbanks/pl-grand.json \
    --bank C:/SoundBanks/IthacaPlayer/pl-grand
```

---

## 8. Troubleshooting

| Problem | Solution |
|---------|----------|
| `torch` import error | Use `.venv312/Scripts/python.exe` (Python 3.12) |
| No MIDI ports | Check device manager, install MIDI driver |
| Build fails | Ensure VS 2019+ with C++ workload |
| `LINK: fatal error` | Close running icrgui.exe before rebuild |
| Silent output | Check `--params` path, verify JSON has `notes` |
| Convolver clipping | Reduce mix slider (default 50% = 2% real mix) |
| "Brinkava" notes | Check stereo_width in inspector (should be < 2.0) |

---

## 9. Documentation

| Document | Content |
|----------|---------|
| [TRAINING_MODULES.md](TRAINING_MODULES.md) | Module-by-module reference |
| [JSON_SCHEMA.md](JSON_SCHEMA.md) | Soundbank JSON format |
| [ARCHITECTURE.md](ARCHITECTURE.md) | C++ engine architecture |
| [SYSEX_PROTOCOL.md](SYSEX_PROTOCOL.md) | MIDI SysEx protocol |
| [PIANO_MODEL_FIXES.md](PIANO_MODEL_FIXES.md) | Synthesis model fixes + physics |
| [SESSION_SUMMARY.md](SESSION_SUMMARY.md) | Development session log |
| [TODO.md](TODO.md) | Priorities and implementation plan |
