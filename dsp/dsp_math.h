#pragma once
/*
 * dsp/dsp_math.h
 * ──────────────
 * Shared DSP primitives — stateless, inline, header-only.
 *
 * Used by: BBE, Limiter, PianoCore EQ, and any future DSP module.
 * All functions are pure: no side-effects, no allocation, RT-safe.
 */

#include <cmath>
#include <algorithm>

namespace dsp {

// ── Constants ────────────────────────────────────────────────────────────────

static constexpr float PI  = 3.14159265358979f;
static constexpr float TAU = 2.f * PI;

// ── Conversions ──────────────────────────────────────────────────────────────

/// Convert decibels to linear amplitude.  0 dB → 1.0, -6 dB → ~0.5
inline float db_to_lin(float db) {
    return std::pow(10.f, db / 20.f);
}

/// Convert linear amplitude to decibels.  1.0 → 0 dB
inline float lin_to_db(float lin) {
    return 20.f * std::log10((std::max)(lin, 1e-9f));
}

// ── Exponential coefficients ─────────────────────────────────────────────────

/// Compute per-sample multiplicative decay coefficient.
///   decay = exp(-1 / (tau_seconds * sample_rate))
/// Used for envelope followers, noise decay, limiter smoothing.
inline float decay_coeff(float tau_seconds, float sample_rate) {
    return std::exp(-1.f / (std::max)(tau_seconds * sample_rate, 1.f));
}

/// Compute 1-pole IIR low-pass alpha from cutoff frequency.
///   alpha = 1 - exp(-2π · fc / sr)
/// fc is clamped to 0.45*sr to stay below Nyquist.
inline float onepole_alpha(float fc_hz, float sample_rate) {
    float fc = (std::min)(fc_hz, sample_rate * 0.45f);
    return 1.f - std::exp(-TAU * fc / sample_rate);
}

// ── Biquad: Direct Form II transposed ────────────────────────────────────────

/// Biquad filter coefficients (normalised: a0 = 1 always).
struct BiquadCoeffs {
    float b0 = 1.f, b1 = 0.f, b2 = 0.f;
    float a1 = 0.f, a2 = 0.f;
};

/// Biquad filter state (DF-II transposed, 2 delay elements).
struct BiquadState {
    float s1 = 0.f, s2 = 0.f;
};

/// Process one sample through a biquad (DF-II transposed).
///
/// Transfer function: H(z) = (b0 + b1·z⁻¹ + b2·z⁻²) / (1 + a1·z⁻¹ + a2·z⁻²)
///
/// Returns filtered output sample.  Updates state in-place.
inline float biquad_tick(float x, const BiquadCoeffs& c, BiquadState& s) {
    float y = c.b0 * x + s.s1;
    s.s1    = c.b1 * x - c.a1 * y + s.s2;
    s.s2    = c.b2 * x - c.a2 * y;
    return y;
}

/// Process one sample through a biquad using Direct Form II (w-state).
///
/// This form stores intermediate w[n] values rather than transposed states.
/// Used by PianoCore EQ cascade (matches original implementation exactly).
///
/// w[0],w[1] are the two delay elements (caller owns the array).
inline float biquad_df2_tick(float x, const BiquadCoeffs& c,
                             float w[2]) {
    float w0 = x - c.a1 * w[0] - c.a2 * w[1];
    float y  = c.b0 * w0 + c.b1 * w[0] + c.b2 * w[1];
    w[1] = w[0];
    w[0] = w0;
    return y;
}

// ── RBJ shelving filter coefficient computation ──────────────────────────────
// Reference: Robert Bristow-Johnson's Audio EQ Cookbook

/// Compute RBJ high-shelf biquad coefficients.
///   fc      = shelf center frequency [Hz]
///   gain_db = boost/cut [dB] (positive = boost)
///   sr      = sample rate [Hz]
inline BiquadCoeffs rbj_high_shelf(float fc, float gain_db, float sr) {
    float A    = std::pow(10.f, gain_db / 40.f);
    float w0   = TAU * fc / sr;
    float cosw = std::cos(w0);
    float sinw = std::sin(w0);
    float al   = sinw / 2.f * std::sqrt((A + 1.f/A) * (1.f/1.f - 1.f) + 2.f);
    float sqA2 = 2.f * std::sqrt(A) * al;

    float a0 =        (A+1.f) - (A-1.f)*cosw + sqA2;
    float ia = 1.f / a0;

    BiquadCoeffs c;
    c.b0 = ( A * ((A+1.f) + (A-1.f)*cosw + sqA2)) * ia;
    c.b1 = (-2.f*A * ((A-1.f) + (A+1.f)*cosw))    * ia;
    c.b2 = ( A * ((A+1.f) + (A-1.f)*cosw - sqA2)) * ia;
    c.a1 = ( 2.f * ((A-1.f) - (A+1.f)*cosw))      * ia;
    c.a2 = (       (A+1.f) - (A-1.f)*cosw - sqA2)  * ia;
    return c;
}

/// Compute RBJ low-shelf biquad coefficients.
///   fc      = shelf center frequency [Hz]
///   gain_db = boost/cut [dB] (positive = boost)
///   sr      = sample rate [Hz]
inline BiquadCoeffs rbj_low_shelf(float fc, float gain_db, float sr) {
    float A    = std::pow(10.f, gain_db / 40.f);
    float w0   = TAU * fc / sr;
    float cosw = std::cos(w0);
    float sinw = std::sin(w0);
    float al   = sinw / 2.f * std::sqrt((A + 1.f/A) * (1.f/1.f - 1.f) + 2.f);
    float sqA2 = 2.f * std::sqrt(A) * al;

    float a0 =        (A+1.f) + (A-1.f)*cosw + sqA2;
    float ia = 1.f / a0;

    BiquadCoeffs c;
    c.b0 = ( A * ((A+1.f) - (A-1.f)*cosw + sqA2)) * ia;
    c.b1 = ( 2.f*A * ((A-1.f) - (A+1.f)*cosw))    * ia;
    c.b2 = ( A * ((A+1.f) - (A-1.f)*cosw - sqA2)) * ia;
    c.a1 = (-2.f * ((A-1.f) + (A+1.f)*cosw))      * ia;
    c.a2 = (       (A+1.f) + (A-1.f)*cosw - sqA2)  * ia;
    return c;
}

// ── Gain envelope smoothing ──────────────────────────────────────────────────

/// Smooth a gain envelope toward a target using exponential approach.
///   current     = current envelope value
///   target      = desired envelope value
///   attack_coeff  = smoothing coefficient when reducing gain (fast)
///   release_coeff = smoothing coefficient when recovering gain (slow)
/// Returns the updated envelope value.
inline float gain_envelope_smooth(float current, float target,
                                  float attack_coeff, float release_coeff) {
    if (target < current)
        return attack_coeff  * current + (1.f - attack_coeff)  * target;
    else
        return release_coeff * current + (1.f - release_coeff) * target;
}

// ── RBJ bandpass filter coefficient computation ──────────────────────────────

/// Compute RBJ constant-skirt-gain bandpass biquad coefficients.
///   fc = center frequency [Hz]
///   Q  = quality factor (bandwidth = fc/Q)
///   sr = sample rate [Hz]
inline BiquadCoeffs rbj_bandpass(float fc, float Q, float sr) {
    float w0   = TAU * fc / sr;
    float sinw = std::sin(w0);
    float cosw = std::cos(w0);
    float al   = sinw / (2.f * Q);

    float a0 = 1.f + al;
    float ia = 1.f / a0;

    BiquadCoeffs c;
    c.b0 = ( al)           * ia;
    c.b1 =  0.f;
    c.b2 = (-al)           * ia;
    c.a1 = (-2.f * cosw)   * ia;
    c.a2 = ( 1.f - al)     * ia;
    return c;
}

} // namespace dsp
