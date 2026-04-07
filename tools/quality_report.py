#!/usr/bin/env python3
"""
tools/quality_report.py
───────────────────────
Compare synthesized output from a soundbank JSON against original WAV recordings.
Compute per-note metrics and correlate with manual listening scores.

Usage:
    python tools/quality_report.py soundbanks/pl-grand-04071523.json \
        --bank C:/SoundBanks/IthacaPlayer/pl-grand

    python tools/quality_report.py soundbanks/pl-grand-04071523.json \
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
    print(f"{'MIDI':>4} {'LSD':>6} {'EnvCorr':>7} {'CentDif':>8} {'Score':>6}  Notes")
    print(f"{'----':>4} {'------':>6} {'-------':>7} {'--------':>8} {'------':>6}  -----")

    for midi in test_midis:
        wav_path = bank_dir / f"m{midi:03d}-vel{args.vel}-{args.sr_tag}.wav"
        if not wav_path.exists():
            # Try other velocities
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
        sr = orig_sr  # render at WAV sample rate

        # Trim original to match render duration
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

        lsd = log_spectral_distance(orig, synth, sr)
        env_corr = envelope_correlation(orig, synth, sr)
        cent_diff = spectral_centroid_diff(orig, synth, sr)
        manual = manual_scores.get(midi, float("nan"))

        results.append({
            "midi": midi, "lsd": lsd, "env_corr": env_corr,
            "cent_diff": cent_diff, "manual_score": manual,
        })

        score_str = f"{manual:.2f}" if not math.isnan(manual) else "     -"
        notes = ""
        if lsd > 15: notes += " HIGH-LSD"
        if env_corr < 0.7: notes += " LOW-ENV"
        if abs(cent_diff) > 500: notes += " CENTROID"
        print(f"{midi:4d} {lsd:6.1f} {env_corr:7.3f} {cent_diff:+8.0f} {score_str}{notes}")

    # Summary
    if results:
        lsds = [r["lsd"] for r in results]
        envs = [r["env_corr"] for r in results]
        print(f"\n{'='*60}")
        print(f"  LSD:      {np.mean(lsds):5.1f} avg  [{min(lsds):.1f} - {max(lsds):.1f}]")
        print(f"  EnvCorr:  {np.mean(envs):5.3f} avg  [{min(envs):.3f} - {max(envs):.3f}]")

        # Correlation with manual scores
        scored = [(r["manual_score"], r["lsd"], r["env_corr"])
                  for r in results if not math.isnan(r["manual_score"])]
        if len(scored) >= 4:
            ms = np.array([s[0] for s in scored])
            ls = np.array([s[1] for s in scored])
            ec = np.array([s[2] for s in scored])

            corr_lsd = float(np.corrcoef(ms, -ls)[0, 1])  # negative: lower LSD = better
            corr_env = float(np.corrcoef(ms, ec)[0, 1])

            print(f"\n  Correlation with manual scores ({len(scored)} notes):")
            print(f"    LSD vs score:     r = {corr_lsd:+.3f}  ({'good' if abs(corr_lsd) > 0.5 else 'weak'})")
            print(f"    EnvCorr vs score: r = {corr_env:+.3f}  ({'good' if abs(corr_env) > 0.5 else 'weak'})")

            # Combined metric attempt
            if abs(corr_lsd) > 0.3 or abs(corr_env) > 0.3:
                # Normalize and combine
                ls_norm = (ls - ls.mean()) / (ls.std() + 1e-10)
                ec_norm = (ec - ec.mean()) / (ec.std() + 1e-10)
                combined = -ls_norm + ec_norm  # lower LSD + higher env = better
                corr_comb = float(np.corrcoef(ms, combined)[0, 1])
                print(f"    Combined:         r = {corr_comb:+.3f}  ({'good' if abs(corr_comb) > 0.5 else 'weak'})")


if __name__ == "__main__":
    main()
