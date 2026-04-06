#pragma once
/*
 * bbe.h — Simplified BBE Sonic Maximizer.
 *
 * Approximation of the BBE process using two RBJ biquad shelving filters:
 *   Definition : high shelf boost at 5 kHz   (0..12 dB, MIDI-controlled)
 *   Bass Boost : low  shelf boost at 180 Hz  (0..10 dB, MIDI-controlled)
 *
 * Applied to stereo L/R independently.
 * Filter math provided by dsp::rbj_high_shelf / dsp::rbj_low_shelf.
 */

#include "dsp/dsp_math.h"

class BBE {
public:
    void prepare(float sample_rate);
    void setDefinition(float gain_db);   // 0..12 dB
    void setBassBoost (float gain_db);   // 0..10 dB
    void setEnabled(bool on) { enabled_ = on; }
    bool isEnabled()   const { return enabled_; }

    void process(float* L, float* R, int n_samples);
    void reset();

private:
    float sample_rate_ = 48000.f;

    dsp::BiquadCoeffs def_coeff_{};
    dsp::BiquadState  def_state_l_{}, def_state_r_{};

    dsp::BiquadCoeffs bass_coeff_{};
    dsp::BiquadState  bass_state_l_{}, bass_state_r_{};

    float def_gain_db_  = 0.f;
    float bass_gain_db_ = 0.f;
    bool  enabled_      = false;
};
