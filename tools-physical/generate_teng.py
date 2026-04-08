"""
tools-physical/generate_teng.py
--------------------------------
Dual-rail waveguide piano synthesizer (Teng 2012 / Smith 1992).

Renders WAV files from a physical-model soundbank JSON or from
explicit parameters. Each note is synthesized as N detuned strings
panned across a stereo field.

Usage:
    # Render from bank (all anchor notes)
    python tools-physical/generate_teng.py \\
        --bank soundbanks-physical/physical-piano-04081305.json

    # Render specific MIDI notes
    python tools-physical/generate_teng.py \\
        --bank soundbanks-physical/physical-piano-04081305.json \\
        --midi 60 64 72

    # Render with multiple velocities
    python tools-physical/generate_teng.py \\
        --bank soundbanks-physical/physical-piano-04081305.json \\
        --midi 60 --vel 0.3 0.6 0.9

    # Custom output directory and duration
    python tools-physical/generate_teng.py \\
        --bank soundbanks-physical/physical-piano-04081305.json \\
        --output-dir tmp_audio/teng_test --duration 3.0

Output filenames:  m060-v06-f262.wav  (MIDI-velocity-freq)
"""

import argparse
import json
import math
import os
import struct
import sys
import numpy as np
from datetime import datetime

SR = 48000


# ── DSP primitives ───────────────────────────────────────────────────────

def one_pole_lp(x, g, p, state):
    """One-pole low-pass: y = g*(1-p)*x + p*y_prev"""
    y = g * (1.0 - p) * x + p * state
    return y, y


def allpass_frac(x, a, state):
    """First-order allpass: H = (a + z^-1) / (1 + a*z^-1)"""
    y = a * x + state
    new_state = x - a * y
    return y, new_state


# ── Dual-rail waveguide core ─────────────────────────────────────────────

def _dual_rail_string(f0, hammer_force, exc_x0,
                      T60_fund, T60_nyq, gauge,
                      n_disp, a_disp, n_samples):
    """
    Single dual-rail waveguide string.

    Two parallel delay lines model right- and left-traveling waves.
    Hammer force is injected at physical position x0 on both rails,
    creating two wave fronts that arrive at bridge/nut at different
    times — naturally producing the comb-filter notches from hammer
    position (no Fourier series needed).

    Signal path per sample:

        output ← upper[bridge]
        upper[bridge] → loss → dispersion → tuning → negate → lower[bridge]
        lower[nut] → negate → upper[nut]
        shift upper →,  shift lower ←
        inject hammer force at position n0

    Returns: mono numpy array (n_samples,)
    """
    N_period = SR / f0

    # ── Loss filter (Välimäki one-pole) ──────────────────────────────
    T60_nyq_eff = T60_nyq / gauge
    g_dc  = np.clip(10.0 ** (-3.0 * N_period / (T60_fund * SR)), 0.5, 0.9999)
    g_nyq = np.clip(10.0 ** (-3.0 * N_period / (max(T60_nyq_eff, 0.001) * SR)),
                    0.01, g_dc)
    pole = np.clip((g_dc - g_nyq) / (g_dc + g_nyq), 0.0, 0.95)

    # ── Delay compensation ───────────────────────────────────────────
    # Each allpass in the loop adds frequency-dependent group delay.
    # Compensate at DC so fundamental stays in tune.
    #   loss filter:    τ = pole / (1 - pole²)
    #   dispersion AP:  τ = (1 - a) / (1 + a)  per stage
    filter_delay = pole / (1.0 - pole * pole) if abs(pole) > 0.001 else 0.0
    disp_delay   = n_disp * (1.0 - a_disp) / (1.0 + a_disp) if n_disp > 0 else 0.0

    # Each rail = half the compensated period
    N_comp = N_period - filter_delay - disp_delay
    M = max(4, int(N_comp / 2))
    frac = N_comp / 2 - M
    if frac < 0.1:
        M -= 1; frac += 1.0
    ap_a = (1.0 - frac) / (1.0 + frac)   # tuning allpass coefficient

    # ── Hammer position ──────────────────────────────────────────────
    n0 = max(1, min(M - 2, round(exc_x0 * M)))

    # ── Rails ────────────────────────────────────────────────────────
    upper = np.zeros(M)   # right-traveling (nut → bridge)
    lower = np.zeros(M)   # left-traveling  (bridge → nut)

    # Filter states
    lp_state = 0.0
    ap_state = 0.0
    disp_states = [0.0] * max(n_disp, 1)

    output = np.zeros(n_samples)
    n_hammer = len(hammer_force)

    for n in range(n_samples):
        # 1. Output: right-traveling wave arriving at bridge
        output[n] = upper[M - 1]

        # 2. Bridge reflection: loss → dispersion → tuning → negate
        x = upper[M - 1]
        x, lp_state = one_pole_lp(x, g_dc, pole, lp_state)
        for di in range(n_disp):
            x, disp_states[di] = allpass_frac(x, a_disp, disp_states[di])
        x, ap_state = allpass_frac(x, ap_a, ap_state)

        # 3. Nut reflection: rigid termination (-1)
        nut_ref = -lower[0]

        # 4. Propagate: shift upper → , shift lower ←
        upper[1:] = upper[:-1].copy()
        upper[0] = nut_ref

        lower[:-1] = lower[1:].copy()
        lower[M - 1] = -x    # bridge reflection (negated)

        # 5. Inject hammer force at physical position n0
        if n < n_hammer:
            f = hammer_force[n]
            upper[n0] += f
            lower[n0] += f

    return output


# ── Multi-string stereo renderer ─────────────────────────────────────────

def render_note(midi, velocity=0.6, duration_s=2.0,
                T60_fund=None, T60_nyq=None,
                exc_x0=1.0/7.0, B=0.0,
                n_strings=3, detune_cents=1.0,
                stereo_spread=0.3, gauge=1.0,
                n_disp_stages=None, **kwargs):
    """
    Render a single piano note using dual-rail waveguide synthesis.

    Runs n_strings parallel waveguide strings with slight detuning,
    panned across the stereo field. Each string is a physically
    separate dual-rail simulation.

    Args:
        midi:           MIDI note number (21-108)
        velocity:       Hammer velocity 0.0-1.0
        duration_s:     Note duration in seconds
        T60_fund:       Decay time of fundamental (seconds)
        T60_nyq:        Decay time at Nyquist (seconds, before gauge scaling)
        exc_x0:         Hammer striking position as fraction of string (1/7 typical)
        B:              Inharmonicity coefficient
        n_strings:      Number of strings per note (1-3)
        detune_cents:   Detuning between outer strings (cents)
        stereo_spread:  Stereo width 0.0 (mono) to 1.0 (hard L/R)
        gauge:          String thickness multiplier (affects HF damping)
        n_disp_stages:  Override for dispersion allpass stages (None=auto from B)

    Returns:
        (left, right): tuple of stereo numpy arrays
    """
    f0 = 440.0 * 2.0 ** ((midi - 69) / 12.0)
    t_keyboard = (midi - 21) / 87.0

    if T60_fund is None:
        T60_fund = 10.0 - t_keyboard * 9.0
    if T60_nyq is None:
        T60_nyq = 0.04 - t_keyboard * 0.02

    # ── Dispersion ───────────────────────────────────────────────────
    a_disp = -0.15
    if B > 0:
        N_period = SR / f0
        n_disp = max(0, min(int(B * N_period**2 * 0.5), 16))
    else:
        n_disp = 0
    if n_disp_stages is not None:
        n_disp = n_disp_stages
    if n_disp == 0:
        a_disp = 0.0

    # ── Hammer force: half-sine pulse ────────────────────────────────
    # Contact time: longer in bass, shorter in treble & forte
    contact_ms = max(1.5, 4.0 - 2.0 * t_keyboard - velocity * 0.5)
    n_contact = max(8, int(contact_ms * 0.001 * SR))
    t_h = np.arange(n_contact)
    hammer_force = velocity * 0.5 * np.sin(np.pi * t_h / n_contact)

    n_samples = int(SR * duration_s)
    output_L = np.zeros(n_samples)
    output_R = np.zeros(n_samples)

    for si in range(n_strings):
        # Detune outer strings by ±detune_cents
        if n_strings > 1:
            offset = (si - (n_strings - 1) / 2.0) * detune_cents
            f0_str = f0 * 2.0 ** (offset / 1200.0)
        else:
            f0_str = f0

        # Pan: spread strings across stereo field (cos/sin law)
        if n_strings > 1:
            spread_norm = (si - (n_strings - 1) / 2.0) / ((n_strings - 1) / 2.0)
            pan = 0.5 + stereo_spread * spread_norm * 0.5
        else:
            pan = 0.5
        gain_L = math.cos(pan * math.pi / 2)
        gain_R = math.sin(pan * math.pi / 2)

        mono = _dual_rail_string(
            f0_str, hammer_force, exc_x0,
            T60_fund, T60_nyq, gauge,
            n_disp, a_disp, n_samples)

        output_L += gain_L * mono
        output_R += gain_R * mono

    return output_L, output_R


# ── WAV I/O ──────────────────────────────────────────────────────────────

def write_wav_stereo(path, left, right, sr=48000):
    """Write stereo float arrays as 16-bit PCM WAV."""
    peak = max(np.max(np.abs(left)), np.max(np.abs(right)))
    if peak > 0:
        left  = left  / peak * 0.9
        right = right / peak * 0.9
    left_s  = np.clip(left  * 32767, -32767, 32767).astype(np.int16)
    right_s = np.clip(right * 32767, -32767, 32767).astype(np.int16)
    interleaved = np.empty(len(left_s) * 2, dtype=np.int16)
    interleaved[0::2] = left_s
    interleaved[1::2] = right_s
    with open(path, 'wb') as f:
        n_bytes = len(interleaved) * 2
        f.write(struct.pack('<4sI4s', b'RIFF', 36 + n_bytes, b'WAVE'))
        f.write(struct.pack('<4sIHHIIHH', b'fmt ', 16, 1, 2, sr, sr * 4, 4, 16))
        f.write(struct.pack('<4sI', b'data', n_bytes))
        f.write(interleaved.tobytes())


def write_wav_mono(path, data, sr=48000):
    """Write mono float array as 16-bit PCM WAV."""
    peak = np.max(np.abs(data))
    if peak > 0:
        data = data / peak * 0.9
    samples = np.clip(data * 32767, -32767, 32767).astype(np.int16)
    with open(path, 'wb') as f:
        n = len(samples)
        f.write(struct.pack('<4sI4s', b'RIFF', 36 + n * 2, b'WAVE'))
        f.write(struct.pack('<4sIHHIIHH', b'fmt ', 16, 1, 1, sr, sr * 2, 2, 16))
        f.write(struct.pack('<4sI', b'data', n * 2))
        f.write(samples.tobytes())


# ── Bank loader ──────────────────────────────────────────────────────────

def load_bank(path):
    """Load a physical model bank JSON and return notes dict."""
    with open(path) as f:
        bank = json.load(f)
    return bank.get("notes", {})


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Dual-rail waveguide piano renderer (Teng 2012)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # All anchor notes from bank
  python tools-physical/generate_teng.py \\
      --bank soundbanks-physical/physical-piano-04081305.json

  # Specific notes with multiple velocities
  python tools-physical/generate_teng.py \\
      --bank soundbanks-physical/physical-piano-04081305.json \\
      --midi 48 60 72 --vel 0.3 0.6 0.9

  # Custom output dir and duration
  python tools-physical/generate_teng.py \\
      --bank soundbanks-physical/physical-piano-04081305.json \\
      --output-dir tmp_audio/experiment1 --duration 3.0

  # Override dispersion stages for A/B testing
  python tools-physical/generate_teng.py \\
      --bank soundbanks-physical/physical-piano-04081305.json \\
      --midi 64 --disp-stages 0 6 12 23
""")
    parser.add_argument("--bank", required=True,
                        help="Path to soundbank JSON")
    parser.add_argument("--midi", type=int, nargs="*", default=None,
                        help="MIDI notes to render (default: 36 48 60 72 84)")
    parser.add_argument("--vel", type=float, nargs="*", default=[0.6],
                        help="Velocities 0.0-1.0 (default: 0.6)")
    parser.add_argument("--duration", type=float, default=2.5,
                        help="Note duration in seconds (default: 2.5)")
    parser.add_argument("--output-dir", default="tmp_audio/teng",
                        help="Output directory (default: tmp_audio/teng)")
    parser.add_argument("--stereo-spread", type=float, default=0.3,
                        help="Stereo width 0.0-1.0 (default: 0.3)")
    parser.add_argument("--disp-stages", type=int, nargs="*", default=None,
                        help="Override dispersion stages (renders one file per value)")
    parser.add_argument("--mono", action="store_true",
                        help="Output mono WAV (1 string, no detuning)")
    args = parser.parse_args()

    # Load bank
    notes = load_bank(args.bank)
    bank_name = os.path.splitext(os.path.basename(args.bank))[0]

    # Determine MIDI notes
    if args.midi:
        midis = args.midi
    else:
        midis = [36, 48, 60, 72, 84]

    # Output dir
    os.makedirs(args.output_dir, exist_ok=True)

    print(f"Bank: {bank_name}")
    print(f"Notes: {midis}")
    print(f"Velocities: {args.vel}")
    print(f"Duration: {args.duration}s")
    print(f"Output: {os.path.abspath(args.output_dir)}")
    print()

    for midi in midis:
        key = f"m{midi:03d}"
        if key not in notes:
            print(f"  MIDI {midi}: not in bank, skipping")
            continue

        p = notes[key]
        f0 = p["f0_hz"]

        for vel in args.vel:
            vel_int = int(vel * 10)  # 0.6 → 6

            # Determine dispersion variants
            if args.disp_stages is not None:
                disp_variants = args.disp_stages
            else:
                disp_variants = [None]  # use bank default

            for disp_override in disp_variants:
                # Build render params from bank
                render_params = dict(
                    midi=midi,
                    velocity=vel,
                    duration_s=args.duration,
                    T60_fund=p["T60_fund"],
                    T60_nyq=p["T60_nyq"],
                    exc_x0=p.get("exc_x0", 1.0/7.0),
                    B=p.get("B", 0),
                    gauge=p.get("gauge", 1.0),
                    stereo_spread=args.stereo_spread,
                )

                if args.mono:
                    render_params["n_strings"] = 1
                    render_params["detune_cents"] = 0
                else:
                    render_params["n_strings"] = p.get("n_strings", 3)
                    render_params["detune_cents"] = p.get("detune_cents", 1.0)

                if disp_override is not None:
                    render_params["n_disp_stages"] = disp_override
                else:
                    render_params["n_disp_stages"] = p.get("n_disp_stages", None)

                # Render
                n_str = render_params.get("n_strings", 3)
                n_disp_actual = render_params["n_disp_stages"]
                disp_label = f"" if disp_override is None else f"-d{disp_override:02d}"

                print(f"  MIDI {midi:3d} ({f0:7.1f} Hz) vel={vel:.1f} "
                      f"strings={n_str} disp={n_disp_actual}{disp_label}")

                L, R = render_note(**render_params)

                # Filename: m060-v06-f262.wav  or  m060-v06-f262-d12.wav
                sr_tag = f"f{SR // 1000}"  # f48 or f44
                fname = f"m{midi:03d}-v{vel_int:02d}-{sr_tag}{disp_label}.wav"
                fpath = os.path.join(args.output_dir, fname)

                if args.mono:
                    write_wav_mono(fpath, L)
                else:
                    write_wav_stereo(fpath, L, R)

                print(f"    -> {fpath}")

    print(f"\nDone. Files in: {os.path.abspath(args.output_dir)}")


if __name__ == "__main__":
    main()
