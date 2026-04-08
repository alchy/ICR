"""
tools-physical/generate_teng_v2.py
-----------------------------------
Dual-rail waveguide piano with Chaigne-Askenfelt hammer model.

v2 replaces the simplified half-sine hammer pulse (v1) with a physics-based
finite-difference hammer model. The hammer is a nonlinear mass-spring system
(F = K|delta|^p) that interacts with a simplified FD string. Reflected waves
come back and re-compress the hammer felt, creating multi-pulse force signals
with velocity-dependent spectral content.

Usage:
    python tools-physical/generate_teng_v2.py \\
        --bank soundbanks-physical/physical-piano-04081305.json

    python tools-physical/generate_teng_v2.py \\
        --bank soundbanks-physical/physical-piano-04081305.json \\
        --midi 36 48 60 72 84 --vel 0.3 0.6 0.9

Output:  tmp_audio/teng_v2/m060-v06-f262.wav
"""

import argparse
import json
import math
import os
import struct
import numpy as np

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


def _lerp(a, b, t):
    return a + (b - a) * t


def _log_lerp(a, b, t):
    """Interpolate in log space (for K which spans 1e8 to 1e11)."""
    return 10.0 ** _lerp(math.log10(a), math.log10(b), t)


# ── Chaigne-Askenfelt hammer model ───────────────────────────────────────
#
# Physical parameters from Chaigne & Askenfelt (1994), Table 4.1 in Teng.
# Three anchor notes: C2 (MIDI 36), C4 (MIDI 60), C7 (MIDI 96).
#
#               C2          C4          C7
#   Ms (g)      35.0        3.93        0.467
#   L  (m)      1.90        0.62        0.09
#   Mh (g)      4.9         2.97        2.2
#   T  (N)      750         670         750
#   p           2.3         2.5         3.0
#   K           1e8         4.5e9       1e11

_ANCHORS = {
    #        Ms_g,  L_m,   Mh_g,  T_N,  p_exp, K_stiff
    36:     (35.0,  1.90,  4.9,   750,  2.3,   1e8),
    60:     (3.93,  0.62,  2.97,  670,  2.5,   4.5e9),
    96:     (0.467, 0.09,  2.2,   750,  3.0,   1e11),
}


def _interp_anchor(midi, idx):
    """Interpolate hammer parameter from 3 anchor points."""
    if midi <= 36:
        return _ANCHORS[36][idx]
    elif midi <= 60:
        t = (midi - 36) / 24.0
        if idx == 5:  # K: log interpolation
            return _log_lerp(_ANCHORS[36][idx], _ANCHORS[60][idx], t)
        return _lerp(_ANCHORS[36][idx], _ANCHORS[60][idx], t)
    elif midi <= 96:
        t = (midi - 60) / 36.0
        if idx == 5:
            return _log_lerp(_ANCHORS[60][idx], _ANCHORS[96][idx], t)
        return _lerp(_ANCHORS[60][idx], _ANCHORS[96][idx], t)
    else:
        return _ANCHORS[96][idx]


def chaigne_hammer(midi, v0, exc_x0=0.12):
    """
    Compute hammer force using Chaigne-Askenfelt (1994) FD model.

    Simulates the nonlinear interaction between hammer felt and string:
      F = K × |delta|^p     where delta = y_hammer - y_string(x0)

    The string is modeled with a simplified finite-difference scheme
    (stiff string wave equation with damping). Reflected waves interact
    with the hammer, creating a multi-pulse force signal.

    Args:
        midi:   MIDI note number (for parameter interpolation)
        v0:     Initial hammer velocity in m/s (pp≈1, mf≈3, ff≈5)
        exc_x0: Striking position as fraction of string length

    Returns:
        v_in: numpy array — hammer velocity input (F / 2Z) for waveguide
        F_raw: numpy array — raw force signal in Newtons (for analysis)
    """
    # Interpolate physical parameters
    Ms = _interp_anchor(midi, 0) / 1000.0   # g → kg
    L  = _interp_anchor(midi, 1)             # m
    Mh = _interp_anchor(midi, 2) / 1000.0   # g → kg
    T  = _interp_anchor(midi, 3)             # N
    p  = _interp_anchor(midi, 4)             # exponent
    K  = _interp_anchor(midi, 5)             # stiffness

    # Damping and stiffness (Chaigne & Askenfelt values)
    b1 = 0.5
    b3 = 6.25e-9
    epsilon = 3.82e-5

    # Wave impedance and speed
    rho_L = Ms / L                           # linear density (kg/m)
    R0 = math.sqrt(T * rho_L)               # wave impedance
    c  = math.sqrt(T / rho_L)               # wave speed (m/s)

    # Spatial grid: N chosen to satisfy stiff-string Courant stability
    # For stiff string: r ≤ 1/sqrt(1 + 4*epsilon*N²), where r = c*N/(SR*L)
    # Find largest stable N with safety margin
    N = 15
    for N_try in range(120, 14, -1):
        r_try = c * N_try / (SR * L)
        r_max = 1.0 / math.sqrt(1.0 + 4.0 * epsilon * N_try**2)
        if r_try <= 0.9 * r_max:
            N = N_try
            break

    dt = 1.0 / SR
    i0 = max(2, min(N - 3, round(exc_x0 * N)))

    # FD coefficients (Teng Appendix II, from stiff string wave equation)
    D  = 1.0 + b1 / SR + 2.0 * b3 * SR
    r  = c * N / (SR * L)
    a1 = (2.0 - 2.0*r**2 + b3*SR - 6.0*epsilon*N**2*r**2) / D
    a2 = (-1.0 + b1/SR + 2.0*b3*SR) / D
    a3 = (r**2 * (1.0 + 4.0*epsilon*N**2)) / D
    a4 = (b3*SR - epsilon*N**2*r**2) / D
    a5 = (-b3*SR) / D

    # Allocate (max 10ms contact)
    max_steps = int(0.010 * SR)   # 480 @ 48kHz
    y  = np.zeros((N, max_steps))  # string displacement
    yh = np.zeros(max_steps)       # hammer displacement
    F  = np.zeros(max_steps)       # force signal

    dt2 = dt * dt

    # ── Initial conditions ───────────────────────────────────────────
    # t=0: everything at rest
    # t=1: hammer moves with initial velocity
    yh[1] = v0 * dt

    # Simplified string update (Taylor)
    y[1:N-1, 1] = (y[2:N, 0] + y[0:N-2, 0]) / 2.0
    delta = yh[1] - y[i0, 1]
    if delta > 0:
        F[1] = K * abs(delta)**p

    # t=2
    y[1:N-1, 2] = y[2:N, 1] + y[0:N-2, 1] - y[1:N-1, 0]
    y[i0, 2] += dt2 * N * F[1] / Ms
    yh[2] = 2.0*yh[1] - yh[0] - dt2 * F[1] / Mh
    delta = yh[2] - y[i0, 2]
    if delta > 0:
        F[2] = K * abs(delta)**p

    # ── Main FD loop ─────────────────────────────────────────────────
    actual_len = max_steps
    no_contact = 0

    for n in range(3, max_steps):
        # Boundary: fixed ends
        # y[0, n] = 0  (already zero)
        # y[N-1, n] = 0

        # Edge point idx=1 (4th-order stiffness needs special boundary)
        y[1, n] = (a1*y[1, n-1] + a2*y[1, n-2] +
                   a3*(y[2, n-1] + y[0, n-1]) +
                   a4*(y[3, n-1] - y[1, n-1]) +
                   a5*(y[2, n-2] + y[0, n-2] + y[1, n-3]))

        # Edge point idx=N-2
        y[N-2, n] = (a1*y[N-2, n-1] + a2*y[N-2, n-2] +
                     a3*(y[N-1, n-1] + y[N-3, n-1]) +
                     a4*(y[N-4, n-1] - y[N-2, n-1]) +
                     a5*(y[N-1, n-2] + y[N-3, n-2] + y[N-2, n-3]))

        # Interior points (vectorized)
        if N > 5:
            y[2:N-2, n] = (a1*y[2:N-2, n-1] + a2*y[2:N-2, n-2] +
                           a3*(y[3:N-1, n-1] + y[1:N-3, n-1]) +
                           a4*(y[4:N, n-1] + y[0:N-4, n-1]) +
                           a5*(y[3:N-1, n-2] + y[1:N-3, n-2] +
                               y[2:N-2, n-3]))

        # Striking point (includes hammer force injection)
        y[i0, n] = (a1*y[i0, n-1] + a2*y[i0, n-2] +
                    a3*(y[i0+1, n-1] + y[i0-1, n-1]) +
                    a4*(y[i0+2, n-1] + y[i0-2, n-1]) +
                    a5*(y[i0+1, n-2] + y[i0-1, n-2] + y[i0, n-3]) +
                    dt2 * N * F[n-1] / Ms)

        # Hammer displacement
        yh[n] = 2.0*yh[n-1] - yh[n-2] - dt2 * F[n-1] / Mh

        # Nonlinear felt compression
        delta = yh[n] - y[i0, n]
        if delta > 0:
            F[n] = K * abs(delta)**p
            no_contact = 0
        else:
            F[n] = 0.0
            no_contact += 1

        # Stop when hammer has clearly separated
        if no_contact > 100:
            actual_len = n + 1
            break

    # Convert force → velocity input for waveguide
    F_out = F[:actual_len]
    v_in = F_out / (2.0 * R0)

    return v_in, F_out


# ── Dual-rail waveguide core ─────────────────────────────────────────────

def _dual_rail_string(f0, hammer_v_in, exc_x0,
                      T60_fund, T60_nyq, gauge,
                      n_disp, a_disp, n_samples):
    """
    Single dual-rail waveguide string.

    Two parallel delay lines model right- and left-traveling waves.
    Hammer velocity input is injected at physical position x0 on both
    rails, creating two wave fronts.

    Returns: mono numpy array (n_samples,)
    """
    N_period = SR / f0

    # Loss filter (Välimäki one-pole)
    T60_nyq_eff = T60_nyq / gauge
    g_dc  = np.clip(10.0 ** (-3.0 * N_period / (T60_fund * SR)), 0.5, 0.9999)
    g_nyq = np.clip(10.0 ** (-3.0 * N_period / (max(T60_nyq_eff, 0.001) * SR)),
                    0.01, g_dc)
    pole = np.clip((g_dc - g_nyq) / (g_dc + g_nyq), 0.0, 0.95)

    # Delay compensation
    filter_delay = pole / (1.0 - pole * pole) if abs(pole) > 0.001 else 0.0
    disp_delay   = n_disp * (1.0 - a_disp) / (1.0 + a_disp) if n_disp > 0 else 0.0

    N_comp = N_period - filter_delay - disp_delay
    M = max(4, int(N_comp / 2))
    frac = N_comp / 2 - M
    if frac < 0.1:
        M -= 1; frac += 1.0
    ap_a = (1.0 - frac) / (1.0 + frac)

    n0 = max(1, min(M - 2, round(exc_x0 * M)))

    upper = np.zeros(M)
    lower = np.zeros(M)

    lp_state = 0.0
    ap_state = 0.0
    disp_states = [0.0] * max(n_disp, 1)

    output = np.zeros(n_samples)
    n_hammer = len(hammer_v_in)

    for n in range(n_samples):
        output[n] = upper[M - 1]

        x = upper[M - 1]
        x, lp_state = one_pole_lp(x, g_dc, pole, lp_state)
        for di in range(n_disp):
            x, disp_states[di] = allpass_frac(x, a_disp, disp_states[di])
        x, ap_state = allpass_frac(x, ap_a, ap_state)

        nut_ref = -lower[0]

        upper[1:] = upper[:-1].copy()
        upper[0] = nut_ref

        lower[:-1] = lower[1:].copy()
        lower[M - 1] = -x

        if n < n_hammer:
            f = hammer_v_in[n]
            upper[n0] += f
            lower[n0] += f

    return output


# ── Multi-string stereo renderer ─────────────────────────────────────────

def _default_note_params(midi):
    """
    Physics-based defaults for dual-rail waveguide — no bank needed.

    Interpolated from physical string properties across the keyboard.
    Tuned for the Chaigne hammer + dual-rail topology (NOT the single-rail
    Fourier excitation which needs different T60/gauge values).
    """
    f0 = 440.0 * 2.0 ** ((midi - 69) / 12.0)
    t = (midi - 21) / 87.0   # 0..1 across keyboard

    # Inharmonicity B: wound bass high, treble low
    if midi <= 48:
        B = _lerp(2e-3, 1e-3, (midi - 21) / 27.0)
    elif midi <= 72:
        B = _lerp(1e-3, 4e-4, (midi - 48) / 24.0)
    else:
        B = _lerp(4e-4, 5e-5, (midi - 72) / 36.0)

    # Gauge: thicker bass strings → more HF damping
    if midi <= 48:
        gauge = _lerp(3.0, 2.0, (midi - 21) / 27.0)
    elif midi <= 72:
        gauge = _lerp(2.0, 1.2, (midi - 48) / 24.0)
    else:
        gauge = _lerp(1.2, 0.8, (midi - 72) / 36.0)

    # Decay times
    T60_fund = _lerp(12.0, 1.5, t)
    T60_nyq  = _lerp(0.30, 0.15, t)

    # Multi-string
    if midi <= 27:
        n_strings = 1
    elif midi <= 48:
        n_strings = 2
    else:
        n_strings = 3
    detune_cents = _lerp(2.5, 0.3, t)

    # Dispersion stages (from B)
    N_total = SR / f0
    n_raw = int(B * N_total**2 * 0.5)
    n_disp_stages = 0 if n_raw < 3 else min(n_raw, 16)

    return dict(
        f0_hz=f0, B=B, gauge=gauge,
        T60_fund=T60_fund, T60_nyq=T60_nyq,
        exc_x0=1.0/7.0,
        n_strings=n_strings, detune_cents=detune_cents,
        n_disp_stages=n_disp_stages,
    )


def render_note(midi, velocity=0.6, duration_s=2.0,
                T60_fund=None, T60_nyq=None,
                exc_x0=1.0/7.0, B=None,
                n_strings=None, detune_cents=None,
                stereo_spread=0.3, gauge=None,
                n_disp_stages=None, **kwargs):
    """
    Render a piano note with Chaigne-Askenfelt hammer + dual-rail waveguide.

    All string parameters default to physics-based interpolation when not
    provided (no bank file needed).

    Returns: (left, right) stereo numpy arrays
    """
    f0 = 440.0 * 2.0 ** ((midi - 69) / 12.0)
    t_keyboard = (midi - 21) / 87.0

    # Fill missing params from physics-based defaults
    defaults = _default_note_params(midi)
    if T60_fund is None:
        T60_fund = defaults["T60_fund"]
    if T60_nyq is None:
        T60_nyq = defaults["T60_nyq"]
    if B is None:
        B = defaults["B"]
    if gauge is None:
        gauge = defaults["gauge"]
    if n_strings is None:
        n_strings = defaults["n_strings"]
    if detune_cents is None:
        detune_cents = defaults["detune_cents"]
    if n_disp_stages is None:
        n_disp_stages = defaults["n_disp_stages"]

    # Dispersion
    a_disp = -0.15
    n_disp = n_disp_stages
    if n_disp == 0:
        a_disp = 0.0

    # Hammer: velocity 0..1 → v0 in m/s (pp≈1, mf≈3, ff≈5)
    v0 = max(0.5, velocity * 6.0)
    hammer_v_in, F_raw = chaigne_hammer(midi, v0, exc_x0)

    peak_force = np.max(F_raw)
    contact_ms = len(F_raw) / SR * 1000
    print(f"  MIDI {midi} ({f0:.1f} Hz): Chaigne hammer v0={v0:.1f} m/s")
    print(f"  force: {peak_force:.0f}N peak, {contact_ms:.1f}ms contact, "
          f"{len(F_raw)} samples")
    print(f"  {n_strings} strings, detune={detune_cents:.1f}c, "
          f"B={B:.1e}, disp={n_disp}, gauge={gauge:.1f}")

    n_samples = int(SR * duration_s)
    output_L = np.zeros(n_samples)
    output_R = np.zeros(n_samples)

    for si in range(n_strings):
        if n_strings > 1:
            offset = (si - (n_strings - 1) / 2.0) * detune_cents
            f0_str = f0 * 2.0 ** (offset / 1200.0)
        else:
            f0_str = f0

        if n_strings > 1:
            spread_norm = (si - (n_strings - 1) / 2.0) / ((n_strings - 1) / 2.0)
            pan = 0.5 + stereo_spread * spread_norm * 0.5
        else:
            pan = 0.5
        gain_L = math.cos(pan * math.pi / 2)
        gain_R = math.sin(pan * math.pi / 2)

        mono = _dual_rail_string(
            f0_str, hammer_v_in, exc_x0,
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
    with open(path) as f:
        bank = json.load(f)
    return bank.get("notes", {})


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Dual-rail waveguide piano with Chaigne-Askenfelt hammer (v2)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python tools-physical/generate_teng_v2.py \\
      --bank soundbanks-physical/physical-piano-04081305.json

  python tools-physical/generate_teng_v2.py \\
      --bank soundbanks-physical/physical-piano-04081305.json \\
      --midi 48 60 72 --vel 0.3 0.6 0.9
""")
    parser.add_argument("--bank", default=None,
                        help="Soundbank JSON (optional — uses physics defaults if omitted)")
    parser.add_argument("--midi", type=int, nargs="*", default=None)
    parser.add_argument("--vel", type=float, nargs="*", default=[0.6])
    parser.add_argument("--duration", type=float, default=2.5)
    parser.add_argument("--output-dir", default="tmp_audio/teng_v2")
    parser.add_argument("--stereo-spread", type=float, default=0.3)
    parser.add_argument("--disp-stages", type=int, nargs="*", default=None)
    parser.add_argument("--mono", action="store_true")
    args = parser.parse_args()

    notes = load_bank(args.bank) if args.bank else {}
    if args.midi:
        midis = args.midi
    else:
        midis = [36, 48, 60, 72, 84]

    os.makedirs(args.output_dir, exist_ok=True)

    source = os.path.basename(args.bank) if args.bank else "physics defaults"
    print(f"Params: {source}")
    print(f"Notes: {midis}  Velocities: {args.vel}  Duration: {args.duration}s")
    print(f"Output: {os.path.abspath(args.output_dir)}")
    print()

    for midi in midis:
        key = f"m{midi:03d}"
        p = notes.get(key, {})
        f0 = 440.0 * 2.0 ** ((midi - 69) / 12.0)

        for vel in args.vel:
            vel_int = int(vel * 10)

            disp_variants = args.disp_stages if args.disp_stages else [None]
            for disp_ov in disp_variants:
                # Build params: use bank values if present, else None → defaults
                render_params = dict(
                    midi=midi, velocity=vel, duration_s=args.duration,
                    T60_fund=p.get("T60_fund"),
                    T60_nyq=p.get("T60_nyq"),
                    exc_x0=p.get("exc_x0", 1.0/7.0),
                    B=p.get("B"),
                    gauge=p.get("gauge"),
                    stereo_spread=args.stereo_spread,
                )
                if args.mono:
                    render_params["n_strings"] = 1
                    render_params["detune_cents"] = 0
                else:
                    render_params["n_strings"] = p.get("n_strings")
                    render_params["detune_cents"] = p.get("detune_cents")

                if disp_ov is not None:
                    render_params["n_disp_stages"] = disp_ov
                else:
                    render_params["n_disp_stages"] = p.get("n_disp_stages")

                disp_label = "" if disp_ov is None else f"-d{disp_ov:02d}"
                L, R = render_note(**render_params)

                sr_tag = f"f{SR // 1000}"  # f48 or f44
                fname = f"m{midi:03d}-v{vel_int:02d}-{sr_tag}{disp_label}.wav"
                fpath = os.path.join(args.output_dir, fname)

                if args.mono:
                    write_wav_mono(fpath, L)
                else:
                    write_wav_stereo(fpath, L, R)
                print(f"    -> {fpath}\n")

    print(f"Done. Files in: {os.path.abspath(args.output_dir)}")


if __name__ == "__main__":
    main()
