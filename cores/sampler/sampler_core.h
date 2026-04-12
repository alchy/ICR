#pragma once
/*
 * cores/sampler/sampler_core.h
 * ----------------------------
 * SamplerCore -- WAV sample playback engine.
 *
 * Voice pool architecture: N voices (default 32) allocated from a free-list.
 * Multiple voices can play the same MIDI pitch simultaneously (sustain pedal
 * compatible).  When the pool is full, the quietest releasing voice is stolen.
 *
 * Sample directory: soundbanks-sampler/{bank_name}/
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
#include <thread>
#include <vector>

// -- Constants ----------------------------------------------------------------

static constexpr int SAMPLER_DEFAULT_POOL_SIZE = 32;
static constexpr int SAMPLER_MAX_POOL_SIZE     = 128;
static constexpr int SAMPLER_VEL_LAYERS        = 8;
static constexpr float SAMPLER_ATTACK_MS       = 3.f;    // click-prevention onset
static constexpr float SAMPLER_RELEASE_MS      = 200.f;  // key-release fadeout
static constexpr float SAMPLER_DAMPING_MS      = 21.f;   // retrigger crossfade

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

    // Sample playback — two layers for velocity crossfade
    const SampleBuffer* sample_lo = nullptr;
    const SampleBuffer* sample_hi = nullptr;
    float    vel_blend   = 0.f;
    int      position    = 0;

    // Envelope
    float    vel_gain    = 1.f;
    float    onset_gain  = 0.f;
    float    onset_step  = 0.f;
    float    rel_gain    = 1.f;
    float    rel_step    = 0.f;

    // Damping buffer for click-free retrigger
    float    damp_buf[2 * 2048] = {};
    int      damp_len    = 0;
    int      damp_pos    = 0;
    bool     damping     = false;

    // Stereo pan
    float    pan_l       = 0.707f;
    float    pan_r       = 0.707f;

    // Current envelope level (for voice stealing priority)
    float currentEnvLevel() const noexcept {
        float env = vel_gain;
        if (in_onset) env *= onset_gain;
        if (releasing) env *= rel_gain;
        return env;
    }
};

// -- VoiceManager (voice pool) ------------------------------------------------

class SamplerVoiceManager {
public:
    explicit SamplerVoiceManager(int pool_size = SAMPLER_DEFAULT_POOL_SIZE);

    bool processBlock(float* out_l, float* out_r, int n_samples,
                      float sample_rate) noexcept;

    // Allocate a voice from the pool and initialize it.
    // Returns the pool index, or -1 if allocation failed.
    int allocVoice(int midi, uint8_t velocity,
                   const SampleBuffer* sample_lo,
                   const SampleBuffer* sample_hi,
                   float vel_blend,
                   float sample_rate,
                   float keyboard_spread) noexcept;

    // Release all voices playing the given MIDI note.
    void releaseNote(int midi, float sample_rate, float release_ms) noexcept;

    // Release all active voices.
    void releaseAll(float sample_rate, float release_ms) noexcept;

    int  poolSize()    const { return pool_size_; }
    int  activeCount() const noexcept;
    const SamplerVoice& poolVoice(int idx) const { return voices_[idx]; }

private:
    // Find a free voice slot, or steal the quietest releasing voice.
    int findFreeSlot() noexcept;

    SamplerVoice voices_[SAMPLER_MAX_POOL_SIZE];
    int          pool_size_;
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

    void noteOff(uint8_t midi, SamplerVoiceManager& vm, float sr,
                 float release_ms = 200.f) noexcept;
    void sustainPedal(bool down, SamplerVoiceManager& vm, float sr,
                      float release_ms = 200.f) noexcept;
    void allNotesOff(SamplerVoiceManager& vm, float sr,
                     float release_ms = 200.f) noexcept;

    int lastMidi() const { return last_midi_.load(std::memory_order_relaxed); }
    int lastVel()  const { return last_vel_.load(std::memory_order_relaxed); }

    static int velToLayer(uint8_t velocity) {
        return std::min(7, (int)(velocity - 1) / 16);
    }

private:
    std::atomic<bool> sustain_{false};
    std::atomic<bool> delayed_offs_[128] = {};   // indexed by MIDI note
    std::atomic<int>  last_midi_{-1};
    std::atomic<int>  last_vel_{0};
};

// -- SamplerCore -- ISynthCore implementation ---------------------------------

class SamplerCore final : public ISynthCore {
public:
    SamplerCore();
    ~SamplerCore();

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
    std::string coreVersion() const override { return "2.0"; }
    bool        isLoaded()    const override { return loaded_; }

    // -- Bank management (called from GUI thread) -----------------------------
    const std::vector<std::string>& bankNames() const { return bank_names_; }
    const std::string& activeBankName() const { return active_bank_name_; }
    bool selectBank(const std::string& name, Logger& logger);
    bool isBankLoading() const { return bank_loading_.load(std::memory_order_relaxed); }

private:
    void discoverBanks(const std::string& base_dir);
    bool loadBank(SampleBank& bank, float sr, Logger& logger);

    std::vector<SampleBank> banks_;
    std::vector<std::string> bank_names_;
    std::string active_bank_name_;
    int active_bank_idx_ = -1;

    SamplerVoiceManager voice_mgr_;
    SamplerPatchManager patch_mgr_;

    float sample_rate_ = 48000.f;
    bool  loaded_      = false;

    std::atomic<float> gain_            {1.0f};
    std::atomic<float> keyboard_spread_ {0.60f};
    std::atomic<float> release_time_    {1.0f};

    mutable std::mutex bank_mutex_;

    std::atomic<bool> bank_loading_{false};
    std::thread       load_thread_;
    Logger            load_logger_;
};
