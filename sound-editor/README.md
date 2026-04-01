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
│   ├── layer_registry.py parameter layer definitions
│   └── requirements.txt
└── frontend/         Three.js + Vite web app
    ├── index.html
    └── src/
        ├── main.js             entry point / wiring
        ├── scene/
        │   ├── ParameterSpace.js   3D scene, OrbitControls
        │   ├── CardMesh.js         per-note data point card
        │   └── SplineMesh.js       fitted spline tube geometry
        ├── editor/
        │   ├── SplineEditor.js     click interactions, fit/redraw
        │   └── LayerBrowser.js     left-panel layer list
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

## Interaction

| Action              | Effect                              |
|---------------------|-------------------------------------|
| Left-drag           | Orbit camera                        |
| Right-drag / scroll | Pan / zoom                          |
| Click card          | Pull spline toward that value       |
| Alt+click / right-click card | Toggle anchor point        |
| "Fit spline" button | Recompute and redraw curve          |

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
