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

### What happens

The pipeline produces two files from the WAV recordings:

**1. Soundbank JSON (params.json)** — per-note physical parameters

For each recorded note, the extractor analyzes the WAV and fits a
parametric model:

- **Partial detection:** FFT peak-picking finds harmonic frequencies.
  Inharmonicity `f_k = k * f0 * sqrt(1 + B * k^2)` is fitted from the
  measured peak positions.  Up to 60 partials per note.

- **Envelope fitting:** Each partial's amplitude over time is fitted with
  a bi-exponential decay `a1*exp(-t/tau1) + (1-a1)*exp(-t/tau2)` which
  captures the piano's double-decay (fast prompt sound + slow aftersound).
  Onset STFT measures peak A0, main STFT data feeds the decay fit (avoids
  hammer transient contamination).

- **Noise analysis:** The hammer knock is modeled as filtered Gaussian
  noise.  Harmonics are subtracted (sin+cos basis), the residual gives
  `A_noise` (amplitude) and `noise_centroid_hz` (spectral center).

- **Beat detection:** String detuning between unison strings produces
  amplitude beating.  Per-partial autocorrelation extracts `beat_hz`.

- **Spectral EQ:** LTASE comparison between original recording and
  additive synthesis gives a correction curve, fitted as a 10-section
  biquad cascade.  This captures fine spectral details the partial model
  misses.

- **RMS calibration:** Each note is rendered through the Python synthesizer
  (matching the C++ signal path exactly) and normalized to `target_rms`.

- **Cross-velocity correction:** Spectral shape from forte layers (vel 5-7)
  is applied to piano layers (vel 0-4) where noise-floor contamination
  would otherwise make quiet notes sound muffled.

**2. Soundboard IR (soundboard.wav)** — instrument body resonance

The additive synthesis model produces "clean" cosine partials, but a real
piano sound is shaped by the soundboard, bridge, and room.  The IR
captures this missing character:

- For ~20 notes spread across the keyboard, the pipeline renders pure
  additive synthesis (no EQ, no noise) and compares its spectrum with
  the original recording.

- The ratio `H(f) = FFT(original) / FFT(synthesis)` is the effective
  transfer function of everything the model doesn't capture — soundboard
  resonance, room reflections, string-bridge coupling.

- The transfer functions are averaged across notes (Wiener regularized
  deconvolution) to produce a stable, note-independent body profile.

- The result is normalized to unity gain at 2-6 kHz (so high frequencies
  pass through unchanged) and truncated to 25 ms (captures resonance
  character without reverb/echo artifacts).

- At playback, the IR is convolved with the synthesis output at low mix
  (0-4%), adding subtle warmth and body without coloring the sound
  aggressively.

**Output:**
```
soundbanks/pl-grand-04071830.json              <- soundbank parameters
soundbanks/pl-grand-04071830-soundboard.wav    <- soundboard IR (25ms)
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
