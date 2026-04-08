# ICR — Training Modules Reference

Analysis pipeline for extracting piano parameters from WAV recordings
and exporting JSON soundbanks + soundboard IR for the C++ ICR engine.

---

## Pipeline

```
python run-training.py analyze --bank C:/SoundBanks/IthacaPlayer/pl-grand
```

```
WAV bank directory
    |
    v
ParamExtractor              extractor.py
    | per-note: partials, envelopes, noise, inharmonicity
    | onset/decay separation, damping law constraint
    v
StructuralOutlierFilter     structural_outlier_filter.py
    | remove statistical outliers
    v
EQFitter                    eq_fitter.py
    | spectral EQ biquad cascade (10 sections)
    | stereo_width extraction (clamped 0.2-2.0)
    v
SoundbankExporter           exporter.py
    | JSON with RMS calibration (biquad BP noise model)
    | spectral shape borrowing (vel 5-7 -> vel 0-4)
    | A_noise cap at 1.0
    v
extract_soundboard_ir.py    tools/
    | deconvolve synthesis from recordings
    | average across ~20 notes, normalize, 25ms IR
    v
soundbanks/{bank}-{timestamp}.json         ← parameters
soundbanks/{bank}-{timestamp}-soundboard.wav  ← body IR
```

---

## Modules

### extractor.py — ParamExtractor

Core analysis module.  Key features:

- **Onset/decay separation:** onset STFT for A0 peak measurement (scales
  with f0 for bass), bi-exp fit from main STFT data only (avoids hammer
  transient contamination)
- **Bi-exp envelope:** 8-initialization multi-start fitter with random
  restarts, tau1 floor 0.05s, fit_quality metric per partial
- **Damping law constraint:** global R+eta*f^2 fit from reliable partials,
  replaces suspect tau values (tau1 < 0.02s or > 10s)
- **Noise analysis:** sin()+cos() basis for complete harmonic subtraction,
  attack_tau capped at 0.10s
- **Beat detection:** per-partial autocorrelation, 0.1-10 Hz range

Output per note: `f0_hz, B, phi_diff, attack_tau, A_noise,
noise_centroid_hz, partials[k]{f_hz, A0, tau1, tau2, a1, beat_hz, phi,
fit_quality, damping_derived}`

### structural_outlier_filter.py — StructuralOutlierFilter

Median + polynomial curve fit per parameter across MIDI range.
Flags notes deviating beyond 2.5 sigma.

### eq_fitter.py — EQFitter

- LTASE spectral envelope comparison (original vs synthesis)
- 10-section min-phase biquad cascade fit
- Sub-fundamental gain clamped to 0 dB
- stereo_width_factor: S/M ratio comparison, clamped [0.2, 2.0]
  (values >2.0 are extraction artifacts from near-mono synthesis)

### exporter.py — SoundbankExporter

- Spectral shape borrowing: A0 shape from vel 5-7 average applied to vel 0-4
  (fixes noise-floor contamination at low velocity)
- RMS calibration: Python renderer matches C++ biquad BP noise model exactly
- A_noise capped at 1.0 (prevents harmonic contamination)
- tau1 floor 0.05s in exported partials
- fit_quality and damping_derived propagated to JSON for GUI display

### synthesizer.py — Synthesizer

Python reference renderer (numpy, stereo).  Used by exporter for RMS
calibration — must match C++ signal path exactly:
- Biquad bandpass noise (Q=1.5) — NOT 1-pole LPF
- 1/2/3-string models with beat detuning
- Bi-exponential envelopes, spectral EQ

### tools/extract_soundboard_ir.py

Extracts effective soundboard impulse response:
- Renders pure additive synthesis (no EQ, no noise) per note
- Computes H(f) = FFT(original) / FFT(synthesis) with Wiener regularization
- Averages across ~20 notes (MIDI 33-87, every 3rd)
- Normalizes to unity gain at 2-6 kHz (HF neutral)
- Truncates to 25ms (body resonance only, no echo)
- Output: mono float32 WAV at source sample rate

---

## File Formats

### Input WAV naming
```
m{midi:03d}-vel{idx}-{sr_tag}.wav
```

### Output
- `{bank}-{MMDDHHmm}.json` — soundbank (see [JSON_SCHEMA.md](JSON_SCHEMA.md))
- `{bank}-{MMDDHHmm}-soundboard.wav` — soundboard IR (25ms mono)
