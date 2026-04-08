#pragma once
/*
 * cores/sampler/sampler_core.h
 * ----------------------------
 * SamplerCore -- WAV sample playback engine.
 *
 * Loads WAV sample banks from disk (IthacaPlayer format: mXXX-velY-fZZ.wav)
 * and plays them back with per-voice envelope, velocity layers, and stereo
 * panning.  Supports bank switching at runtime.
 *
 * Sample directory: C:\SoundBanks\IthacaPlayer\{bank_name}\
 * Each subdirectory with at least one matching WAV file is a selectable bank.
 *
 * Threading: same as ISynthCore -- see i_synth_core.h.
 */

#include "engine/i_synth_core.h"
#include "dsp/dsp_math.h"
#include <array>
#include <atomic>
#include <cstdint>
#include <mutex>
#include <string>
#include <vector>

// -- Constants ----------------------------------------------------------------

static constexpr int SAMPLER_MAX_VOICES   = 128;
static constexpr int SAMPLER_VEL_LAYERS   = 8;
static constexpr float SAMPLER_ATTACK_MS  = 3.f;    // click-prevention onset
static constexpr float SAMPLER_RELEASE_MS = 200.f;  // key-release fadeout
static constexpr float SAMPLER_DAMPING_MS = 21.f;   // retrigger crossfade

// -- Sample buffer ------------------------------------------------------------

struct SampleBuffer {
    std::vector<float> data;   // interleaved stereo [L0,R0,L1,R1,...]
    int   frames      = 0;    // number of stereo frames
    int   sample_rate = 0;
    bool  loaded      = false;
};

// -- Bank structure -----------------------------------------------------------

struct SampleBank {
    std::string name;           // directory name (display name)
    std::string path;           // full path to directory
    // samples[midi][vel_idx] -- 128 notes x 8 velocity layers
    SampleBuffer samples[128][SAMPLER_VEL_LAYERS];
    int  vel_layers_available[128] = {};  // how many vel layers per note
    bool loaded = false;
};

// -- Voice --------------------------------------------------------------------

class SamplerVoice {
public:
    bool process(float* out_l, float* out_r, int n_samples,
                 float sample_rate) noexcept;

    // State
    bool     active      = false;
    bool     releasing   = false;
    bool     in_onset    = false;
    int      midi        = -1;
    uint8_t  velocity    = 0;

    // Sample playback
    const SampleBuffer* sample = nullptr;
    int      position    = 0;     // current frame position in sample

    // Envelope
    float    vel_gain    = 1.f;   // velocity-scaled amplitude
    float    onset_gain  = 0.f;   // onset ramp 0->1
    float    onset_step  = 0.f;
    float    rel_gain    = 1.f;   // release ramp 1->0
    float    rel_step    = 0.f;

    // Damping buffer for click-free retrigger
    float    damp_buf[2 * 2048] = {};  // stereo interleaved, max ~21ms at 96kHz
    int      damp_len    = 0;
    int      damp_pos    = 0;
    bool     damping     = false;

    // Stereo pan
    float    pan_l       = 0.707f;
    float    pan_r       = 0.707f;
};

// -- VoiceManager -------------------------------------------------------------

class SamplerVoiceManager {
public:
    bool processBlock(float* out_l, float* out_r, int n_samples,
                      float sample_rate) noexcept;

    void initVoice(int midi, uint8_t velocity,
                   const SampleBuffer* sample,
                   float sample_rate,
                   float keyboard_spread) noexcept;

    void releaseVoice(int midi, float sample_rate) noexcept;
    void releaseAll(float sample_rate) noexcept;

    SamplerVoice&       voice(int midi)       { return voices_[midi]; }
    const SamplerVoice& voice(int midi) const { return voices_[midi]; }

private:
    SamplerVoice voices_[SAMPLER_MAX_VOICES];
};

// -- PatchManager -------------------------------------------------------------

class SamplerPatchManager {
public:
    void noteOn(uint8_t midi, uint8_t velocity,
                SampleBank& bank,
                SamplerVoiceManager& vm,
                float sample_rate,
                float keyboard_spread,
                std::mutex& bank_mutex) noexcept;

    void noteOff(uint8_t midi, SamplerVoiceManager& vm, float sr) noexcept;
    void sustainPedal(bool down, SamplerVoiceManager& vm, float sr) noexcept;
    void allNotesOff(SamplerVoiceManager& vm, float sr) noexcept;

    int lastMidi() const { return last_midi_.load(std::memory_order_relaxed); }
    int lastVel()  const { return last_vel_.load(std::memory_order_relaxed); }

    /// Map MIDI velocity 1-127 to velocity layer index 0-7
    static int velToLayer(uint8_t velocity) {
        return std::min(7, (int)(velocity - 1) / 16);
    }

private:
    std::atomic<bool> sustain_{false};
    std::atomic<bool> delayed_offs_[SAMPLER_MAX_VOICES] = {};
    std::atomic<int>  last_midi_{-1};
    std::atomic<int>  last_vel_{0};
};

// -- SamplerCore -- ISynthCore implementation ---------------------------------

class SamplerCore final : public ISynthCore {
public:
    SamplerCore();

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

    std::string coreName()    const override { return "SamplerCore"; }
    std::string coreVersion() const override { return "1.0"; }
    bool        isLoaded()    const override { return loaded_; }

    // -- Bank management (called from GUI thread) -----------------------------
    /// Get list of discovered bank names.
    const std::vector<std::string>& bankNames() const { return bank_names_; }
    /// Get currently active bank name.
    const std::string& activeBankName() const { return active_bank_name_; }
    /// Switch to a different bank by name.  Loads from disk if not yet loaded.
    bool selectBank(const std::string& name, Logger& logger);

private:
    /// Scan base directory for sample banks.
    void discoverBanks(const std::string& base_dir);
    /// Load a bank's WAV files from disk.
    bool loadBank(SampleBank& bank, float sr, Logger& logger);

    // All discovered banks (lazy-loaded)
    std::vector<SampleBank> banks_;
    std::vector<std::string> bank_names_;
    std::string active_bank_name_;
    int active_bank_idx_ = -1;

    // Three-layer architecture
    SamplerVoiceManager voice_mgr_;
    SamplerPatchManager patch_mgr_;

    float sample_rate_ = 48000.f;
    bool  loaded_      = false;

    // GUI parameters
    std::atomic<float> gain_            {1.0f};
    std::atomic<float> keyboard_spread_ {0.60f};
    std::atomic<float> release_time_    {1.0f};   // release multiplier

    mutable std::mutex bank_mutex_;
};
