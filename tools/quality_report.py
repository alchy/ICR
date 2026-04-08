#!/usr/bin/env python3
"""
tools/quality_report.py
───────────────────────
Compare synthesized output from a soundbank JSON against original WAV recordings.
Compute per-note metrics and correlate with manual listening scores.

Usage:
    python tools/quality_report.py soundbanks-additive/pl-grand-04071523.json \
        --bank C:/SoundBanks/IthacaPlayer/pl-grand

    python tools/quality_report.py soundbanks-additive/pl-grand-04071523.json \
        --bank C:/SoundBanks/IthacaPlayer/pl-grand \
        --scores "88:0.98,92:0.99,55:0.21,77:0.30"
"""

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

REPO_ROOT = Path(__file__).parent.parent.resolve()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from training.modules.exporter import _render_note_rms_ref


# ── Metrics ───────────────────────────────────────────────────────────────────

def log_spectral_distance(orig: np.ndarray, synth: np.ndarray,
                          sr: int, n_fft: int = 4096) -> float:
    """Average log-spectral distance in dB across time frames."""
    hop = n_fft // 4
    n = min(len(orig), len(synth))
    orig, synth = orig[:n], synth[:n]

    n_frames = max(1, (n - n_fft) // hop)
    distances = []
    for i in range(n_frames):
        s = i * hop
        o_frame = orig[s:s+n_fft] * np.hanning(n_fft)
        s_frame = synth[s:s+n_fft] * np.hanning(n_fft)

        O = np.abs(np.fft.rfft(o_frame)) + 1e-12
        S = np.abs(np.fft.rfft(s_frame)) + 1e-12

        # Log spectral distance (dB)
        lsd = np.sqrt(np.mean((20 * np.log10(O / S)) ** 2))
        distances.append(lsd)

    return float(np.mean(distances)) if distances else 999.0


def envelope_correlation(orig: np.ndarray, synth: np.ndarray,
                         sr: int, hop_ms: float = 10.0) -> float:
    """Correlation between amplitude envelopes."""
    hop = max(1, int(hop_ms * 0.001 * sr))
    n = min(len(orig), len(synth))
    orig, synth = orig[:n], synth[:n]

    n_frames = max(1, n // hop)
    env_o = np.array([np.sqrt(np.mean(orig[i*hop:(i+1)*hop]**2) + 1e-30)
                      for i in range(n_frames)])
    env_s = np.array([np.sqrt(np.mean(synth[i*hop:(i+1)*hop]**2) + 1e-30)
                      for i in range(n_frames)])

    if env_o.std() < 1e-12 or env_s.std() < 1e-12:
        return 0.0
    return float(np.corrcoef(env_o, env_s)[0, 1])


def band_energy_diff(orig: np.ndarray, synth: np.ndarray, sr: int) -> dict:
    """Per-octave-band energy difference in dB (synth - orig).

    Positive = synth has MORE energy in that band.
    Negative = synth is MISSING energy (the "hollow" indicator).

    Bands: 0-250, 250-500, 500-1k, 1k-2k, 2k-4k, 4k-8k, 8k-16k Hz
    """
    n_fft = min(8192, min(len(orig), len(synth)))
    n = n_fft
    O = np.abs(np.fft.rfft(orig[:n] * np.hanning(n))) ** 2
    S = np.abs(np.fft.rfft(synth[:n] * np.hanning(n))) ** 2
    freqs = np.fft.rfftfreq(n, 1.0 / sr)

    bands = [(0, 250), (250, 500), (500, 1000), (1000, 2000),
             (2000, 4000), (4000, 8000), (8000, 16000)]
    result = {}
    for lo, hi in bands:
        mask = (freqs >= lo) & (freqs < hi)
        if not mask.any():
            continue
        e_o = float(np.sum(O[mask]) + 1e-30)
        e_s = float(np.sum(S[mask]) + 1e-30)
        result[f"{lo}-{hi}"] = 10 * math.log10(e_s / e_o)
    return result


def brightness_diff(orig: np.ndarray, synth: np.ndarray, sr: int) -> float:
    """Difference in 'brightness' = ratio of energy above 1kHz to total.

    Positive = synth is brighter than original.
    Negative = synth is duller (the "muffled/hollow" indicator).
    """
    n_fft = min(8192, min(len(orig), len(synth)))
    n = n_fft
    freqs = np.fft.rfftfreq(n, 1.0 / sr)

    O = np.abs(np.fft.rfft(orig[:n] * np.hanning(n))) ** 2
    S = np.abs(np.fft.rfft(synth[:n] * np.hanning(n))) ** 2

    hi_mask = freqs >= 1000
    bright_o = float(np.sum(O[hi_mask]) / (np.sum(O) + 1e-30))
    bright_s = float(np.sum(S[hi_mask]) / (np.sum(S) + 1e-30))

    return bright_s - bright_o  # negative = synth duller


def attack_energy_ratio(orig: np.ndarray, synth: np.ndarray,
                        sr: int, window_ms: float = 50.0) -> float:
    """Ratio of attack energy (first window_ms) between synth and orig, in dB.

    Captures whether the hammer transient / onset is correctly reproduced.
    """
    n_atk = min(int(window_ms * 0.001 * sr), len(orig), len(synth))
    e_o = float(np.sqrt(np.mean(orig[:n_atk] ** 2) + 1e-30))
    e_s = float(np.sqrt(np.mean(synth[:n_atk] ** 2) + 1e-30))
    return 20 * math.log10(e_s / e_o)


def spectral_centroid_diff(orig: np.ndarray, synth: np.ndarray,
                           sr: int) -> float:
    """Difference in spectral centroid (Hz) between original and synthesis."""
    def centroid(x):
        n_fft = min(4096, len(x))
        X = np.abs(np.fft.rfft(x[:n_fft] * np.hanning(n_fft)))
        freqs = np.fft.rfftfreq(n_fft, 1.0/sr)
        total = X.sum()
        if total < 1e-12:
            return 0.0
        return float(np.sum(freqs * X) / total)

    # Use first 200ms for centroid (attack character)
    n_attack = min(int(0.2 * sr), len(orig), len(synth))
    return centroid(synth[:n_attack]) - centroid(orig[:n_attack])


# ── Rendering ─────────────────────────────────────────────────────────────────

def render_from_bank(bank: dict, midi: int, vel_idx: int,
                     sr: int, duration: float) -> np.ndarray:
    """Render a note from soundbank JSON using the same renderer as RMS calibration."""
    key = f"m{midi:03d}_vel{vel_idx}"
    notes = bank.get("notes", {})

    if key not in notes:
        # Fallback to nearest velocity
        for dv in range(8):
            for v in [vel_idx + dv, vel_idx - dv]:
                if 0 <= v <= 7 and f"m{midi:03d}_vel{v}" in notes:
                    key = f"m{midi:03d}_vel{v}"
                    break
            if key in notes:
                break

    if key not in notes:
        return np.zeros(int(duration * sr), dtype=np.float32)

    note = notes[key]
    partials = note.get("partials", [])
    if not partials:
        return np.zeros(int(duration * sr), dtype=np.float32)

    rms_gain = note.get("rms_gain", 1.0)
    eq_biquads = note.get("eq_biquads", [])

    audio = _render_note_rms_ref(
        partials=partials,
        phi_diff=note.get("phi_diff", 0.0),
        attack_tau=note.get("attack_tau", 0.05),
        A_noise=note.get("A_noise", 0.04),
        centroid_hz=note.get("noise_centroid_hz", 3000.0),
        eq_biquads=eq_biquads,
        midi=midi,
        sr=sr,
        duration=duration,
    )

    return audio * np.float32(rms_gain)


def load_wav_mono(path: str) -> tuple:
    """Load WAV, convert to mono float32, return (audio, sr)."""
    audio, sr = sf.read(path, dtype="float32")
    if audio.ndim == 2:
        audio = audio.mean(axis=1)
    return audio, sr


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Quality report: soundbank vs WAV")
    parser.add_argument("soundbank", help="Path to soundbank JSON")
    parser.add_argument("--bank", required=True, help="WAV bank directory")
    parser.add_argument("--vel", type=int, default=4, help="Velocity index (default: 4)")
    parser.add_argument("--duration", type=float, default=3.0,
                        help="Render duration in seconds (default: 3.0)")
    parser.add_argument("--sr-tag", default="f48", help="SR suffix (default: f48)")
    parser.add_argument("--scores", type=str, default=None,
                        help="Manual scores as midi:score,midi:score (e.g. '88:0.98,55:0.21')")
    parser.add_argument("--midi", type=str, default=None,
                        help="Specific MIDI notes (e.g. '21,36,60,88'). Default: auto-select 20 notes")
    args = parser.parse_args()

    bank = json.load(open(args.soundbank))
    meta = bank.get("metadata", {})
    # Detect SR from first WAV file (bank metadata may be wrong)
    bank_dir = Path(args.bank)
    sample_wav = next(bank_dir.glob(f"m*-vel{args.vel}-{args.sr_tag}.wav"), None)
    if sample_wav:
        _info = sf.info(str(sample_wav))
        sr = _info.samplerate
    else:
        sr = meta.get("sr", 48000)

    # Parse manual scores
    manual_scores = {}
    if args.scores:
        for pair in args.scores.split(","):
            m, s = pair.split(":")
            manual_scores[int(m)] = float(s)

    # Select test MIDI notes
    if args.midi:
        test_midis = [int(m) for m in args.midi.split(",")]
    else:
        # Default: spread across keyboard + any manually scored notes
        test_midis = sorted(set(
            [21, 25, 30, 32, 36, 41, 46, 50, 53, 57, 60, 63, 65,
             71, 74, 77, 84, 88, 92, 95, 98, 105]
            + list(manual_scores.keys())
        ))

    bank_dir = Path(args.bank)

    print(f"Soundbank: {args.soundbank}")
    print(f"WAV bank:  {args.bank}")
    print(f"SR: {sr}, vel: {args.vel}, duration: {args.duration}s")
    print(f"Notes: {len(test_midis)}")
    print()

    results = []

    # Header
    print(f"{'MIDI':>4} {'Score':>5} {'EnvC':>5} {'Brite':>6} {'AtkdB':>6}  "
          f"{'<250':>5} {' 500':>5} {'  1k':>5} {'  2k':>5} {'  4k':>5} {'  8k':>5}  Flags")
    print("-" * 95)

    for midi in test_midis:
        wav_path = bank_dir / f"m{midi:03d}-vel{args.vel}-{args.sr_tag}.wav"
        if not wav_path.exists():
            found = False
            for v in range(8):
                alt = bank_dir / f"m{midi:03d}-vel{v}-{args.sr_tag}.wav"
                if alt.exists():
                    wav_path = alt
                    found = True
                    break
            if not found:
                print(f"{midi:4d}  -- WAV not found --")
                continue

        orig, orig_sr = load_wav_mono(str(wav_path))
        sr = orig_sr

        n_render = int(args.duration * sr)
        orig = orig[:n_render]

        synth = render_from_bank(bank, midi, args.vel, sr, args.duration)
        if len(synth) == 0 or np.max(np.abs(synth)) < 1e-12:
            print(f"{midi:4d}  -- empty synthesis --")
            continue

        # Normalize both to same RMS for fair comparison
        orig_rms = np.sqrt(np.mean(orig**2) + 1e-30)
        synth_rms = np.sqrt(np.mean(synth**2) + 1e-30)
        if synth_rms > 1e-10:
            synth = synth * (orig_rms / synth_rms)

        env_corr = envelope_correlation(orig, synth, sr)
        bright = brightness_diff(orig, synth, sr)
        atk_db = attack_energy_ratio(orig, synth, sr)
        bands = band_energy_diff(orig, synth, sr)
        manual = manual_scores.get(midi, float("nan"))

        results.append({
            "midi": midi, "env_corr": env_corr, "brightness": bright,
            "attack_db": atk_db, "bands": bands, "manual_score": manual,
        })

        score_str = f"{manual:.2f}" if not math.isnan(manual) else "    -"
        # Band columns: show dB difference per octave band
        b = bands
        band_str = f"{b.get('0-250',0):+5.1f} {b.get('250-500',0):+5.1f} " \
                   f"{b.get('500-1000',0):+5.1f} {b.get('1000-2000',0):+5.1f} " \
                   f"{b.get('2000-4000',0):+5.1f} {b.get('4000-8000',0):+5.1f}"
        flags = ""
        if bright < -0.1: flags += " DULL"
        if bright > 0.1: flags += " BRIGHT"
        if env_corr < 0.7: flags += " BAD-ENV"
        if atk_db < -6: flags += " WEAK-ATK"
        if atk_db > 6: flags += " LOUD-ATK"
        # Flag bands with >6 dB deficit
        for band_name, val in b.items():
            if val < -6:
                flags += f" -{band_name}"
        print(f"{midi:4d} {score_str} {env_corr:5.2f} {bright:+6.3f} {atk_db:+6.1f}  {band_str}{flags}")

    # Summary and correlation
    if results:
        print(f"\n{'='*95}")

        scored = [r for r in results if not math.isnan(r["manual_score"])]
        if len(scored) >= 4:
            ms = np.array([r["manual_score"] for r in scored])
            ec = np.array([r["env_corr"] for r in scored])
            br = np.array([r["brightness"] for r in scored])
            at = np.array([r["attack_db"] for r in scored])

            # Per-band correlations
            band_names = ["0-250", "250-500", "500-1000", "1000-2000",
                          "2000-4000", "4000-8000"]
            band_corrs = {}
            for bn in band_names:
                vals = np.array([r["bands"].get(bn, 0) for r in scored])
                if vals.std() > 0.01:
                    band_corrs[bn] = float(np.corrcoef(ms, vals)[0, 1])

            print(f"\n  Correlation with manual scores ({len(scored)} notes):")
            corr_env = float(np.corrcoef(ms, ec)[0, 1]) if ec.std() > 0 else 0
            corr_bri = float(np.corrcoef(ms, br)[0, 1]) if br.std() > 0 else 0
            corr_atk = float(np.corrcoef(ms, at)[0, 1]) if at.std() > 0 else 0
            print(f"    EnvCorr:       r = {corr_env:+.3f}")
            print(f"    Brightness:    r = {corr_bri:+.3f}")
            print(f"    AttackEnergy:  r = {corr_atk:+.3f}")
            print(f"\n  Per-band energy diff correlation with score:")
            best_band = ("", 0)
            for bn, c in sorted(band_corrs.items()):
                marker = " <<<" if abs(c) > 0.4 else ""
                print(f"    {bn:>10} Hz:  r = {c:+.3f}{marker}")
                if abs(c) > abs(best_band[1]):
                    best_band = (bn, c)

            # Best composite metric
            if best_band[0]:
                best_vals = np.array([r["bands"].get(best_band[0], 0) for r in scored])
                # Combine best band + env_corr
                if ec.std() > 0 and best_vals.std() > 0:
                    ec_n = (ec - ec.mean()) / ec.std()
                    bv_n = (best_vals - best_vals.mean()) / best_vals.std()
                    composite = ec_n + bv_n
                    corr_comp = float(np.corrcoef(ms, composite)[0, 1])
                    print(f"\n  Best composite (EnvCorr + {best_band[0]}Hz band): r = {corr_comp:+.3f}")


if __name__ == "__main__":
    main()
