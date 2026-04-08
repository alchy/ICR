#pragma once
/*
 * core_engine.h
 * ──────────────
 * Generic real-time engine wrapping an ISynthCore.
 * Replaces ResonatorEngine; works with any registered core.
 *
 * Responsibilities:
 *  - Create and own an ISynthCore (via SynthCoreRegistry)
 *  - Open audio device (miniaudio), run RT callback
 *  - Thread-safe MIDI queue (lock-free ring buffer)
 *  - Master gain / pan (post-core)
 *  - LFO panning (electric-piano style, post-core)
 *  - DspChain (limiter + BBE, master bus)
 *  - Peak metering
 *
 * Usage:
 *   CoreEngine engine;
 *   engine.initialize("ResonatorCore", "soundbanks/params-ks-grand-ft.json",
 *                     "soundbanks/params-ks-grand-ft.synth_config.json", logger);
 *   engine.start();
 *   engine.noteOn(60, 80);
 *   engine.stop();
 */

#include "i_synth_core.h"
#include "../dsp/dsp_chain.h"
#include "core_logger.h"
#include <memory>
#include <string>
#include <vector>
#include <unordered_map>
#include <atomic>
#include <cstdint>

struct ma_device;

static constexpr int CORE_ENGINE_DEFAULT_SR         = 48000;
static constexpr int CORE_ENGINE_DEFAULT_BLOCK_SIZE = 256;

class CoreEngine {
public:
    CoreEngine();
    ~CoreEngine();

    // ── Initialization ────────────────────────────────────────────────────────

    // Load engine config JSON (per-core paths, default_core, etc).
    // Call before initialize().  If not called, hardcoded fallbacks are used.
    bool loadEngineConfig(const std::string& config_path, Logger& logger);

    // Phase 1: instantiate core by name, load params, apply optional config JSON.
    // midi_from / midi_to: optional MIDI note range filter (inclusive).
    // Notes outside [midi_from, midi_to] are not loaded into the core.
    // If params_path is empty, uses the path from engine config (if loaded).
    bool initialize(const std::string& core_name,
                    const std::string& params_path,
                    const std::string& config_json_path,
                    Logger&            logger,
                    int                midi_from = 0,
                    int                midi_to   = 127);

    /// Get engine config value for a core.  Returns empty string if not found.
    std::string coreConfigValue(const std::string& core_name,
                                const std::string& key) const;

    /// Get default core name from engine config (empty if no config loaded).
    const std::string& defaultCoreName() const { return default_core_name_; }

    // Switch the active core at runtime.  MIDI events are routed to the active
    // core only, but ALL instantiated cores continue to processBlock (for dozvuk).
    // Cores are lazy-instantiated on first selection and kept alive in memory.
    // No audio interruption — the old core's voices decay naturally.
    bool switchCore(const std::string& core_name,
                    const std::string& params_path);

    // Phase 2: open audio device and start RT callback.
    bool start();

    // Phase 3: stop audio device (blocks until callback thread exits).
    void stop();

    bool isRunning()     const { return running_.load(); }
    bool isInitialized() const { return active_core_ && active_core_->isLoaded(); }

    // ── Thread-safe MIDI ──────────────────────────────────────────────────────
    void noteOn      (uint8_t midi, uint8_t velocity);
    void noteOff     (uint8_t midi);
    void sustainPedal(uint8_t val);  // >=64 = down
    void allNotesOff ();             // silence all voices immediately

    // ── Master mix ────────────────────────────────────────────────────────────
    void setMasterGain (uint8_t midi_val, Logger& logger);  // 0..127 → level
    void setMasterPan  (uint8_t midi_val) noexcept;         // 64 = center
    void setPanSpeed   (uint8_t midi_val) noexcept;         // 0..127 → 0..2 Hz
    void setPanDepth   (uint8_t midi_val) noexcept;         // 0..127 → 0..1

    // ── DSP chain ─────────────────────────────────────────────────────────────
    void setLimiterThreshold(uint8_t v) noexcept;
    void setLimiterRelease  (uint8_t v) noexcept;
    void setLimiterEnabled  (uint8_t v) noexcept;
    void setBBEDefinition   (uint8_t v) noexcept;
    void setBBEBassBoost    (uint8_t v) noexcept;

    // ── SysEx ─────────────────────────────────────────────────────────────────
    // Process an incoming SysEx message (called from MidiInput callback thread).
    // data: bytes AFTER the leading F0, BEFORE the trailing F7.
    // Returns a PONG response to send, or an empty vector if no response needed.
    std::vector<uint8_t> handleSysEx(const uint8_t* data, int len);

    // ── Offline batch render ───────────────────────────────────────────────────
    // Render a list of notes to mono int16 WAV files without starting the audio
    // device.  Call after initialize() but instead of start().
    //
    // batch_json_path: JSON array [{midi, vel_idx, duration_s}, ...] (vel_idx 0-7)
    // out_dir:         Directory for output WAVs  (m060_vel3.wav, ...)
    // sr:              Sample rate for render (default 48000)
    //
    // Returns number of notes successfully rendered; logs progress via logger_.
    int renderBatch(const std::string& batch_json_path,
                    const std::string& out_dir,
                    int                sr = 48000);

    // ── Accessors ─────────────────────────────────────────────────────────────
    ISynthCore*  core()        noexcept { return active_core_;  }
    const std::string& activeCoreName() const noexcept { return active_core_name_; }
    DspChain*    getDspChain() noexcept { return &dsp_;        }
    Logger&      getLogger()   noexcept { return logger_;      }

    // Access a specific instantiated core by name (nullptr if not yet created).
    ISynthCore* coreByName(const std::string& name) noexcept {
        auto it = cores_.find(name);
        return (it != cores_.end()) ? it->second.get() : nullptr;
    }

    int   activeVoices()     const;   // GUI thread only; allocates via getVizState
    float getOutputPeakLin() const noexcept { return output_peak_lin_.load(std::memory_order_relaxed); }

    uint8_t getLastNoteMidi() const noexcept { return last_note_midi_.load(std::memory_order_relaxed); }
    uint8_t getLastNoteVel()  const noexcept { return last_note_vel_ .load(std::memory_order_relaxed); }

    int sampleRate() const { return sample_rate_; }
    int blockSize()  const { return block_size_;  }

private:
    // ── MIDI event queue (lock-free SPSC ring, instance-local) ──────────────
    struct MidiEvt {
        enum Type : uint8_t { NOTE_ON, NOTE_OFF, SUSTAIN, ALL_NOTES_OFF } type;
        uint8_t midi;
        uint8_t value;
    };
    static constexpr int MIDI_Q_SIZE = 256;
    MidiEvt          midi_q_[MIDI_Q_SIZE];
    std::atomic<int> midi_w_{0};
    std::atomic<int> midi_r_{0};
    void pushMidiEvt(MidiEvt::Type t, uint8_t midi, uint8_t val) noexcept;

    static void audioCallback(ma_device* device, void* output,
                               const void* input, uint32_t frame_count);
    void processBlock(float* out_l, float* out_r, int n_samples) noexcept;
    void applyMasterAndLfo(float* out_l, float* out_r, int n_samples) noexcept;

    // Multi-core: all instantiated cores live here. MIDI goes to active_core_,
    // but processBlock iterates ALL cores (so releasing voices dozvuk naturally).
    std::unordered_map<std::string, std::unique_ptr<ISynthCore>> cores_;
    std::unordered_map<std::string, std::string> core_params_paths_;  // per-core params path
    ISynthCore*  active_core_      = nullptr;   // RT fast-path (never null after init)
    std::string  active_core_name_;

    DspChain                    dsp_;
    Logger                      logger_;

    // Engine config (loaded from JSON, per-core settings)
    std::FILE*  log_file_handle_ = nullptr;   // owned, closed in destructor
    std::string default_core_name_;
    // core_name -> {key -> value} from engine_config.json
    std::unordered_map<std::string,
        std::unordered_map<std::string, std::string>> core_config_;

    // Master mix — atomic so GUI thread writes are safe vs RT reads
    std::atomic<float> master_gain_{1.f};
    std::atomic<float> pan_l_      {1.f};
    std::atomic<float> pan_r_      {1.f};

    // LFO panning — speed/depth written by GUI, phase only touched by RT thread
    std::atomic<float> lfo_speed_  {0.f};   // Hz
    std::atomic<float> lfo_depth_  {0.f};   // 0..1
    float              lfo_phase_  = 0.f;   // RT thread only

    // Audio device
    ma_device*          device_      = nullptr;
    std::atomic<bool>   running_    {false};
    int                 sample_rate_ = CORE_ENGINE_DEFAULT_SR;
    int                 block_size_  = CORE_ENGINE_DEFAULT_BLOCK_SIZE;

    float* buf_l_ = nullptr;
    float* buf_r_ = nullptr;

    // Progressive voice gain (AGC) — smooth auto-gain based on signal energy.
    // Replaces hard limiting as primary dynamic control.
    // Target: keep RMS around agc_target_ regardless of voice count.
    float agc_gain_      = 1.f;    // current smoothed gain (RT thread only)
    float agc_target_    = 0.15f;  // target RMS level
    float agc_attack_    = 0.f;    // per-sample smoothing (computed in start())
    float agc_release_   = 0.f;

    void applyProgressiveGain(float* out_l, float* out_r, int n) noexcept;

    // Peak metering (audio → GUI, relaxed atomic)
    std::atomic<float> output_peak_lin_{0.f};
    float              peak_decay_coeff_ = 0.9878f;

    // Last note (written on noteOn, read by GUI)
    std::atomic<uint8_t> last_note_midi_{60};
    std::atomic<uint8_t> last_note_vel_ {80};

    // SET_BANK chunk reassembly state (MIDI callback thread only — not concurrent)
    std::string bank_chunk_buf_;
    int         bank_chunk_total_ = 0;
    int         bank_chunk_recv_  = 0;
};

// ── Convenience: full startup + interactive loop ──────────────────────────────
// Like runResonator, but selects core by name.
int runCoreEngine(Logger&            logger,
                  const std::string& core_name,
                  const std::string& params_path,
                  int                midi_port       = 0,
                  const std::string& config_json_path = "");
