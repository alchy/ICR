#pragma once
/*
 * cores/additive_synthesis_piano/additive_synthesis_piano_core.h
 * ──────────────────────────────────────────────────────────────
 * AdditiveSynthesisPianoCore — additive piano synthesis engine.
 *
 * Synthesis algorithm (per-MIDI string model):
 *  - MIDI ≤ 27 (bass):   1-string   partial = A0·env·cos(2π·f·t + φ)
 *  - MIDI 28–48 (tenor): 2-string   s1=cos(2π·(f+b/2)·t + φ)
 *                                    s2=cos(2π·(f−b/2)·t + φ+φ_diff)
 *                                    partial = A0·env·(s1+s2)/2
 *  - MIDI > 48 (treble): 3-string symmetric
 *                                    s1=cos(2π·(f−b)·t + φ)       outer left
 *                                    s2=cos(2π·f·t + φ2)          centre (φ2 random)
 *                                    s3=cos(2π·(f+b)·t + φ+φ_diff) outer right
 *                                    partial = A0·env·(s1+s2+s3)/3
 *  - Bi-exponential envelope: a1·exp(−t/τ1) + (1−a1)·exp(−t/τ2)
 *  - Gaussian noise:  A_noise·randn()·exp(−t/attack_tau), biquad bandpass colour
 *  - Spectral EQ:     min-phase biquad cascade (Direct Form II)
 *  - M/S correction:  S *= stereo_width post-EQ
 *  - RMS normalisation: per-note rms_gain pre-computed at export time
 *
 * Parameters are loaded from a JSON soundbank exported by
 *   training/modules/exporter.py
 *
 * Threading: same as ISynthCore — see i_synth_core.h.
 */

#include "engine/i_synth_core.h"
#include "dsp/dsp_math.h"
#include <array>
#include <atomic>
#include <cstdint>
#include <cmath>
#include <mutex>
#include <random>
#include <vector>
#include <unordered_map>
#include <string>

// ── Internal constants ────────────────────────────────────────────────────────

static constexpr int   PIANO_MAX_PARTIALS = 60;
static constexpr int   PIANO_MAX_VOICES   = 128;   // one slot per MIDI note
static constexpr int   PIANO_N_BIQUAD     = 10;    // spectral EQ cascade
static constexpr float PIANO_RELEASE_MS   = 100.f; // key-release fade-out
static constexpr float PIANO_ONSET_MS     = 0.5f;  // click-prevention onset (minimal)
static constexpr float PIANO_SKIP_THRESH  = 2e-7f; // skip silent partials

// ── Biquad coefficients — alias for dsp::BiquadCoeffs ────────────────────────

using PianoBiquadCoeffs = dsp::BiquadCoeffs;

struct PianoPartialParam {
    int   k        = 0;     // partial index, 1-based; used for B recomputation
    float f_hz     = 0.f;
    float A0       = 0.f;
    float tau1     = 0.f;
    float tau2     = 0.f;
    float a1       = 1.f;
    float beat_hz  = 0.f;
    float phi      = 0.f;   // initial phase (precomputed, matching Python RNG)
    // Extraction diagnostics (loaded from JSON, used by GUI only — no RT impact)
    float fit_quality    = 0.f;  // 0..1, 1=perfect fit
    bool  damping_derived = false; // true = tau1 was replaced by damping law
};

struct PianoNoteParam {
    bool  valid           = false;
    bool  is_interpolated = false;   // true = NN-generated, false = measured
    int   K                  = 0;
    float phi_diff           = 0.f;
    float attack_tau         = 0.05f;
    float A_noise            = 0.04f;
    float noise_centroid_hz  = 3000.f; // biquad bandpass center frequency for noise shaping
    float rms_gain           = 1.f;
    float stereo_width       = 1.f;   // M/S correction: S *= stereo_width post-EQ; M unchanged
    float f0_hz              = 440.f;
    float B                  = 0.f;   // inharmonicity; kept so setNoteParam("B") can recompute f_hz[k]
    // Per-note synthesis parameters (override hardcoded defaults if present in JSON)
    float rise_tau           = -1.f;  // attack rise time (s); -1 = use hardcoded midi-based default
    int   n_strings          = -1;    // 1/2/3 string model; -1 = use hardcoded midi-based default
    float decor_strength     = -1.f;  // Schroeder decorrelation; -1 = use hardcoded midi-based default
    // Spectral EQ: min-phase IIR fitted from soundbank spectral_eq curve
    int              n_biquad = 0;
    PianoBiquadCoeffs eq[PIANO_N_BIQUAD];
    PianoPartialParam partials[PIANO_MAX_PARTIALS];
};

// ── Voice runtime state ───────────────────────────────────────────────────────

struct PianoPartialState {
    // Exponential decay state
    float env_fast   = 1.f;
    float env_slow   = 1.f;
    float decay_fast = 0.f;   // exp(-1/(tau1*sr))
    float decay_slow = 0.f;   // exp(-1/(tau2*sr))
    // Precomputed at noteOn (const during voice lifetime)
    float A0_scaled   = 0.f;  // A0 * rms_gain
    float a1          = 1.f;
    float f_hz        = 0.f;
    float beat_hz_h   = 0.f;  // beat_hz * beat_scale * 0.5
    float phi         = 0.f;
    float phi2        = 0.f;  // center string phase (3-string model, MIDI > 48)
    // Phase offset: string 3 (outer right) relative to string 1 (outer left).
    // For 2-string model: offset between string 1 and string 2.
    float phi_diff    = 0.f;
};

class PianoVoice {
public:
    /// Process this voice for n_samples, adding output to out_l/out_r.
    /// Returns false when voice has become inactive (can be reclaimed).
    bool process(float* out_l, float* out_r, int n_samples, float inv_sr) noexcept;

    // ── State (public for initVoice access — will be encapsulated later) ──
    bool     active      = false;
    bool     releasing   = false;
    bool     in_onset    = false;
    int      midi        = -1;
    int      vel_idx     = -1;
    uint32_t t_samples   = 0;
    uint64_t max_t_samp  = 0;  // auto-stop (silence) threshold

    // phi_diff (constant per note, loaded from params)
    float phi_diff       = 0.f;

    // Noise state — biquad bandpass filter (hammer noise shaping)
    float A_noise_sc     = 0.f;  // A_noise * rms_gain * noise_level
    float noise_env      = 1.f;
    float noise_decay    = 0.f;
    dsp::BiquadCoeffs noise_bpf;        // bandpass coefficients
    dsp::BiquadState  noise_bpf_L;      // filter state — left channel
    dsp::BiquadState  noise_bpf_R;      // filter state — right channel

    // Release / onset ramps
    float rel_gain       = 1.f;
    float rel_step       = 0.f;
    float onset_gain     = 0.f;
    float onset_step     = 0.f;

    // Attack rise envelope: 1 - exp(-t / rise_tau)
    // Models the physical string excitation rise time (~1-5 ms for bass,
    // <1 ms for treble).  Multiplies partials only — noise bypasses this.
    float rise_coeff     = 1.f;  // per-sample: exp(-1 / (rise_tau * sr))
    float rise_env       = 0.f;  // current rise level, approaches 1.0

    // Noise PRNG (independent of Python RNG — noise not required to match exactly)
    std::mt19937 rng;
    std::normal_distribution<float> ndist{0.f, 1.f};

    // String model: 1 (bass MIDI≤27), 2 (tenor 28–48), 3 (treble MIDI>48)
    int n_model_strings = 2;

    // Stereo pan gains (constant-power; precomputed at noteOn from MIDI + pan_spread)
    // 1-string: gl1/gr1 = center
    // 2-string: gl1/gr1 = center-half,  gl2/gr2 = center+half
    // 3-string: gl1/gr1 = center-half,  gl2/gr2 = center,  gl3/gr3 = center+half
    float gl1 = 0.707f, gr1 = 0.707f;
    float gl2 = 0.707f, gr2 = 0.707f;
    float gl3 = 0.f,    gr3 = 0.f;

    // Schroeder all-pass decorrelation state (first-order IIR, independent per channel)
    float decor_str = 0.f;
    float ap_g_L    = 0.f;
    float ap_g_R    = 0.f;
    float ap_x_L    = 0.f;
    float ap_y_L    = 0.f;
    float ap_x_R    = 0.f;
    float ap_y_R    = 0.f;

    // M/S stereo width correction (post-EQ): S *= stereo_width; M unchanged
    float stereo_width  = 1.f;

    // Spectral EQ biquad cascade (Direct Form II, independent L/R state)
    int               n_biquad    = 0;
    float             eq_strength = 1.f;   // blend 0=bypass 1=full (snapshot at noteOn)
    PianoBiquadCoeffs eq_coeffs[PIANO_N_BIQUAD];
    float             eq_wL[PIANO_N_BIQUAD][2] = {};
    float             eq_wR[PIANO_N_BIQUAD][2] = {};

    // Active partial state
    int n_partials = 0;
    PianoPartialState partials[PIANO_MAX_PARTIALS];
};

// ── VoiceManager — lifecycle and processing of voice pool ────────────────────

class PianoVoiceManager {
public:
    /// Process all active voices, adding to output buffers.
    bool processBlock(float* out_l, float* out_r, int n_samples, float inv_sr) noexcept;

    /// Initialize a voice with interpolated parameters.
    void initVoice(int midi, int vel_idx, const PianoNoteParam& np,
                   float beat_scale, float noise_level, int rng_seed,
                   float pan_spread, float stereo_decorr,
                   float keyboard_spread, float sample_rate,
                   float eq_strength, float vel_norm) noexcept;

    /// Begin release phase for a voice.
    void releaseVoice(int midi, float sample_rate) noexcept;

    /// Release all active voices.
    void releaseAll(float sample_rate) noexcept;

    PianoVoice&       voice(int midi)       { return voices_[midi]; }
    const PianoVoice& voice(int midi) const { return voices_[midi]; }

private:
    PianoVoice voices_[PIANO_MAX_VOICES];
};

// ── PatchManager — MIDI to native parameter translation ─────────────────────

class PianoPatchManager {
public:
    /// Translate MIDI noteOn to native voice parameters and trigger.
    void noteOn(uint8_t midi, uint8_t velocity,
                PianoNoteParam note_params[][8],
                PianoVoiceManager& vm,
                float sample_rate, float beat_scale, float noise_level,
                int rng_seed, float pan_spread, float stereo_decorr,
                float keyboard_spread, float eq_strength,
                std::mutex& bank_mutex) noexcept;

    /// Translate MIDI noteOff.
    void noteOff(uint8_t midi, PianoVoiceManager& vm, float sample_rate) noexcept;

    /// Handle sustain pedal.
    void sustainPedal(bool down, PianoVoiceManager& vm, float sample_rate) noexcept;

    /// Release all voices.
    void allNotesOff(PianoVoiceManager& vm, float sample_rate) noexcept;

    /// Last triggered note info (for GUI).
    int  lastMidi()   const { return last_midi_.load(std::memory_order_relaxed); }
    int  lastVel()    const { return last_vel_.load(std::memory_order_relaxed); }
    int  lastVelIdx() const { return last_vel_idx_.load(std::memory_order_relaxed); }

    /// Map MIDI velocity 1-127 to vel index 0-7
    static int midiVelToIdx(uint8_t velocity) {
        return std::min(7, (int)(velocity - 1) / 16);
    }

    /// Map MIDI velocity 1-127 to continuous float position 0.0-7.0
    static float midiVelToFloat(uint8_t velocity) {
        return std::min(7.f, (float)(velocity - 1) / 16.f);
    }

    /// Interpolate two PianoNoteParam structs by factor t (0=a, 1=b).
    static PianoNoteParam lerpNoteParams(const PianoNoteParam& a,
                                         const PianoNoteParam& b,
                                         float t) noexcept;

private:
    std::atomic<bool> sustain_          {false};
    std::atomic<bool> delayed_offs_[PIANO_MAX_VOICES] = {};

    std::atomic<int>  last_midi_     {-1};
    std::atomic<int>  last_vel_      {0};
    std::atomic<int>  last_vel_idx_  {0};
};

// ── AdditiveSynthesisPianoCore — top-level ISynthCore implementation ─────────

class AdditiveSynthesisPianoCore final : public ISynthCore {
public:
    AdditiveSynthesisPianoCore();

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

    bool setNoteParam(int midi, int vel,
                      const std::string& key, float value)          override;
    bool setNotePartialParam(int midi, int vel, int k,
                             const std::string& key, float value)   override;
    bool loadBankJson(const std::string& json_str)                  override;
    bool exportBankJson(const std::string& path)                    override;

    CoreVizState getVizState() const override;

    std::string coreName()    const override { return "AdditiveSynthesisPianoCore"; }
    std::string coreVersion() const override { return "1.0"; }
    bool        isLoaded()    const override { return loaded_; }

private:
    // Note parameters [midi 0..127][vel_idx 0..7]
    PianoNoteParam note_params_[128][8];

    // Three-layer architecture
    PianoVoiceManager voice_mgr_;
    PianoPatchManager patch_mgr_;

    float sample_rate_ = 44100.f;
    float inv_sr_      = 1.f / 44100.f;
    bool  loaded_      = false;

    // GUI-settable parameters (read from RT thread via atomic)
    std::atomic<float> beat_scale_   {1.0f};
    std::atomic<float> noise_level_  {1.0f};
    std::atomic<int>   rng_seed_     {0};
    std::atomic<float> pan_spread_      {0.55f};
    std::atomic<float> stereo_decorr_  {1.0f};
    std::atomic<float> keyboard_spread_{0.60f};
    std::atomic<float> eq_strength_    {1.0f};

    // Protects note_params_ during full bank reload.
    mutable std::mutex bank_mutex_;
};
