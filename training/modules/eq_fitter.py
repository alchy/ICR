"""
training/modules/eq_fitter.py
──────────────────────────────
Per-note LTASE spectral EQ fitting and IIR biquad conversion.

Public API:
    eq_fitter = EQFitter()
    params    = eq_fitter.fit_bank(params, bank_dir, workers=4)
    biquads   = eq_fitter.params_to_biquads(freqs_hz, gains_db, sr=44100)

Method (LTASE — Long-Term Average Spectrum Envelope):
    H(f) = LTASE_orig(f) / LTASE_synth(f)
    Captures instrument body resonance; stored as spectral_eq per sample.

The biquad conversion (originally in export_soundbank_params.py) lives here
because it operates on the EQ curve data produced by this module.
"""

import math
import traceback
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import tf2sos

# synthesize_note is imported lazily inside worker initialisers to avoid
# heavyweight torch imports at module load time.


# ─────────────────────────────────────────────────────────────────────────────
# EQFitter
# ─────────────────────────────────────────────────────────────────────────────

class EQFitter:
    """
    Compute per-note spectral EQ correction curves and fit IIR biquads.

    Usage:
        params   = EQFitter().fit_bank(params, bank_dir, workers=4)
        biquads  = EQFitter().params_to_biquads(freqs_hz, gains_db, sr=44100)
    """

    # EQ frequency grid
    N_EQ_POINTS = 64
    EQ_F_MIN    = 20.0
    EQ_F_MAX    = 20_000.0

    # Adaptive N_FFT: target 20 bins per harmonic
    NFFT_BINS_TARGET = 20
    NFFT_EXP_MIN     = 13   # 8192 samples
    NFFT_EXP_MAX     = 15   # 32768 samples

    # 1/6-octave smoothing half-width
    SMOOTH_OCT = 1.0 / 12.0

    def fit_bank(self, params: dict, bank_dir: str, workers: int = None) -> dict:
        """
        Compute spectral_eq for every sample and store it in params in-place.

        Returns the same params dict (samples updated with spectral_eq dicts).
        """
        import os
        workers = workers or max(1, os.cpu_count() - 1)

        keys  = list(params["samples"].keys())
        total = len(keys)
        print(f"EQFitter: processing {total} samples with {workers} workers …")

        results = {}
        done = 0

        with Pool(
            processes=workers,
            initializer=_eq_worker_init,
            initargs=(params, bank_dir),
        ) as pool:
            for key, eq in pool.imap_unordered(_eq_worker, keys):
                done += 1
                log_msg  = eq.pop("_log",   "") if eq else ""
                is_skip  = eq.pop("_skip",  False) if eq else False
                is_err   = eq.pop("_error", False) if eq else False

                if is_err:
                    print(f"  {done}/{total}: {key} … ERROR:\n{log_msg}")
                elif is_skip or eq is None:
                    print(f"  {done}/{total}: {key} … {log_msg}")
                else:
                    results[key] = eq
                    print(f"  {done}/{total}: {key} … {log_msg}")

        n_ok = 0
        for key, eq in results.items():
            params["samples"][key]["spectral_eq"] = eq
            n_ok += 1

        print(f"EQFitter: completed {n_ok}/{total} samples.")
        return params

    def params_to_biquads(self, freqs_hz, gains_db, sr: int,
                          n_sections: int = 5) -> list:
        """
        Fit spectral_eq curve to a min-phase IIR biquad cascade.

        Args:
            freqs_hz:   Array of frequency points (Hz).
            gains_db:   Array of gain values (dB).
            sr:         Sample rate.
            n_sections: Number of biquad sections.

        Returns:
            List of dicts: [{"b": [b0,b1,b2], "a": [a1,a2]}, …]
        """
        return _eq_to_biquads(freqs_hz, gains_db, sr, n_sections)


# ─────────────────────────────────────────────────────────────────────────────
# Multiprocessing worker
# ─────────────────────────────────────────────────────────────────────────────

# Module-level globals for worker processes
_G_PARAMS:   dict = {}
_G_BANK_DIR: str  = ""


def _eq_worker_init(params_dict: dict, bank_dir: str) -> None:
    global _G_PARAMS, _G_BANK_DIR
    _G_PARAMS   = params_dict
    _G_BANK_DIR = bank_dir


def _eq_worker(key: str) -> tuple:
    """Compute spectral_eq for one sample. Top-level for pickling."""
    try:
        eq = _compute_eq_for_sample(key, _G_PARAMS["samples"][key], _G_BANK_DIR)
        return key, eq
    except Exception:
        return key, {"_log": traceback.format_exc(), "_error": True}


# ─────────────────────────────────────────────────────────────────────────────
# Per-sample EQ computation (original algorithm from compute_spectral_eq.py)
# ─────────────────────────────────────────────────────────────────────────────

def _compute_eq_for_sample(key: str, sample: dict, bank_dir: str) -> dict:
    from training.modules.synthesizer import Synthesizer

    midi = sample["midi"]
    vel  = sample["vel"]

    # Find WAV file
    matches = sorted(Path(bank_dir).glob(f"m{midi:03d}-vel{vel}-f*.wav"))
    if not matches:
        return {"_log": f"SKIP: WAV not found for {key}", "_skip": True}

    wav_path = matches[0]
    orig_stereo, sr_orig = sf.read(str(wav_path), dtype="float32", always_2d=True)
    sr_use    = int(sr_orig)
    orig_mono = orig_stereo.mean(axis=1).astype(np.float64)

    # Synthesize (no EQ so we measure the raw synth spectrum)
    synth_dur    = min(float(sample.get("duration_s", 3.0)), 3.0)
    synth_stereo = Synthesizer().render(
        sample, midi=midi, vel=vel, sr=sr_use, duration=synth_dur,
        soundboard_strength=0.0, beat_scale=1.0, pan_spread=0.55,
    )
    synth_mono = synth_stereo.mean(axis=1).astype(np.float64)

    # Align lengths
    n = min(len(orig_mono), len(synth_mono))
    orig_mono        = orig_mono[:n]
    synth_mono       = synth_mono[:n]
    orig_stereo_trim = orig_stereo[:n].astype(np.float64)
    synth_stereo_trim = synth_stereo[:n].astype(np.float64)

    # Stereo width factor: skip first 100 ms to avoid attack transient
    skip    = int(0.10 * sr_use)
    orig_M  = (orig_stereo_trim[skip:,0]  + orig_stereo_trim[skip:,1])  / 2
    orig_S  = (orig_stereo_trim[skip:,0]  - orig_stereo_trim[skip:,1])  / 2
    syn_M   = (synth_stereo_trim[skip:,0] + synth_stereo_trim[skip:,1]) / 2
    syn_S   = (synth_stereo_trim[skip:,0] - synth_stereo_trim[skip:,1]) / 2
    rms     = lambda x: float(np.sqrt(np.mean(x**2)) + 1e-12)
    width_factor = float(np.clip(rms(orig_S)/rms(orig_M) / (rms(syn_S)/rms(syn_M)+1e-12),
                                 0.2, 8.0))

    # Adaptive N_FFT
    n_fft, hop = _adaptive_nfft(midi, sr_use)

    # LTASE via STFT
    ltase_orig  = _compute_ltase(orig_mono,  n_fft, hop)
    ltase_synth = _compute_ltase(synth_mono, n_fft, hop)
    freqs_fft   = np.linspace(0.0, sr_use/2.0, n_fft//2+1)

    # H = ratio with regularisation
    eps    = max(ltase_synth.max(), ltase_orig.max()) * 1e-3
    H      = (ltase_orig + eps) / (ltase_synth + eps)
    H_smooth = _smooth_octave(H, freqs_fft, EQFitter.SMOOTH_OCT)

    # Convert to dB, normalise mean above 100 Hz to 0 dB
    H_db = 20.0 * np.log10(np.maximum(H_smooth, 1e-10))
    mask = freqs_fft > 100.0
    H_db -= H_db[mask].mean() if mask.any() else H_db.mean()

    # Resample to 64 log-spaced points
    eq_freqs = np.logspace(np.log10(EQFitter.EQ_F_MIN),
                           np.log10(EQFitter.EQ_F_MAX),
                           EQFitter.N_EQ_POINTS)
    eq_gains = np.interp(eq_freqs, freqs_fft, H_db)

    log_msg = (f"N_FFT={n_fft}  EQ peak={eq_gains.max():.1f}dB "
               f"range={eq_gains.max()-eq_gains.min():.1f}dB  "
               f"width={width_factor:.3f}")

    return {
        "freqs_hz":           eq_freqs.tolist(),
        "gains_db":           eq_gains.tolist(),
        "stereo_width_factor": width_factor,
        "_log":               log_msg,
    }


# ─────────────────────────────────────────────────────────────────────────────
# STFT / LTASE helpers
# ─────────────────────────────────────────────────────────────────────────────

def _adaptive_nfft(midi: int, sr: int) -> tuple:
    f0  = 440.0 * 2.0**((midi-69)/12.0)
    raw = int(EQFitter.NFFT_BINS_TARGET * sr / f0)
    exp = max(EQFitter.NFFT_EXP_MIN, min(EQFitter.NFFT_EXP_MAX, round(math.log2(raw))))
    n   = 1 << exp
    return n, n // 4


def _compute_ltase(audio: np.ndarray, n_fft: int, hop: int) -> np.ndarray:
    """Long-Time Average Spectral Envelope via STFT (mean magnitude)."""
    window = np.hanning(n_fft)
    n_bins = n_fft // 2 + 1
    frames = [
        np.abs(np.fft.rfft(audio[s:s+n_fft] * window, n=n_fft))
        for s in range(0, len(audio)-n_fft+1, hop)
    ]
    if not frames:
        return np.ones(n_bins)
    return np.stack(frames, axis=-1).mean(axis=-1)


def _smooth_octave(H: np.ndarray, freqs: np.ndarray, half_width_oct: float) -> np.ndarray:
    """Per-bin 1/6-octave smoothing; DC bin is left unchanged."""
    factor   = 2.0**half_width_oct
    H_smooth = np.empty_like(H)
    for i, f in enumerate(freqs):
        if f <= 0.0:
            H_smooth[i] = H[i]
            continue
        mask        = (freqs >= f/factor) & (freqs <= f*factor)
        H_smooth[i] = H[mask].mean() if mask.any() else H[i]
    return H_smooth


# ─────────────────────────────────────────────────────────────────────────────
# IIR biquad fitting (from export_soundbank_params.py)
# ─────────────────────────────────────────────────────────────────────────────

def _eq_to_biquads(freqs_hz, gains_db, sr: int, n_sections: int = 5) -> list:
    """Fit spectral_eq curve to a min-phase IIR biquad cascade."""
    N_FFT        = 2048
    f_uni        = np.linspace(0, sr/2, N_FFT//2+1)
    gains_interp = np.interp(f_uni, freqs_hz, gains_db,
                             left=gains_db[0], right=gains_db[-1])
    H_mag = 10.0**(gains_interp/20.0)
    H_min = _mag_to_min_phase(H_mag)

    f_fit = np.geomspace(30.0, min(sr*0.47, 18_000.0), 256)
    w_fit = 2*math.pi*f_fit/sr
    H_fit = (np.interp(f_fit, f_uni, H_min.real)
             + 1j*np.interp(f_fit, f_uni, H_min.imag))

    b, a = _invfreqz(H_fit, w_fit, n_sections*2)
    a_s  = _stabilize(a)

    try:
        sos = tf2sos(b, a_s)
        if len(sos) < n_sections:
            pad = np.tile([1.,0.,0.,1.,0.,0.], (n_sections-len(sos), 1))
            sos = np.vstack([sos, pad])
        else:
            sos = sos[:n_sections]
    except Exception:
        sos = np.tile([1.,0.,0.,1.,0.,0.], (n_sections, 1))

    return [{"b": [float(r[0]/r[3]), float(r[1]/r[3]), float(r[2]/r[3])],
             "a": [float(r[4]/r[3]), float(r[5]/r[3])]} for r in sos]


def _mag_to_min_phase(H_mag: np.ndarray) -> np.ndarray:
    """Cepstral minimum-phase reconstruction from a magnitude spectrum."""
    N_h      = len(H_mag)
    N        = (N_h-1)*2
    log_H    = np.log(np.maximum(H_mag, 1e-8))
    log_full = np.concatenate([log_H[:-1], log_H[-1::-1][:-1]])
    cep      = np.real(np.fft.ifft(log_full))
    win      = np.zeros(N)
    win[0]   = 1.0; win[1:N//2] = 2.0
    if N % 2 == 0: win[N//2] = 1.0
    return np.exp(np.fft.fft(cep*win))[:N_h]


def _invfreqz(H_complex, w, order) -> tuple:
    """Least-squares IIR design (equation error method)."""
    nb = na = order
    cols  = [np.exp(-1j*k*w) for k in range(nb+1)]
    cols += [-H_complex*np.exp(-1j*k*w) for k in range(1, na+1)]
    A_mat = np.column_stack(cols)
    A_r   = np.vstack([A_mat.real, A_mat.imag])
    rhs_r = np.concatenate([H_complex.real, H_complex.imag])
    x, *_ = np.linalg.lstsq(A_r, rhs_r, rcond=None)
    return x[:nb+1], np.concatenate([[1.0], x[nb+1:]])


def _stabilize(a: np.ndarray) -> np.ndarray:
    """Reflect unstable poles inside the unit circle."""
    poles      = np.roots(a)
    mask       = np.abs(poles) >= 0.999
    poles[mask] = 0.999*poles[mask] / np.abs(poles[mask])
    return np.poly(poles).real
