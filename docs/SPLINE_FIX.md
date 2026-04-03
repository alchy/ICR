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
| `--extend-partials` | Extend NN notes to the maximum partial count of measured notes before applying the spline. New partials are initialised with inharmonic `f_hz` and values from the last existing partial; the spline then fills in correct `tau1`, `tau2`, `A0`, etc. **Requires `--fix-interpolated`.** |
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

### Fix NN notes AND extend harmonics

```
python tools/spline_fix.py \
    --file-in  soundbanks/params-vv-rhodes-icr-eval.json \
    --fix-interpolated \
    --extend-partials \
    --auto-anchors 12 \
    --file-out soundbanks/params-vv-rhodes-full.json
```

NN notes that have fewer harmonics than measured notes will be extended to the
maximum measured partial count, with new partial values filled from the spline.

---

## Training pipeline workflows

Two pipelines are available, producing comparable output for A/B listening:

| Workflow | Command | What changes vs baseline |
|---|---|---|
| **icr-eval** | `python run-training.py icr-eval --bank ...` | Baseline — NN trained on raw extracted params; spline_fix cleans NN output post-export |
| **smooth-icr-eval** | `python run-training.py smooth-icr-eval --bank ...` | Spline-smoothed params as NN training targets; NN output is final (no second spline) |
| **smooth-icr-eval + extend** | `python run-training.py smooth-icr-eval --bank ... --extend-partials` | Same + measured notes extended to max partial count before training |

### Output files per workflow

All pipelines write to `soundbanks/` using `{bank}` = WAV directory name (e.g. `vv-rhodes`).

#### icr-eval
```
params-{bank}-icr-eval.json                  final: raw measured + spline(NN positions)
params-{bank}-icr-eval-pure-nn.json          all 704 notes from NN only
```

#### smooth-icr-eval
```
params-{bank}-smooth-icr-eval-pre-smooth.json          intermediate: raw measured only
params-{bank}-smooth-icr-eval-pre-smooth-spline.json   intermediate: spline-smoothed (training targets)
params-{bank}-smooth-icr-eval.json                     final: raw measured + NN output
params-{bank}-smooth-icr-eval-pure-nn.json             all 704 notes from NN only
```

#### File legend

| Suffix | Obsah | Určení |
|---|---|---|
| *(žádný)* | finální banka — raw měřené zachovány, NN doplňuje neměřené pozice | **player** |
| `-pure-nn` | všech 704 not z NN, žádné měřené nezachováno | A/B poslech |
| `-pre-smooth` | raw měřené noty bez vyhlazení | debug / reference |
| `-pre-smooth-spline` | měřené po spline vyhlazení | trénovací targety NN |

---

## Experiment — comparing the two workflows

Both pipelines run with the same settings (`N_EVAL_MIDI=24`, `eval_vels=[0..7]`)
and produce directly comparable soundbanks for A/B listening.

### What each pipeline tests

| | **icr-eval** | **smooth-icr-eval** | **smooth + --extend-partials** |
|---|---|---|---|
| NN training targets | raw extracted params | spline-smoothed measured | smooth + full partials |
| NN output after export | kept via spline_fix | **kept directly** | **kept directly** |
| Harmonic count (K_valid) | as generated by NN | as generated by NN | extended to max measured |
| Velocity eval coverage | [0..7] | [0..7] | [0..7] |
| MIDI eval coverage | 24 notes | 24 notes | 24 notes |

### Research questions

1. **Does smoothing training targets help?** *(icr-eval vs smooth-icr-eval)*
   The NN in `smooth-icr-eval` trains on a consistent, noise-free curve instead of
   raw extracted values. Does this improve generalisation to unmeasured MIDI positions?

2. **Does pre-training partial extension help?** *(smooth-icr-eval vs smooth + extend-partials)*
   Measured notes are extended to full partial count before training so the NN
   learns complete harmonic structure. Does richer training data improve tone in
   the extrapolation zone?

### Expected outputs

```
soundbanks/params-vv-rhodes-icr-eval.json
soundbanks/params-vv-rhodes-smooth-icr-eval.json
```

Load both into the sound editor and compare by playing the same notes across
the full keyboard, paying attention to:
- Notes in the extrapolation zone (MIDI 21–32 and 90–108)
- Velocity consistency (same note at vel 0 vs vel 7)
- Transition smoothness between neighbouring notes

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
