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

    # --- Dispersion (disabled for now) ---
    # Single allpass creates buzz artifacts for bass notes (a_disp > 0.3).
    # Proper inharmonicity requires multi-stage cascade (Van Duyne & Smith).
    # TODO: implement after clean string is validated.
    a_disp = 0.0
    print(f"  Dispersion: OFF (clean string test)")

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


if __name__ == "__main__":
    print("=== Pure string test (Karplus-Strong, one-pole loss) ===\n")

    for midi in [36, 48, 60, 72, 84]:
        f0 = 440.0 * 2.0 ** ((midi - 69) / 12.0)
        print(f"\nMIDI {midi} ({f0:.1f} Hz):")
        audio = make_string(midi, velocity_01=0.6, duration_s=4.0)
        import os; os.makedirs("tmp_audio", exist_ok=True)
        fname = f"tmp_audio/test_string_midi{midi}.wav"
        write_wav(fname, audio)
        analyze(audio, f0)
        print(f"  Written: {fname}")

    print("\nDone. Listen to the WAV files.")
