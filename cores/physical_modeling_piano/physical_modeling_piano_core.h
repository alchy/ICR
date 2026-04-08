#pragma once
/*
 * cores/physical_modeling_piano/physical_modeling_piano_core.h
 * ------------------------------------------------------------
 * PhysicalModelingPianoCore -- Karplus-Strong string synthesis.
 *
 * Single-loop waveguide per voice:
 *   [Delay N] -> [Fractional Allpass] -> [Dispersion Cascade] -> [Loss LPF] -> loop
 *
 * Excitation: Fourier series with two-stage rolloff, odd harmonic boost,
 * gauge-dependent spectral shaping.  Per-note params from JSON bank.
 *
 * Current: one string per voice (multi-string beating in future iteration).
 *
 * Threading: same as ISynthCore -- see i_synth_core.h.
 */

#include "engine/i_synth_core.h"
#include "physical_modeling_piano_math.h"
#include "dsp/dsp_math.h"

#include <array>
#include <atomic>
#include <cstdint>
#include <mutex>
#include <random>
#include <string>
#include <vector>

// -- Constants ----------------------------------------------------------------

static constexpr int PHYS_MAX_VOICES     = 128;
static constexpr int PHYS_MAX_DISP       = 16;    // max dispersion allpass stages
static constexpr float PHYS_RELEASE_MS   = 200.f;
static constexpr float PHYS_ONSET_MS     = 0.3f;

// -- Per-note bank parameters -------------------------------------------------

struct PhysicsNoteParam {
    bool  valid          = false;
    int   midi           = 60;
    float f0_hz          = 261.6f;

    // String
    float B              = 4e-4f;    // inharmonicity
    float gauge          = 1.5f;     // string thickness
    float T60_fund       = 5.f;      // fundamental T60 (s)
    float T60_nyq        = 0.25f;    // Nyquist T60 (s)

    // Excitation
    float exc_rolloff    = 0.1f;     // harmonic rolloff (0=flat, 2=triangle)
    float exc_x0         = 1.f/7.f;  // strike position
    float odd_boost      = 1.8f;
    int   knee_k         = 10;
    float knee_slope     = 3.5f;
    int   n_harmonics    = 80;

    // Dispersion
    int   n_disp_stages  = 0;
    float disp_coeff     = -0.15f;

    // Multi-string (future)
    int   n_strings      = 1;        // currently always 1
    float detune_cents   = 1.f;
};

// -- Voice --------------------------------------------------------------------

class PhysicsVoice {
public:
    bool process(float* out_l, float* out_r, int n_samples) noexcept;

    bool     active      = false;
    bool     releasing   = false;
    int      midi        = -1;
    uint32_t t_samples   = 0;
    uint64_t max_t_samp  = 0;

    // Waveguide delay line
    physics::DelayLine delay;

    // Loop filters
    physics::LossFilter  loss;
    float  ap_frac_a     = 0.f;    // fractional delay allpass coeff
    float  ap_frac_state = 0.f;

    // Dispersion cascade
    int    n_disp        = 0;
    float  disp_coeff    = -0.15f;
    float  disp_states[PHYS_MAX_DISP] = {};

    // Output
    float  output_scale  = 1.f;
    float  pan_l         = 0.707f;
    float  pan_r         = 0.707f;

    // Envelope
    float  rel_gain      = 1.f;
    float  rel_step      = 0.f;
    float  onset_gain    = 0.f;
    float  onset_step    = 0.f;
    bool   in_onset      = false;

    // Hammer noise
    float  noise_amp     = 0.f;
    float  noise_env     = 1.f;
    float  noise_decay   = 0.f;
    dsp::BiquadCoeffs noise_bpf;
    float  noise_wL[2]   = {};
    float  noise_wR[2]   = {};
    std::mt19937 rng;
    std::normal_distribution<float> ndist{0.f, 1.f};
};

// -- VoiceManager -------------------------------------------------------------

class PhysicsVoiceManager {
public:
    bool processBlock(float* out_l, float* out_r, int n_samples) noexcept;

    void initVoice(int midi, uint8_t velocity,
                   const PhysicsNoteParam& np, float sr,
                   float keyboard_spread) noexcept;

    void releaseVoice(int midi, float sr) noexcept;
    void releaseAll(float sr) noexcept;

    PhysicsVoice&       voice(int midi)       { return voices_[midi]; }
    const PhysicsVoice& voice(int midi) const { return voices_[midi]; }

private:
    PhysicsVoice voices_[PHYS_MAX_VOICES];
};

// -- PatchManager -------------------------------------------------------------

class PhysicsPatchManager {
public:
    void noteOn(uint8_t midi, uint8_t velocity,
                PhysicsNoteParam note_params[],
                PhysicsVoiceManager& vm,
                float sample_rate,
                float keyboard_spread,
                std::mutex& bank_mutex) noexcept;

    void noteOff(uint8_t midi, PhysicsVoiceManager& vm, float sr) noexcept;
    void sustainPedal(bool down, PhysicsVoiceManager& vm, float sr) noexcept;
    void allNotesOff(PhysicsVoiceManager& vm, float sr) noexcept;

    int lastMidi() const { return last_midi_.load(std::memory_order_relaxed); }
    int lastVel()  const { return last_vel_.load(std::memory_order_relaxed); }

private:
    std::atomic<bool> sustain_{false};
    std::atomic<bool> delayed_offs_[PHYS_MAX_VOICES] = {};
    std::atomic<int>  last_midi_{-1};
    std::atomic<int>  last_vel_{0};
};

// -- PhysicalModelingPianoCore ------------------------------------------------

class PhysicalModelingPianoCore final : public ISynthCore {
public:
    PhysicalModelingPianoCore();

    bool load(const std::string& params_path, float sr, Logger& logger,
              int midi_from = 0, int midi_to = 127) override;
    void setSampleRate(float sr) override;

    void noteOn      (uint8_t midi, uint8_t velocity) override;
    void noteOff     (uint8_t midi)                   override;
    void sustainPedal(bool down)                      override;
    void allNotesOff ()                               override;

    bool processBlock(float* out_l, float* out_r, int n_samples) noexcept override;

    bool setParam(const std::string& key, float value)      override;
    bool getParam(const std::string& key, float& out) const override;
    std::vector<CoreParamDesc> describeParams()        const override;

    bool loadBankJson(const std::string& json_str) override;

    CoreVizState getVizState() const override;

    std::string coreName()    const override { return "PhysicalModelingPianoCore"; }
    std::string coreVersion() const override { return "0.3"; }
    bool        isLoaded()    const override { return loaded_; }

private:
    void populateDefaults(int midi_from, int midi_to);
    bool loadBankFromJson(const std::string& json_str, Logger& logger);

    PhysicsNoteParam note_params_[128];

    PhysicsVoiceManager voice_mgr_;
    PhysicsPatchManager patch_mgr_;

    float sample_rate_ = 48000.f;
    bool  loaded_      = false;

    // GUI-settable global scalers
    std::atomic<float> brightness_       {1.0f};   // scales T60_nyq
    std::atomic<float> stiffness_scale_  {1.0f};   // scales B
    std::atomic<float> sustain_scale_    {1.0f};   // scales T60_fund
    std::atomic<float> keyboard_spread_  {0.60f};
    std::atomic<float> odd_scale_        {1.0f};   // scales odd_boost
    std::atomic<float> gauge_scale_      {1.0f};   // scales gauge

    mutable std::mutex bank_mutex_;
};
