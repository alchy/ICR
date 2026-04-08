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
import math
import os
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from params_store   import ParamsStore
from spline_engine  import SplineEngine, SplineState, SplineConfig, ControlPoint
from sysex_bridge   import SysExBridge, list_output_ports
from layer_registry import get_all_layers, group_layers, get_layer, build_layers_from_schema
from schema_infer   import infer_schema
from eq_editor      import refit_biquads

app = FastAPI(title="ICR Sound Editor", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Soundbank search paths ────────────────────────────────────────────────────
# Resolved relative to this file: backend/ → repo root → soundbanks-additive/
_BACKEND_DIR  = Path(__file__).parent
_REPO_ROOT    = _BACKEND_DIR.parent.parent          # sound-editor/backend → repo root
_BANKS_DIR    = Path(os.environ.get("ICR_BANKS_DIR", _REPO_ROOT / "soundbanks-additive"))


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

class SysExMasterRequest(BaseModel):
    param_key: str
    value:     float

class AuditionRequest(BaseModel):
    midi:     int
    velocity: int   = 80
    duration_ms: int = 500   # 0 = noteOn only (manual noteOff)

class CompareRequest(BaseModel):
    src_midi: int
    src_vel:  int
    dst_midi: int
    dst_vel:  int

class CorrectRequest(BaseModel):
    src_midi:    int
    src_vel:     int
    dst_midi:    int
    dst_vel:     int
    corrections: dict[str, float]   # param_key -> correction_pct (0=base, +5=5% above)
    copy_missing_partials: bool = False

class EqUpdateRequest(BaseModel):
    freqs_hz:  list[float]
    gains_db:  list[float]

class ExportRequest(BaseModel):
    path: str

class FitAllRequest(BaseModel):
    velocities:  list[int] = list(range(8))   # which vel layers to fit
    coherence:   float = 0.0                   # 0 = independent, 1 = average

class KeepRequest(BaseModel):
    velocities:  list[int] = list(range(8))
    coherence:   float = 0.0


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

@app.get("/schema")
def get_schema():
    """
    Return dynamic layer schema inferred from the loaded soundbank.

    If no bank is loaded, falls back to static registry defaults.
    Response: { scalar: [Layer], per_partial: [Layer], eq: [Layer], k_max: int }
    """
    notes = store.all_notes()
    if notes:
        schema = infer_schema(notes)
    else:
        # Fallback: use static registry with default k_max
        from schema_infer import infer_schema as _inf
        schema = {"scalar": [l.id for l in get_all_layers(1) if l.partial_k is None],
                  "per_partial": ["f_hz","A0","tau1","tau2","a1","beat_hz","phi"],
                  "eq": ["gains_db"],
                  "k_max": 60}
    layers = build_layers_from_schema(schema)
    # Serialize dataclasses to dicts
    return {dim: [vars(l) for l in lst] for dim, lst in layers.items()} | {"k_max": schema["k_max"]}

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
    """Fit spline for the default velocity and return evaluated values."""
    state    = _get_spline(layer_id)
    raw_data = store.extract_layer(layer_id)
    fitted   = engine.fit(state, raw_data)
    store.update_layer_values(layer_id, {
        f"m{m:03d}_vel{_default_vel(layer_id)}": v
        for m, v in fitted.items()
    })
    return fitted

@app.post("/spline/{layer_id}/fit_all")
def fit_all_velocities(layer_id: str, req: FitAllRequest):
    """
    Fit one spline per velocity layer, apply coherence blending, return curves.

    Returns:
        { vel: { "fitted": {midi: value}, "curve": {"x": [...], "y": [...]} } }
    """
    import numpy as np

    all_raw  = store.extract_layer(layer_id)    # {"m060_vel3": 0.41, ...}
    vels     = req.velocities
    coherence = max(0.0, min(1.0, req.coherence))
    n_curve   = 200

    # ── Per-velocity fit ──────────────────────────────────────────────────────
    per_vel: dict[int, dict[int, float]] = {}
    for vel in vels:
        vel_data = {k: v for k, v in all_raw.items() if k.endswith(f"_vel{vel}")}
        state    = _get_spline(f"{layer_id}__vel{vel}")
        fitted   = engine.fit(state, vel_data)
        per_vel[vel] = fitted

    original_per_vel = {v: dict(d) for v, d in per_vel.items()}  # snapshot before blending

    # ── Coherence blending ────────────────────────────────────────────────────
    if coherence > 0 and len(vels) > 1:
        midi_keys = sorted(set(m for d in per_vel.values() for m in d))
        avg = {
            m: float(np.mean([per_vel[v][m] for v in vels if m in per_vel[v]]))
            for m in midi_keys
        }
        for vel in vels:
            for m in per_vel[vel]:
                ind = per_vel[vel][m]
                per_vel[vel][m] = ind + coherence * (avg.get(m, ind) - ind)

    # ── Build curves — do NOT write to store (preview only) ──────────────────
    result = {}
    x_query = list(np.linspace(21, 108, n_curve))

    for vel in vels:
        orig    = original_per_vel.get(vel, {})
        blended = per_vel[vel]

        def _curve(d):
            xs = sorted(d); ys = [d[x] for x in xs]
            return list(np.interp(x_query, xs, ys)) if len(xs) >= 2 else [0.0] * len(x_query)

        result[vel] = {
            "fitted":   blended,
            "original": orig,
            "curve":    {"x": x_query, "y": _curve(blended)},
            "curve_original": {"x": x_query, "y": _curve(orig)},
        }

    return result

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


# ── Keep / override endpoints ────────────────────────────────────────────────

@app.post("/spline/{layer_id}/keep")
def keep_layer(layer_id: str, req: KeepRequest):
    """Commit current blended result as override (persists through export)."""
    import numpy as np

    all_raw   = store.extract_layer(layer_id)
    vels      = req.velocities
    coherence = max(0.0, min(1.0, req.coherence))

    per_vel: dict[int, dict[int, float]] = {}
    for vel in vels:
        vel_data = {k: v for k, v in all_raw.items() if k.endswith(f"_vel{vel}")}
        state    = _get_spline(f"{layer_id}__vel{vel}")
        per_vel[vel] = engine.fit(state, vel_data)

    if coherence > 0 and len(vels) > 1:
        midi_keys = sorted(set(m for d in per_vel.values() for m in d))
        avg = {
            m: float(np.mean([per_vel[v][m] for v in vels if m in per_vel[v]]))
            for m in midi_keys
        }
        for vel in vels:
            for m in per_vel[vel]:
                ind = per_vel[vel][m]
                per_vel[vel][m] = ind + coherence * (avg.get(m, ind) - ind)

    # Write blended values to override store (keyed per velocity)
    for vel in vels:
        override_key = f"{layer_id}__vel{vel}"
        store.keep_layer(override_key, {
            f"m{m:03d}_vel{vel}": v for m, v in per_vel[vel].items()
        })

    return {"kept": [f"{layer_id}__vel{v}" for v in vels]}

@app.delete("/spline/{layer_id}/keep")
def unkeep_layer(layer_id: str, velocities: str = ""):
    """Remove override for this layer (restores originals on export)."""
    vels = [int(v) for v in velocities.split(",") if v.strip().isdigit()] if velocities else list(range(8))
    removed = []
    for vel in vels:
        key = f"{layer_id}__vel{vel}"
        store.unkeep_layer(key)
        removed.append(key)
    return {"removed": removed}

@app.post("/spline/{layer_id}/apply")
def apply_layer(layer_id: str, req: KeepRequest):
    """
    Bake current overrides (or freshly fitted values) into _params permanently.
    Clears the override — the applied values become the new baseline.
    Export priority: Keep > Applied-baseline > raw-original.
    """
    import numpy as np
    vels = req.velocities
    baked: dict[str, float] = {}

    for vel in vels:
        override_key = f"{layer_id}__vel{vel}"
        if override_key in store._overrides:
            # Use existing override (from Keep)
            baked.update(store._overrides[override_key])
            store.unkeep_layer(override_key)
        else:
            # No keep active — fit and bake current spline
            all_raw  = store.extract_layer(layer_id)
            vel_data = {k: v for k, v in all_raw.items() if k.endswith(f"_vel{vel}")}
            state    = _get_spline(f"{layer_id}__vel{vel}")
            fitted   = engine.fit(state, vel_data)
            baked.update({f"m{m:03d}_vel{vel}": v for m, v in fitted.items()})

    store.update_layer_values(layer_id, baked)
    return {"applied": True, "notes": len(baked)}

@app.post("/spline/{layer_id}/fill_missing")
def fill_missing(layer_id: str, req: KeepRequest):
    """
    Compute spline values for notes that are missing this layer's value,
    and bake them directly into _params.

    Only notes where extract_layer() returns nothing are affected.
    Existing measured values are never overwritten.
    Returns the list of newly filled note keys.
    """
    import numpy as np
    vels    = req.velocities
    filled: dict[str, float] = {}

    for vel in vels:
        # Existing data for this vel
        all_raw  = store.extract_layer(layer_id)
        vel_data = {k: v for k, v in all_raw.items() if k.endswith(f"_vel{vel}")}

        # Which notes are missing at this vel?
        missing_keys = {
            k for k in store.missing_notes(layer_id)
            if k.endswith(f"_vel{vel}")
        }
        if not missing_keys or not vel_data:
            continue

        # Fit spline on existing data
        state  = _get_spline(f"{layer_id}__vel{vel}")
        fitted = engine.fit(state, vel_data)    # { midi: value }

        for note_key in missing_keys:
            try:
                midi = int(note_key[1:4])
            except (ValueError, IndexError):
                continue
            if midi in fitted:
                filled[note_key] = fitted[midi]

    if filled:
        store.update_layer_values(layer_id, filled)

    return {"filled": len(filled), "notes": sorted(filled.keys())}


@app.get("/spline/{layer_id}/keep_status")
def keep_status(layer_id: str):
    kept = store.kept_layers()
    vels = [int(k.split("__vel")[1]) for k in kept if k.startswith(f"{layer_id}__vel")]
    return {"kept_velocities": vels}


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


# ── EQ editor endpoints ───────────────────────────────────────────────────────

@app.get("/eq/{midi}/{vel}")
def get_eq(midi: int, vel: int):
    """
    Return the spectral EQ curve and current biquads for one note.

    Response:
        {
          "freqs_hz":  [...],   # frequency grid (from spectral_eq or default)
          "gains_db":  [...],   # gains (dB)
          "eq_biquads": [...]   # current biquad coefficients
        }
    """
    note = store.get_note(midi, vel)
    if note is None:
        raise HTTPException(404, f"Note m{midi:03d}_vel{vel} not found")

    spectral_eq = note.get("spectral_eq", {})
    return {
        "freqs_hz":   spectral_eq.get("freqs_hz", []),
        "gains_db":   spectral_eq.get("gains_db", []),
        "eq_biquads": note.get("eq_biquads", []),
    }

@app.post("/eq/{midi}/{vel}")
def update_eq(midi: int, vel: int, req: EqUpdateRequest):
    """
    Replace the EQ curve for one note, recompute biquads, update the store.

    The new biquads take effect for the next /sysex/bank push.
    Does not automatically send SysEx — call /sysex/bank afterwards.
    """
    note = store.get_note(midi, vel)
    if note is None:
        raise HTTPException(404, f"Note m{midi:03d}_vel{vel} not found")

    sr = store._meta.get("sr", 44100)
    new_biquads = refit_biquads(req.freqs_hz, req.gains_db, sr=sr)

    note["spectral_eq"] = {"freqs_hz": req.freqs_hz, "gains_db": req.gains_db}
    note["eq_biquads"]  = new_biquads

    return {"ok": True, "n_biquads": len(new_biquads)}


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

@app.post("/sysex/master")
def sysex_master(req: SysExMasterRequest):
    if not bridge.is_open():
        raise HTTPException(400, "MIDI port not connected")
    try:
        bridge.set_master(req.param_key, req.value)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"sent": True}

@app.post("/sysex/ping")
def sysex_ping():
    if not bridge.is_open():
        raise HTTPException(400, "MIDI port not connected")
    bridge.ping()
    return {"ping": True}


# ── MIDI Audition ─────────────────────────────────────────────────────────────

@app.post("/midi/audition")
def midi_audition(req: AuditionRequest):
    """Send noteOn, optionally wait duration_ms then noteOff.

    duration_ms=0 sends noteOn only (caller must send /midi/audition/off later).
    """
    if not bridge.is_open():
        raise HTTPException(400, "MIDI port not connected")
    bridge.note_on(req.midi, req.velocity)
    if req.duration_ms > 0:
        def _off():
            time.sleep(req.duration_ms / 1000.0)
            try:
                bridge.note_off(req.midi)
            except Exception:
                pass
        threading.Thread(target=_off, daemon=True).start()
    return {"sent": True, "midi": req.midi, "velocity": req.velocity}

@app.post("/midi/audition/off")
def midi_audition_off(midi: int = 60):
    """Send noteOff for a specific MIDI note."""
    if not bridge.is_open():
        raise HTTPException(400, "MIDI port not connected")
    bridge.note_off(midi)
    return {"sent": True, "midi": midi}


# ── Note Compare & Correct ───────────────────────────────────────────────────

def _compare_notes(src: dict, dst: dict) -> list[dict]:
    """Compute per-parameter base + deviation between source and destination.

    Base = expected destination value if it followed source's proportional
    relationships to f0.  Deviation = % difference from base.
    """
    src_f0 = src.get("f0_hz", 440.0)
    dst_f0 = dst.get("f0_hz", 440.0)
    f0_ratio = dst_f0 / src_f0 if src_f0 > 0 else 1.0

    results = []

    # Scalar params — some scale with f0, some don't
    scale_with_f0 = {"f0_hz", "noise_centroid_hz"}
    no_scale = {"B", "A_noise", "attack_tau", "rms_gain", "phi_diff",
                "stereo_width", "rise_tau"}

    for key in list(scale_with_f0) + sorted(no_scale):
        if key not in src and key not in dst:
            continue
        src_val = src.get(key, 0.0)
        dst_val = dst.get(key, 0.0)
        base = src_val * f0_ratio if key in scale_with_f0 else src_val
        dev_pct = ((dst_val / base) - 1.0) * 100.0 if abs(base) > 1e-12 else 0.0
        results.append({
            "key": key, "level": "note",
            "src": src_val, "dst": dst_val, "base": base,
            "deviation_pct": round(dev_pct, 2),
        })

    # Partial params
    src_partials = src.get("partials", [])
    dst_partials = dst.get("partials", [])
    dst_k_map = {p.get("k", i+1): i for i, p in enumerate(dst_partials)}

    freq_scale = {"f_hz"}   # scales with f0
    amp_keep = {"A0", "a1", "phi", "beat_hz"}  # no f0 scaling
    time_keep = {"tau1", "tau2"}  # no scaling

    for si, sp in enumerate(src_partials):
        k = sp.get("k", si + 1)
        di = dst_k_map.get(k)
        missing = (di is None)

        for pkey in ["f_hz", "A0", "tau1", "tau2", "a1", "beat_hz"]:
            src_pval = sp.get(pkey, 0.0)
            if missing:
                base = src_pval * f0_ratio if pkey in freq_scale else src_pval
                results.append({
                    "key": pkey, "level": "partial", "k": k,
                    "src": src_pval, "dst": None, "base": base,
                    "deviation_pct": None, "missing": True,
                })
            else:
                dp = dst_partials[di]
                dst_pval = dp.get(pkey, 0.0)
                base = src_pval * f0_ratio if pkey in freq_scale else src_pval
                dev = ((dst_pval / base) - 1.0) * 100.0 if abs(base) > 1e-12 else 0.0
                results.append({
                    "key": pkey, "level": "partial", "k": k,
                    "src": src_pval, "dst": dst_pval, "base": base,
                    "deviation_pct": round(dev, 2), "missing": False,
                })

    return results


@app.post("/editor/compare")
def editor_compare(req: CompareRequest):
    """Compare source and destination notes.

    Returns per-parameter table with base values and deviations.
    """
    src = store.get_note(req.src_midi, req.src_vel)
    dst = store.get_note(req.dst_midi, req.dst_vel)
    if not src:
        raise HTTPException(404, f"Source note m{req.src_midi:03d}_vel{req.src_vel} not found")
    if not dst:
        raise HTTPException(404, f"Destination note m{req.dst_midi:03d}_vel{req.dst_vel} not found")

    return {
        "src": {"midi": req.src_midi, "vel": req.src_vel, "f0_hz": src.get("f0_hz")},
        "dst": {"midi": req.dst_midi, "vel": req.dst_vel, "f0_hz": dst.get("f0_hz")},
        "f0_ratio": dst.get("f0_hz", 440) / src.get("f0_hz", 440),
        "params": _compare_notes(src, dst),
    }


@app.post("/editor/correct")
def editor_correct(req: CorrectRequest):
    """Apply proportional corrections to destination note.

    corrections: { "A0_k3": 0.0, "tau1_k1": 5.0 }
      key format: "param" for note-level, "param_kN" for partial-level
      value: 0.0 = set to base (100% correction), +5.0 = 5% above base, etc.

    copy_missing_partials: if True, partials present in source but not in
    destination are created with f0-proportional frequency.
    """
    src = store.get_note(req.src_midi, req.src_vel)
    dst = store.get_note(req.dst_midi, req.dst_vel)
    if not src or not dst:
        raise HTTPException(404, "Source or destination note not found")

    src_f0 = src.get("f0_hz", 440.0)
    dst_f0 = dst.get("f0_hz", 440.0)
    f0_ratio = dst_f0 / src_f0 if src_f0 > 0 else 1.0

    freq_keys = {"f_hz", "f0_hz", "noise_centroid_hz"}
    applied = 0

    for corr_key, pct in req.corrections.items():
        # Parse key: "tau1_k3" -> partial key "tau1", k=3
        # or "rms_gain" -> note-level
        if "_k" in corr_key:
            parts = corr_key.rsplit("_k", 1)
            pkey = parts[0]
            k = int(parts[1])
            # Find source partial
            src_p = next((p for p in src.get("partials", [])
                          if p.get("k") == k), None)
            if not src_p:
                continue
            src_val = src_p.get(pkey, 0.0)
            base = src_val * f0_ratio if pkey in freq_keys else src_val
            corrected = base * (1.0 + pct / 100.0)
            # Write to destination partial
            dst_partials = dst.get("partials", [])
            dst_p = next((p for p in dst_partials if p.get("k") == k), None)
            if dst_p:
                dst_p[pkey] = corrected
                applied += 1
        else:
            pkey = corr_key
            src_val = src.get(pkey, 0.0)
            base = src_val * f0_ratio if pkey in freq_keys else src_val
            corrected = base * (1.0 + pct / 100.0)
            dst[pkey] = corrected
            applied += 1

    # Copy missing partials
    copied = 0
    if req.copy_missing_partials:
        dst_partials = dst.setdefault("partials", [])
        dst_ks = {p.get("k") for p in dst_partials}
        for sp in src.get("partials", []):
            k = sp.get("k")
            if k and k not in dst_ks:
                new_p = dict(sp)  # copy all fields
                new_p["f_hz"] = sp.get("f_hz", 0.0) * f0_ratio
                dst_partials.append(new_p)
                copied += 1
        # Sort by k
        dst_partials.sort(key=lambda p: p.get("k", 0))

    return {"applied": applied, "copied_partials": copied}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _default_vel(layer_id: str) -> int:
    state = splines.get(layer_id)
    if state and state.config.velocity >= 0:
        return state.config.velocity
    return 3   # default velocity band
