"""
tools/reexport_eq.py
───────────────────
Re-export soundbank with fixed EQ biquads and rms_gain.

Reads an existing soundbank that has spectral_eq per note, applies the
sub-fundamental gain clamping fix (clip boosts to 0 below f0*0.8), and
recomputes eq_biquads + rms_gain.  Does NOT re-run extraction or LTASE.

Usage:
    python tools/reexport_eq.py soundbanks/pl-grand.json soundbanks/pl-grand.json
"""

import json
import sys
import time
from pathlib import Path

# Repo root on path
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
from training.modules.eq_fitter import _eq_to_biquads
from training.modules.exporter import (
    _render_note_rms_ref,
    VEL_GAMMA,
    TARGET_RMS_DEFAULT,
    PIANO_N_BIQUAD,
)


def reexport(in_path: str, out_path: str) -> None:
    print(f"Reading: {in_path}")
    with open(in_path) as f:
        bank = json.load(f)

    sr = bank["metadata"].get("sr", 44100)
    duration = bank["metadata"].get("duration_s", 3.0)
    target_rms = bank["metadata"].get("target_rms", TARGET_RMS_DEFAULT)
    print(f"sr={sr}  duration={duration}s  target_rms={target_rms}")

    notes = bank["notes"]
    total = len(notes)
    n_eq_updated = 0
    n_rms_updated = 0
    t0 = time.monotonic()

    for i, (key, note) in enumerate(notes.items()):
        seq = note.get("spectral_eq")
        if not seq:
            continue

        freqs_hz = seq.get("freqs_hz")
        gains_db = seq.get("gains_db")
        if not freqs_hz or not gains_db:
            continue

        f0_hz = float(note.get("f0_hz", 0.0))
        f_arr = np.array(freqs_hz, dtype=np.float64)
        g_arr = np.array(gains_db, dtype=np.float64)

        # Sub-fundamental clamping
        if f0_hz > 100.0:
            sub_mask = f_arr < f0_hz * 0.8
            n_clamped = int(np.sum(sub_mask & (g_arr > 0)))
            g_arr[sub_mask] = np.minimum(g_arr[sub_mask], 0.0)
            if n_clamped > 0 and (i < 5 or key in ("m072_vel3", "m084_vel3", "m096_vel3")):
                print(f"  {key}: clamped {n_clamped} sub-f0 bins (f0={f0_hz:.0f} Hz)")

        # Recompute biquads
        try:
            new_biquads = _eq_to_biquads(f_arr, g_arr, sr, n_sections=PIANO_N_BIQUAD)
        except Exception as e:
            print(f"  {key}: biquad fit failed: {e}")
            continue

        note["eq_biquads"] = new_biquads
        n_eq_updated += 1

        # noise_centroid_hz floor: extracted values for bass/tenor are dominated by
        # harmonic residual (centroid ~120-200 Hz), causing boomy "bottle" noise at
        # the same frequency as the piano tone.  Apply a minimum of 1000 Hz so the
        # attack noise is always spectrally above the lower harmonics.
        CENTROID_MIN_HZ = 1000.0
        centroid_hz_orig = float(note.get("noise_centroid_hz", 3000.0))
        centroid_hz = max(centroid_hz_orig, CENTROID_MIN_HZ)
        if centroid_hz != centroid_hz_orig and (i < 5 or i % 100 == 0):
            print(f"  {key}: centroid_hz {centroid_hz_orig:.0f} -> {centroid_hz:.0f} Hz")
        note["noise_centroid_hz"] = centroid_hz

        # Recompute rms_gain
        partials = note.get("partials", [])
        phi_diff = float(note.get("phi_diff", 0.0))
        attack_tau = float(note.get("attack_tau", 0.05))
        A_noise = float(note.get("A_noise", 0.0))
        vel_idx = int(note.get("vel", 3))
        midi = int(note.get("midi", 60))

        audio = _render_note_rms_ref(
            partials, phi_diff, attack_tau, A_noise, centroid_hz,
            new_biquads, midi, sr, duration,
        )
        rms = float(np.sqrt(np.mean(audio**2) + 1e-12))
        vel_gain = ((vel_idx + 1) / 8.0) ** VEL_GAMMA
        new_rms_gain = ((target_rms * vel_gain) / rms) if rms > 1e-10 else 1.0
        old_rms_gain = note.get("rms_gain", 1.0)
        note["rms_gain"] = new_rms_gain
        n_rms_updated += 1

        if i < 5 or key in ("m072_vel3", "m084_vel3", "m096_vel3"):
            print(f"  {key}: rms_gain {old_rms_gain:.4f} -> {new_rms_gain:.4f}")

        if (i + 1) % 100 == 0:
            ela = time.monotonic() - t0
            print(f"  {i+1}/{total}  ({ela:.1f}s)", flush=True)

    ela = time.monotonic() - t0
    print(f"\nUpdated: {n_eq_updated} biquads, {n_rms_updated} rms_gains  ({ela:.1f}s)")

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(bank, f, separators=(",", ":"))
    size_mb = Path(out_path).stat().st_size / 1e6
    print(f"Written: {out_path}  ({size_mb:.1f} MB)")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    reexport(sys.argv[1], sys.argv[2])
