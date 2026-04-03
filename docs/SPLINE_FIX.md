# spline_fix.py — Soundbank Post-Processing Tool

`tools/spline_fix.py` fits a smoothing spline over every `(layer, velocity)` pair in a soundbank JSON and optionally replaces outlier values, fills missing MIDI positions, or re-generates NN-interpolated notes from measured data.

---

## When to use it

| Problem | Recommended flags |
|---|---|
| NN-generated notes sound wrong (wrong amplitude, wrong decay) | `--fix-interpolated` |
| A few notes have hugely wrong rms_gain or tau1 | `--smooth-outliers 3.0` |
| Some MIDI positions are completely missing | `--fill-missing` |
| You want a globally smooth keyboard sweep | `--smooth-all` |
| You just want to see what is wrong before touching anything | `--report` |

The most common use case after an ICR-eval or NN training run is `--fix-interpolated`, which replaces all NN-generated notes with values derived from a spline fitted exclusively on measured notes.

---

## Installation

Requires `scipy` (already in the project venv):

```
pip install scipy
```

---

## Usage

```
python tools/spline_fix.py --file-in PATH [options]
```

### Required

| Flag | Description |
|---|---|
| `--file-in PATH` | Input soundbank JSON |

### Output

| Flag | Description |
|---|---|
| `--file-out PATH` | Output path. Default: `<stem>-spline.json` next to input. If the input stem already ends with `-spline`, the file is overwritten in-place. |

### Operations (at least one required)

| Flag | Description |
|---|---|
| `--fix-interpolated` | Fit spline on measured notes only; replace every NN-interpolated note with the spline value. Best fix for NN-generated soundbanks. |
| `--smooth-outliers K` | Replace any point where `|value − spline| > K × std` with the spline value. `K=3.0` is a safe starting point. |
| `--smooth-all` | Replace every non-anchor value with the smooth spline. Maximum smoothing. |
| `--fill-missing` | Add values for MIDI positions that have no data at all (21–108). |
| `--report` | Dry run: print stats per layer without writing any output. |

### Anchors

| Flag | Description |
|---|---|
| `--anchor-midi N [N ...]` | Lock the spline at these MIDI positions. Anchor notes are never replaced and pull the spline toward their values (weight ×10). Use well-sounding notes as anchors. |
| `--auto-anchors N` | Automatically select N best-measured notes as anchors, spread evenly across the keyboard. Quality metric: `K_valid × min(tau2/tau1, 10) × a1_quality`. Rewards notes with many valid partials and clear bi-exponential decay — i.e. notes where the extractor was most reliable. Can be combined with `--anchor-midi` (anchors are merged). |
| `--report-anchors` | Print the auto-selected anchors (MIDI, note name, score, K_valid, tau2/tau1) and exit without processing. Use this to inspect anchor selection before committing to a run. |

### Identifying NN notes

| Flag | Description |
|---|---|
| `--ref-bank PATH` | Path to the original measured-only soundbank JSON. Required for older banks that do not contain the `_interpolated` flag; notes absent from the reference are treated as NN-generated. |

### Filtering

| Flag | Description |
|---|---|
| `--layers L1,L2,...` | Comma-separated layer IDs to process. Default: all layers. |
| `--vel V1,V2,...` | Comma-separated velocity indices (0–7) to process. Default: 0–7. |

### Spline tuning

| Flag | Default | Description |
|---|---|---|
| `--stiffness FLOAT` | 1.0 | Higher = more rigid spline (less wiggle). |
| `--degree INT` | 3 | Spline degree: 1 (linear), 2, 3 (cubic), 5 (quintic). |

---

## Log-space parameters

The following parameters span several orders of magnitude and are fitted in log-space so that the spline treats multiplicative ratios uniformly across the keyboard:

`B`, `rms_gain`, `tau1`, `tau2`, `tau_r`, `A0`, `f0_hz`, `beat_hz`, `centroid_hz`, `A_noise`, `attack_tau`, `duration_s`, `df`

All other parameters are fitted in linear space.

---

## Layer naming

Layers follow the same naming convention as the sound editor:

| Pattern | Example | Meaning |
|---|---|---|
| `<param>` | `rms_gain` | Top-level scalar field |
| `noise.<param>` | `noise.A_noise` | Noise sub-object field |
| `<param>_k<N>` | `tau1_k3` | Per-partial field, partial index N (1-based) |

Run `--report` to see all layer IDs present in your soundbank.

---

## Examples

### Inspect a soundbank without changing anything

```
python tools/spline_fix.py \
    --file-in soundbanks/params-vv-rhodes-icr-eval.json \
    --report
```

### Preview auto-anchor selection before processing

```
python tools/spline_fix.py \
    --file-in soundbanks/params-vv-rhodes-icr-eval.json \
    --auto-anchors 12 --report-anchors
```

Output shows a table with MIDI number, note name, score, K_valid, and tau2/tau1 for each selected anchor.

### Fix NN notes in an icr-eval bank (has `_interpolated` flag)

```
python tools/spline_fix.py \
    --file-in  soundbanks/params-vv-rhodes-icr-eval.json \
    --file-out soundbanks/params-vv-rhodes-icr-eval-spline.json \
    --fix-interpolated \
    --auto-anchors 12
```

### Fix NN notes in an older bank (no `_interpolated` flag)

```
python tools/spline_fix.py \
    --file-in   soundbanks/params-vv-rhodes-nn.json \
    --ref-bank  soundbanks/params-vv-rhodes.json \
    --fix-interpolated \
    --auto-anchors 12 \
    --file-out  soundbanks/params-vv-rhodes-nn-spline.json
```

`--ref-bank` is required for older banks to correctly identify which notes are measured vs NN-generated. It also enables proper anchor scoring.

### Combine auto-anchors with manual anchors

```
python tools/spline_fix.py \
    --file-in soundbanks/params-vv-rhodes-icr-eval.json \
    --fix-interpolated \
    --auto-anchors 10 \
    --anchor-midi 65 \
    --file-out soundbanks/params-vv-rhodes-final.json
```

Anchors from both flags are merged. Use `--anchor-midi` to force-lock a specific note you know sounds good.

### Fix only rms_gain outliers

```
python tools/spline_fix.py \
    --file-in soundbanks/params-vv-rhodes-icr-eval.json \
    --smooth-outliers 3.0 \
    --layers rms_gain
```

### Smooth all layers, anchor known-good notes

```
python tools/spline_fix.py \
    --file-in soundbanks/params-vv-rhodes-icr-eval.json \
    --smooth-all \
    --anchor-midi 44 54 60 65 72
```

### Smooth only tau1 for partials 1–3 at low velocities

```
python tools/spline_fix.py \
    --file-in soundbanks/params-vv-rhodes-icr-eval.json \
    --smooth-outliers 2.5 \
    --layers tau1_k1,tau1_k2,tau1_k3 \
    --vel 0,1,2
```

---

## How it works

1. For each `(layer, velocity)` pair, extract `{midi: value}` from the soundbank.
2. Optionally filter to measured-only notes (`--fix-interpolated` / `--ref-bank`).
3. Fit a `scipy.interpolate.UnivariateSpline` (smoothing parameter proportional to `1/stiffness`).
4. Anchor notes get weight ×10, pulling the spline toward their exact values.
5. Log-space params are transformed before fitting and back-transformed after.
6. Depending on the operation flags, write back replaced / filled values.
7. The modified `notes` dict is written to the output JSON with the original header fields preserved.

---

## Notes

- The tool does **not** refit EQ biquads. If you change `spectral_eq` values, reload the soundbank in the sound editor and use the "Refit EQ" button.
- Anchor notes are **never** replaced by any operation, including `--smooth-all`.
- When `--fix-interpolated` is combined with `--smooth-outliers` or `--smooth-all`, the secondary operation runs on the full (already-fixed) dataset afterward.
- The output JSON is compact (no whitespace), identical format to the exporter output.
