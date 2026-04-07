#pragma once
/*
 * cores/piano/piano_math.h
 * ────────────────────────
 * Pure DSP mathematics for piano additive synthesis — stateless, inline,
 * header-only.  Every function takes inputs and returns outputs with no
 * side-effects, making the math independently testable.
 *
 * Used by: PianoCore::processBlock() and PianoCore::initVoice().
 */

#include "dsp/dsp_math.h"
#include <cmath>
#include <algorithm>

namespace piano {

// ── Partial frequency (inharmonicity) ────────────────────────────────────────

/// Compute the frequency of partial k given fundamental f0 and
/// inharmonicity coefficient B.
///   f_k = k · f0 · √(1 + B · k²)
///
/// B is the piano-string inharmonicity constant (typically 1e-5 .. 1e-3).
/// k is 1-based partial index.
inline float partial_frequency(int k, float f0_hz, float B) {
    return (float)(k * f0_hz * std::sqrt(1.0 + (double)B * k * k));
}

// ── Bi-exponential envelope ──────────────────────────────────────────────────

/// Evaluate the bi-exponential decay envelope and advance one sample.
///
///   env = a1 · env_fast + (1 - a1) · env_slow
///
/// After evaluation, env_fast and env_slow are multiplied by their
/// respective decay coefficients (precomputed as exp(-1/(tau*sr))).
///
/// Returns the envelope amplitude for the current sample.
inline float biexp_envelope_tick(float a1,
                                 float& env_fast, float& env_slow,
                                 float decay_fast, float decay_slow) {
    float env = a1 * env_fast + (1.f - a1) * env_slow;
    env_fast *= decay_fast;
    env_slow *= decay_slow;
    return env;
}

// ── String models (phase → stereo sample pair) ──────────────────────────────

/// Result of a string-model computation: left and right sample contributions.
struct StereoSample {
    float L;
    float R;
};

/// 1-string model (bass, MIDI ≤ 27).
///   Single cosine oscillator at the partial frequency.
///   s1 = cos(phase_carrier)
inline StereoSample string_model_1(float phase_c,
                                   float A0_env,
                                   float gl1, float gr1) {
    float s1   = std::cos(phase_c);
    float base = A0_env;
    return { base * s1 * gl1, base * s1 * gr1 };
}

/// 2-string model (tenor, MIDI 28–48).
///   s1 = cos(phase_c + phase_beat)
///   s2 = cos(phase_c - phase_beat + phi_diff)
///   Average of two strings, each panned independently.
inline StereoSample string_model_2(float phase_c, float phase_b,
                                   float phi_diff,
                                   float A0_env,
                                   float gl1, float gr1,
                                   float gl2, float gr2) {
    float s1   = std::cos(phase_c + phase_b);
    float s2   = std::cos(phase_c - phase_b + phi_diff);
    float base = A0_env * 0.5f;
    return { base * (s1 * gl1 + s2 * gl2),
             base * (s1 * gr1 + s2 * gr2) };
}

/// 3-string model (treble, MIDI > 48, symmetric).
///   s1 = cos(phase_c - 2·phase_b)               outer left
///   s2 = cos(2π·f·t + phi2)                      centre (random phase)
///   s3 = cos(phase_c + 2·phase_b + phi_diff)     outer right
///   Average of three strings, each panned independently.
///
///   tpi2_f = 2π · t · f_hz  (carrier without initial phase — for centre string)
inline StereoSample string_model_3(float phase_c, float phase_b,
                                   float tpi2_f, float phi2,
                                   float phi_diff,
                                   float A0_env,
                                   float gl1, float gr1,
                                   float gl2, float gr2,
                                   float gl3, float gr3) {
    float s1   = std::cos(phase_c - 2.f * phase_b);
    float s2   = std::cos(tpi2_f + phi2);
    float s3   = std::cos(phase_c + 2.f * phase_b + phi_diff);
    float base = A0_env / 3.0f;
    return { base * (s1 * gl1 + s2 * gl2 + s3 * gl3),
             base * (s1 * gr1 + s2 * gr2 + s3 * gr3) };
}

// ── Schroeder first-order all-pass decorrelation ─────────────────────────────

/// Single-channel first-order all-pass filter.
///   y[n] = -g · x[n] + x[n-1] - g · y[n-1]
///
/// x_prev, y_prev: delay elements, updated in-place.
/// Returns the all-pass filtered sample.
inline float allpass_1st_tick(float x, float g,
                              float& x_prev, float& y_prev) {
    float y = -g * x + x_prev - g * y_prev;
    x_prev = x;
    y_prev = y;
    return y;
}

/// Apply Schroeder decorrelation to a stereo pair.
///   Blends dry and all-pass-filtered signals by decor_strength.
///   If decor_strength ≤ 0.01, this is a no-op.
///
/// All-pass states (ap_x_L, ap_y_L, ap_x_R, ap_y_R) are updated in-place.
inline void allpass_decorrelate(float& samp_L, float& samp_R,
                                float g_L, float g_R,
                                float decor_str,
                                float& ap_x_L, float& ap_y_L,
                                float& ap_x_R, float& ap_y_R) {
    if (decor_str <= 0.01f) return;

    float Lap = allpass_1st_tick(samp_L, g_L, ap_x_L, ap_y_L);
    float Rap = allpass_1st_tick(samp_R, g_R, ap_x_R, ap_y_R);

    float dry = 1.f - decor_str;
    samp_L = samp_L * dry + Lap * decor_str;
    samp_R = samp_R * dry + Rap * decor_str;
}

// ── Spectral EQ: biquad cascade (DF-II) ─────────────────────────────────────

/// Apply a cascade of N biquad sections to a stereo pair with dry/wet blend.
///
///   n_biquad   = number of active biquad sections
///   eq_coeffs  = coefficient array  [n_biquad]
///   eq_wL/eq_wR = DF-II state arrays [n_biquad][2], updated in-place
///   eq_strength = blend factor (0 = bypass, 1 = full EQ)
///
/// Uses dsp::biquad_df2_tick for each section.
inline void eq_cascade_stereo(float& samp_L, float& samp_R,
                              int n_biquad,
                              const dsp::BiquadCoeffs* eq_coeffs,
                              float eq_wL[][2], float eq_wR[][2],
                              float eq_strength) {
    if (n_biquad <= 0 || eq_strength <= 0.001f) return;

    float wetL = samp_L, wetR = samp_R;
    for (int bi = 0; bi < n_biquad; bi++) {
        wetL = dsp::biquad_df2_tick(wetL, eq_coeffs[bi], eq_wL[bi]);
        wetR = dsp::biquad_df2_tick(wetR, eq_coeffs[bi], eq_wR[bi]);
    }
    float dry = 1.f - eq_strength;
    samp_L = samp_L * dry + wetL * eq_strength;
    samp_R = samp_R * dry + wetR * eq_strength;
}

// ── M/S stereo width correction ──────────────────────────────────────────────

/// Apply mid-side stereo width adjustment.
///   M = (L + R) / 2  — mono component (invariant)
///   S = (L - R) / 2 · width — stereo difference scaled
///   L' = M + S,  R' = M - S
///
/// No-op if |width - 1| ≤ 0.001.
inline void ms_stereo_width(float& samp_L, float& samp_R, float width) {
    if (std::abs(width - 1.f) <= 0.001f) return;
    float M = (samp_L + samp_R) * 0.5f;
    float S = (samp_L - samp_R) * 0.5f * width;
    samp_L = M + S;
    samp_R = M - S;
}

// ── Constant-power panning ───────────────────────────────────────────────────

/// Compute the stereo pan angle for a MIDI note on the keyboard.
///   center = π/4 + (midi - 64.5) / 87 · keyboard_spread / 2
///
/// Returns the center angle in radians.  cos(angle) = left gain,
/// sin(angle) = right gain (constant-power law).
inline float keyboard_pan_angle(int midi, float keyboard_spread) {
    return (dsp::PI / 4.f) + ((float)midi - 64.5f) / 87.0f
           * keyboard_spread * 0.5f;
}

/// Compute constant-power pan gains from an angle [0, π/2].
///   gl = cos(angle),  gr = sin(angle)
inline void constant_power_pan(float angle, float& gl, float& gr) {
    gl = std::cos(angle);
    gr = std::sin(angle);
}

// ── Schroeder decorrelation coefficients ─────────────────────────────────────

/// Compute all-pass decorrelation gains from MIDI note.
///   ds = clamp((midi - 40) / 60, 0, 1) · 0.45 · stereo_decorr
///   g_L =  0.35 + ds · 0.25
///   g_R = -(0.35 + ds · 0.20)
struct DecorCoeffs {
    float decor_str;
    float g_L;
    float g_R;
};

inline DecorCoeffs compute_decor_coeffs(int midi, float stereo_decorr) {
    float ds = (std::min)(1.0f, (std::max)(0.0f, ((float)midi - 40.0f) / 60.0f))
               * 0.45f * stereo_decorr;
    return { ds,
              0.35f + ds * 0.25f,
            -(0.35f + ds * 0.20f) };
}

// ── Onset / release ramps ────────────────────────────────────────────────────

/// Compute the per-sample step for a linear ramp.
///   step = 1 / (ramp_ms · sr / 1000)
inline float ramp_step(float ramp_ms, float sample_rate) {
    return 1.f / (ramp_ms * 0.001f * sample_rate);
}

/// Advance an onset ramp by one sample.
///   gain += step;  if gain >= 1 → clamp and signal completion.
/// Returns the current gate value.  `done` is set to true when ramp completes.
inline float onset_ramp_tick(float& gain, float step, bool& done) {
    gain += step;
    if (gain >= 1.f) {
        gain = 1.f;
        done = true;
    }
    return gain;
}

/// Advance a release ramp by one sample.
///   gain += step  (step is negative)
/// Returns true if the voice should be killed (gain ≤ 0).
inline bool release_ramp_tick(float& gain, float step) {
    gain += step;
    if (gain <= 0.f) {
        gain = 0.f;
        return true;  // voice is dead
    }
    return false;
}

// ── Attack rise envelope ─────────────────────────────────────────────────────

/// Compute the rise time constant (tau) from MIDI note number.
///
/// Models the physical string excitation rise time:
///   Bass (MIDI 21-33):  ~3-5 ms  (heavy wound strings, slow hammer)
///   Middle (MIDI 48-72): ~1-2 ms (plain steel strings)
///   Treble (MIDI 84+):   ~0.3 ms (short stiff strings, hard hammer)
///
/// Interpolated linearly across the keyboard.
inline float rise_tau_from_midi(int midi) {
    // Bass anchor: MIDI 21 → 4.0 ms, Treble anchor: MIDI 108 → 0.2 ms
    float t = (float)(midi - 21) / (108.f - 21.f);  // 0..1
    t = (std::max)(0.f, (std::min)(1.f, t));
    float rise_ms = 4.0f - t * 3.8f;                // 4.0 → 0.2 ms
    return rise_ms * 0.001f;                         // seconds
}

/// Advance the attack rise envelope by one sample.
///   rise_env approaches 1.0 exponentially: rise_env += (1 - rise_env) * (1 - coeff)
/// Returns the current rise factor [0, 1].
inline float rise_envelope_tick(float& rise_env, float rise_coeff) {
    rise_env = 1.f - (1.f - rise_env) * rise_coeff;
    return rise_env;
}

} // namespace piano
