#pragma once
/*
 * cores/physical_modeling_piano/physical_modeling_piano_core.h
 * ────────────────────────────────────────────────────────────
 * PhysicalModelingPianoCore -- digital waveguide piano synthesis engine.
 *
 * Unlike AdditiveSynthesisPianoCore (additive, analysis-resynthesis),
 * PhysicalModelingPianoCore models the physical energy flow:
 *
 *   Hammer --> String waveguide --> Bridge junction --> Soundboard --> Air
 *       ^           | (delay + loss + dispersion)          |
 *       +-----------+ (reflection back into string)        +--> Stereo output
 *
 * Energy conservation (Kirchhoff-style):
 *   At every junction, sum of incoming and outgoing wave power = 0.
 *   Losses are applied explicitly via loop filter (damping) and
 *   radiation (soundboard coupling).  The total system energy
 *   monotonically decreases -- no energy is created or destroyed.
 *
 * Synthesis algorithm per string:
 *   1. Hammer generates excitation force F = K_H * max(0, xi-u)^p
 *   2. Force enters the string delay line at strike position x0
 *   3. String = two delay lines (right-going + left-going waves)
 *   4. At bridge: reflection (k_r) + transmission to soundboard (k_t)
 *   5. At nut: full reflection (-1, rigid termination)
 *   6. Loss filter in loop: frequency-dependent damping
 *   7. Dispersion allpass: inharmonicity from string stiffness
 *   8. Soundboard: bank of resonant modes excited by bridge force
 *   9. Output: sum of radiated soundboard modes + direct string radiation
 *
 * Multi-string: 1/2/3 strings per note, each with independent waveguide,
 * slightly detuned for beating.  Coupled at the bridge.
 *
 * Can be driven by:
 *   a) Pure physics defaults (no JSON needed -- playable out of the box)
 *   b) JSON parameters matching AdditiveSynthesisPianoCore format
 *      (f0_hz, B, tau1, tau2, etc.) with automatic translation to
 *      physical parameters
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

static constexpr int PHYS_MAX_VOICES = 128;
static constexpr float PHYS_RELEASE_MS = 200.f;    // damper fall time
static constexpr float PHYS_ONSET_MS   = 0.3f;     // DC-offset prevention

// -- Per-string waveguide state -----------------------------------------------

struct PhysicsString {
    // Delay lines: right-going and left-going travelling waves
    physics::DelayLine delay_r;  // nut -> bridge
    physics::DelayLine delay_l;  // bridge -> nut

    // Loop filter (frequency-dependent damping)
    physics::LossFilter loss;

    // Dispersion (inharmonicity)
    physics::DispersionFilter dispersion;

    // Frequency offset from nominal f0 (detuning for multi-string beating)
    float f0_hz = 440.f;

    // Bridge junction coefficients
    physics::JunctionCoeffs junction;

    // Hammer felt low-pass: filters excitation before injection.
    // Simulates felt compliance — harder hammer = higher cutoff = brighter.
    float felt_lp_state = 0.f;    // one-pole LPF state
    float felt_lp_coeff = 0.5f;   // alpha: 0=no filter, 1=bypass

    // Hammer interaction point (as delay tap position)
    int strike_tap = 0;  // delay_r position for hammer contact

    // Last string displacement at hammer contact point
    float u_at_hammer = 0.f;
};

// -- Per-note physical parameters ---------------------------------------------

struct PhysicsNoteParam {
    bool  valid       = false;
    float f0_hz       = 440.f;
    float B           = 0.f;     // inharmonicity
    int   n_strings   = 3;
    float detune_cents = 0.5f;

    // Hammer parameters (Chabassier-style)
    float K_H          = 1e9f;   // felt stiffness
    float p            = 2.5f;   // nonlinear exponent
    float M_H          = 0.009f; // hammer mass (kg)
    float x0_ratio     = 0.125f; // strike position (fraction of string length)

    // String damping
    float tau_fund     = 10.f;   // fundamental decay time (s)
    float tau_high     = 1.f;    // high-frequency decay time (s)

    // Bridge coupling
    float impedance_ratio = 0.01f;  // Z_string / Z_soundboard

    // Output gain
    float gain         = 1.f;
};

// -- Voice --------------------------------------------------------------------

class PhysicsVoice {
public:
    /// Process this voice for n_samples, adding output to out_l/out_r.
    /// Returns false when voice has become inactive.
    bool process(float* out_l, float* out_r, int n_samples,
                 float inv_sr, float dt) noexcept;

    // -- State ----------------------------------------------------------------
    bool     active     = false;
    bool     releasing  = false;
    int      midi       = -1;
    uint32_t t_samples  = 0;
    uint64_t max_t_samp = 0;

    // Strings (1, 2, or 3 waveguides)
    int n_strings = 1;
    PhysicsString strings[physics::MAX_STRINGS];

    // Hammer (shared across all strings of this note)
    physics::HammerState hammer;

    // Soundboard modes (shared across strings)
    physics::SoundboardMode sb_modes[physics::SOUNDBOARD_MODES];

    // Output gain and panning
    float gain     = 1.f;
    float pan_l    = 0.707f;
    float pan_r    = 0.707f;

    // Release: damper simulation (increases loss dramatically)
    float rel_gain = 1.f;
    float rel_step = 0.f;  // negative step for fadeout

    // Onset gate (DC prevention)
    float onset_gain = 0.f;
    float onset_step = 0.f;
    bool  in_onset   = false;

    // String mixing gain (1/n_strings)
    float string_mix = 1.f;

    // Impedance-matched injection scale: F / (2*Z_string) → wave variable
    // Precomputed at initVoice from f0 and sample rate.
    float injection_scale = 0.f;

    // Output scale: compensates for small bridge transmission coefficient k_t
    // so that the final output reaches audible levels.
    float output_scale = 1.f;

    // Hammer noise: short burst of bandpass-filtered noise at attack.
    // Models the physical "thwack" of felt hitting steel — adds punch
    // and brightness to the onset that the waveguide alone cannot produce.
    float noise_amp     = 0.f;   // initial noise amplitude
    float noise_env     = 1.f;   // exponential decay envelope
    float noise_decay   = 0.f;   // per-sample decay coefficient
    dsp::BiquadCoeffs noise_bpf;       // bandpass filter coefficients
    float noise_wL[2] = {};            // DF-II state L
    float noise_wR[2] = {};            // DF-II state R
    std::mt19937 rng;
    std::normal_distribution<float> ndist{0.f, 1.f};
};

// -- VoiceManager -------------------------------------------------------------

class PhysicsVoiceManager {
public:
    bool processBlock(float* out_l, float* out_r, int n_samples,
                      float inv_sr, float dt) noexcept;

    void initVoice(int midi, const PhysicsNoteParam& np,
                   uint8_t velocity, float sr,
                   float keyboard_spread,
                   float hammer_hardness,
                   float damping_scale,
                   float brightness,
                   float detune_scale,
                   float soundboard_mix) noexcept;

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
                float hammer_hardness,
                float damping_scale,
                float brightness,
                float detune_scale,
                float soundboard_mix,
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

// -- PhysicalModelingPianoCore -- ISynthCore implementation --------------------

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

    CoreVizState getVizState() const override;

    std::string coreName()    const override { return "PhysicalModelingPianoCore"; }
    std::string coreVersion() const override { return "0.1"; }
    bool        isLoaded()    const override { return loaded_; }

private:
    /// Populate note_params_[midi] from physical defaults.
    void populateDefaults(int midi_from, int midi_to);

    /// Populate note_params_ from AdditiveSynthesisPianoCore-format JSON
    /// (translates additive params to physical equivalents).
    bool loadFromAdditiveSynthesisJson(const std::string& json_str,
                                       Logger& logger,
                                       int midi_from, int midi_to);

    // Note parameters (one per MIDI note -- no velocity layers in physics model,
    // velocity controls hammer speed directly)
    PhysicsNoteParam note_params_[128];

    // Three-layer architecture
    PhysicsVoiceManager voice_mgr_;
    PhysicsPatchManager patch_mgr_;

    float sample_rate_ = 44100.f;
    float inv_sr_      = 1.f / 44100.f;
    float dt_          = 1.f / 44100.f;
    bool  loaded_      = false;

    // GUI-settable parameters
    std::atomic<float> hammer_hardness_  {1.0f};   // scales K_H
    std::atomic<float> damping_scale_    {1.0f};   // scales tau_fund/tau_high
    std::atomic<float> soundboard_mix_   {1.0f};   // soundboard contribution
    std::atomic<float> brightness_       {1.0f};   // scales tau_high (more = brighter)
    std::atomic<float> keyboard_spread_  {0.60f};  // stereo width
    std::atomic<float> detune_scale_     {1.0f};   // scales detuning

    mutable std::mutex bank_mutex_;
};
