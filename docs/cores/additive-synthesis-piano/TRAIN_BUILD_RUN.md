# AdditiveSynthesisPianoCore -- Analysis & Run

From WAV bank to synthesizer playback.
For C++ build instructions, see [BUILD.md](../../engine/BUILD.md).
Module reference: [TRAINING_MODULES.md](TRAINING_MODULES.md).

---

## 1. Analyze a WAV Bank

```bash
.venv312/Scripts/python.exe run-extract-additive.py analyze --bank C:/SoundBanks/IthacaPlayer/pl-grand
```

### What happens

The pipeline produces two files from the WAV recordings:

**1. Soundbank JSON** -- per-note physical parameters
([schema](JSON_SCHEMA.md))

For each recorded note, the extractor analyzes the WAV and fits a parametric model:

- **Partial detection:** FFT peak-picking, inharmonicity fit `f_k = k*f0*sqrt(1+B*k^2)`, up to 60 partials
- **Envelope fitting:** Bi-exponential decay `a1*exp(-t/tau1) + (1-a1)*exp(-t/tau2)`, onset/decay separation
- **Noise analysis:** Harmonic subtraction (sin+cos basis), `A_noise` + `noise_centroid_hz`
- **Beat detection:** Per-partial autocorrelation extracts `beat_hz`
- **Spectral EQ:** LTASE comparison, 10-section biquad cascade
- **RMS calibration:** Python renderer matches C++ signal path exactly
- **Cross-velocity correction:** Spectral shape from forte layers (vel 5-7) applied to piano layers (vel 0-4)

**2. Soundboard IR** -- instrument body resonance (25 ms mono WAV)

Deconvolved from recordings, averaged across ~20 notes, normalized to unity at 2-6 kHz.

**Output:**
```
soundbanks-additive/pl-grand-04071830.json
soundbanks-additive/pl-grand-04071830-soundboard.wav
```

**Options:**
```
--out soundbanks-additive/my-piano.json    Custom output path
--workers 8                       Parallel extraction workers
--skip-eq                         Skip spectral EQ fitting
--skip-outliers                   Skip outlier detection
--skip-ir                         Skip soundboard IR extraction
--skip-physics-floor              Raw extraction only (no harmonic correction)
--sr-tag f44                      If WAVs are 44.1 kHz (default: f48)
```

### WAV File Naming

```
m{midi:03d}-vel{idx}-{sr_tag}.wav
```
- `midi`: 021-108 (A0-C8), `vel idx`: 0-7 (pp to ff), `sr_tag`: f44 or f48

---

## 2. Run

```bash
./build/bin/Release/icrgui.exe \
    --core AdditiveSynthesisPianoCore \
    --params soundbanks-additive/pl-grand-04071830.json \
    --ir soundbanks-additive/pl-grand-04071830-soundboard.wav
```

Without soundboard IR: omit `--ir` for dry additive synthesis + EQ only.

---

## 3. Signal Chain

```
Per-voice (AdditiveSynthesisPianoCore):
  Partials (1/2/3-string model, bi-exp envelope)
    x Attack rise envelope (1-exp(-t/tau_rise))
  + Noise (biquad bandpass at centroid_hz, Q=1.5)
  -> Allpass decorrelation (Schroeder)
  -> Spectral EQ (10-section biquad cascade, DF-II)
  -> M/S stereo width correction

Master bus (DspChain):
  -> Soundboard IR convolution (25ms, 0-4% mix)
  -> BBE Sonic Maximizer (high shelf + low shelf)
  -> Peak Limiter (attack 1ms, variable release)
  -> Master gain + LFO pan
```

---

## 4. Diagnostic Tools

```bash
# Inspect soundbank parameters per register
python tools/inspect_bank.py soundbanks-additive/pl-grand.json

# Inspect specific note with partials detail
python tools/inspect_bank.py soundbanks-additive/pl-grand.json --midi 57 --vel 4

# Quality report (compare synthesis vs original WAV)
python tools/quality_report.py soundbanks-additive/pl-grand.json \
    --bank C:/SoundBanks/IthacaPlayer/pl-grand

# Blind listening test via MIDI loopback
python tools/blind_scoring.py --port "loopMIDI Port" \
    --params soundbanks-additive/pl-grand.json

# Profile optimizer -- learn from good notes, fix bad ones
python tools/profile_optimizer.py soundbanks-additive/pl-grand.json \
    --scores "62:0.98,88:0.98,57:0.30,50:0.34"
```

| Tool | Purpose |
|------|---------|
| `inspect_bank.py` | Per-register stats, per-note detail, velocity comparison |
| `quality_report.py` | Per-band spectral distance vs original WAV |
| `blind_scoring.py` | Randomized MIDI listening test, score 0-9 |
| `profile_optimizer.py` | Learn parameter profiles from good notes, correct bad ones |
| `extract_soundboard_ir.py` | Deconvolve soundboard IR from recordings |

---

## 5. Troubleshooting

| Problem | Solution |
|---------|----------|
| `torch` import error | Use `.venv312/Scripts/python.exe` (Python 3.12) |
| Silent output | Check `--params` path, verify JSON has `notes` array |
| "Brinkava" notes | Check stereo_width in inspector (should be < 2.0) |
| Blind scoring no sound | Verify loopMIDI port matches ICR MIDI input |
