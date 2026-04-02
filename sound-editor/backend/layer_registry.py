"""
sound-editor/backend/layer_registry.py
────────────────────────────────────────
Defines all editable parameter layers and their metadata.

A layer is one parameter across the (midi, vel) space.
Partial-indexed layers (e.g. tau1[k]) are expanded at runtime.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Layer:
    id:          str              # e.g. "tau1_k1", "A0_k3", "beat_hz_k1", "f0_hz"
    label:       str              # human-readable
    group:       str              # "envelope", "partial", "noise", "global"
    partial_k:   Optional[int]    # None for scalar params
    min_val:     float = 0.0
    max_val:     float = 1.0
    color_hex:   str  = "#4af"    # default spline colour in 3D view
    log_scale:   bool = False     # display on log Z axis


# ── Scalar layers (not per-partial) ──────────────────────────────────────────

SCALAR_LAYERS = [
    Layer("f0_hz",      "F0 (Hz)",           "global",   None,  20.0,  5000.0, "#fff", True),
    Layer("B",          "Inharmonicity B",   "global",   None,  0.0,   0.005,  "#aaf"),
    Layer("A_noise",    "Noise Amplitude",   "noise",    None,  0.0,   2.0,    "#fa0"),
    Layer("attack_tau", "Attack τ",          "noise",    None,  0.001, 0.1,    "#f80", True),
    Layer("rms_gain",   "RMS Gain",          "global",   None,  0.0,   0.3,    "#8f8"),
    Layer("phi_diff",   "Phase Diff φ",      "global",   None,  0.0,   6.2832, "#a8f"),
]

# ── Per-partial layers (expanded for k = 1..K_MAX) ───────────────────────────

K_MAX = 60

_PARTIAL_TEMPLATES = [
    # (param_key, label_fmt, group, min, max, color, log_scale)
    ("f_hz",    "f[k{k}] Hz",    "partial",  10.0,   8000.0, "#7df", True),
    ("A0",      "A0[k{k}]",      "envelope", 0.0,    50.0,   "#4f4", False),
    ("tau1",    "τ1[k{k}]",      "envelope", 0.001,  5.0,    "#08f", True),
    ("tau2",    "τ2[k{k}]",      "envelope", 0.01,   30.0,   "#04a", True),
    ("a1",      "a1[k{k}]",      "envelope", 0.0,    1.0,    "#adf", False),
    ("beat_hz", "beat[k{k}] Hz", "partial",  0.0,    10.0,   "#f4a", False),
    ("phi",     "φ[k{k}]",       "partial",  0.0,    6.2832, "#d8f", False),
]


def build_partial_layers(k_max: int = K_MAX) -> list[Layer]:
    layers = []
    colors = ["#7df", "#4f4", "#08f", "#04a", "#adf", "#f4a"]
    for k in range(1, k_max + 1):
        for i, (key, label_fmt, group, mn, mx, col, log) in enumerate(_PARTIAL_TEMPLATES):
            layers.append(Layer(
                id        = f"{key}_k{k}",
                label     = label_fmt.format(k=k),
                group     = group,
                partial_k = k,
                min_val   = mn,
                max_val   = mx,
                color_hex = col,
                log_scale = log,
            ))
    return layers


# ── Full registry ─────────────────────────────────────────────────────────────

def get_all_layers(k_max: int = K_MAX) -> list[Layer]:
    return SCALAR_LAYERS + build_partial_layers(k_max)


def get_layer(layer_id: str, k_max: int = K_MAX) -> Optional[Layer]:
    for layer in get_all_layers(k_max):
        if layer.id == layer_id:
            return layer
    return None


def group_layers(k_max: int = K_MAX) -> dict[str, list[Layer]]:
    groups: dict[str, list[Layer]] = {}
    for layer in get_all_layers(k_max):
        groups.setdefault(layer.group, []).append(layer)
    return groups


# ── Dynamic schema → Layer objects ───────────────────────────────────────────
# Metadata hints for known keys (min, max, color, log_scale).
# Unknown keys get safe defaults.

_SCALAR_HINTS: dict[str, dict] = {
    k: {"min": l.min_val, "max": l.max_val, "color": l.color_hex, "log": l.log_scale,
        "label": l.label}
    for l in SCALAR_LAYERS
    for k in [l.id]
}

_PARTIAL_HINTS: dict[str, dict] = {
    key: {"min": mn, "max": mx, "color": col, "log": log, "label_fmt": fmt}
    for key, fmt, _grp, mn, mx, col, log in _PARTIAL_TEMPLATES
}


def build_layers_from_schema(schema: dict) -> dict[str, list]:
    """
    Build Layer lists from a dynamically inferred schema dict.

    Returns:
        {
          "scalar":      [Layer, ...],
          "per_partial": [Layer, ...],   # expanded for k=1..k_max
          "eq":          [Layer, ...],
        }
    """
    k_max = max(1, schema.get("k_max", K_MAX))
    result: dict[str, list[Layer]] = {"scalar": [], "per_partial": [], "eq": []}

    # Scalar
    for key in schema.get("scalar", []):
        h = _SCALAR_HINTS.get(key, {})
        result["scalar"].append(Layer(
            id        = key,
            label     = h.get("label", key),
            group     = "scalar",
            partial_k = None,
            min_val   = h.get("min", 0.0),
            max_val   = h.get("max", 1.0),
            color_hex = h.get("color", "#4af"),
            log_scale = h.get("log", False),
        ))

    # Per-partial (expanded)
    for key in schema.get("per_partial", []):
        h = _PARTIAL_HINTS.get(key, {})
        fmt = h.get("label_fmt", f"{key}[k{{k}}]")
        for k in range(1, k_max + 1):
            result["per_partial"].append(Layer(
                id        = f"{key}_k{k}",
                label     = fmt.format(k=k),
                group     = "per_partial",
                partial_k = k,
                min_val   = h.get("min", 0.0),
                max_val   = h.get("max", 1.0),
                color_hex = h.get("color", "#4af"),
                log_scale = h.get("log", False),
            ))

    # EQ (one layer per array key — treated as a special curve layer)
    EQ_HINTS = {
        "gains_db":           {"label": "EQ Gains (dB)", "min": -24.0, "max": 24.0, "color": "#ff8"},
        "stereo_width_factor": {"label": "Stereo Width",  "min": 0.0,   "max": 2.0,  "color": "#f8a"},
    }
    for key in schema.get("eq", []):
        h = EQ_HINTS.get(key, {})
        result["eq"].append(Layer(
            id        = f"eq_{key}",
            label     = h.get("label", f"EQ {key}"),
            group     = "eq",
            partial_k = None,
            min_val   = h.get("min", -1.0),
            max_val   = h.get("max",  1.0),
            color_hex = h.get("color", "#ff8"),
            log_scale = False,
        ))

    return result
