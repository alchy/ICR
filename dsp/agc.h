#pragma once
/*
 * dsp/agc.h
 * ----------
 * Progressive voice gain (Automatic Gain Control).
 *
 * Stateless header-only AGC suitable for embedding in individual cores
 * or in the master bus.  Designed for easy portability to standalone
 * HW targets (ARM microcontrollers, DSP chips).
 *
 * Usage:
 *   AgcState agc;
 *   agc_init(agc, sample_rate);                 // once at init
 *   agc_process(agc, out_l, out_r, n_samples);  // per block, in-place
 *
 * The AGC measures per-block RMS and smoothly adjusts gain to keep the
 * signal near a target level.  It only attenuates (never amplifies),
 * preventing clipping from polyphonic voice accumulation without the
 * pumping artifacts of a brick-wall limiter.
 *
 * Parameters (compile-time or set after init):
 *   target_rms   — desired output RMS (default 0.15, ~ -16 dB)
 *   attack_ms    — gain reduction speed (default 5 ms)
 *   release_ms   — gain recovery speed (default 200 ms)
 *   gain_floor   — minimum gain (default 0.05, prevents silence)
 *
 * Dependencies: only <cmath> — no STL, no allocation, RT-safe.
 */

#include <cmath>

namespace dsp {

struct AgcState {
    float gain        = 1.f;     // current smoothed gain (RT state)
    float target_rms  = 0.15f;   // desired output RMS level
    float attack_coeff  = 0.f;   // per-sample smoothing (fast reduce)
    float release_coeff = 0.f;   // per-sample smoothing (slow recover)
    float gain_floor  = 0.05f;   // minimum gain (never fully silent)
};

/// Initialize AGC coefficients from sample rate and time constants.
///   attack_ms  — gain reduction time (default 5 ms)
///   release_ms — gain recovery time (default 200 ms)
inline void agc_init(AgcState& s, float sample_rate,
                     float attack_ms  = 5.f,
                     float release_ms = 200.f) {
    s.gain          = 1.f;
    s.attack_coeff  = 1.f - std::exp(-1.f / (attack_ms  * 0.001f * sample_rate));
    s.release_coeff = 1.f - std::exp(-1.f / (release_ms * 0.001f * sample_rate));
}

/// Process a stereo block in-place.
///
/// Measures RMS across both channels, computes target gain to reach
/// target_rms, then applies smoothed gain per sample.
///
/// - Never amplifies (gain capped at 1.0)
/// - Never silences (gain floored at gain_floor)
/// - Fast attack (respond to transients), slow release (avoid pumping)
inline void agc_process(AgcState& s, float* out_l, float* out_r,
                        int n_samples) noexcept {
    // Measure block RMS (both channels)
    float sum_sq = 0.f;
    for (int i = 0; i < n_samples; i++) {
        sum_sq += out_l[i] * out_l[i] + out_r[i] * out_r[i];
    }
    float rms = std::sqrt(sum_sq / (float)(n_samples * 2));

    // Compute target gain
    float target_gain = 1.f;
    if (rms > 1e-6f) {
        target_gain = s.target_rms / rms;
        if (target_gain > 1.f)          target_gain = 1.f;
        if (target_gain < s.gain_floor) target_gain = s.gain_floor;
    }

    // Smooth envelope: fast attack (reduce), slow release (recover)
    float coeff = (target_gain < s.gain) ? s.attack_coeff : s.release_coeff;

    // Apply per-sample with smoothing
    for (int i = 0; i < n_samples; i++) {
        s.gain += (target_gain - s.gain) * coeff;
        out_l[i] *= s.gain;
        out_r[i] *= s.gain;
    }
}

/// Reset AGC to unity gain (e.g. after core switch or silence).
inline void agc_reset(AgcState& s) {
    s.gain = 1.f;
}

} // namespace dsp
