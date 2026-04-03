"""
tools/spline_fix.py
────────────────────
Post-process a soundbank JSON with per-layer spline smoothing.

Fits an independent smoothing spline for every (layer, velocity) pair and
optionally:
  - anchors the spline at specified MIDI positions (good notes preserved)
  - replaces statistical outliers with spline values
  - fills MIDI positions that have no value
  - replaces all values with the smooth spline curve

Parameters that span many orders of magnitude (B, rms_gain, tau1, tau2, A0,
f0_hz, beat_hz, centroid_hz, A_noise) are fitted in log-space so that the
spline treats ratios equally across the whole keyboard range.

Usage examples:

  # Report only — see which layers have outliers
  python tools/spline_fix.py --file-in soundbanks/params-vv-rhodes-icr-eval.json --report

  # Fix rms_gain outliers (main velocity-consistency problem)
  python tools/spline_fix.py --file-in soundbanks/params-vv-rhodes-icr-eval.json \\
      --smooth-outliers 3.0 --layers rms_gain

  # Anchor good notes + smooth outliers across all layers
  python tools/spline_fix.py --file-in soundbanks/params-vv-rhodes-icr-eval.json \\
      --anchor-midi 44 54 60 72 --smooth-outliers 3.0

  # Fill missing + smooth all
  python tools/spline_fix.py --file-in soundbanks/params-vv-rhodes-icr-eval.json \\
      --fill-missing --smooth-all --file-out soundbanks/fixed.json
"""

import argparse
import copy
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
from scipy.interpolate import UnivariateSpline, interp1d


# ── Parameters fitted in log-space ────────────────────────────────────────────

_LOG_SPACE_PARAMS = {
    "B", "rms_gain", "tau1", "tau2", "tau_r",
    "A0", "f0_hz", "beat_hz", "centroid_hz", "A_noise", "attack_tau",
    "duration_s", "df",
}

_LOG_EPS = 1e-12   # guard against log(0)


# ── Layer schema detection ─────────────────────────────────────────────────────

_SKIP_TOP = {"midi", "vel", "K_valid", "partials", "spectral_eq",
             "noise", "eq_biquads", "duration_s", "phi_diff",
             "_interpolated", "f0_nominal_hz"}
_SKIP_NOISE = {"attack_tau"}   # noise sub-keys promoted to top level in extract


def _detect_layers(notes: dict) -> list[str]:
    """Return all layer IDs present in the first complete note."""
    layers = []
    for note in notes.values():
        # Scalar
        for key, val in note.items():
            if key in _SKIP_TOP:
                continue
            if isinstance(val, (int, float)):
                layers.append(key)
        # Noise sub-keys
        noise = note.get("noise", {})
        for key, val in noise.items():
            if isinstance(val, (int, float)):
                layers.append(f"noise.{key}")
        # Per-partial
        partials = note.get("partials", [])
        if partials:
            for key in partials[0]:
                if key not in ("k", "f_hz"):
                    for ki in range(len(partials)):
                        layers.append(f"{key}_k{ki+1}")
        break
    return layers


# ── Value extraction / update ─────────────────────────────────────────────────

def _parse_layer_id(layer_id: str):
    """
    Return (access_fn, update_fn) for a layer_id.

    access_fn(note) → float | None
    update_fn(note, value) → None
    """
    # noise sub-key: "noise.attack_tau"
    if layer_id.startswith("noise."):
        subkey = layer_id[6:]
        def _get(note):
            return note.get("noise", {}).get(subkey)
        def _set(note, v):
            if "noise" in note:
                note["noise"][subkey] = v
        return _get, _set

    # per-partial: "tau1_k3"
    m = re.match(r"^(.+)_k(\d+)$", layer_id)
    if m:
        param, ki = m.group(1), int(m.group(2)) - 1   # 1-indexed → 0-indexed
        def _get(note):
            p = note.get("partials", [])
            if ki < len(p):
                return p[ki].get(param)
            return None
        def _set(note, v):
            p = note.get("partials", [])
            if ki < len(p):
                p[ki][param] = v
        return _get, _set

    # scalar top-level
    def _get(note):
        return note.get(layer_id)
    def _set(note, v):
        if layer_id in note:
            note[layer_id] = v
    return _get, _set


def _extract_layer_vel(notes: dict, layer_id: str, vel: int,
                       measured_only: bool = False,
                       ref_keys: "set[str] | None" = None) -> dict[int, float]:
    """Return {midi: value} for notes matching this layer+vel.

    measured_only=True: skip NN-generated notes.
      - Uses _interpolated flag if present in the note.
      - Falls back to ref_keys: notes absent from ref_keys are treated as NN.
    """
    getter, _ = _parse_layer_id(layer_id)
    result = {}
    suffix = f"_vel{vel}"
    for key, note in notes.items():
        if not key.endswith(suffix):
            continue
        if measured_only:
            if note.get("_interpolated", False):
                continue
            if ref_keys is not None and key not in ref_keys:
                continue
        midi = int(key[1:4])
        val = getter(note)
        if val is not None and math.isfinite(float(val)) and float(val) > 0:
            result[midi] = float(val)
    return result


def _interpolated_midis_vel(notes: dict, vel: int,
                            ref_keys: set[str] | None = None) -> set[int]:
    """Return set of MIDI numbers that are NN-interpolated for this velocity.

    Uses _interpolated flag if present; falls back to ref_keys comparison
    (notes absent from ref_keys are treated as NN-generated).
    """
    suffix = f"_vel{vel}"
    result = set()
    for key, note in notes.items():
        if not key.endswith(suffix):
            continue
        midi = int(key[1:4])
        if note.get("_interpolated", False):
            result.add(midi)
        elif ref_keys is not None and key not in ref_keys:
            result.add(midi)
    return result


def _update_layer_vel(notes: dict, layer_id: str, vel: int,
                      updates: dict[int, float]):
    """Write {midi: value} back into notes."""
    _, setter = _parse_layer_id(layer_id)
    suffix = f"_vel{vel}"
    for key, note in notes.items():
        if not key.endswith(suffix):
            continue
        midi = int(key[1:4])
        if midi in updates:
            setter(note, updates[midi])


# ── Spline fitting ─────────────────────────────────────────────────────────────

def _fit_spline(xs: np.ndarray, ys: np.ndarray, ws: np.ndarray,
                degree: int, stiffness: float):
    """Fit UnivariateSpline; fall back to linear interp on failure."""
    n = len(xs)
    if n < 2:
        return None
    s = float(np.sum(ws ** 2)) / max(stiffness, 1e-6)
    try:
        return UnivariateSpline(xs, ys, w=ws, k=min(degree, n - 1), s=s, ext=3)
    except Exception:
        return interp1d(xs, ys, kind="linear", fill_value="extrapolate")


def _process_layer_vel(
    data: dict[int, float],
    anchor_midis: set[int],
    smooth_outliers_k: float | None,
    fill_missing: bool,
    smooth_all: bool,
    degree: int,
    stiffness: float,
    log_space: bool,
) -> tuple[dict[int, float], int, int]:
    """
    Returns (updates, n_replaced, n_filled).
    updates: {midi: new_value} — only changed notes.
    """
    if not data:
        return {}, 0, 0

    # Work in log-space?
    if log_space:
        work = {m: math.log(max(v, _LOG_EPS)) for m, v in data.items()}
    else:
        work = dict(data)

    xs = np.array(sorted(work))
    ys = np.array([work[x] for x in xs])

    # Weights: anchors get high weight
    ws = np.ones(len(xs))
    for i, x in enumerate(xs):
        if int(x) in anchor_midis:
            ws[i] = 10.0

    spline = _fit_spline(xs, ys, ws, degree, stiffness)
    if spline is None:
        return {}, 0, 0

    updates: dict[int, float] = {}
    n_replaced = 0
    n_filled   = 0

    # smooth-outliers: replace points far from spline
    if smooth_outliers_k is not None:
        residuals = np.array([work[x] - float(spline(x)) for x in xs])
        std = float(np.std(residuals)) or 1.0
        for xi, x in enumerate(xs):
            midi = int(x)
            if midi in anchor_midis:
                continue
            if abs(residuals[xi]) > smooth_outliers_k * std:
                new_val = float(spline(x))
                updates[midi] = math.exp(new_val) if log_space else new_val
                n_replaced += 1

    # smooth-all: replace all non-anchor points
    if smooth_all:
        for x in xs:
            midi = int(x)
            if midi in anchor_midis:
                continue
            new_val = float(spline(x))
            updates[midi] = math.exp(new_val) if log_space else new_val

    # fill-missing: evaluate at MIDI positions not in data
    if fill_missing:
        all_midis = set(range(21, 109))
        missing = all_midis - set(int(x) for x in xs)
        for midi in sorted(missing):
            new_val = float(spline(midi))
            updates[midi] = math.exp(new_val) if log_space else new_val
            n_filled += 1

    return updates, n_replaced, n_filled


# ── Main ──────────────────────────────────────────────────────────────────────

def run(args):
    file_in = Path(args.file_in)
    if not file_in.exists():
        print(f"ERROR: {file_in} not found", file=sys.stderr)
        sys.exit(1)

    if args.file_out:
        file_out = Path(args.file_out)
    else:
        stem = file_in.stem
        if stem.endswith("-spline"):
            file_out = file_in   # overwrite if already spline-fixed
        else:
            file_out = file_in.parent / (stem + "-spline.json")

    print(f"Input : {file_in}  ({file_in.stat().st_size/1e6:.1f} MB)")

    bank  = json.loads(file_in.read_text())
    notes = bank.get("notes", {})

    # Reference bank for identifying measured vs NN notes (fallback for old banks)
    ref_keys: set[str] | None = None
    if args.ref_bank:
        ref_path = Path(args.ref_bank)
        if ref_path.exists():
            ref_keys = set(json.loads(ref_path.read_text()).get("notes", {}).keys())
            print(f"Ref   : {ref_path}  ({len(ref_keys)} measured notes)")
        else:
            print(f"WARNING: --ref-bank {ref_path} not found", file=sys.stderr)
    print(f"Notes : {len(notes)}")

    # Detect layers
    all_layers = _detect_layers(notes)
    if args.layers:
        req = set(args.layers.split(","))
        layers = [l for l in all_layers if l in req]
        missing = req - set(layers)
        if missing:
            print(f"WARNING: layers not found: {missing}", file=sys.stderr)
    else:
        layers = all_layers
    print(f"Layers: {len(layers)}")

    # Velocities
    if args.vel:
        vels = [int(v) for v in args.vel.split(",")]
    else:
        vels = list(range(8))

    # Anchor MIDIs
    anchor_midis = set(args.anchor_midi) if args.anchor_midi else set()

    # Outlier threshold
    smooth_k = args.smooth_outliers  # None if not set

    # Dry-run if --report
    report_only = args.report

    if report_only:
        print("\n-- Report --------------------------------------------------")
        print(f"{'layer':30s} {'vel':>4}  {'pts':>5}  {'outliers':>8}  "
              f"{'fill':>5}  {'min':>10}  {'max':>10}")
        print("-" * 80)

    total_replaced = 0
    total_filled   = 0
    out_notes = copy.deepcopy(notes) if not report_only else notes

    for layer_id in layers:
        log_sp = any(layer_id.startswith(p) or layer_id == p
                     for p in _LOG_SPACE_PARAMS)
        for vel in vels:
            # --fix-interpolated: fit spline on measured notes only,
            # then replace all NN-interpolated notes with spline values
            if args.fix_interpolated:
                measured_data  = _extract_layer_vel(notes, layer_id, vel,
                                                    measured_only=True,
                                                    ref_keys=ref_keys)
                interp_midis   = _interpolated_midis_vel(notes, vel, ref_keys)
                if not measured_data or not interp_midis:
                    data = _extract_layer_vel(notes, layer_id, vel)
                else:
                    # Fit on measured, evaluate at interpolated positions
                    if log_sp:
                        work = {m: math.log(max(v, _LOG_EPS))
                                for m, v in measured_data.items()}
                    else:
                        work = dict(measured_data)
                    xs = np.array(sorted(work))
                    ys = np.array([work[x] for x in xs])
                    ws = np.ones(len(xs))
                    for i, x in enumerate(xs):
                        if int(x) in anchor_midis:
                            ws[i] = 10.0
                    spline = _fit_spline(xs, ys, ws, args.degree,
                                         args.stiffness)
                    if spline is not None and not report_only:
                        fix_updates = {}
                        for midi in interp_midis:
                            v2 = float(spline(midi))
                            fix_updates[midi] = (math.exp(v2) if log_sp
                                                 else v2)
                        _update_layer_vel(out_notes, layer_id, vel,
                                          fix_updates)
                        total_replaced += len(fix_updates)
                    data = _extract_layer_vel(notes, layer_id, vel)

            else:
                data = _extract_layer_vel(notes, layer_id, vel)

            if not data:
                continue

            updates, n_rep, n_fill = _process_layer_vel(
                data, anchor_midis, smooth_k,
                args.fill_missing, args.smooth_all,
                args.degree, args.stiffness, log_sp,
            )

            if report_only:
                vals = list(data.values())
                interp_count = len(_interpolated_midis_vel(notes, vel, ref_keys))
                print(f"{layer_id:30s} {vel:>4}  {len(data):>5}  "
                      f"{n_rep:>8}  {n_fill:>5}  "
                      f"{min(vals):>10.4g}  {max(vals):>10.4g}  "
                      f"(NN:{interp_count})")
            else:
                if updates:
                    _update_layer_vel(out_notes, layer_id, vel, updates)
                total_replaced += n_rep
                total_filled   += n_fill

    if report_only:
        print("-" * 80)
        return

    print(f"\nReplaced (outliers/smooth): {total_replaced}")
    print(f"Filled  (missing)         : {total_filled}")

    if total_replaced == 0 and total_filled == 0 and not anchor_midis:
        print("Nothing changed — did you forget --smooth-outliers, "
              "--smooth-all, or --fill-missing?")
        return

    out_bank = {**{k: v for k, v in bank.items() if k != "notes"},
                "notes": out_notes}
    file_out.parent.mkdir(parents=True, exist_ok=True)
    file_out.write_text(json.dumps(out_bank, separators=(",", ":")))
    size_mb = file_out.stat().st_size / 1e6
    print(f"\nWritten: {file_out}  ({size_mb:.1f} MB)")


def main():
    p = argparse.ArgumentParser(
        description="Post-process soundbank JSON with per-layer spline smoothing.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--file-in",  required=True, metavar="PATH",
                   help="Input soundbank JSON")
    p.add_argument("--file-out", metavar="PATH",
                   help="Output path (default: <stem>-spline.json)")
    p.add_argument("--anchor-midi", type=int, nargs="+", metavar="MIDI",
                   help="MIDI note(s) to use as anchor points (spline passes exactly through these)")
    p.add_argument("--smooth-outliers", type=float, metavar="K",
                   help="Replace points where |value - spline| > K×std with spline value")
    p.add_argument("--fill-missing", action="store_true",
                   help="Fill MIDI positions with no value using spline interpolation")
    p.add_argument("--smooth-all", action="store_true",
                   help="Replace all values with smooth spline (maximum smoothing)")
    p.add_argument("--layers", metavar="L1,L2,...",
                   help="Comma-separated layer IDs to process (default: all)")
    p.add_argument("--vel", metavar="V1,V2,...",
                   help="Comma-separated velocities to process (default: 0-7)")
    p.add_argument("--stiffness", type=float, default=1.0,
                   help="Spline stiffness (higher = more rigid, default: 1.0)")
    p.add_argument("--degree", type=int, default=3, choices=[1, 2, 3, 5],
                   help="Spline degree (default: 3 = cubic)")
    p.add_argument("--ref-bank", metavar="PATH",
                   help="Original measured soundbank JSON; used by "
                        "--fix-interpolated to identify NN notes when "
                        "_interpolated flag is absent (older banks)")
    p.add_argument("--fix-interpolated", action="store_true",
                   help="Fit spline on measured notes only, replace all "
                        "NN-interpolated notes with spline values "
                        "(best fix for NN-generated soundbanks)")
    p.add_argument("--report", action="store_true",
                   help="Dry run: print stats without writing output")
    args = p.parse_args()

    if not any([args.anchor_midi, args.smooth_outliers is not None,
                args.fill_missing, args.smooth_all, args.fix_interpolated,
                args.report]):
        p.error("Specify at least one operation: --smooth-outliers, "
                "--smooth-all, --fill-missing, --anchor-midi, "
                "--fix-interpolated, or --report")

    run(args)


if __name__ == "__main__":
    main()
