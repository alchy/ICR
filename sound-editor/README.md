# ICR Sound Editor

3D parameter editor for the Ithaca Core Resonator synthesizer.

## Architecture

```
sound-editor/
├── backend/          Python FastAPI REST server
│   ├── main.py           all endpoints
│   ├── params_store.py   soundbank JSON in-memory store
│   ├── spline_engine.py  weighted smoothing spline (scipy)
│   ├── sysex_bridge.py   MIDI SysEx output (mido)
│   ├── schema_infer.py   dynamic schema detection from loaded bank
│   ├── layer_registry.py parameter layer hints / display metadata
│   └── requirements.txt
└── frontend/         Three.js + Vite web app
    ├── index.html
    └── src/
        ├── main.js             entry point / wiring
        ├── scene/
        │   ├── ParameterSpace.js   3D scene, OrbitControls, ghost layers
        │   ├── CardMesh.js         per-note sphere (default/anchor/kept/ghost)
        │   └── SplineMesh.js       fitted spline tube geometry
        ├── editor/
        │   ├── SplineEditor.js     click interactions, fit/redraw, keep/apply
        │   ├── LayerBrowser.js     left-panel dynamic layer list with tabs
        │   └── VelSelector.js      velocity circles + coherence/stickiness controls
        └── comms/
            └── ApiClient.js        REST fetch wrapper
```

## 3D Space Layout

| Axis | Maps to              | World range |
|------|----------------------|-------------|
| X    | MIDI note (21–108)   | −5 … +5     |
| Y    | Parameter value      | 0 … 8       |
| Z    | Velocity layer (0–7) | 0 … 8.4     |

## Running

### Backend

```bash
cd sound-editor/backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### Frontend

```bash
cd sound-editor/frontend
npm install
npm run dev
# → http://localhost:5173
```

## Layer browser

The left panel lists parameter layers grouped into three tabs:

| Tab | Contents |
|-----|----------|
| **Scalar** | One float per (midi, vel): `f0_hz`, `B`, `rms_gain`, … |
| **Per-partial** | One float per (midi, vel, k): `tau1_k1`, `A0_k3`, … — k-filter dropdown |
| **EQ** | Spectral EQ curves — opens EQ editor, no dot/spline view |

Each row has:
- **[K]** badge — layer is kept (blue dots, locked to spline blend)
- **●** checkbox — show/hide dots for this layer (active = full color, inactive = gray ghost)
- **⌇** checkbox — show/hide spline for this layer (active = colored tube, inactive = gray line)

The schema is inferred from the loaded soundbank automatically — no hardcoded layer list.

## Card colors

| Color | Meaning |
|-------|---------|
| Blue | Normal data point |
| Gold | Anchor point (spline passes exactly through here) |
| Bright blue | Kept / blended value |
| Gray (transparent) | Ghost — inactive layer |

## Interaction

| Action | Effect |
|--------|--------|
| Left-drag | Orbit camera |
| Right-drag / scroll | Pan / zoom |
| Click card | Pull spline toward that value (stickiness controls strength) |
| Alt+click card | Toggle anchor point on this note |
| **Fit spline** button | Recompute and redraw spline |

## Velocity selector

Top bar — circles **①②③④⑤⑥⑦⑧** select which velocities are active.
Multiple velocities can be selected; all operations apply to all selected velocities.

| Control | Effect |
|---------|--------|
| Coherence slider | 0 = fit each velocity independently; 1 = all share one curve |
| Stickiness slider | How hard click-pulls attract the spline (single velocity only) |
| **Keep** | Overlay spline-blended values as blue dots (reversible) |
| **Apply** | Bake current spline values into the soundbank baseline (irreversible) |
| **Fill missing** | Interpolate and bake values for notes that have no measured data |

## Anchor workflow (fixing NN-generated soundbanks)

Use this to fix bad notes from a neural-network soundbank while preserving good ones:

1. Play notes in ICRGUI — the "LAST NOTE" panel shows the MIDI number.
2. Note down MIDI numbers of notes that sound correct.
3. In the editor, select the target layer and velocity.
4. Type the good MIDI numbers in the **MIDI: 44, 54, 56 …** field → click **Set**.
   - Check **global anchors** to apply to all scalar + per-partial layers at once.
5. The spline will pass exactly through the anchored notes and interpolate the rest.
6. Click **Apply** to bake the smoothed values into the soundbank.
7. **Clear** removes all anchors for the selected velocity/layers.

## SysEx Protocol

```
F0 7D 01 <cmd> <data…> F7
```

| cmd | Command          |
|-----|-----------------|
| 01  | SET_NOTE_PARAM   |
| 02  | SET_NOTE_PARTIAL |
| 03  | SET_BANK (chunked) |
| 10  | SET_MASTER       |
| F0  | PING             |
