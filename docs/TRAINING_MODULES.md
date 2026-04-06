# ICR — Training Modules Reference

Analysis pipeline for extracting piano parameters from WAV recordings
and exporting them as JSON soundbanks for the C++ ICR engine.

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
    | per-note analysis: partials, envelopes, noise, inharmonicity
    v
StructuralOutlierFilter     structural_outlier_filter.py
    | remove statistical outliers across note parameters
    v
EQFitter                    eq_fitter.py
    | fit spectral EQ biquad cascade from LTASE spectral envelope
    v
SoundbankExporter           exporter.py
    | assemble JSON with RMS calibration (uses Synthesizer internally)
    v
soundbanks/{bank}.json      ready for ICR C++ playback
```

---

## Modules

### extractor.py — ParamExtractor

Core analysis module. Extracts physical parameters from each WAV sample:

- **Partial detection**: Peak-picking in STFT magnitude spectrum
- **Frequency & inharmonicity**: f_k = k * f0 * sqrt(1 + B * k^2), curve_fit for B
- **Envelope fitting**: Bi-exponential a1*exp(-t/tau1) + (1-a1)*exp(-t/tau2)
  - High-res onset prepend (frame scales with f0 for bass resolution)
  - Multi-start optimization with 4 initializations
- **Beat detection**: Per-partial string detuning via autocorrelation
- **Noise analysis**: A_noise, attack_tau (capped at 100ms), centroid_hz
- **Phase extraction**: Initial phase matching Python RNG for C++ reproducibility

Key parameters per note: `f0_hz, B, phi_diff, attack_tau, A_noise,
noise_centroid_hz, rms_gain, stereo_width, partials[k]{f_hz, A0, tau1, tau2, a1, beat_hz, phi}`

### structural_outlier_filter.py — StructuralOutlierFilter

Detects and removes notes with anomalous parameter distributions:
- Fits smooth curves (median + polynomial) across MIDI range per parameter
- Flags notes deviating beyond configurable threshold (default 2.5 sigma)
- Preserves musical variation while removing extraction failures

### eq_fitter.py — EQFitter

Spectral envelope correction via biquad IIR cascade:
- Computes LTASE (Long-Term Average Spectral Envelope) from WAV
- Compares measured spectrum with additive synthesis prediction
- Fits min-phase biquad cascade (up to 5 sections) to the difference
- Sub-fundamental gain clamped to prevent boost below f0
- Exports `eq_biquads` array with {b[3], a[2]} per section

### exporter.py — SoundbankExporter

Assembles the final JSON soundbank:
- Converts extracted parameters to ICR JSON schema
- RMS gain calibration via Python Synthesizer rendering
- Noise centroid floor at 1000 Hz (prevents extraction artifacts)
- attack_tau capped at min(extracted, tau1_k1, 0.10s)
- EQ biquad coefficients from eq_fitter
- Output format: `{"notes": [{midi, vel, f0_hz, B, partials: [...], ...}]}`

### synthesizer.py — Synthesizer

Python reference synthesizer (numpy, stereo):
- Additive synthesis matching C++ PianoCore model
- 1/2/3-string models with beat detuning
- Bi-exponential envelopes, noise, spectral EQ
- Used by exporter for RMS gain calibration
- `eq_freq_min` default: 80 Hz (applies EQ down to bass register)

### pipeline_simple.py

Single pipeline orchestrator:
```python
def run(bank_dir, out_path, workers=None, skip_eq=False,
        skip_outliers=False, sr_tag="f48") -> str
```

---

## File Format

Input WAV naming: `m{midi:03d}-vel{idx}-{sr_tag}.wav`
- midi: 021-108 (A0-C8)
- vel idx: 0-7 (pp to ff)
- sr_tag: f44 or f48

Output: see [JSON_SCHEMA.md](JSON_SCHEMA.md)
