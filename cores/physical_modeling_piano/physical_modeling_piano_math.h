#pragma once
/*
 * cores/physical_modeling_piano/physical_modeling_piano_math.h
 * ────────────────────────────────────────────────────────────
 * Pure DSP mathematics for physical piano modelling -- stateless, inline,
 * header-only.
 *
 * Implements a commuted digital waveguide piano model based on:
 *   - Smith (1993): Physical Modelling using Digital Waveguides
 *   - Bank & Sujbert (2005): Generation of longitudinal vibrations in piano
 *   - Chabassier, Chaigne & Joly (2012): Modeling of a grand piano (INRIA)
 *   - Valimaki et al. (2006): Commuted waveguide synthesis of the clavichord
 *
 * Energy conservation:
 *   The waveguide junction at the bridge enforces Kirchhoff-style energy
 *   balance: the sum of incoming and outgoing wave power at each junction
 *   equals zero (lossless junction).  Losses are applied independently via
 *   the loop filter (frequency-dependent damping) and radiation filter.
 *
 * Used by: PhysicalModelingPianoCore
 */

#include "dsp/dsp_math.h"
#include <cmath>
#include <algorithm>
#include <cstring>

namespace physics {

// -- Constants ----------------------------------------------------------------

static constexpr int   MAX_DELAY_SAMPLES = 8192;  // supports A0 (27.5 Hz) up to 96 kHz
static constexpr int   MAX_STRINGS       = 3;
static constexpr float SPEED_OF_SOUND    = 343.f; // m/s (for radiation)

// -- Delay line (fractional, allpass-interpolated) ----------------------------

/// Fixed-size delay line with first-order allpass fractional delay.
///
/// Digital waveguide string = two delay lines (travelling waves) + loss filter.
/// The allpass interpolation preserves allpass phase characteristics,
/// which is critical for accurate tuning without amplitude distortion.
struct DelayLine {
    float buf[MAX_DELAY_SAMPLES] = {};
    int   len      = 256;    // integer part of total delay
    int   write_ix = 0;
    // Allpass fractional delay: H(z) = (a + z^-1) / (1 + a*z^-1)
    float ap_a     = 0.f;    // allpass coefficient
    float ap_state = 0.f;    // delay element
};

/// Write a sample into the delay line.
inline void delay_write(DelayLine& d, float x) {
    d.buf[d.write_ix] = x;
    d.write_ix = (d.write_ix + 1) % d.len;
}

/// Read from delay line at position `tap` samples behind write head.
inline float delay_read(const DelayLine& d, int tap) {
    int idx = d.write_ix - tap - 1;
    if (idx < 0) idx += d.len;
    return d.buf[idx];
}

/// Read with first-order allpass fractional delay interpolation.
///   Total delay = integer_tap + fractional
///   H(z) = (ap_a + z^-1) / (1 + ap_a * z^-1)
inline float delay_read_allpass(DelayLine& d, int tap) {
    float raw = delay_read(d, tap);
    float y = d.ap_a * raw + d.ap_state;
    d.ap_state = raw - d.ap_a * y;
    return y;
}

/// Compute delay length and allpass coefficient for a target frequency.
///   total_delay = sr / f0 - filter_delay
///   integer part -> delay line length
///   fractional part -> allpass coefficient
///
/// filter_delay accounts for group delay of the loop filter (in samples).
/// Returns {integer_len, ap_coeff}.
struct DelayTuning {
    int   len;
    float ap_a;
};

inline DelayTuning compute_delay_tuning(float f0_hz, float sr,
                                         float filter_delay_samples) {
    float total = sr / f0_hz - filter_delay_samples;
    if (total < 2.f) total = 2.f;
    int   N = (int)total;
    float frac = total - (float)N;

    // Allpass coefficient for fractional delay:
    //   a = (1 - frac) / (1 + frac)  when 0 < frac < 1
    // When frac ~ 0, use N-1 and frac+1 to keep a in stable range
    if (frac < 0.1f) {
        N -= 1;
        frac += 1.f;
    }
    float ap = (1.f - frac) / (1.f + frac);

    N = std::min(N, MAX_DELAY_SAMPLES - 1);
    N = std::max(N, 2);

    return { N, ap };
}

/// Reset delay line to target length and allpass coefficient.
inline void delay_reset(DelayLine& d, const DelayTuning& t) {
    d.len      = t.len;
    d.ap_a     = t.ap_a;
    d.ap_state = 0.f;
    d.write_ix = 0;
    std::memset(d.buf, 0, sizeof(float) * t.len);
}

// -- Loss filter (frequency-dependent string damping) -------------------------

/// One-pole low-pass loss filter for waveguide loop.
///
/// Models frequency-dependent damping of piano strings:
///   H(z) = g * (1-b) / (1 - b*z^-1)
///
/// where:
///   g = overall loop gain (< 1 for decay)
///   b = pole position (0 = flat loss, ->1 = more treble damping)
///
/// The combined effect: low frequencies decay slowly (long tau2),
/// high frequencies decay fast (short tau1).  This is the physical
/// mechanism behind bi-exponential envelope perception.
///
/// Chabassier: damping = R + eta*f^2  ->  frequency-dependent loss per sample.
struct LossFilter {
    float g = 0.999f;  // overall gain per round-trip
    float b = 0.3f;    // pole coefficient (treble damping)
    float s = 0.f;     // filter state
};

/// Compute loss filter coefficients from physical parameters.
///
///   f0       = fundamental frequency [Hz]
///   tau_fund  = decay time of fundamental [s] (corresponds to tau2 in biexp)
///   tau_high  = decay time at Nyquist [s] (corresponds to tau1 in biexp)
///   sr       = sample rate
///
/// The gain g is set so that the fundamental decays with time constant tau_fund.
/// The pole b is set so that high frequencies decay faster (tau_high).
inline LossFilter compute_loss_filter(float f0, float tau_fund,
                                       float tau_high, float sr) {
    // Gain per round-trip at fundamental: exp(-1 / (tau_fund * f0))
    //   because there are f0 round-trips per second.
    float g_fund = std::exp(-1.f / std::max(tau_fund * f0, 1.f));
    float g_high = std::exp(-1.f / std::max(tau_high * f0, 1.f));

    // From the filter response at DC and Nyquist:
    //   |H(1)|  = g*(1-b)/(1-b) = g        (matches g_fund)
    //   |H(-1)| = g*(1-b)/(1+b)            (matches g_high)
    // Solving: b = (g_fund - g_high) / (g_fund + g_high)
    float b = (g_fund - g_high) / std::max(g_fund + g_high, 1e-9f);
    b = std::max(0.f, std::min(b, 0.98f));

    return { g_fund, b, 0.f };
}

/// Process one sample through the loss filter.
///   y[n] = g * ((1-b) * x[n] + b * y[n-1])
inline float loss_filter_tick(float x, LossFilter& f) {
    float y = f.g * ((1.f - f.b) * x + f.b * f.s);
    f.s = y;
    return y;
}

/// Approximate group delay of loss filter at fundamental (in samples).
///   For one-pole: delay ~ b / (1 - b^2) at low frequencies.
inline float loss_filter_delay(const LossFilter& f) {
    float b2 = f.b * f.b;
    return f.b / std::max(1.f - b2, 0.01f);
}

// -- Hammer model (nonlinear felt compression) --------------------------------

/// Felt hammer state.
///
/// Models the hammer-string interaction as a nonlinear spring:
///   F = K_H * max(0, xi - u)^p
///
/// where xi = hammer displacement, u = string displacement at contact point,
/// K_H = felt stiffness, p = nonlinear exponent.
///
/// Chabassier parameters:
///   K_H: 4e8 (bass) to 2.3e11 (treble)
///   p:   2.27 (bass) to 3.0 (treble)
///   M_H: 12 g (bass) to 6.77 g (treble)
///
/// In the commuted model, we inject the hammer force directly into the
/// string delay line rather than solving the full PDE interaction.
struct HammerState {
    float xi       = 0.f;   // hammer position (m)
    float vi       = 0.f;   // hammer velocity (m/s)
    float K_H      = 1e9f;  // felt stiffness
    float p        = 2.5f;  // nonlinear exponent
    float M_H      = 0.009f;// hammer mass (kg)
    float x0_ratio = 0.125f;// strike position as fraction of string length
    bool  in_contact = false;
};

/// Initialize hammer for a note-on event.
///
///   v0 = hammer velocity (m/s), derived from MIDI velocity
///   The hammer starts just touching the string (xi = 0) with velocity v0.
inline void hammer_init(HammerState& h, float v0,
                        float K_H, float p, float M_H) {
    h.xi    = 0.f;
    h.vi    = v0;
    h.K_H   = K_H;
    h.p     = p;
    h.M_H   = M_H;
    h.in_contact = true;
}

/// Advance hammer by one sample.  Returns the force applied to the string.
///
///   u_string = string displacement at contact point
///   dt = 1 / sample_rate
///
/// The hammer separates from the string when xi < u_string (hammer
/// bounces back), after which force = 0.
inline float hammer_tick(HammerState& h, float u_string, float dt) {
    if (!h.in_contact) return 0.f;

    // Advance hammer position first (symplectic Euler: position before force)
    h.xi += h.vi * dt;

    float compression = h.xi - u_string;

    if (compression <= 0.f) {
        // Hammer has bounced off (or hasn't reached string yet)
        // Only separate if hammer is moving away (vi < 0 means retreating)
        if (h.vi < 0.f) {
            h.in_contact = false;
            return 0.f;
        }
        // Still approaching — no force yet but keep in contact
        return 0.f;
    }

    float F = h.K_H * std::pow(compression, h.p);

    // Newton: M_H * a = -F  (hammer decelerates)
    float a = -F / h.M_H;
    h.vi += a * dt;

    return F;
}

/// Convert MIDI velocity (1-127) to hammer velocity in m/s.
///
/// Chabassier: 0.5 m/s (pp) to 4.5 m/s (ff).
/// Mapping: v = v_min + (v_max - v_min) * (vel/127)^gamma
///
/// gamma > 1 gives a more natural feel (exponential-like response).
inline float midi_to_hammer_velocity(uint8_t velocity, float gamma = 1.5f) {
    static constexpr float V_MIN = 0.5f;
    static constexpr float V_MAX = 4.5f;

    float t = (float)velocity / 127.f;
    float curved = std::pow(t, gamma);
    return V_MIN + (V_MAX - V_MIN) * curved;
}

// -- Hammer excitation spectrum (commuted model) ------------------------------

/// Compute the spectral excitation weight for partial k based on hammer
/// strike position and felt compression.
///
///   sin(k * pi * x0/L) gives the modal coupling -- partials at strike position
///   nodes (k = L/x0, 2L/x0, ...) are suppressed (e.g., k=8,16,24 for x0=L/8).
///
///   The felt compression acts as a low-pass: higher p -> harder hammer ->
///   more high-frequency content.
///
///   spectral_rolloff models felt low-pass: exp(-k * f0 / f_cutoff)
///   f_cutoff depends on hammer hardness (higher p -> higher cutoff).
inline float hammer_spectral_weight(int k, float x0_ratio, float p,
                                     float f0, float sr) {
    // Modal coupling (strike position)
    float modal = std::abs(std::sin((float)k * dsp::PI * x0_ratio));

    // Felt low-pass (harder hammer = more partials)
    // f_cutoff ~ K_H^(1/(2p+1)) but simplified:
    //   p=2.3 (soft, bass) -> cutoff ~ 2 kHz
    //   p=3.0 (hard, treble) -> cutoff ~ 8 kHz
    float f_cutoff = 500.f + (p - 2.0f) * 6000.f;
    float f_k = (float)k * f0;
    float rolloff = std::exp(-f_k / std::max(f_cutoff, 100.f));

    return modal * rolloff;
}

// -- Soundboard coupling (bridge junction) ------------------------------------

/// Two-port junction scattering coefficients.
///
/// At the bridge, string impedance Z_s meets soundboard impedance Z_b.
/// The scattering coefficients determine how much wave energy:
///   - reflects back into the string (k_r)
///   - transmits into the soundboard (k_t)
///
/// Energy conservation: k_r^2 + k_t^2 * (Z_s/Z_b) = 1
///
/// For piano: Z_s << Z_b -> most energy reflects (k_r ~ -1), small
/// fraction transmits (long sustain, gradual radiation).
struct JunctionCoeffs {
    float k_r;  // reflection coefficient (string side)
    float k_t;  // transmission coefficient (to soundboard)
};

/// Compute bridge junction coefficients.
///   Kirchhoff junction: incoming = outgoing
///   k_r = (Z_b - Z_s) / (Z_b + Z_s)
///   k_t = 2 * Z_s / (Z_b + Z_s)
///
/// Parametrized by impedance ratio r = Z_s / Z_b.
inline JunctionCoeffs compute_junction(float impedance_ratio) {
    // r = Z_s / Z_b  (typically 0.001 to 0.05 for piano)
    float r = std::max(impedance_ratio, 1e-6f);
    float k_r = (1.f - r) / (1.f + r);
    float k_t = 2.f * r / (1.f + r);
    return { k_r, k_t };
}

// -- Soundboard resonator (simplified modal model) ----------------------------

/// Simplified soundboard as a bank of resonant modes.
/// Each mode is a second-order resonator (damped harmonic oscillator).
///
/// In a full model, the soundboard has ~100+ modes below 1 kHz.
/// We use a simplified version with a small number of modes that
/// capture the gross spectral envelope.
static constexpr int SOUNDBOARD_MODES = 24;

struct SoundboardMode {
    float freq  = 200.f;  // resonance frequency (Hz)
    float decay = 0.99f;  // per-sample decay
    float gain  = 0.01f;  // coupling gain
    // Resonator state (two-pole)
    float y1 = 0.f, y2 = 0.f;
    float c1 = 0.f, c2 = 0.f;  // coefficients (precomputed)
};

/// Initialize soundboard mode coefficients.
///   freq  = resonance frequency [Hz]
///   Q     = quality factor
///   gain  = coupling amplitude
///   sr    = sample rate
inline void soundboard_mode_init(SoundboardMode& m, float freq, float Q,
                                  float gain, float sr) {
    m.freq  = freq;
    m.gain  = gain;
    float w = dsp::TAU * freq / sr;
    m.decay = std::exp(-w / std::max(Q, 0.5f));
    m.c1    = 2.f * m.decay * std::cos(w);
    m.c2    = -(m.decay * m.decay);
    m.y1    = 0.f;
    m.y2    = 0.f;
}

/// Process one sample through a soundboard mode.
///   x = input excitation (from bridge)
///   Returns radiated output.
inline float soundboard_mode_tick(SoundboardMode& m, float x) {
    float y = m.gain * x + m.c1 * m.y1 + m.c2 * m.y2;
    m.y2 = m.y1;
    m.y1 = y;
    return y;
}

// -- Inharmonicity dispersion filter ------------------------------------------

/// Second-order allpass for inharmonicity (string stiffness).
///
/// Piano strings are not ideal -- stiffness causes dispersion:
///   f_k = k * f0 * sqrt(1 + B * k^2)
///
/// In a waveguide model, this is implemented as an allpass filter in
/// the delay loop.  The allpass adds frequency-dependent phase shift
/// that stretches upper partials, matching the inharmonicity equation.
///
/// We use a second-order allpass tuned to approximate B across the
/// first ~15-20 partials.
struct DispersionFilter {
    float a1 = 0.f, a2 = 0.f;
    float s1 = 0.f, s2 = 0.f;
};

/// Compute dispersion allpass coefficients from inharmonicity B.
///
/// Simplified approach: coefficient ~ -B * N^2,
/// calibrated to match first 15 partials within ~5 cents.
inline DispersionFilter compute_dispersion(float B, float f0, float sr) {
    DispersionFilter d;
    if (B < 1e-7f) return d;  // no inharmonicity

    // Empirical fit: a1 controls dispersion slope
    float N = sr / f0;  // delay in samples
    float beta = B * N * N;
    // Keep coefficient magnitude bounded for stability
    float ab = std::min(beta * 0.5f, 0.9f);
    d.a1 = -ab;
    d.a2 = ab * 0.3f;  // mild second-order correction
    return d;
}

/// Process one sample through dispersion allpass (second-order).
///   w[n] = x[n] - a1*w[n-1] - a2*w[n-2]
///   y[n] = a2*w[n] + a1*w[n-1] + w[n-2]
inline float dispersion_tick(float x, DispersionFilter& d) {
    float w = x - d.a1 * d.s1 - d.a2 * d.s2;
    float y = d.a2 * w + d.a1 * d.s1 + d.s2;
    d.s2 = d.s1;
    d.s1 = w;
    return y;
}

/// Approximate total group delay of dispersion filter at fundamental (samples).
inline float dispersion_delay(const DispersionFilter& d) {
    // For allpass, group delay at DC ~ (a1 + 2*a2) / (1 + a1 + a2)
    float num = -d.a1 + 2.f * (-d.a2);
    float den = 1.f - d.a1 - d.a2;
    return std::abs(num / std::max(std::abs(den), 0.01f));
}

// -- Multi-string detuning (beating) ------------------------------------------

/// Compute detuning in Hz for each string of a unison group.
///
///   n_strings = 1, 2, or 3
///   detune_cents = total detuning spread (cents)
///   f0 = fundamental frequency (Hz)
///
/// Returns array of frequency offsets from f0.
struct StringDetuning {
    float offsets[MAX_STRINGS] = {};
    int   count = 1;
};

inline StringDetuning compute_detuning(int n_strings, float detune_cents,
                                        float f0) {
    StringDetuning sd;
    sd.count = std::max(1, std::min(n_strings, MAX_STRINGS));

    if (sd.count == 1) {
        sd.offsets[0] = 0.f;
    } else {
        // Convert cents to Hz: df = f0 * (2^(cents/1200) - 1)
        float df = f0 * (std::pow(2.f, detune_cents / 1200.f) - 1.f);
        if (sd.count == 2) {
            sd.offsets[0] = -df * 0.5f;
            sd.offsets[1] =  df * 0.5f;
        } else {
            sd.offsets[0] = -df;
            sd.offsets[1] =  0.f;
            sd.offsets[2] =  df;
        }
    }
    return sd;
}

// -- Keyboard mapping helpers -------------------------------------------------

/// Compute physical parameters from MIDI note.
/// These are default heuristics; can be overridden from JSON.

/// Hammer stiffness K_H: exponential from bass to treble.
///   MIDI 21 -> 4e8, MIDI 108 -> 2.3e11
inline float default_K_H(int midi) {
    float t = (float)(midi - 21) / 87.f;
    return std::pow(10.f, 8.6f + t * 2.76f);  // 10^8.6 to 10^11.36
}

/// Nonlinear exponent p: 2.27 (bass) to 3.0 (treble).
inline float default_p(int midi) {
    float t = (float)(midi - 21) / 87.f;
    return 2.27f + t * 0.73f;
}

/// Hammer mass M_H: 12 g (bass) to 6.77 g (treble).
inline float default_M_H(int midi) {
    float t = (float)(midi - 21) / 87.f;
    return 0.012f - t * 0.00523f;
}

/// String count per note.
inline int default_n_strings(int midi) {
    return (midi <= 27) ? 1 : (midi <= 48) ? 2 : 3;
}

/// Detuning in cents: larger for bass (1-2 cents), smaller for treble (~0.1).
inline float default_detune_cents(int midi) {
    float t = (float)(midi - 21) / 87.f;
    return 1.5f - t * 1.4f;  // 1.5 -> 0.1 cents
}

/// Impedance ratio Z_s/Z_b: governs sustain length and coupling.
/// Must be very small: each round-trip loses ~k_t of amplitude to the
/// soundboard.  At f0=262 Hz, impedance_ratio=0.01 would give 2% loss
/// per trip → effective tau ≈ 0.2 s (way too fast).
///   Bass: ~0.0002 (very long sustain), Treble: ~0.002 (shorter)
inline float default_impedance_ratio(int midi) {
    float t = (float)(midi - 21) / 87.f;
    return 0.0002f + t * 0.0018f;
}

/// Fundamental decay time tau2 (s): bass ~20s, treble ~1s.
inline float default_tau_fund(int midi) {
    float t = (float)(midi - 21) / 87.f;
    return 20.f - t * 19.f;
}

/// High-frequency decay time tau1 (s): bass ~2s, treble ~0.2s.
inline float default_tau_high(int midi) {
    float t = (float)(midi - 21) / 87.f;
    return 2.f - t * 1.8f;
}

/// Inharmonicity B: bass ~5e-4, treble ~5e-5.
inline float default_B(int midi) {
    float t = (float)(midi - 21) / 87.f;
    return 5e-4f * std::pow(10.f, -t);
}

/// Default soundboard modes: 24 modes approximating grand piano response.
/// Frequencies span 60 Hz to 8 kHz, covering the full radiating range.
/// More modes = richer, less synthetic body resonance.
struct SoundboardPreset {
    float freqs[SOUNDBOARD_MODES];
    float Qs[SOUNDBOARD_MODES];
    float gains[SOUNDBOARD_MODES];
};

inline SoundboardPreset default_soundboard() {
    return {
        // freqs: 24 modes spanning 60 Hz - 8 kHz (measured Steinway-like)
        {  60.f,  95.f, 120.f, 155.f, 195.f, 240.f, 300.f, 370.f,
          450.f, 550.f, 670.f, 820.f, 1000.f, 1200.f, 1500.f, 1800.f,
         2200.f, 2700.f, 3300.f, 4000.f, 4800.f, 5800.f, 7000.f, 8000.f },
        // Qs: higher in mid-range (more resonant), lower at extremes
        {  10.f, 15.f, 18.f, 22.f, 25.f, 28.f, 30.f, 32.f,
           35.f, 32.f, 30.f, 28.f, 25.f, 22.f, 20.f, 18.f,
           16.f, 14.f, 12.f, 10.f,  8.f,  7.f,  6.f,  5.f },
        // gains: bell-shaped, peak in 200-800 Hz (soundboard resonance region)
        { 0.008f, 0.012f, 0.016f, 0.020f, 0.024f, 0.026f, 0.028f, 0.026f,
          0.024f, 0.022f, 0.020f, 0.018f, 0.015f, 0.012f, 0.010f, 0.008f,
          0.006f, 0.005f, 0.004f, 0.003f, 0.002f, 0.0015f, 0.001f, 0.0008f }
    };
}

} // namespace physics
