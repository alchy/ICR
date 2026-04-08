#pragma once
/*
 * convolver.h — Simple stereo convolution with a short impulse response.
 *
 * Applies a mono IR to L and R independently (direct time-domain convolution).
 * Designed for soundboard IRs (~100 ms, ~4800 samples at 48 kHz).
 *
 * The convolution adds the "body" and "warmth" that the additive synthesis
 * model cannot produce — soundboard resonances, room character, and
 * string-bridge coupling effects.
 *
 * Signal chain:  AdditiveSynthesisPianoCore → [EQ] → Convolver → BBE → Limiter → output
 */

#include <vector>
#include <string>

class Convolver {
public:
    /// Load IR from a mono WAV file.  Returns false on error.
    bool loadIR(const std::string& path, float sample_rate);

    /// Load IR from raw float samples.
    void setIR(const float* ir, int length);

    void setEnabled(bool on) { enabled_ = on; }
    bool isEnabled()   const { return enabled_; }

    /// Wet/dry mix: 0 = bypass, 1 = full convolution.
    void setMix(float mix) { mix_ = mix; }
    float getMix() const   { return mix_; }

    /// Process stereo block in-place.
    void process(float* L, float* R, int n_samples);

    /// Reset internal buffers (call after IR change or seek).
    void reset();

    int irLength() const { return (int)ir_.size(); }

private:
    std::vector<float> ir_;          // impulse response samples
    std::vector<float> buf_L_;       // input history buffer (ring)
    std::vector<float> buf_R_;
    int                write_pos_ = 0;
    float              mix_       = 0.02f; // default: subtle body coloring (GUI 50%)
    bool               enabled_   = false;
};
