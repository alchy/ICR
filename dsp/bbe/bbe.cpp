#include "bbe.h"

void BBE::prepare(float sample_rate) {
    sample_rate_ = sample_rate;
    def_coeff_   = dsp::rbj_high_shelf(5000.f, def_gain_db_,  sample_rate);
    bass_coeff_  = dsp::rbj_low_shelf (180.f,  bass_gain_db_, sample_rate);
    reset();
}

void BBE::setDefinition(float gain_db) {
    def_gain_db_ = gain_db;
    def_coeff_   = dsp::rbj_high_shelf(5000.f, gain_db, sample_rate_);
}

void BBE::setBassBoost(float gain_db) {
    bass_gain_db_ = gain_db;
    bass_coeff_   = dsp::rbj_low_shelf(180.f, gain_db, sample_rate_);
}

void BBE::reset() {
    def_state_l_  = {};
    def_state_r_  = {};
    bass_state_l_ = {};
    bass_state_r_ = {};
}

void BBE::process(float* L, float* R, int n_samples) {
    if (!enabled_) return;

    for (int i = 0; i < n_samples; i++) {
        L[i] = dsp::biquad_tick(L[i], def_coeff_,  def_state_l_);
        R[i] = dsp::biquad_tick(R[i], def_coeff_,  def_state_r_);
        L[i] = dsp::biquad_tick(L[i], bass_coeff_, bass_state_l_);
        R[i] = dsp::biquad_tick(R[i], bass_coeff_, bass_state_r_);
    }
}
