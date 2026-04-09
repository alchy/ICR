"""
tests/test_string.py
---------------------
Minimal Karplus-Strong string test — NO piano, NO hammer, NO soundboard.
Just a delay line + one-pole loss filter + fractional delay allpass.

If this doesn't sound like a plucked string, nothing else will help.

Usage:
    python tests/test_string.py
    # Writes test_string_midi60.wav
"""

import numpy as np
import struct
import sys

SR = 48000


def load_bank(path):
    """Load a physical model bank JSON and return notes dict."""
    import json
    with open(path) as f:
        bank = json.load(f)
    return bank.get("notes", {})


def render_from_bank(bank_path, midis=None, duration_s=2.0, velocity=0.6):
    """Render notes from a physical bank JSON file."""
    import os
    notes = load_bank(bank_path)
    if midis is None:
        midis = [36, 48, 60, 72, 84]

    os.makedirs("tmp_audio", exist_ok=True)
    for f in os.listdir("tmp_audio"):
        if f.endswith(".wav"):
            os.remove(os.path.join("tmp_audio", f))

    bank_name = os.path.splitext(os.path.basename(bank_path))[0]
    print(f"\nRendering from bank: {bank_name}")

    for midi in midis:
        key = f"m{midi:03d}"
        if key not in notes:
            print(f"  MIDI {midi}: not in bank, skipping")
            continue

        p = notes[key]
        print(f"\n  MIDI {midi} ({p['f0_hz']:.1f} Hz):")
        audio = make_string_v2(
            midi, velocity_01=velocity, duration_s=duration_s,
            T60_fund=p.get("T60_fund"),
            T60_nyq=p.get("T60_nyq"),
            exc_rolloff=p.get("exc_rolloff", 0.1),
            exc_x0=p.get("exc_x0", 1.0/7.0),
            n_harmonics=p.get("n_harmonics", 80),
            B=p.get("B", 0),
            odd_boost=p.get("odd_boost", 1.8),
            knee_k=p.get("knee_k", 10),
            knee_slope=p.get("knee_slope", 3.0),
            gauge=p.get("gauge", 1.0),
        )
        fname = f"tmp_audio/{bank_name}_m{midi:03d}.wav"
        write_wav(fname, audio)
        print(f"  -> {fname}")

    print(f"\nFiles: C:\\Users\\jindr\\PycharmProjects\\ICR\\tmp_audio\\")


def one_pole_lp(x, g, p, state):
    """One-pole low-pass: y = g*(1-p)*x + p*y_prev"""
    y = g * (1.0 - p) * x + p * state
    return y, y  # output, new_state


def allpass_frac(x, a, state):
    """First-order allpass fractional delay: H = (a + z^-1)/(1 + a*z^-1)"""
    y = a * x + state
    new_state = x - a * y
    return y, new_state


def make_string(midi, velocity_01=0.5, duration_s=4.0):
    """
    Minimal plucked string.

    Topology (Smith 1992):
        excitation --> [Delay N] --> [Frac Allpass] --> [Loss LPF] --+--> output
                        ^                                            |
                        +--------------------------------------------+

    No sign inversion (both terminations rigid, net = +1 per trip).
    """
    f0 = 440.0 * 2.0 ** ((midi - 69) / 12.0)
    t_keyboard = (midi - 21) / 87.0  # 0..1

    # --- Loss filter design (Smith/Bank T60 method) ---
    # T60_fund: how long fundamental rings (seconds to drop 60 dB)
    # T60_nyq: how long Nyquist component rings
    # T60_nyq controls how fast high harmonics die → how quickly the waveform
    # becomes sinusoidal.  Must be short enough to smooth the triangle within
    # ~50ms, but long enough to retain attack brightness.
    T60_fund = 10.0 - t_keyboard * 9.0    # bass 10s, treble 1s
    T60_nyq  = 0.04 - t_keyboard * 0.02   # bass 40ms, treble 20ms

    N_total = SR / f0
    g_dc  = 10.0 ** (-3.0 * N_total / (T60_fund * SR))
    g_nyq = 10.0 ** (-3.0 * N_total / (max(T60_nyq, 0.001) * SR))
    g_dc  = np.clip(g_dc, 0.5, 0.9999)
    g_nyq = np.clip(g_nyq, 0.01, g_dc)

    p = (g_dc - g_nyq) / (g_dc + g_nyq)
    p = np.clip(p, 0.0, 0.95)

    print(f"  MIDI {midi}: f0={f0:.1f} Hz, N={N_total:.1f}, g_dc={g_dc:.5f}, g_nyq={g_nyq:.5f}, pole={p:.4f}")

    # --- Delay line = full period N ---
    # No sign inversion — excitation must be bipolar (displacement shape
    # of a plucked string: triangle going up then back to zero).
    filter_delay = p / (1.0 - p * p) if abs(p) > 0.001 else 0.0
    full_N = N_total - filter_delay
    N_int = int(full_N)
    frac = full_N - N_int
    if frac < 0.1:
        N_int -= 1
        frac += 1.0
    ap_a = (1.0 - frac) / (1.0 + frac)
    N_int = max(N_int, 4)

    print(f"  Delay: N_int={N_int} (half-period), frac={frac:.4f}, ap_a={ap_a:.4f}")

    # --- Excitation: Fourier series of plucked string ---
    # Instead of a raw triangle (which has sharp edges → saw artifacts),
    # we compute the exact Fourier series of a plucked-at-x0 string.
    # This gives smooth sinusoidal waveform from sample 0.
    #
    #   y(x) = sum_k  (2/(k*pi))^2 * sin(k*pi*x0/L) * sin(k*pi*x/L)
    #
    # x0/L = pluck position ratio (1/7 ≈ 0.143)
    # Amplitudes ~ sin(k*pi*x0/L) / k^2  (triangle Fourier coefficients)
    delay = np.zeros(N_int)
    x0_ratio = 1.0 / 7.0  # pluck at 1/7 of string
    n_harmonics = min(40, N_int // 2)  # up to Nyquist
    amp = velocity_01 * 0.5
    for k in range(1, n_harmonics + 1):
        # Amplitude from pluck position (modal excitation)
        ak = np.sin(k * np.pi * x0_ratio) / (k * k)
        # Phase: all cosine (displacement at t=0)
        for i in range(N_int):
            delay[i] += ak * np.sin(2 * np.pi * k * i / N_int)

    # Normalize and scale
    peak = np.max(np.abs(delay))
    if peak > 0:
        delay *= amp / peak

    print(f"  Excitation: Fourier series, {n_harmonics} harmonics, x0={x0_ratio:.3f}, amp={amp:.3f}")

    # --- Dispersion: cascade of mild allpass filters ---
    # Single strong allpass (coeff > 0.3) creates buzz.
    # Instead: multiple weak allpass in cascade (Van Duyne & Smith 1994).
    # Each allpass adds a small phase shift; N stages accumulate to
    # stretch upper partials by the correct amount.
    #
    # Number of stages: proportional to B * N^2 (more for bass, less for treble)
    # Coefficient per stage: small fixed value (-0.1 to -0.2)
    if B > 0:
        beta = B * (N_total ** 2)
        n_disp = max(0, min(int(beta * 0.5), 16))  # 0-16 stages
        a_disp_per = -0.15  # mild per-stage coefficient
    else:
        n_disp = 0
        a_disp_per = 0.0
    print(f"  Dispersion: {n_disp} allpass stages, coeff={a_disp_per:.2f}")

    # --- Synthesis loop ---
    n_samples = int(SR * duration_s)
    output = np.zeros(n_samples)
    write_ptr = 0
    lp_state = 0.0
    ap_state = 0.0
    disp_state = 0.0

    for n in range(n_samples):
        # Read oldest sample from delay
        read_ptr = (write_ptr + 1) % N_int
        sample = delay[read_ptr]

        # Output = bridge signal
        output[n] = sample

        # Fractional delay allpass (tuning)
        frac_out, ap_state = allpass_frac(sample, ap_a, ap_state)

        # Dispersion allpass (inharmonicity)
        disp_out, disp_state = allpass_frac(frac_out, a_disp, disp_state)

        # One-pole loss filter
        filtered, lp_state = one_pole_lp(disp_out, g_dc, p, lp_state)

        # Write back — NO sign inversion (full-period delay, bipolar excitation)
        delay[write_ptr] = filtered

        # Advance write pointer
        write_ptr = (write_ptr + 1) % N_int

    return output


def write_wav(path, data, sr=48000):
    """Write mono float array as 16-bit PCM WAV."""
    peak = np.max(np.abs(data))
    if peak > 0:
        data = data / peak * 0.9  # normalize to -1 dB
    samples = np.clip(data * 32767, -32767, 32767).astype(np.int16)
    with open(path, 'wb') as f:
        n = len(samples)
        f.write(struct.pack('<4sI4s', b'RIFF', 36 + n * 2, b'WAVE'))
        f.write(struct.pack('<4sIHHIIHH', b'fmt ', 16, 1, 1, sr, sr * 2, 2, 16))
        f.write(struct.pack('<4sI', b'data', n * 2))
        f.write(samples.tobytes())


def analyze(data, f0, sr=48000):
    """Quick spectral + decay analysis."""
    # Spectrum (0.02-0.5s to skip initial transient)
    s0, s1 = int(0.02 * sr), int(0.5 * sr)
    if s1 > len(data): s1 = len(data)
    chunk = data[s0:s1] * np.hanning(s1 - s0)
    fft = np.abs(np.fft.rfft(chunk))
    freqs = np.fft.rfftfreq(len(chunk), 1 / sr)

    peaks = []
    for i in range(1, len(fft) - 1):
        if fft[i] > fft[i-1] and fft[i] > fft[i+1] and fft[i] > np.max(fft) * 0.01:
            peaks.append(i)
    peaks.sort(key=lambda i: -fft[i])

    print(f"\n  Spectrum (k, freq, dB relative to peak):")
    for i in sorted(peaks[:10], key=lambda i: freqs[i]):
        f = freqs[i]
        db = 20 * np.log10(fft[i] + 1e-20) - 20 * np.log10(np.max(fft))
        k = round(f / f0)
        print(f"    k={k:2d}  {f:7.1f} Hz  {db:5.1f} dB")

    print(f"\n  Decay:")
    for t in [0, 0.5, 1.0, 2.0, 3.0]:
        s = int(t * sr)
        e = min(s + sr // 4, len(data))
        if s < len(data) and e <= len(data):
            rms = np.sqrt(np.mean(data[s:e] ** 2))
            print(f"    {t:.1f}s: {20 * np.log10(rms + 1e-20):.1f} dB")


def make_string_v2(midi, velocity_01=0.5, duration_s=2.0,
                   T60_fund=None, T60_nyq=None,
                   exc_rolloff=2.0, exc_x0=1.0/7.0,
                   n_harmonics=40, B=0.0,
                   label="", **kwargs):
    """
    Parameterized string for A/B testing.

    exc_rolloff: harmonic amplitude ~ sin(k*pi*x0) / k^rolloff
                 2.0 = triangle/nylon, 1.0 = brighter/steel, 0.5 = very bright
    B: inharmonicity (0 = perfect, 5e-4 = bass steel)
    """
    f0 = 440.0 * 2.0 ** ((midi - 69) / 12.0)
    t_keyboard = (midi - 21) / 87.0
    gauge = kwargs.get("gauge", 1.0)

    if T60_fund is None:
        T60_fund = 10.0 - t_keyboard * 9.0
    if T60_nyq is None:
        T60_nyq = 0.04 - t_keyboard * 0.02

    N_total = SR / f0

    # Gauge effect on loss filter: thicker string → more HF damping
    T60_nyq_eff = T60_nyq / gauge
    g_dc  = np.clip(10.0 ** (-3.0 * N_total / (T60_fund * SR)), 0.5, 0.9999)
    g_nyq = np.clip(10.0 ** (-3.0 * N_total / (max(T60_nyq_eff, 0.001) * SR)), 0.01, g_dc)
    p = np.clip((g_dc - g_nyq) / (g_dc + g_nyq), 0.0, 0.95)

    # Dispersion cascade setup (must be computed before delay line sizing)
    n_disp_override = kwargs.get("n_disp_stages", None)
    if B > 0:
        beta = B * (N_total ** 2)
        n_disp = max(0, min(int(beta * 0.5), 16))
        a_disp_per = -0.15
    else:
        n_disp = 0
        a_disp_per = 0.0
    if n_disp_override is not None:
        n_disp = n_disp_override
        if n_disp > 0:
            a_disp_per = -0.15

    # Delay compensation: loss filter + dispersion allpass cascade
    filter_delay = p / (1.0 - p * p) if abs(p) > 0.001 else 0.0
    disp_delay = n_disp * (1.0 - a_disp_per) / (1.0 + a_disp_per) if n_disp > 0 else 0.0
    full_N = N_total - filter_delay - disp_delay
    N_int = max(4, int(full_N))
    frac = full_N - N_int
    if frac < 0.1:
        N_int -= 1; frac += 1.0
    ap_a = (1.0 - frac) / (1.0 + frac)

    print(f"  pole={p:.3f} g_nyq={g_nyq:.4f} rolloff=1/k^{exc_rolloff} B={B:.1e} gauge={gauge:.1f}")
    print(f"  Delay comp: filter={filter_delay:.2f} disp={disp_delay:.2f} ({n_disp} stages)")

    even_boost = kwargs.get("even_boost", 1.0)
    odd_boost = kwargs.get("odd_boost", 1.0)
    knee_k = kwargs.get("knee_k", 12)         # below knee: mild rolloff; above: steep
    knee_slope = kwargs.get("knee_slope", 2.0) # rolloff exponent above knee

    # Fourier excitation: two-stage rolloff
    #   k <= knee_k:  amplitude ~ modal / k^exc_rolloff  (flat-ish, rich body)
    #   k >  knee_k:  amplitude drops steeply as k^knee_slope  (smooth waveform)
    delay = np.zeros(N_int)
    n_harm = min(n_harmonics, N_int // 2)
    for k in range(1, n_harm + 1):
        modal = np.sin(k * np.pi * exc_x0)
        if k <= knee_k:
            ak = modal / (k ** exc_rolloff) if exc_rolloff > 0 else modal
        else:
            ak_knee = modal / (knee_k ** exc_rolloff) if exc_rolloff > 0 else modal
            ak = ak_knee * (knee_k / k) ** knee_slope
        # Gauge effect on excitation: thicker string = stronger fundamental,
        # weaker high harmonics (more mass resists high-freq vibration)
        if gauge != 1.0:
            ak *= gauge ** (1.0 - k * 0.05)  # k=1: boost by gauge, k=20: attenuate
        # Even/odd boost
        if k % 2 == 0:
            ak *= even_boost
        else:
            ak *= odd_boost
        f_k_ratio = k * np.sqrt(1.0 + B * k * k) if B > 0 else float(k)
        for i in range(N_int):
            delay[i] += ak * np.sin(2 * np.pi * f_k_ratio * i / N_int)

    peak = np.max(np.abs(delay))
    if peak > 0:
        delay *= velocity_01 * 0.5 / peak

    print(f"  Dispersion: {n_disp} stages, coeff={a_disp_per:.2f}")

    # Synthesis
    n_samples = int(SR * duration_s)
    output = np.zeros(n_samples)
    write_ptr = 0
    lp_state = ap_state = 0.0
    disp_states = [0.0] * max(n_disp, 1)

    for n in range(n_samples):
        read_ptr = (write_ptr + 1) % N_int
        sample = delay[read_ptr]
        output[n] = sample

        # Fractional delay (tuning)
        x, ap_state = allpass_frac(sample, ap_a, ap_state)

        # Dispersion cascade (stiffness → inharmonicity)
        for di in range(n_disp):
            x, disp_states[di] = allpass_frac(x, a_disp_per, disp_states[di])

        # Loss filter
        filtered, lp_state = one_pole_lp(x, g_dc, p, lp_state)
        delay[write_ptr] = filtered
        write_ptr = (write_ptr + 1) % N_int

    return output


# ── Dual-rail waveguide (Teng 2012 / Smith 1992) ─────────────────────────
#
# Two delay lines model right- and left-traveling waves on the string.
# Hammer force is injected at physical position x0, creating two wave
# fronts that reach bridge and nut at different times — naturally producing
# the comb-filter effect from hammer position (no Fourier series needed).
#
# Topology:
#
#        n0 (hammer)
#         |
#  [Nut]  v                          [Bridge]
#    |  ←---lower----[+]----upper--->   |
#   -1  --->lower----[+]----upper---←  [H] → output
#                                       |
#   H = loss × dispersion × tuning × (-1)
#

def write_wav_stereo(path, left, right, sr=48000):
    """Write stereo float arrays as 16-bit PCM WAV."""
    peak = max(np.max(np.abs(left)), np.max(np.abs(right)))
    if peak > 0:
        left = left / peak * 0.9
        right = right / peak * 0.9
    left_s = np.clip(left * 32767, -32767, 32767).astype(np.int16)
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


def _dual_rail_string(f0, hammer_force, exc_x0,
                      T60_fund, T60_nyq, gauge,
                      n_disp, a_disp, n_samples):
    """Run a single dual-rail waveguide string. Returns mono output."""

    N_period = SR / f0

    # Loss filter coefficients (same as single-rail)
    T60_nyq_eff = T60_nyq / gauge
    g_dc  = np.clip(10.0 ** (-3.0 * N_period / (T60_fund * SR)), 0.5, 0.9999)
    g_nyq = np.clip(10.0 ** (-3.0 * N_period / (max(T60_nyq_eff, 0.001) * SR)),
                    0.01, g_dc)
    pole = np.clip((g_dc - g_nyq) / (g_dc + g_nyq), 0.0, 0.95)

    # Delay compensation: loss filter + dispersion cascade
    filter_delay = pole / (1.0 - pole * pole) if abs(pole) > 0.001 else 0.0
    disp_delay = n_disp * (1.0 - a_disp) / (1.0 + a_disp) if n_disp > 0 else 0.0

    # Each rail = half the compensated period
    N_comp = N_period - filter_delay - disp_delay
    M = max(4, int(N_comp / 2))
    frac = N_comp / 2 - M
    if frac < 0.1:
        M -= 1; frac += 1.0
    ap_a = (1.0 - frac) / (1.0 + frac)

    # Hammer position in rail coordinates
    n0 = max(1, min(M - 2, round(exc_x0 * M)))

    # Allocate rails
    upper = np.zeros(M)   # right-traveling (nut → bridge)
    lower = np.zeros(M)   # left-traveling  (bridge → nut)

    # Filter states
    lp_state = 0.0
    ap_state = 0.0
    disp_states = [0.0] * max(n_disp, 1)

    output = np.zeros(n_samples)
    n_hammer = len(hammer_force)

    for n in range(n_samples):
        # Output: right-traveling wave arriving at bridge
        output[n] = upper[M - 1]

        # Bridge reflection: loss → dispersion → tuning → negate
        x = upper[M - 1]
        x, lp_state = one_pole_lp(x, g_dc, pole, lp_state)
        for di in range(n_disp):
            x, disp_states[di] = allpass_frac(x, a_disp, disp_states[di])
        x, ap_state = allpass_frac(x, ap_a, ap_state)

        # Nut reflection: rigid termination (-1)
        nut_ref = -lower[0]

        # Shift upper rail right (nut → bridge)
        upper[1:] = upper[:-1].copy()
        upper[0] = nut_ref

        # Shift lower rail left (bridge → nut)
        lower[:-1] = lower[1:].copy()
        lower[M - 1] = -x   # bridge reflection (negated)

        # Inject hammer force at physical position n0
        if n < n_hammer:
            f = hammer_force[n]
            upper[n0] += f
            lower[n0] += f

    return output


def make_string_teng(midi, velocity_01=0.5, duration_s=2.0,
                     T60_fund=None, T60_nyq=None,
                     exc_x0=1.0/7.0, B=0.0,
                     n_strings=3, detune_cents=1.0,
                     stereo_spread=0.3,
                     **kwargs):
    """
    Dual-rail waveguide piano string (Teng 2012 / Smith 1992).

    Key differences from make_string_v2 (single-rail):
    - Two delay lines: upper (nut→bridge) and lower (bridge→nut)
    - Hammer force (half-sine pulse) injected at physical position x0
    - Natural comb-filter from hammer position (no Fourier series)
    - Multi-string with detuning for beating + two-stage decay
    - Stereo from panning detuned strings across L/R

    Returns: (left, right) stereo numpy arrays
    """
    f0 = 440.0 * 2.0 ** ((midi - 69) / 12.0)
    t_keyboard = (midi - 21) / 87.0
    gauge = kwargs.get("gauge", 1.0)

    if T60_fund is None:
        T60_fund = 10.0 - t_keyboard * 9.0
    if T60_nyq is None:
        T60_nyq = 0.04 - t_keyboard * 0.02

    # Dispersion cascade setup
    n_disp_override = kwargs.get("n_disp_stages", None)
    a_disp = -0.15
    if B > 0:
        N_period = SR / f0
        n_disp = max(0, min(int(B * N_period**2 * 0.5), 16))
    else:
        n_disp = 0
    if n_disp_override is not None:
        n_disp = n_disp_override
    if n_disp == 0:
        a_disp = 0.0

    # Hammer force: half-sine pulse
    # Contact time: longer in bass, shorter in treble, shorter at high velocity
    contact_ms = max(1.5, 4.0 - 2.0 * t_keyboard - velocity_01 * 0.5)
    n_contact = max(8, int(contact_ms * 0.001 * SR))
    t_h = np.arange(n_contact)
    hammer_force = velocity_01 * 0.5 * np.sin(np.pi * t_h / n_contact)

    print(f"  MIDI {midi} ({f0:.1f} Hz): Teng dual-rail")
    print(f"  hammer: {contact_ms:.1f}ms, {n_strings} strings, "
          f"detune={detune_cents:.1f}c, B={B:.1e}, disp={n_disp}, gauge={gauge:.1f}")

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

        # Pan: spread strings across stereo field
        if n_strings > 1:
            spread_norm = (si - (n_strings - 1) / 2.0) / ((n_strings - 1) / 2.0)
            pan = 0.5 + stereo_spread * spread_norm * 0.5
        else:
            pan = 0.5
        gain_L = np.cos(pan * np.pi / 2)
        gain_R = np.sin(pan * np.pi / 2)

        mono = _dual_rail_string(
            f0_str, hammer_force, exc_x0,
            T60_fund, T60_nyq, gauge,
            n_disp, a_disp, n_samples)

        output_L += gain_L * mono
        output_R += gain_R * mono

    return output_L, output_R


def run_experiment(name, variants, midi=60, duration_s=2.0):
    """Generate variants for A/B comparison."""
    import os
    os.makedirs("tmp_audio", exist_ok=True)

    f0 = 440.0 * 2.0 ** ((midi - 69) / 12.0)
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"  MIDI {midi} ({f0:.1f} Hz)")
    print(f"{'='*60}")

    for idx, v in enumerate(variants):
        label = v.get("label", f"v{idx}")
        print(f"\n[{idx}] {label}:")
        audio = make_string_v2(midi, velocity_01=0.6, duration_s=duration_s, **v)
        fname = f"tmp_audio/m{midi:03d}_{idx}_{label}.wav"
        write_wav(fname, audio)
        print(f"  -> {fname}")

    print(f"\n{'='*60}")
    print(f"Files: C:\\Users\\jindr\\PycharmProjects\\ICR\\tmp_audio\\")
    for idx, v in enumerate(variants):
        print(f"  [{idx}] {v.get('label','')}")
    print(f"{'='*60}")
    print("Rate each 0-9 (0=fuj, 9=excellent)")


if __name__ == "__main__":
    # R7 winners: m060_D (knee10,s4), m048_C (B15,knee12), m036_A
    # New: string gauge for thicker/hutnejsi sound

    run_experiment(
        "Round 8: string gauge (thickness) — MIDI 60",
        midi=60,
        variants=[
            # R7 winner reference (normal gauge)
            {"label": "A_ref_g1",
             "exc_rolloff": 0.1, "T60_nyq": 0.3, "B": 8e-4,
             "odd_boost": 1.8, "knee_k": 10, "knee_slope": 4.0,
             "n_harmonics": 80, "gauge": 1.0},
            # Thicker string
            {"label": "B_g1.5",
             "exc_rolloff": 0.1, "T60_nyq": 0.3, "B": 8e-4,
             "odd_boost": 1.8, "knee_k": 10, "knee_slope": 4.0,
             "n_harmonics": 80, "gauge": 1.5},
            # Heavy string
            {"label": "C_g2",
             "exc_rolloff": 0.1, "T60_nyq": 0.3, "B": 8e-4,
             "odd_boost": 1.8, "knee_k": 10, "knee_slope": 4.0,
             "n_harmonics": 80, "gauge": 2.0},
            # Heavy + stronger B (thick steel = more stiffness)
            {"label": "D_g2_B12",
             "exc_rolloff": 0.1, "T60_nyq": 0.3, "B": 1.2e-3,
             "odd_boost": 1.8, "knee_k": 10, "knee_slope": 4.0,
             "n_harmonics": 80, "gauge": 2.0},
            # Very heavy + very stiff
            {"label": "E_g3_B15",
             "exc_rolloff": 0.1, "T60_nyq": 0.3, "B": 1.5e-3,
             "odd_boost": 2.0, "knee_k": 10, "knee_slope": 4.0,
             "n_harmonics": 80, "gauge": 3.0},
            # Combo: thick + stiff + high knee
            {"label": "F_g2_B12_k12",
             "exc_rolloff": 0.1, "T60_nyq": 0.35, "B": 1.2e-3,
             "odd_boost": 2.0, "knee_k": 12, "knee_slope": 3.5,
             "n_harmonics": 80, "gauge": 2.0},
        ]
    )

    run_experiment(
        "Round 8b: MIDI 48 bass with gauge",
        midi=48,
        variants=[
            {"label": "A_ref",
             "exc_rolloff": 0.1, "T60_nyq": 0.35, "B": 1.5e-3,
             "odd_boost": 2.0, "knee_k": 12, "knee_slope": 3.0,
             "n_harmonics": 80, "gauge": 1.0},
            {"label": "B_g2",
             "exc_rolloff": 0.1, "T60_nyq": 0.35, "B": 1.5e-3,
             "odd_boost": 2.0, "knee_k": 12, "knee_slope": 3.0,
             "n_harmonics": 80, "gauge": 2.0},
            {"label": "C_g3_B20",
             "exc_rolloff": 0.1, "T60_nyq": 0.35, "B": 2e-3,
             "odd_boost": 2.0, "knee_k": 12, "knee_slope": 3.0,
             "n_harmonics": 80, "gauge": 3.0},
            {"label": "D_g2_k15",
             "exc_rolloff": 0.1, "T60_nyq": 0.4, "B": 1.5e-3,
             "odd_boost": 2.0, "knee_k": 15, "knee_slope": 3.0,
             "n_harmonics": 80, "gauge": 2.0},
        ]
    )

    run_experiment(
        "Round 8c: MIDI 36 deep bass with gauge",
        midi=36,
        variants=[
            {"label": "A_g2",
             "exc_rolloff": 0.1, "T60_nyq": 0.4, "B": 1.5e-3,
             "odd_boost": 2.0, "knee_k": 12, "knee_slope": 3.0,
             "n_harmonics": 80, "gauge": 2.0},
            {"label": "B_g3_wound",
             "exc_rolloff": 0.1, "T60_nyq": 0.5, "B": 2.5e-3,
             "odd_boost": 2.0, "knee_k": 15, "knee_slope": 3.0,
             "n_harmonics": 80, "gauge": 3.0},
            {"label": "C_g4_heavy",
             "exc_rolloff": 0.1, "T60_nyq": 0.5, "B": 3e-3,
             "odd_boost": 2.0, "knee_k": 15, "knee_slope": 3.0,
             "n_harmonics": 80, "gauge": 4.0},
        ]
    )
