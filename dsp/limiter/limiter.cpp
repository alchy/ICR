#include "limiter.h"
#include <algorithm>

static constexpr float ATTACK_MS = 1.f;   // fixed fast attack

void Limiter::prepare(float sample_rate, int /*max_block_size*/) {
    sample_rate_   = sample_rate;
    attack_coeff_  = dsp::decay_coeff(ATTACK_MS * 0.001f, sample_rate);
    release_coeff_ = dsp::decay_coeff(200.f * 0.001f, sample_rate);   // default 200 ms
    gain_          = 1.f;
}

void Limiter::setThresholdDb(float db) {
    threshold_lin_ = dsp::db_to_lin(db);
}

void Limiter::setReleaseMs(float ms) {
    ms = std::max(10.f, std::min(ms, 2000.f));
    release_coeff_ = dsp::decay_coeff(ms * 0.001f, sample_rate_);
}

void Limiter::process(float* L, float* R, int n_samples) {
    if (!enabled_) {
        gain_red_db_ = 0.f;
        return;
    }

    for (int i = 0; i < n_samples; i++) {
        float peak = std::max(std::abs(L[i]), std::abs(R[i]));

        // Desired gain: reduce if peak > threshold
        float target = (peak > threshold_lin_ && peak > 1e-9f)
                     ? threshold_lin_ / peak
                     : 1.f;

        // Smooth envelope: attack if reducing, release if recovering
        gain_ = dsp::gain_envelope_smooth(gain_, target,
                                          attack_coeff_, release_coeff_);
        gain_ = std::min(gain_, 1.f);

        L[i] *= gain_;
        R[i] *= gain_;
    }

    // Meter: gain reduction in dB
    gain_red_db_ = dsp::lin_to_db(std::max(gain_, 1e-9f));
}

float Limiter::gainReductionDb() const {
    return gain_red_db_;
}
