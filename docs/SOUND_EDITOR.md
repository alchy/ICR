# ICR Sound Editor

Interactive 3D editor for ICR synthesizer soundbanks.
Visualises every parameter across the keyboard (MIDI) and velocity space,
lets you sculpt them with weighted splines, and streams changes live to
the synthesizer via SysEx.

---

## Architecture

```
sound-editor/
├── backend/              Python 3.10+ FastAPI server  (port 8000)
│   ├── main.py           REST API — all endpoints
│   ├── params_store.py   In-memory soundbank store
│   ├── spline_engine.py  Weighted smoothing spline (scipy)
│   ├── sysex_bridge.py   MIDI SysEx output (mido + python-rtmidi)
│   ├── layer_registry.py Parameter layer definitions
│   └── requirements.txt
└── frontend/             Three.js + Vite  (port 5173)
    ├── index.html        Single-page app + CSS
    └── src/
        ├── main.js                   Entry point, panel wiring
        ├── scene/
        │   ├── ParameterSpace.js     3D scene, camera, OrbitControls
        │   ├── CardMesh.js           Per-note data point (box mesh)
        │   └── SplineMesh.js         Fitted spline (tube geometry)
        ├── editor/
        │   ├── SplineEditor.js       Interaction: pull / anchor / fit
        │   └── LayerBrowser.js       Left panel layer list
        └── comms/
            └── ApiClient.js          Fetch wrapper for backend REST
```

---

## Running

### Backend

```bash
cd sound-editor/backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Requires Python ≥ 3.10, scipy, mido, python-rtmidi.

### Frontend

```bash
cd sound-editor/frontend
npm install
npm run dev        # → http://localhost:5173
```

Requires Node ≥ 18, installs Three.js and Vite locally.

---

## 3D Space

```
        Y  (parameter value)
        │
        │       ● ● ●   ← cards, one per (MIDI, vel)
        │     ●●●●●●●●
        │
        └──────────────── X  (MIDI note 21–108)
       /
      Z  (velocity layer 0–7)
```

| Axis | Meaning              | World range |
|------|----------------------|-------------|
| X    | MIDI note (21–108)   | −5 … +5     |
| Y    | Parameter value      | 0 … 8 (normalised per layer) |
| Z    | Velocity layer (0–7) | 0 … 8.4     |

---

## Layers

A **layer** is one parameter across all (MIDI, velocity) slots.

### Scalar layers (whole note)

| Layer ID    | Label              | Range           |
|-------------|--------------------|-----------------|
| `f0_hz`     | F0 (Hz)            | 20 – 5000       |
| `B`         | Inharmonicity B    | 0 – 0.005       |
| `A_noise`   | Noise Amplitude    | 0 – 2.0         |
| `attack_tau`| Attack τ           | 0.001 – 0.1     |
| `rms_gain`  | RMS Gain           | 0 – 0.3         |

### Per-partial layers (expanded for k = 1..60)

Each template is instantiated 60 times, giving layer IDs like `tau1_k1`, `tau1_k2`, …

| Template key | Label        | Range         |
|-------------|--------------|---------------|
| `f_hz`      | f[k] Hz      | 10 – 8000     |
| `A0`        | A0[k]        | 0 – 50        |
| `tau1`      | τ1[k]        | 0.001 – 5.0   |
| `tau2`      | τ2[k]        | 0.01 – 30.0   |
| `a1`        | a1[k]        | 0 – 1.0       |
| `beat_hz`   | beat[k] Hz   | 0 – 10.0      |

Total: 5 scalar + 60 × 6 partial = **365 layers**.

---

## Spline model

The spline minimises a weighted least-squares + smoothness functional:

```
minimise  Σᵢ λᵢ · (f(xᵢ) − yᵢ)²  +  α · ∫ f″(x)² dx
```

| Symbol | Name        | Meaning                                         |
|--------|-------------|-------------------------------------------------|
| λᵢ     | Stickiness  | Per-point adhesion weight (0 = ignored, ∞ = interpolated) |
| α      | Stiffness   | Global smoothing strength (high = rigid, low = floppy) |
| xᵢ     | MIDI note   | 21–108                                          |
| yᵢ     | Value       | Parameter value at that note                    |

Implemented via `scipy.interpolate.UnivariateSpline` with per-point weights.
Fallback to linear interpolation if fewer than `degree+1` points are available.

### Stiffness → smoothing parameter mapping

```
s = Σ(wᵢ²) / stiffness
```

Higher stiffness → smaller `s` → spline passes closer to all data points.

### Control point types

| Type   | Stickiness default | Description                              |
|--------|--------------------|------------------------------------------|
| Pull   | 3.0                | Temporary bias; removed freely           |
| Anchor | 8.0 (min)          | User-designated ideal sample; persists   |

---

## Camera controls

| Input                   | Effect                  |
|-------------------------|-------------------------|
| Left-drag               | Orbit (rotate view)     |
| Right-drag              | Pan                     |
| Scroll wheel            | Zoom                    |

---

## Velocity selector (top bar)

```
①②③④⑤⑥⑦⑧   Coherence [──i──] 0.00  [Keep]  [Apply]   (Stickiness [──i──] when one vel selected)
```

Circles **①–⑧** correspond to velocity layers 0–7.  
Click a circle to toggle it on/off. At least one must stay active.  
The colour of each circle matches its spline tube in the 3D view.

---

## Spline shaping

| Action                      | Effect                                              |
|-----------------------------|-----------------------------------------------------|
| Click a data point (sphere) | Pull all selected splines toward that value         |
| Alt+click / right-click     | Toggle anchor on that (MIDI, vel) slot              |
| × in Control Points list    | Remove a control point                              |
| Stiffness slider + Apply    | Change global smoothing; re-fit immediately         |
| Fit spline button           | Manual re-fit                                       |

**Coherence** (0.0 – 1.0) blends selected velocity layers toward each other:

| Value | Behaviour                                              |
|-------|--------------------------------------------------------|
| 0.0   | Each velocity layer fits its own data independently    |
| 0.5   | Each layer moves halfway toward the cross-vel average  |
| 1.0   | All selected layers collapse to a common average curve |

Coherence is a **live preview** — moving the slider back returns splines to their original positions.  
It never writes to the data store until you press **Keep** or **Apply**.

---

## Non-destructive editing model

The editor has three data states per layer:

```
Raw originals  (_params)
      │
      ├── Keep override  (_overrides)   ← reversible, shown as blue dots
      │
      └── Applied baseline (_params)   ← irreversible bake, new originals
```

### Keep

Press **Keep** to overlay the current blended (coherence-modified) values:

- Original spheres turn **gray and translucent**
- New **blue spheres** appear at the spline-fitted positions
- The override is stored separately; `_params` is untouched

Press **Keep ✓** again to **undo Keep** — blue dots disappear, originals restore.

### Apply

Press **Apply** to **bake the current values into the baseline permanently**:

- If Keep is active, its values are baked; override is cleared
- If Keep is not active, the current spline fit is baked
- Original spheres move to the new positions (they *are* the new originals)
- **Irreversible within the session**

Apply enables iterative refinement:

```
Fit → Keep → inspect → Unkeep → adjust → Keep → Apply → repeat
```

### Export priority

| State                    | What gets exported                   |
|--------------------------|--------------------------------------|
| Keep active + Applied    | Keep override (most recent)          |
| Keep active, no Apply    | Keep override                        |
| Applied, Keep off        | Applied baseline (baked _params)     |
| Neither                  | Raw originals                        |

---

## Workflow

1. `python run-editor.py` — starts backend (:8000) + Vite (:5173), opens browser
2. Select soundbank from the dropdown (bottom bar) → **Load**
3. Select a layer from the left panel (e.g. `A_noise`, `tau1_k1`)
4. Spheres appear in 3D space — one per (MIDI, velocity) data point
5. **Shape splines:**
   - Select velocity layers with ①–⑧ circles
   - Adjust Coherence to blend layers together
   - Click spheres to pull; Alt+click to anchor
6. **Preview on synth:**
   - Connect MIDI loopback port (right panel)
   - Click **Send bank →** — SysEx SET_BANK streams to ICR
   - Play — hear changes in real time
7. **Commit:**
   - **Keep** → reversible overlay (blue dots visible)
   - **Apply** → bake into baseline (irreversible)
8. Repeat for other layers
9. **Save soundbank** — export path in right panel → saved JSON ready for ICR

---

## REST API reference

### Params

| Method | Path              | Description                     |
|--------|-------------------|---------------------------------|
| GET    | `/params`         | Meta info + note count          |
| POST   | `/params/load`    | Load soundbank from file path   |
| POST   | `/params/upload`  | Upload soundbank JSON directly  |
| GET    | `/params/notes`   | All notes (compact, no partials)|

### Layers

| Method | Path                      | Description               |
|--------|---------------------------|---------------------------|
| GET    | `/layers`                 | All layers grouped by type|
| GET    | `/layers/{layer_id}/values` | Raw values for a layer  |

### Splines

| Method | Path                            | Description                   |
|--------|---------------------------------|-------------------------------|
| GET    | `/spline/{layer_id}`            | Current spline state          |
| PUT    | `/spline/{layer_id}/config`     | Update stiffness / degree     |
| POST   | `/spline/{layer_id}/fit`        | Fit single velocity (legacy)  |
| POST   | `/spline/{layer_id}/fit_all`    | Fit all vel layers + coherence (preview, no store write) |
| POST   | `/spline/{layer_id}/keep`       | Commit blended values as override |
| DELETE | `/spline/{layer_id}/keep`       | Remove override (restore originals) |
| POST   | `/spline/{layer_id}/apply`      | Bake override into `_params` permanently |
| GET    | `/spline/{layer_id}/keep_status`| List which velocities have overrides |
| POST   | `/spline/{layer_id}/curve`      | Dense curve points for display|
| POST   | `/spline/{layer_id}/anchor`     | Add / update anchor point     |
| POST   | `/spline/{layer_id}/pull`       | Add / update pull point       |
| DELETE | `/spline/{layer_id}/point/{midi}` | Remove a control point     |

### Soundbank

| Method | Path                  | Description              |
|--------|-----------------------|--------------------------|
| GET    | `/soundbank/preview`  | Current soundbank as JSON|
| POST   | `/soundbank/export`   | Save to file             |

### MIDI / SysEx

| Method | Path              | Description                      |
|--------|-------------------|----------------------------------|
| GET    | `/midi/ports`     | List available MIDI output ports |
| POST   | `/midi/connect`   | Open a MIDI port                 |
| DELETE | `/midi/disconnect`| Close current port               |
| GET    | `/midi/status`    | Connection status                |
| POST   | `/sysex/note`     | Send SET_NOTE_PARAM              |
| POST   | `/sysex/partial`  | Send SET_NOTE_PARTIAL            |
| POST   | `/sysex/bank`     | Send full bank (chunked)         |
| POST   | `/sysex/ping`     | Send PING                        |

---

## SplineConfig fields

```json
{
  "stiffness":        1.0,
  "bass_split":       52,
  "bass_stiffness":   1.0,
  "treble_stiffness": 1.0,
  "degree":           3
}
```

`degree`: 1 = linear, 3 = cubic (default), 5 = quintic.  
`bass_split`: MIDI note where bass/treble regions divide (for independent stiffness).

---

## Adding a new layer

1. Add an entry to `SCALAR_LAYERS` or `_PARTIAL_TEMPLATES` in `layer_registry.py`.
2. Ensure `params_store.py` `extract_layer()` can find the key in the soundbank JSON.
3. If it is a new per-partial key, add the corresponding `PARTIAL_PARAM_IDS` entry in `sysex_bridge.py`.
4. No frontend changes needed — the layer browser auto-populates from `/layers`.
