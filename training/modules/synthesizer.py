"""
training/modules/synthesizer.py
─────────────────────────────────
Physics-based and differentiable synthesis.

Public API:
    Synthesizer().render(params, midi, vel, ...)           → np.ndarray (N, 2) stereo
    DifferentiableRenderer().render(model, midi, vel, ...) → torch.Tensor (N,) mono

Synthesizer contains all physics_synth logic inline (stereo, numpy-based).
DifferentiableRenderer contains all torch_synth logic inline (mono, PyTorch,
differentiable). Both preserve the original algorithms exactly.
"""

import math
from typing import Union

import numpy as np
from scipy.signal import fftconvolve, lfilter


# ─────────────────────────────────────────────────────────────────────────────
# Synthesizer  (physics-based stereo renderer)
# ─────────────────────────────────────────────────────────────────────────────

class Synthesizer:
    """
    Physics-based stereo piano synthesizer.

    Wraps physics_synth.synthesize_note() logic; all parameters are the same.

    Usage:
        audio = Synthesizer().render(params_dict, midi=60, vel=3)
        # Returns np.ndarray of shape (N, 2), dtype float32.
    """

    def render(
        self,
        params:              dict,
        midi:                int,
        vel:                 int,
        sr:                  int   = 44_100,
        duration:            float = None,
        beat_scale:          float = 1.0,
        noise_level:         float = 1.0,
        eq_strength:         float = 1.0,
        soundboard_strength: float = 0.0,
        pan_spread:          float = 0.55,
        target_rms:          float = 0.06,
        harmonic_brightness: float = 0.0,
        fade_out:            float = 0.5,
        stereo_decorr:       float = 1.0,
        stereo_boost:        float = 1.0,
        eq_freq_min:         float = 80.0,
        onset_ms:            float = 3.0,
        rng_seed:            int   = None,
    ) -> np.ndarray:
        """
        Synthesize a piano note.

        Args:
            params:   Sample dict with keys partials, noise, spectral_eq, etc.
            midi:     MIDI note number (used for stringing and panning).
            vel:      Velocity index 0-7 (stored in params, not used for gain here).
            sr:       Sample rate.
            duration: Render duration in seconds (None → use params["duration_s"]).

        Returns:
            (N, 2) float32 stereo array normalised to target_rms.
        """
        return _synthesize_note(
            params, duration=duration, sr=sr,
            soundboard_strength=soundboard_strength,
            beat_scale=beat_scale, pan_spread=pan_spread,
            eq_strength=eq_strength, eq_freq_min=eq_freq_min,
            stereo_boost=stereo_boost, harmonic_brightness=harmonic_brightness,
            fade_out=fade_out, target_rms=target_rms,
            noise_level=noise_level, stereo_decorr=stereo_decorr,
            onset_ms=onset_ms, rng_seed=rng_seed,
        )


# ─────────────────────────────────────────────────────────────────────────────
# DifferentiableRenderer  (PyTorch proxy, for MRSTFT fine-tuning)
# ─────────────────────────────────────────────────────────────────────────────

class DifferentiableRenderer:
    """
    Differentiable mono piano proxy for gradient-based fine-tuning.

    Implements the original torch_synth.render_note_differentiable() logic
    inline. All synthesis operations are native PyTorch; gradients flow from
    MRSTFT loss back through audio → physics parameters → NN weights.

    Usage:
        renderer = DifferentiableRenderer()
        audio    = renderer.render(model, midi=60, vel=3)
        # Returns torch.Tensor of shape (N,), dtype float32, grad-enabled.
    """

    # k_feat normalisation constant — must match profile_trainer.k_feat
    _K_FEAT_NORM = 90

    def render(
        self,
        model,
        midi:        int,
        vel:         int,
        sr:          int   = 44_100,
        duration:    float = 3.0,
        beat_scale:  Union[float, "torch.Tensor"] = 1.0,
        noise_level: Union[float, "torch.Tensor"] = 1.0,
        target_rms:  float = 0.06,
        vel_gamma:   float = 0.7,
        k_max:       int   = 60,
        rng_seed:    int   = 0,
    ) -> "torch.Tensor":
        """
        Render a differentiable mono note via the torch proxy.

        Returns a (N,) float32 torch.Tensor normalised to target_rms*vel_gain.
        Gradients flow through the InstrumentProfile model parameters and any
        tensor-valued beat_scale / noise_level.
        """
        return _render_differentiable(
            model, midi, vel,
            sr=sr, duration=duration,
            beat_scale=beat_scale, noise_level=noise_level,
            target_rms=target_rms, vel_gamma=vel_gamma,
            k_max=k_max, rng_seed=rng_seed,
            k_feat_norm=self._K_FEAT_NORM,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Core physics synthesis (all original logic from physics_synth.py)
# ─────────────────────────────────────────────────────────────────────────────

def _n_strings(midi: int) -> int:
    """Acoustic string count — mirrors C++ piano_core.cpp model:
      MIDI ≤ 27: 1 string (bass)
      MIDI 28-48: 2 strings (tenor)
      MIDI > 48: 3 strings symmetric (treble)"""
    if midi <= 27: return 1
    if midi <= 48: return 2
    return 3


def _pan_gains(angle: float) -> tuple:
    """Constant-power stereo pan. angle=pi/4 is center."""
    return math.cos(angle), math.sin(angle)


def _string_angles(midi: int, n_str: int, pan_spread: float) -> list:
    """Pan angle per string. Bass=left, treble=right.
    Matches C++: 1-string=center, 2-string=±half, 3-string=left/center/right."""
    center = math.pi/4 + (midi-64.5)/87.0*0.20
    if n_str == 1: return [center]
    half = pan_spread/2
    if n_str == 2: return [center-half, center+half]
    return [center-half, center, center+half]


# ── Soundboard IR (PARKED — strength=0.0 by default) ─────────────────────────

_SOUNDBOARD_IR_CACHE: dict = {}


def _get_soundboard_ir(sr: int) -> np.ndarray:
    if sr not in _SOUNDBOARD_IR_CACHE:
        _SOUNDBOARD_IR_CACHE[sr] = _make_soundboard_ir(sr)
    return _SOUNDBOARD_IR_CACHE[sr]


def _make_soundboard_ir(sr: int, duration: float = 0.3,
                        n_modes: int = 40, seed: int = 42) -> np.ndarray:
    rng    = np.random.default_rng(seed)
    n      = int(duration*sr)
    t      = np.arange(n, dtype=np.float64)/sr
    ir     = np.zeros(n, dtype=np.float64)
    freqs  = np.concatenate([rng.uniform(50, 600, n_modes//2),
                              rng.uniform(600, 3000, n_modes//4),
                              rng.uniform(3000, 5000, n_modes//4)])[:n_modes]
    T60    = np.clip(60/(math.pi*freqs)*80, 0.02, 0.5)
    amps   = (1.0/freqs**0.5); amps /= amps.sum()
    phases = rng.uniform(0, 2*math.pi, n_modes)
    for f, T, A, phi in zip(freqs, T60, amps, phases):
        ir += A * np.exp(-t/(T/2.303)) * np.cos(2*math.pi*f*t+phi)
    peak = np.abs(ir).max()
    if peak > 1e-10: ir = ir/peak*0.5
    return ir.astype(np.float32)


# ── Spectral EQ application ───────────────────────────────────────────────────

def _apply_spectral_eq(audio: np.ndarray, eq_data: dict,
                       sr: int, strength: float = 1.0,
                       freq_min: float = 400.0) -> np.ndarray:
    if strength < 0.005 or not eq_data:
        return audio
    freqs_stored = np.array(eq_data.get("freqs_hz", []), dtype=np.float64)
    gains_db     = np.array(eq_data.get("gains_db", []), dtype=np.float64)
    if len(freqs_stored) == 0:
        return audio

    # Zero EQ gains below freq_min (transition over one octave)
    if freq_min > 0:
        fade_low = freq_min/2.0
        for i, f in enumerate(freqs_stored):
            if f < fade_low:            gains_db[i] = 0.0
            elif f < freq_min:          gains_db[i] *= (f-fade_low)/(freq_min-fade_low)

    n       = len(audio)
    n_fft   = 1 << (n-1).bit_length()
    freqs_f = np.fft.rfftfreq(n_fft, d=1.0/sr)
    H       = np.interp(freqs_f, freqs_stored,
                        10.0**(gains_db/20.0),
                        left=10.0**(gains_db[0]/20.0),
                        right=10.0**(gains_db[-1]/20.0))
    H_blend = 1.0 + strength*(H-1.0)

    result = np.empty_like(audio)
    for ch in range(audio.shape[1]):
        X = np.fft.rfft(audio[:,ch].astype(np.float64), n=n_fft)
        result[:,ch] = np.fft.irfft(X*H_blend, n=n_fft)[:n].astype(np.float32)
    return result


def _apply_stereo_width(audio: np.ndarray,
                        width_factor: float, stereo_boost: float = 1.0) -> np.ndarray:
    effective = float(np.clip(width_factor*stereo_boost, 0.0, 6.0))
    if abs(effective-1.0) < 0.01:
        return audio
    L = audio[:,0].astype(np.float64)
    R = audio[:,1].astype(np.float64)
    M = (L+R)/2.0; S = (L-R)/2.0*effective
    result = np.empty_like(audio)
    result[:,0] = (M+S).astype(np.float32)
    result[:,1] = (M-S).astype(np.float32)
    return result


# ── Main synthesis function (original physics_synth.synthesize_note logic) ────

def _synthesize_note(
    params:              dict,
    duration:            float = None,
    sr:                  int   = 44_100,
    soundboard_strength: float = 0.0,
    beat_scale:          float = 1.0,
    pan_spread:          float = 0.55,
    eq_strength:         float = 1.0,
    eq_freq_min:         float = 400.0,
    stereo_boost:        float = 1.0,
    harmonic_brightness: float = 0.0,
    fade_out:            float = 0.5,
    target_rms:          float = 0.06,
    noise_level:         float = 1.0,
    stereo_decorr:       float = 1.0,
    onset_ms:            float = 3.0,
    rng_seed:            int   = None,
) -> np.ndarray:
    rng = np.random.default_rng(rng_seed)
    if duration is None:
        duration = min(params.get("duration_s", 4.0), 8.0)
    n    = int(duration*sr)
    t    = np.arange(n, dtype=np.float64)/sr

    L = np.zeros(n, dtype=np.float64)
    R = np.zeros(n, dtype=np.float64)

    partials = params.get("partials", [])
    if not partials:
        return np.zeros((n, 2), dtype=np.float32)

    A0_ref = next((p["A0"] for p in partials if p["A0"] and p["A0"] > 1e-10), 1.0)
    midi   = params.get("midi", 60)
    n_str  = _n_strings(midi)
    angles = _string_angles(midi, n_str, pan_spread)

    for p in partials:
        f  = p["f_hz"]
        A  = p["A0"]
        if A is None or A < 1e-10 or f > sr*0.495:
            continue
        k  = p.get("k", 1) or 1
        bright_gain = (1.0 + harmonic_brightness*math.log2(k)
                       if harmonic_brightness != 0.0 and k > 1 else 1.0)
        amp  = (A/A0_ref)*bright_gain
        tau1 = p.get("tau1") or 3.0
        tau2 = p.get("tau2")
        a1   = p.get("a1") or 1.0
        # "mono" key absent in exported soundbank JSON → default False (use bi-exp).
        # a1=1.0 effectively gives mono even with bi-exp formula, matching C++.
        env  = (a1*np.exp(-t/tau1) + (1-a1)*np.exp(-t/tau2)
                if tau2 is not None and not p.get("mono", False)
                else np.exp(-t/tau1))
        beat = (p.get("beat_hz", 0.0) or 0.0)*beat_scale

        # String model matching C++ piano_core.cpp:
        #   1-string (MIDI≤27): single oscillator at f
        #   2-string (28–48):   s1=cos((f+beat/2)t), s2=cos((f-beat/2)t)
        #   3-string (MIDI>48): symmetric — s1=cos((f-beat)t), s2=cos(ft), s3=cos((f+beat)t)
        #   beat_hz is the inner-pair detuning; outer pair beats at 2*beat_hz.
        if n_str == 1:
            phi = rng.uniform(0, 2*math.pi)
            s   = amp*env*np.cos(2*math.pi*f*t + phi)
            gl, gr = _pan_gains(angles[0])
            L += s*gl; R += s*gr
        elif n_str == 2:
            pa, pb = rng.uniform(0, 2*math.pi, 2)
            if beat < 0.05:
                sa = amp*env*np.cos(2*math.pi*f*t + pa)
                sb = amp*env*np.cos(2*math.pi*f*t + pb)
            else:
                sa = amp*env*np.cos(2*math.pi*(f + beat/2)*t + pa)
                sb = amp*env*np.cos(2*math.pi*(f - beat/2)*t + pb)
            gla, gra = _pan_gains(angles[0]); glb, grb = _pan_gains(angles[1])
            L += (sa*gla + sb*glb)*0.5; R += (sa*gra + sb*grb)*0.5
        else:  # n_str == 3, treble
            pa, pb, pc = rng.uniform(0, 2*math.pi, 3)
            sa = amp*env*np.cos(2*math.pi*(f - beat)*t + pa)   # outer left
            sb = amp*env*np.cos(2*math.pi*f*t + pb)            # center
            sc = amp*env*np.cos(2*math.pi*(f + beat)*t + pc)   # outer right
            gla, gra = _pan_gains(angles[0])
            glb, grb = _pan_gains(angles[1])
            glc, grc = _pan_gains(angles[2])
            L += (sa*gla + sb*glb + sc*glc)/3.0
            R += (sa*gra + sb*grb + sc*grc)/3.0

    # Attack noise (independent L/R) — flat soundbank keys only
    taun_raw = params.get("attack_tau", 0.05) or 0.05
    cent     = params.get("noise_centroid_hz", 3000.0) or 3000.0
    A_noise  = (params.get("A_noise", 0.06) or 0.06) * noise_level
    tau1_k1  = next((p.get("tau1", 3.0) for p in partials
                     if p.get("k")==1 and p.get("A0") and p["A0"]>1e-10), 3.0) or 3.0
    taun     = min(taun_raw, tau1_k1)
    alp      = 1.0 - math.exp(-2*math.pi*min(cent, sr*0.45)/sr)
    nenv     = np.exp(-t/max(taun, 0.001))
    for buf in (L, R):
        raw = rng.standard_normal(n)
        sh  = np.zeros(n); y = 0.0
        for i in range(n):
            y = alp*raw[i] + (1-alp)*y; sh[i] = y
        buf += A_noise*sh*nenv

    # Optional soundboard convolution
    if soundboard_strength > 0.005:
        ir = _get_soundboard_ir(sr)
        L  = L + soundboard_strength*fftconvolve(L, ir, mode="same")
        R  = R + soundboard_strength*fftconvolve(R, ir, mode="same")

    # Fade out
    if fade_out > 0 and duration > fade_out:
        nf = int(fade_out*sr)
        fade = np.linspace(1.0, 0.0, nf)
        L[-nf:] *= fade; R[-nf:] *= fade

    # Frequency-dependent stereo decorrelation via Schroeder all-pass pair
    decor_strength = min(1.0, (midi-40)/60.0)*0.45*stereo_decorr
    if decor_strength > 0.01:
        g_L = 0.35 + decor_strength*0.25
        g_R = -(0.35 + decor_strength*0.20)
        L_ap = lfilter([-g_L,1.0],[1.0,g_L], L)
        R_ap = lfilter([-g_R,1.0],[1.0,g_R], R)
        L = L*(1-decor_strength) + L_ap*decor_strength
        R = R*(1-decor_strength) + R_ap*decor_strength

    # RMS normalise
    rms = math.sqrt((np.mean(L**2)+np.mean(R**2))/2)
    if rms > 1e-10:
        scale = min(target_rms/rms, 0.95/max(np.abs(L).max(), np.abs(R).max()))
        L *= scale; R *= scale

    stereo = np.stack([L, R], axis=1).astype(np.float32)

    # Spectral EQ
    eq_data = params.get("spectral_eq")
    if eq_data and eq_strength > 0.005:
        stereo = _apply_spectral_eq(stereo, eq_data, sr, eq_strength, eq_freq_min)
        rms_post = math.sqrt(np.mean(stereo**2))
        if rms_post > 1e-10:
            scale = min(target_rms/rms_post, 0.95/np.abs(stereo).max())
            stereo = stereo*scale

    # Stereo width scaling — read from flat key (exported by exporter.py)
    width_factor = float(params.get("stereo_width", 1.0) or 1.0)
    if abs(width_factor*stereo_boost - 1.0) > 0.01:
        stereo = _apply_stereo_width(stereo, width_factor, stereo_boost)
        rms_w  = math.sqrt(np.mean(stereo**2))
        if rms_w > 1e-10:
            scale  = min(target_rms/rms_w, 0.95/np.abs(stereo).max())
            stereo = stereo*scale

    # Onset ramp (eliminates click from non-zero initial phase)
    n_onset = min(int(onset_ms*0.001*sr), n//10)
    if n_onset > 1:
        stereo[:n_onset] *= np.linspace(0.0, 1.0, n_onset, dtype=np.float32)[:,np.newaxis]

    return stereo


# ─────────────────────────────────────────────────────────────────────────────
# Differentiable render (all torch_synth.render_note_differentiable logic)
# ─────────────────────────────────────────────────────────────────────────────

def _render_differentiable(
    model,
    midi:        int,
    vel:         int,
    *,
    sr:          int   = 44_100,
    duration:    float = 3.0,
    beat_scale         = 1.0,
    noise_level        = 1.0,
    target_rms:  float = 0.06,
    vel_gamma:   float = 0.7,
    k_max:       int   = 60,
    rng_seed:    int   = 0,
    k_feat_norm: int   = 90,
):
    """
    Differentiable mono proxy. Original torch_synth algorithm preserved exactly.

    2-string model per partial:
      s1 = cos(2π*(f+beat/2)*t + phi1)
      s2 = cos(2π*(f-beat/2)*t + phi1 + phi_diff)
      partial = A0 * env * (s1+s2) / 2

    Gradients flow through: B_net, tau nets, A0_net, biexp_net,
                            noise_net, df_net, phi_net.
    """
    import torch
    from training.modules.profile_trainer import midi_feat, vel_feat, k_feat, midi_to_hz

    device = next(model.parameters()).device

    mf = midi_feat(midi).to(device)
    vf = vel_feat(vel).to(device)
    f0 = midi_to_hz(midi)

    n_max_nyquist = max(1, int(sr/2/f0))
    K = min(k_max, n_max_nyquist)

    n = int(duration*sr)
    t = torch.arange(n, dtype=torch.float32, device=device)/sr

    # Batch feature tensors
    kf_b   = torch.stack([k_feat(k, k_max=k_feat_norm) for k in range(1, K+1)]).to(device)
    k_vals = torch.arange(1, K+1, dtype=torch.float32, device=device)
    mf_b   = mf.unsqueeze(0).expand(K, -1)
    vf_b   = vf.unsqueeze(0).expand(K, -1)

    # Inharmonicity B → partial frequencies
    B      = torch.exp(model.forward_B(mf, vf).clamp(max=0.0)).squeeze()
    f_hzs  = k_vals*f0*torch.sqrt(1.0 + B*k_vals**2)
    valid  = ((f_hzs < sr*0.495) & torch.isfinite(f_hzs)).float().unsqueeze(1)

    # Beat frequencies
    log_df  = model.forward_df(mf_b, kf_b, vf_b).squeeze(-1)
    beat_hz = torch.exp(log_df).clamp(min=1e-4)
    if isinstance(beat_scale, torch.Tensor):
        beat_hz = beat_hz*beat_scale
    else:
        beat_hz = beat_hz*float(beat_scale)

    # Decay times
    tau1_k1    = torch.exp(model.forward_tau1_k1(mf, vf)).squeeze()
    log_ratios = model.forward_tau_ratio(mf_b, kf_b, vf_b).squeeze(-1)
    log_k_bias = -0.3*torch.log(k_vals)
    log_ratios = torch.minimum(log_ratios, torch.zeros_like(log_ratios))
    log_ratios = torch.maximum(log_ratios, log_k_bias-2.0)
    tau1s      = (tau1_k1*torch.exp(log_ratios)).clamp(min=0.005)

    # Amplitudes
    A0s = torch.exp(
        model.forward_A0(mf_b, kf_b, vf_b).squeeze(-1)
    ).clamp(min=1e-6)

    # Bi-exponential parameters
    biexp       = model.forward_biexp(mf_b, kf_b, vf_b)
    a1s         = torch.sigmoid(biexp[:,0]).clamp(0.05, 0.99)
    tau2_ratios = torch.exp(biexp[:,1]).clamp(min=3.0)
    tau2s       = tau1s*tau2_ratios

    # Noise parameters
    noise_pred = model.forward_noise(mf, vf).squeeze()
    attack_tau = torch.exp(noise_pred[0]).clamp(0.002, 1.0)
    A_noise    = torch.exp(noise_pred[2]).clamp(0.001, 0.5)

    # Learned phi_diff from phi_net
    phi_diff = model.forward_phi(mf, vf).squeeze()

    # Fixed random realizations (deterministic per note)
    seed = rng_seed + midi*256 + vel
    gen  = torch.Generator()
    gen.manual_seed(seed)
    phis      = torch.rand(K, generator=gen).mul(2*math.pi).to(device)
    noise_raw = torch.randn(n, generator=gen).to(device)

    # Bi-exponential envelope
    env_fast = torch.exp(-t.unsqueeze(0)/tau1s.unsqueeze(1))
    env_slow = torch.exp(-t.unsqueeze(0)/tau2s.unsqueeze(1))
    envs     = a1s.unsqueeze(1)*env_fast + (1.0-a1s).unsqueeze(1)*env_slow

    # 2-string oscillators per partial
    half_beat  = beat_hz.unsqueeze(1)*0.5
    phase_base = 2.0*math.pi*t.unsqueeze(0)*f_hzs.unsqueeze(1)
    beat_phase = 2.0*math.pi*t.unsqueeze(0)*half_beat
    phi_init   = phis.unsqueeze(1)
    osc1 = torch.cos(phase_base + beat_phase + phi_init)
    osc2 = torch.cos(phase_base - beat_phase + phi_init + phi_diff)
    oscs = (osc1 + osc2)*0.5

    audio = (A0s.unsqueeze(1)*envs*oscs*valid).sum(0)

    # Noise signal
    noise_env = torch.exp(-t/attack_tau.clamp(min=1e-4))
    if isinstance(noise_level, torch.Tensor):
        audio = audio + A_noise*noise_level*noise_raw*noise_env
    else:
        audio = audio + A_noise*float(noise_level)*noise_raw*noise_env

    # RMS normalisation
    vel_gain   = ((vel+1)/8.0)**float(vel_gamma)
    rms        = torch.sqrt(audio.pow(2).mean() + 1e-10)
    audio_norm = audio*(float(target_rms)*vel_gain/rms)

    return audio_norm
