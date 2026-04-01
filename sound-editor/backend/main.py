"""
sound-editor/backend/main.py
──────────────────────────────
FastAPI backend for ICR Sound Editor.

Run:
    cd sound-editor/backend
    pip install -r requirements.txt
    uvicorn main:app --reload --port 8000
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from params_store   import ParamsStore
from spline_engine  import SplineEngine, SplineState, SplineConfig, ControlPoint
from sysex_bridge   import SysExBridge, list_output_ports
from layer_registry import get_all_layers, group_layers, get_layer

app = FastAPI(title="ICR Sound Editor", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Soundbank search paths ────────────────────────────────────────────────────
# Resolved relative to this file: backend/ → repo root → soundbanks/
_BACKEND_DIR  = Path(__file__).parent
_REPO_ROOT    = _BACKEND_DIR.parent.parent          # sound-editor/backend → repo root
_BANKS_DIR    = Path(os.environ.get("ICR_BANKS_DIR", _REPO_ROOT / "soundbanks"))


# ── Singletons ────────────────────────────────────────────────────────────────

store   = ParamsStore()
engine  = SplineEngine()
bridge  = SysExBridge()
splines: dict[str, SplineState] = {}   # layer_id → SplineState


def _get_spline(layer_id: str) -> SplineState:
    if layer_id not in splines:
        splines[layer_id] = SplineState(layer_id=layer_id)
    return splines[layer_id]


# ── Models ────────────────────────────────────────────────────────────────────

class LoadFileRequest(BaseModel):
    path: str

class SplineConfigRequest(BaseModel):
    stiffness:         float = 1.0
    bass_split:        int   = 52
    bass_stiffness:    float = 1.0
    treble_stiffness:  float = 1.0
    degree:            int   = 3

class ControlPointRequest(BaseModel):
    midi:        int
    value:       float
    stickiness:  float = 1.0
    is_anchor:   bool  = False

class PullRequest(BaseModel):
    midi:        float   # can be fractional for in-between positions
    value:       float
    stickiness:  float = 3.0

class MidiPortRequest(BaseModel):
    port_name: str

class SysExNoteRequest(BaseModel):
    midi:      int
    vel:       int
    param_key: str
    value:     float

class SysExPartialRequest(BaseModel):
    midi:      int
    vel:       int
    k:         int
    param_key: str
    value:     float

class ExportRequest(BaseModel):
    path: str


# ── Params endpoints ──────────────────────────────────────────────────────────

@app.get("/params")
def get_params():
    return {"n_notes": len(store.all_notes()), "meta": store._meta}

@app.post("/params/load")
def load_params(req: LoadFileRequest):
    n = store.load_file(req.path)
    return {"loaded": n, "path": req.path}

@app.post("/params/upload")
async def upload_params(file: UploadFile = File(...)):
    content = await file.read()
    data = json.loads(content)
    n = store.load_dict(data)
    return {"loaded": n}

@app.get("/params/notes")
def get_notes():
    """Return all notes (compact — no partials array)."""
    result = {}
    for key, note in store.all_notes().items():
        result[key] = {k: v for k, v in note.items() if k != "partials"}
    return result


# ── Layer endpoints ───────────────────────────────────────────────────────────

@app.get("/layers")
def get_layers():
    return group_layers()

@app.get("/layers/{layer_id}/values")
def get_layer_values(layer_id: str):
    """Return raw extracted values for a layer."""
    values = store.extract_layer(layer_id)
    if not values:
        raise HTTPException(404, f"No data for layer {layer_id!r}")
    return values


# ── Spline endpoints ──────────────────────────────────────────────────────────

@app.get("/spline/{layer_id}")
def get_spline(layer_id: str):
    state = _get_spline(layer_id)
    return {
        "layer_id":      state.layer_id,
        "config":        state.config.__dict__,
        "control_points": [cp.__dict__ for cp in state.control_points],
    }

@app.put("/spline/{layer_id}/config")
def update_spline_config(layer_id: str, req: SplineConfigRequest):
    state = _get_spline(layer_id)
    state.config = SplineConfig(**req.dict())
    return {"ok": True}

@app.post("/spline/{layer_id}/fit")
def fit_spline(layer_id: str):
    """Fit spline and return evaluated values for all MIDI notes."""
    state    = _get_spline(layer_id)
    raw_data = store.extract_layer(layer_id)
    fitted   = engine.fit(state, raw_data)
    store.update_layer_values(layer_id, {
        f"m{m:03d}_vel{_default_vel(layer_id)}": v
        for m, v in fitted.items()
    })
    return fitted

@app.post("/spline/{layer_id}/curve")
def get_spline_curve(layer_id: str, n_points: int = 200):
    """Return dense curve points for 3D visualization."""
    import numpy as np
    state    = _get_spline(layer_id)
    raw_data = store.extract_layer(layer_id)
    x_query  = list(np.linspace(21, 108, n_points))
    y_vals   = engine.evaluate_points(state, raw_data, x_query)
    return {"x": x_query, "y": y_vals}

@app.post("/spline/{layer_id}/anchor")
def set_anchor(layer_id: str, req: ControlPointRequest):
    state = _get_spline(layer_id)
    state.add_anchor(req.midi, req.value, req.stickiness)
    return {"ok": True, "n_points": len(state.control_points)}

@app.post("/spline/{layer_id}/pull")
def pull_spline(layer_id: str, req: PullRequest):
    state = _get_spline(layer_id)
    state.add_pull(int(req.midi), req.value, req.stickiness)
    return {"ok": True}

@app.delete("/spline/{layer_id}/point/{midi}")
def remove_point(layer_id: str, midi: int):
    state = _get_spline(layer_id)
    state.remove_point(midi)
    return {"ok": True}


# ── Soundbank listing + export ───────────────────────────────────────────────

@app.get("/soundbanks/list")
def list_soundbanks():
    """Return all .json files found in the ICR_BANKS_DIR directory."""
    if not _BANKS_DIR.is_dir():
        return {"dir": str(_BANKS_DIR), "files": []}
    files = sorted(
        p.name for p in _BANKS_DIR.iterdir()
        if p.suffix.lower() == ".json"
    )
    return {"dir": str(_BANKS_DIR), "files": files}

@app.post("/soundbanks/load/{filename}")
def load_soundbank_by_name(filename: str):
    """Load a soundbank by filename from the banks directory."""
    path = _BANKS_DIR / filename
    if not path.exists():
        raise HTTPException(404, f"File not found: {path}")
    n = store.load_file(str(path))
    return {"loaded": n, "path": str(path)}

@app.get("/soundbank/preview")
def preview_soundbank():
    return store.to_dict()

@app.post("/soundbank/export")
def export_soundbank(req: ExportRequest):
    store.save(req.path)
    return {"saved": req.path}


# ── MIDI / SysEx endpoints ────────────────────────────────────────────────────

@app.get("/midi/ports")
def get_midi_ports():
    return {"ports": list_output_ports()}

@app.post("/midi/connect")
def connect_midi(req: MidiPortRequest):
    try:
        bridge.open(req.port_name)
        return {"connected": req.port_name}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.delete("/midi/disconnect")
def disconnect_midi():
    bridge.close()
    return {"disconnected": True}

@app.get("/midi/status")
def midi_status():
    return {"connected": bridge.is_open(), "port": bridge._port_name}

@app.post("/sysex/note")
def sysex_note(req: SysExNoteRequest):
    if not bridge.is_open():
        raise HTTPException(400, "MIDI port not connected")
    bridge.set_note_param(req.midi, req.vel, req.param_key, req.value)
    return {"sent": True}

@app.post("/sysex/partial")
def sysex_partial(req: SysExPartialRequest):
    if not bridge.is_open():
        raise HTTPException(400, "MIDI port not connected")
    bridge.set_note_partial(req.midi, req.vel, req.k, req.param_key, req.value)
    return {"sent": True}

@app.post("/sysex/bank")
def sysex_bank():
    if not bridge.is_open():
        raise HTTPException(400, "MIDI port not connected")
    data = json.dumps(store.to_dict()).encode("utf-8")
    bridge.set_bank(data)
    return {"sent": True, "bytes": len(data)}

@app.post("/sysex/ping")
def sysex_ping():
    if not bridge.is_open():
        raise HTTPException(400, "MIDI port not connected")
    bridge.ping()
    return {"ping": True}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _default_vel(layer_id: str) -> int:
    state = splines.get(layer_id)
    if state and state.config.velocity >= 0:
        return state.config.velocity
    return 3   # default velocity band
