"""
training/modules/extractor.py
──────────────────────────────
Physical parameter extraction from a WAV sample bank.

Public API:
    extractor = ParamExtractor()
    params = extractor.extract_bank(bank_dir, workers=4)
    note   = extractor.extract_note(wav_path)

All heavy lifting is the original logic from extract_params.py,
reorganised into a class with short, named methods.
"""

import json
import math
import warnings
from multiprocessing import Pool, cpu_count
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.optimize import curve_fit
from scipy.signal import welch

warnings.filterwarnings("ignore", category=RuntimeWarning)


# ─────────────────────────────────────────────────────────────────────────────
# ParamExtractor
# ─────────────────────────────────────────────────────────────────────────────

class ParamExtractor:
    """
    Extract physical parameters (B, τ, A0, noise, beating) from a WAV bank.

    Usage:
        params = ParamExtractor().extract_bank("/path/to/bank")
        note   = ParamExtractor().extract_note("/path/to/m060-vel3-f44.wav")
    """

    # How far the duration of a re-recorded file may drop before we treat it
    # as accidentally kicked (and keep the previous extraction instead).
    KICK_THRESHOLD = 0.70

    def extract_bank(self, bank_dir: str, workers: int = None,
                     sr_tag: str = "f48") -> dict:
        """
        Extract all WAV files in bank_dir in parallel.

        Args:
            bank_dir:  Directory with WAV files named m{midi}-vel{v}-{sr_tag}.wav
            workers:   Parallel worker count (None = auto).
            sr_tag:    Preferred sample-rate tag, e.g. "f48" (48 000 Hz, default)
                       or "f44" (44 100 Hz).  Falls back to the other tag, then
                       to any f* pattern for single-SR banks.

        Returns the full params dict (keys: bank_dir, n_samples, summary, samples).
        """
        bank_path = Path(bank_dir)
        wav_files = sorted(bank_path.glob(f"m*-vel*-{sr_tag}.wav"))
        if not wav_files:
            fallback = "f44" if sr_tag == "f48" else "f48"
            wav_files = sorted(bank_path.glob(f"m*-vel*-{fallback}.wav"))
        if not wav_files:
            wav_files = sorted(bank_path.glob("m*-vel*-f*.wav"))
        if not wav_files:
            raise FileNotFoundError(f"No WAV files found in {bank_dir}")

        work = self._build_work_list(wav_files)

        workers = workers or max(1, cpu_count() - 1)
        print(f"Extracting {len(work)} files with {workers} workers …")

        results = self._run_parallel(work, workers)
        summary = _compute_summary(results)

        return {
            "bank_dir":  str(bank_path),
            "n_samples": len(results),
            "summary":   summary,
            "samples":   results,
        }

    def extract_note(self, wav_path: str) -> dict:
        """Extract parameters from a single WAV file (e.g. m060-vel3-f44.wav)."""
        p = Path(wav_path)
        parts = p.stem.split("-")
        midi = int(parts[0][1:])
        vel  = int(parts[1][3:])
        return _analyze_file(wav_path, midi, vel)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _build_work_list(self, wav_files: list) -> list:
        work = []
        for wav_path in wav_files:
            parts = wav_path.stem.split("-")
            try:
                midi = int(parts[0][1:])
                vel  = int(parts[1][3:])
            except (ValueError, IndexError):
                continue
            work.append((str(wav_path), midi, vel))
        return work

    def _run_parallel(self, work: list, workers: int) -> dict:
        results = {}
        total = len(work)
        done  = 0

        if workers == 1 or total == 1:
            for path, midi, vel in work:
                key, data, err = _analyze_worker((path, midi, vel))
                done += 1
                self._log_result(done, total, key, data, err)
                if data is not None:
                    results[key] = data
        else:
            with Pool(workers) as pool:
                for key, data, err in pool.imap_unordered(_analyze_worker, work):
                    done += 1
                    self._log_result(done, total, key, data, err)
                    if data is not None:
                        results[key] = data

        return results

    def _log_result(self, done: int, total: int, key: str, data, err) -> None:
        if err:
            print(f"  {done}/{total}: {key} … ERROR: {err}")
        else:
            print(f"  {done}/{total}: {key} … "
                  f"B={data['B']:.5f}  partials={data['n_partials']}  "
                  f"dur={data['duration_s']:.1f}s")


# ─────────────────────────────────────────────────────────────────────────────
# Worker (top-level for multiprocessing pickling)
# ─────────────────────────────────────────────────────────────────────────────

def _analyze_worker(args: tuple) -> tuple:
    path, midi, vel = args
    key = f"m{midi:03d}_vel{vel}"
    try:
        data = _analyze_file(path, midi, vel)
        return key, data, None
    except Exception as exc:
        return key, None, str(exc)


# ─────────────────────────────────────────────────────────────────────────────
# Per-file analysis (all original logic preserved)
# ─────────────────────────────────────────────────────────────────────────────

def _analyze_file(path: str, midi: int, vel: int) -> dict:
    """Full physical parameter extraction for one sample file."""
    audio, sr = _load_mono(path)
    duration   = len(audio) / sr
    f0_nominal = _midi_to_hz(midi)

    result = {
        "midi":         midi,
        "vel":          vel,
        "f0_nominal_hz": float(f0_nominal),
        "sr":           sr,
        "duration_s":   float(duration),
        "B":            0.0,
        "f0_fitted_hz": float(f0_nominal),
        "n_strings":    _n_strings(midi),
        "n_partials":   0,
        "partials":     [],
        "noise":        {},
    }

    if duration < 0.1:
        return result

    t_spec_start = min(0.1, duration * 0.05)
    t_spec_end   = min(t_spec_start + 4.0, duration * 0.95)
    freqs, spec  = _compute_spectrum(audio, sr, t_spec_start, t_spec_end)

    peaks, B, f0_fit = _detect_harmonic_peaks(freqs, spec, f0_nominal, sr)
    result["B"]             = float(B)
    result["f0_fitted_hz"]  = float(f0_fit)
    result["n_partials"]    = len(peaks)

    if not peaks:
        return result

    # Adaptive STFT frame size for time-domain envelope extraction
    raw_frame = int(20 * sr / f0_nominal)
    frame_exp = max(11, min(15, round(math.log2(raw_frame))))
    frame_env = 1 << frame_exp
    hop_env   = frame_env // 4
    stft_times, stft_freqs, stft_mag = _compute_stft(audio, sr, hop_env, frame_env)

    partials_out = []
    for p in peaks:
        k  = p["k"]
        fk = _inharmonic_freq(k, f0_fit, B)
        partial = _extract_partial(stft_times, stft_freqs, stft_mag, p, fk)
        partials_out.append(partial)

    result["partials"] = partials_out
    result["noise"]    = _analyze_noise(audio, sr, peaks, f0_fit, B)

    # Longitudinal (phantom) partials for low bass notes only
    if midi < 50 and stft_mag.shape[0] >= 8:
        long_parts = _detect_longitudinal_partials(
            freqs, spec, peaks, f0_fit, B, sr,
            stft_times, stft_freqs, stft_mag,
        )
        if long_parts:
            result["partials"].extend(long_parts)
            result["n_longitudinal"] = len(long_parts)

    result["n_partials"] = len(result["partials"])
    return _sanitize_for_json(result)


def _extract_partial(stft_times, stft_freqs, stft_mag, peak: dict, fk: float) -> dict:
    """Extract decay + beating parameters for one partial."""
    k = peak["k"]

    if stft_mag.shape[0] >= 8:
        t_env, a_env = _partial_envelope(stft_times, stft_freqs, stft_mag, fk)
    else:
        t_env, a_env = np.array([]), np.array([])

    if len(t_env) < 12:
        return {
            "k": k, "f_hz": float(fk),
            "A0": float(peak["amp"]),
            "tau1": None, "tau2": None, "a1": 1.0, "mono": True,
            "beat_hz": 0.0, "beat_depth": 0.0,
        }

    i_peak = _find_peak_frame(a_env)
    decay  = _fit_decay(t_env, a_env, i_peak)
    beat   = _detect_beating(t_env, a_env, i_peak)

    return {
        "k":          k,
        "f_hz":       float(fk),
        "A0":         float(decay["A0"]),
        "tau1":       decay["tau1"],
        "tau2":       decay["tau2"],
        "a1":         float(decay["a1"]),
        "mono":       decay["mono"],
        "beat_hz":    beat["beat_hz"],
        "beat_depth": beat["beat_depth"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Signal processing helpers (all original logic)
# ─────────────────────────────────────────────────────────────────────────────

def _midi_to_hz(midi: int) -> float:
    return 440.0 * 2.0 ** ((midi - 69) / 12.0)


def _n_strings(midi: int) -> int:
    """Standard piano stringing thresholds."""
    if midi <= 27: return 1
    if midi <= 48: return 2
    return 3


def _load_mono(path: str) -> tuple:
    audio, sr = sf.read(path, dtype="float32", always_2d=True)
    if audio.shape[1] > 1:
        audio = audio.mean(axis=1)
    else:
        audio = audio[:, 0]
    return audio, sr


def _compute_spectrum(audio: np.ndarray, sr: int,
                      t_start: float, t_end: float,
                      zero_pad: int = 8) -> tuple:
    i0 = int(t_start * sr)
    i1 = min(int(t_end * sr), len(audio))
    if i1 <= i0 + 512:
        i0 = 0; i1 = len(audio)
    segment = audio[i0:i1] * np.hanning(i1 - i0)
    n_fft   = (i1 - i0) * zero_pad
    spec    = np.abs(np.fft.rfft(segment, n=n_fft))
    freqs   = np.fft.rfftfreq(n_fft, 1.0 / sr)
    return freqs, spec


def _find_peak_near(freqs: np.ndarray, spec: np.ndarray,
                    f_center: float, width_frac: float = 0.025) -> tuple:
    mask = (freqs >= f_center * (1 - width_frac)) & (freqs <= f_center * (1 + width_frac))
    if not mask.any():
        return f_center, 0.0
    local = spec[mask]; idx = local.argmax()
    return float(freqs[mask][idx]), float(local[idx])


def _detect_harmonic_peaks(freqs, spec, f0_nominal, sr, n_max=90) -> tuple:
    nyquist = sr / 2.0

    # Step 1: refine f0 from k=1
    f0_k1, amp_k1 = _find_peak_near(freqs, spec, f0_nominal, 0.03)
    f0_est = f0_k1 if amp_k1 >= 1e-12 else f0_nominal

    # Step 2: rough B from mid-register partials
    B_est = 0.0
    for k_probe in [5, 6, 7, 8]:
        f_exp = k_probe * f0_est
        if f_exp > nyquist * 0.9: break
        f_peak, amp = _find_peak_near(freqs, spec, f_exp, 0.04)
        if amp < 1e-12: continue
        ratio = f_peak / (k_probe * f0_est)
        if ratio > 1.0:
            B_est = max(B_est, (ratio**2 - 1.0) / k_probe**2)

    # Step 3: collect all peaks
    peaks = []
    for k in range(1, n_max + 1):
        f_inh = k * f0_est * math.sqrt(1.0 + B_est * k * k)
        if f_inh > nyquist * 0.97: break
        width = max(0.008, 0.025 / math.sqrt(k))
        f_peak, amp = _find_peak_near(freqs, spec, f_inh, width)
        if amp < 1e-12: continue
        peaks.append({"k": k, "f_measured": f_peak, "amp": amp})

    if len(peaks) < 3:
        return peaks, B_est, f0_est

    B, f0_fit = _fit_B_f0(peaks, f0_est)
    return peaks, B, f0_fit


def _fit_B_f0(peaks: list, f0_nominal: float) -> tuple:
    ks = np.array([p["k"] for p in peaks], dtype=float)
    fs = np.array([p["f_measured"] for p in peaks], dtype=float)
    ws = np.array([p["amp"] for p in peaks], dtype=float)
    ws /= ws.sum() + 1e-12

    def model(k, f0, B):
        return k * f0 * np.sqrt(1.0 + max(B, 0.0) * k**2)

    try:
        popt, _ = curve_fit(model, ks, fs,
                            p0=[f0_nominal, 1e-4],
                            bounds=([f0_nominal*0.97, 0.0], [f0_nominal*1.03, 5e-3]),
                            sigma=1.0/(ws+1e-9), absolute_sigma=False, maxfev=8000)
        return float(max(popt[1], 0.0)), float(popt[0])
    except Exception:
        try:
            popt, _ = curve_fit(lambda k, B: k*f0_nominal*np.sqrt(1.0+max(B,0.0)*k**2),
                                ks, fs, p0=[1e-4], bounds=([0.0],[5e-3]), maxfev=4000)
            return float(max(popt[0], 0.0)), f0_nominal
        except Exception:
            return 0.0, f0_nominal


def _inharmonic_freq(k: int, f0: float, B: float) -> float:
    return k * f0 * math.sqrt(1.0 + B * k * k)


def _compute_stft(audio, sr, hop=1024, frame=4096) -> tuple:
    window  = np.hanning(frame)
    n_frames = (len(audio) - frame) // hop
    if n_frames < 8:
        return np.array([]), np.array([]), np.zeros((0, 0))
    mag = np.zeros((n_frames, frame // 2 + 1), dtype=np.float32)
    for i in range(n_frames):
        seg     = audio[i*hop : i*hop+frame] * window
        mag[i]  = np.abs(np.fft.rfft(seg))
    times = np.array([(i*hop + frame//2) / sr for i in range(n_frames)])
    freqs = np.fft.rfftfreq(frame, 1.0/sr)
    return times, freqs, mag


def _partial_envelope(times, freqs, mag, f_center, search_bins=3) -> tuple:
    if mag.shape[0] < 8:
        return np.array([]), np.array([])
    freq_res   = freqs[1] - freqs[0] if len(freqs) > 1 else 1.0
    target_bin = int(round(f_center / freq_res))
    lo = max(0, target_bin - search_bins)
    hi = min(mag.shape[1] - 1, target_bin + search_bins)
    bins   = np.arange(lo, hi+1, dtype=np.float32)
    sigma  = max(1.0, (hi-lo)/2.0)
    weights = np.exp(-0.5*((bins-target_bin)/sigma)**2)
    weights /= weights.sum() + 1e-12
    amps = (mag[:, lo:hi+1] * weights[np.newaxis, :]).sum(axis=1).astype(np.float64)
    if amps.max() < 1e-12:
        return np.array([]), np.array([])
    return times, amps


def _find_peak_frame(amps: np.ndarray) -> int:
    if len(amps) == 0:
        return 0
    smoothed = np.convolve(amps, np.ones(3)/3, mode="same")
    return int(smoothed.argmax())


def _fit_decay(times, amps, i_peak) -> dict:
    default = {
        "tau1": None, "tau2": None, "a1": 1.0,
        "A0": float(amps[i_peak]) if len(amps) > i_peak else 0.0,
        "mono": True,
    }
    if i_peak >= len(times):
        return default

    t = times[i_peak:] - times[i_peak]
    a = amps[i_peak:]
    if len(t) < 12 or a[0] < 1e-12:
        return default

    A0     = float(a[0])
    a_norm = np.clip(a / A0, 1e-8, 1.2)
    result = dict(default)
    result["A0"] = A0

    # Single exponential
    try:
        popt, _ = curve_fit(lambda t, tau: np.exp(-t/tau),
                            t, a_norm, p0=[3.0],
                            bounds=([0.05], [120.0]), maxfev=3000)
        result["tau1"] = float(popt[0])
        result["mono"] = True
    except Exception:
        result["tau1"] = float(t[-1]/3.0) if t[-1] > 0 else 3.0

    # Bi-exponential — multi-start search, residual-gated acceptance
    tau2_max  = min(60.0, t[-1] * 0.9)
    tau1_max  = min(20.0, t[-1] * 0.5)   # was hard-capped at 5.0
    mono_tau  = result["tau1"] or 3.0

    if t[-1] > 0.8 and tau2_max > 0.3 and A0 > 1e-6:

        def bi_exp(t, a1, tau1, tau2):
            a1   = np.clip(a1, 0.01, 0.99)
            tau1 = max(tau1, 0.01)
            tau2 = max(tau2, tau1 * 1.1)
            return a1 * np.exp(-t / tau1) + (1 - a1) * np.exp(-t / tau2)

        # Four diverse initializations to escape local minima
        inits = [
            (0.3,  min(mono_tau * 0.15, tau1_max * 0.5),  mono_tau),
            (0.5,  min(mono_tau * 0.30, tau1_max * 0.5),  min(mono_tau * 2.5, tau2_max * 0.8)),
            (0.2,  0.05,                                   min(mono_tau * 0.5, tau2_max * 0.5)),
            (0.7,  min(mono_tau * 0.50, tau1_max * 0.8),  min(mono_tau * 4.0, tau2_max * 0.8)),
        ]

        best_popt = None
        best_residual = np.inf
        for a1_0, tau1_0, tau2_0 in inits:
            try:
                popt2, _ = curve_fit(
                    bi_exp, t, a_norm,
                    p0=[a1_0, tau1_0, tau2_0],
                    bounds=([0.01, 0.02, 0.1], [0.99, tau1_max, tau2_max]),
                    maxfev=8000,
                )
                a1c, tau1c, tau2c = popt2
                if tau2c > tau1c * 1.3 and 0.03 < a1c < 0.97 and tau2c < tau2_max * 0.95:
                    pred = bi_exp(t, a1c, tau1c, tau2c)
                    res  = float(np.mean((a_norm - pred) ** 2))
                    if res < best_residual:
                        best_residual = res
                        best_popt = (float(a1c), float(tau1c), float(tau2c))
            except Exception:
                pass

        if best_popt is not None:
            a1, tau1, tau2 = best_popt
            mono_pred     = np.exp(-t / mono_tau)
            mono_residual = float(np.mean((a_norm - mono_pred) ** 2))
            # Accept bi-exp when it fits meaningfully better AND two components are distinct
            if best_residual < mono_residual * 0.85 and tau2 / tau1 > 1.3:
                result["tau1"] = tau1
                result["tau2"] = tau2
                result["a1"]   = a1
                result["mono"] = False

    return result


def _detect_beating(times, amps, i_peak) -> dict:
    result = {"beat_hz": 0.0, "beat_depth": 0.0}
    t = times[i_peak:]; a = amps[i_peak:]
    if len(a) < 48:
        return result

    t_rel = t - t[0]
    a_log = np.log(a + 1e-15)
    if len(t_rel) < 4:
        return result

    trend    = np.polyval(np.polyfit(t_rel, a_log, 1), t_rel)
    residual = a_log - trend - (a_log - trend).mean()

    dt     = float(np.mean(np.diff(t_rel))) if len(t_rel) > 1 else 0.02
    n      = len(residual)
    spec   = np.abs(np.fft.rfft(residual * np.hanning(n)))
    freqs  = np.fft.rfftfreq(n, dt)

    mask = (freqs >= 0.1) & (freqs <= 10.0)
    if not mask.any():
        return result

    local_spec  = spec[mask]
    local_freqs = freqs[mask]
    idx_max     = local_spec.argmax()
    beat_hz     = float(local_freqs[idx_max])
    snr         = local_spec[idx_max] / (np.median(local_spec) + 1e-15)
    beat_depth  = float(np.clip(2 * local_spec[idx_max]*2/n, 0.0, 1.0))

    if snr > 3.0 and beat_depth > 0.02:
        result["beat_hz"]    = beat_hz
        result["beat_depth"] = beat_depth
    return result


def _analyze_noise(audio, sr, peaks, f0, B) -> dict:
    result = {"attack_tau": 0.05, "A_noise": 0.001,
              "centroid_hz": 2000.0, "spectral_slope_db_oct": -3.0}

    if len(audio) < sr*0.1:
        return result

    attack  = audio[:int(0.2*sr)].copy()
    n       = len(attack)
    t_vec   = np.arange(n) / sr

    # Subtract top harmonics via least squares
    basis = []
    for p in peaks[:30]:
        fk = _inharmonic_freq(p["k"], f0, B)
        if fk < sr/2*0.95:
            basis.append(np.cos(2*math.pi*fk*t_vec).astype(np.float32))

    if basis:
        H = np.column_stack(basis)
        c, *_ = np.linalg.lstsq(H, attack, rcond=None)
        residual = attack - (H @ c).astype(np.float32)
    else:
        residual = attack.copy()

    hop = 256; frame = 1024
    n_frames = max(1, (len(residual)-frame)//hop)
    rms_env = np.array([
        math.sqrt(max(np.mean(residual[i*hop:i*hop+frame]**2), 1e-30))
        for i in range(n_frames)
    ])
    rms_signal = np.array([
        math.sqrt(max(np.mean(attack[i*hop:i*hop+frame]**2), 1e-30))
        for i in range(n_frames)
    ])
    t_rms = np.array([(i*hop+frame//2)/sr for i in range(n_frames)])

    if len(rms_env) >= 6:
        i_peak      = rms_env.argmax()
        sig_at_peak = float(rms_signal[i_peak])
        if sig_at_peak > 1e-10:
            result["A_noise"] = float(rms_env[i_peak]) / sig_at_peak
        else:
            result["A_noise"] = float(rms_env[i_peak])

        t_dec = t_rms[i_peak:] - t_rms[i_peak]
        a_dec = rms_env[i_peak:]
        if len(t_dec) >= 4 and a_dec[0] > 1e-10:
            try:
                popt, _ = curve_fit(
                    lambda t, tau: a_dec[0]*np.exp(-t/tau),
                    t_dec, a_dec, p0=[0.05], bounds=([0.003],[1.0]), maxfev=2000)
                result["attack_tau"] = float(popt[0])
            except Exception:
                pass

    # Spectral centroid from noise residual
    noise_for_centroid = residual
    if len(noise_for_centroid) >= frame//2:
        try:
            f_w, psd = welch(noise_for_centroid[:frame], fs=sr, nperseg=frame//2)
            if psd.sum() > 0:
                result["centroid_hz"] = float(np.sum(f_w*psd) / psd.sum())
                mask = (f_w >= 200) & (f_w <= 8000)
                if mask.sum() >= 4:
                    coeffs = np.polyfit(np.log2(f_w[mask]+1), np.log10(psd[mask]+1e-30), 1)
                    result["spectral_slope_db_oct"] = float(coeffs[0]*10)
        except Exception:
            pass

    return result


def _detect_longitudinal_partials(freqs, spec, transverse_peaks, f0_fit, B, sr,
                                   stft_times, stft_freqs, stft_mag) -> list:
    """Detect longitudinal (phantom) partials at f ≈ 2·f_k for bass notes."""
    long_partials    = []
    transverse_freqs = {p["f_measured"] for p in transverse_peaks}

    for p in transverse_peaks[:12]:
        f_trans         = p["f_measured"]
        f_long_expected = 2.0 * f_trans
        if f_long_expected >= sr/2*0.97:
            continue

        f_peak, amp = _find_peak_near(freqs, spec, f_long_expected, 0.02)
        if amp < 1e-12:
            continue

        mask      = (freqs >= f_long_expected*0.95) & (freqs <= f_long_expected*1.05)
        noise_est = float(np.median(spec[mask])) if mask.any() else 0.0
        if amp < noise_est*3.0:
            continue

        if any(abs(f_peak - tf)/max(tf, 1.0) < 0.005 for tf in transverse_freqs):
            continue

        tau_long = None
        if stft_mag.shape[0] >= 8:
            t_env, a_env = _partial_envelope(stft_times, stft_freqs, stft_mag, f_peak)
            if len(t_env) >= 12:
                dec      = _fit_decay(t_env, a_env, _find_peak_frame(a_env))
                tau_long = dec["tau1"]

        long_partials.append({
            "k":               p["k"],
            "f_hz":            float(f_peak),
            "A0":              float(amp),
            "tau1":            tau_long,
            "tau2":            None,
            "a1":              1.0,
            "mono":            True,
            "is_longitudinal": True,
            "beat_hz":         0.0,
            "beat_depth":      0.0,
        })

    return long_partials


# ─────────────────────────────────────────────────────────────────────────────
# Summary + JSON helpers
# ─────────────────────────────────────────────────────────────────────────────

def _compute_summary(results: dict) -> dict:
    B_by_midi  = {}
    f0_by_midi = {}
    for data in results.values():
        m = data["midi"]
        if data["B"] > 0:
            B_by_midi.setdefault(m, []).append(data["B"])
        f0_by_midi.setdefault(m, []).append(data["f0_fitted_hz"])

    B_mean  = {m: float(np.mean(vs)) for m, vs in B_by_midi.items()}
    f0_mean = {m: float(np.mean(vs)) for m, vs in f0_by_midi.items()}

    B_fit = {"slope": 0.0, "intercept": math.log(1e-4)}
    if len(B_mean) >= 4:
        midis = np.array(sorted(B_mean.keys()))
        Bs    = np.array([B_mean[m] for m in midis])
        valid = Bs > 0
        if valid.sum() >= 4:
            try:
                coeffs = np.polyfit(midis[valid], np.log(Bs[valid]), 1)
                B_fit  = {"slope": float(coeffs[0]), "intercept": float(coeffs[1])}
            except Exception:
                pass

    tuning_cents = {}
    for m, f0_vals in f0_by_midi.items():
        f0_et       = _midi_to_hz(m)
        f0_measured = float(np.mean(f0_vals))
        cents = (1200*math.log2(f0_measured/f0_et)
                 if f0_measured > 0 and f0_et > 0 else 0.0)
        tuning_cents[m] = round(cents, 2)

    return {
        "n_midi_notes": len(set(d["midi"] for d in results.values())),
        "n_velocities": len(set(d["vel"]  for d in results.values())),
        "B_by_midi":    {str(k): v for k, v in B_mean.items()},
        "B_log_linear_fit":    B_fit,
        "f0_fitted_hz_by_midi": {str(k): v for k, v in f0_mean.items()},
        "tuning_cents_by_midi": {str(k): v for k, v in tuning_cents.items()},
    }


def _sanitize_for_json(obj):
    """Recursively replace NaN/Inf with None for JSON compliance."""
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj
