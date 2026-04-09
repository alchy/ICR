/*
 * cores/sampler/sampler_core.cpp
 * ------------------------------
 * WAV sample playback engine.  Discovers banks from a base directory,
 * loads WAV files on demand, plays back with envelope and velocity layers.
 */

#include "sampler_core.h"
#include "wav_loader.h"
#include "engine/synth_core_registry.h"

#include <algorithm>
#include <cstdio>
#include <cstring>
#include <cmath>
#include <regex>

#include <filesystem>

REGISTER_SYNTH_CORE("SamplerCore", SamplerCore)

// Default base directory for sample banks.
// Normally provided by icr-config.json via CoreEngine.
static const char* DEFAULT_SAMPLE_DIR = "soundbanks-sampler";

// -- Directory scanning helpers (cross-platform via std::filesystem) ----------

static std::vector<std::string> listSubdirectories(const std::string& dir) {
    std::vector<std::string> result;
    namespace fs = std::filesystem;
    std::error_code ec;
    for (const auto& entry : fs::directory_iterator(dir, ec)) {
        if (ec) break;
        if (entry.is_directory() && entry.path().filename().string()[0] != '.')
            result.push_back(entry.path().filename().string());
    }
    std::sort(result.begin(), result.end());
    return result;
}

static std::vector<std::string> listFiles(const std::string& dir) {
    std::vector<std::string> result;
    namespace fs = std::filesystem;
    std::error_code ec;
    for (const auto& entry : fs::directory_iterator(dir, ec)) {
        if (ec) break;
        if (entry.is_regular_file())
            result.push_back(entry.path().filename().string());
    }
    return result;
}

// -- Path separator -----------------------------------------------------------

static const char PATH_SEP = '/';

// -- Constructor --------------------------------------------------------------

SamplerCore::SamplerCore() {}

SamplerCore::~SamplerCore() {
    if (load_thread_.joinable()) load_thread_.join();
}

// -- Bank discovery -----------------------------------------------------------

void SamplerCore::discoverBanks(const std::string& base_dir) {
    banks_.clear();
    bank_names_.clear();

    auto subdirs = listSubdirectories(base_dir);
    // WAV name pattern: mXXX-velY-fZZ.wav
    std::regex wav_pat(R"(m\d{3}-vel\d-f\d+\.wav)", std::regex::icase);

    for (const auto& name : subdirs) {
        std::string full = base_dir + PATH_SEP + name;
        auto files = listFiles(full);

        bool has_wav = false;
        for (const auto& fname : files) {
            if (std::regex_match(fname, wav_pat)) {
                has_wav = true;
                break;
            }
        }

        if (has_wav) {
            SampleBank bank;
            bank.name = name;
            bank.path = full;
            banks_.push_back(std::move(bank));
            bank_names_.push_back(name);
        }
    }
}

// -- Bank loading -------------------------------------------------------------

bool SamplerCore::loadBank(SampleBank& bank, float sr, Logger& logger) {
    if (bank.loaded) return true;

    logger.log("SamplerCore", LogSeverity::Info,
               "Loading bank: " + bank.name + " from " + bank.path);

    auto files = listFiles(bank.path);
    std::regex wav_pat(R"(m(\d{3})-vel(\d)-f(\d+)\.wav)", std::regex::icase);

    int count = 0;
    for (const auto& fname : files) {
        std::smatch match;
        if (!std::regex_match(fname, match, wav_pat)) continue;

        int midi = std::stoi(match[1].str());
        int vel  = std::stoi(match[2].str());
        if (midi < 0 || midi > 127 || vel < 0 || vel >= SAMPLER_VEL_LAYERS) continue;

        std::string full_path = bank.path + PATH_SEP + fname;
        wav::WavData w = wav::load(full_path);
        if (!w.valid) {
            logger.log("SamplerCore", LogSeverity::Warning,
                       "Failed to load: " + fname);
            continue;
        }

        SampleBuffer& sb = bank.samples[midi][vel];
        sb.data        = std::move(w.samples);
        sb.frames      = w.frames;
        sb.sample_rate = w.sample_rate;
        sb.loaded      = true;

        if (vel >= bank.vel_layers_available[midi])
            bank.vel_layers_available[midi] = vel + 1;

        count++;
    }

    bank.loaded = (count > 0);
    if (bank.loaded) {
        logger.log("SamplerCore", LogSeverity::Info,
                   "Loaded " + std::to_string(count) + " samples from " + bank.name);
    }
    return bank.loaded;
}

bool SamplerCore::selectBank(const std::string& name, Logger& logger) {
    for (int i = 0; i < (int)banks_.size(); i++) {
        if (banks_[i].name != name) continue;

        if (banks_[i].loaded) {
            // Already loaded — switch immediately
            std::lock_guard<std::mutex> lk(bank_mutex_);
            active_bank_idx_  = i;
            active_bank_name_ = name;
            return true;
        }

        // Not yet loaded — launch async load
        if (bank_loading_.load(std::memory_order_relaxed)) {
            logger.log("SamplerCore", LogSeverity::Warning,
                       "Bank load already in progress, ignoring");
            return false;
        }

        // Join any previous load thread
        if (load_thread_.joinable()) load_thread_.join();

        load_logger_ = logger;
        bank_loading_.store(true, std::memory_order_relaxed);

        int idx = i;
        float sr = sample_rate_;
        load_thread_ = std::thread([this, idx, sr]() {
            bool ok = loadBank(banks_[idx], sr, load_logger_);
            if (ok) {
                std::lock_guard<std::mutex> lk(bank_mutex_);
                active_bank_idx_  = idx;
                active_bank_name_ = banks_[idx].name;
            }
            bank_loading_.store(false, std::memory_order_relaxed);
            load_logger_.log("SamplerCore", LogSeverity::Info,
                ok ? ("Bank ready: " + banks_[idx].name)
                   : ("Bank load failed: " + banks_[idx].name));
        });

        return true;
    }
    return false;
}

// -- ISynthCore implementation ------------------------------------------------

bool SamplerCore::load(const std::string& params_path, float sr,
                        Logger& logger, int midi_from, int midi_to) {
    sample_rate_ = sr;

    // params_path for SamplerCore = base directory (or use default)
    std::string base_dir = params_path.empty() ? DEFAULT_SAMPLE_DIR : params_path;
    discoverBanks(base_dir);

    if (banks_.empty()) {
        logger.log("SamplerCore", LogSeverity::Warning,
                   "No sample banks found in " + base_dir);
        // Still "loaded" -- works with no sound until a bank is selected
        loaded_ = true;
        return true;
    }

    logger.log("SamplerCore", LogSeverity::Info,
               "Found " + std::to_string(banks_.size()) + " banks in " + base_dir);

    // Auto-load first bank
    if (selectBank(banks_[0].name, logger)) {
        logger.log("SamplerCore", LogSeverity::Info,
                   "Default bank: " + banks_[0].name);
    }

    loaded_ = true;
    return true;
}

void SamplerCore::setSampleRate(float sr) {
    sample_rate_ = sr;
}

// -- MIDI ---------------------------------------------------------------------

void SamplerCore::noteOn(uint8_t midi, uint8_t velocity) {
    if (midi >= SAMPLER_MAX_VOICES) return;
    if (velocity == 0) { noteOff(midi); return; }
    if (active_bank_idx_ < 0) return;

    patch_mgr_.noteOn(midi, velocity, banks_[active_bank_idx_],
                      voice_mgr_, sample_rate_,
                      keyboard_spread_.load(std::memory_order_relaxed),
                      bank_mutex_);
}

void SamplerCore::noteOff(uint8_t midi) {
    if (midi >= SAMPLER_MAX_VOICES) return;
    float rel_ms = release_time_.load(std::memory_order_relaxed) * 1000.f;
    patch_mgr_.noteOff(midi, voice_mgr_, sample_rate_, rel_ms);
}

void SamplerCore::sustainPedal(bool down) {
    float rel_ms = release_time_.load(std::memory_order_relaxed) * 1000.f;
    patch_mgr_.sustainPedal(down, voice_mgr_, sample_rate_, rel_ms);
}

void SamplerCore::allNotesOff() {
    float rel_ms = release_time_.load(std::memory_order_relaxed) * 1000.f;
    patch_mgr_.allNotesOff(voice_mgr_, sample_rate_, rel_ms);
}

// -- PatchManager -------------------------------------------------------------

void SamplerPatchManager::noteOn(
        uint8_t midi, uint8_t velocity,
        SampleBank& bank,
        SamplerVoiceManager& vm,
        float sample_rate,
        float keyboard_spread,
        std::mutex& bank_mutex) noexcept {
    std::unique_lock<std::mutex> lk(bank_mutex, std::try_to_lock);
    if (!lk.owns_lock()) return;

    // Velocity -> continuous float position 0.0-7.0 across layers
    int n_layers = bank.vel_layers_available[midi];
    if (n_layers == 0) return;

    float vel_float = (std::min)(7.f, (float)(velocity - 1) / 16.f);
    int lo_idx = (int)vel_float;
    int hi_idx = (std::min)(lo_idx + 1, n_layers - 1);
    float blend = vel_float - (float)lo_idx;

    // Find loaded samples for lo and hi layers (with fallback)
    const SampleBuffer* lo = nullptr;
    const SampleBuffer* hi = nullptr;

    // Search for lo: down from lo_idx, then up
    for (int v = (std::min)(lo_idx, n_layers - 1); v >= 0; v--)
        if (bank.samples[midi][v].loaded) { lo = &bank.samples[midi][v]; break; }
    if (!lo)
        for (int v = lo_idx + 1; v < SAMPLER_VEL_LAYERS; v++)
            if (bank.samples[midi][v].loaded) { lo = &bank.samples[midi][v]; break; }
    if (!lo) return;

    // Search for hi: up from hi_idx, then down
    for (int v = (std::min)(hi_idx, n_layers - 1); v < SAMPLER_VEL_LAYERS; v++)
        if (bank.samples[midi][v].loaded) { hi = &bank.samples[midi][v]; break; }
    if (!hi) hi = lo;  // same layer if only one available

    vm.initVoice(midi, velocity, lo, hi, blend, sample_rate, keyboard_spread);

    last_midi_.store(midi,     std::memory_order_relaxed);
    last_vel_ .store(velocity, std::memory_order_relaxed);
}

void SamplerPatchManager::noteOff(uint8_t midi, SamplerVoiceManager& vm,
                                   float sr, float release_ms) noexcept {
    if (sustain_.load(std::memory_order_relaxed))
        delayed_offs_[midi].store(true, std::memory_order_relaxed);
    else
        vm.releaseVoice(midi, sr, release_ms);
}

void SamplerPatchManager::sustainPedal(bool down, SamplerVoiceManager& vm,
                                        float sr, float release_ms) noexcept {
    sustain_.store(down, std::memory_order_relaxed);
    if (!down) {
        for (int m = 0; m < SAMPLER_MAX_VOICES; m++) {
            if (delayed_offs_[m].load(std::memory_order_relaxed)) {
                vm.releaseVoice(m, sr, release_ms);
                delayed_offs_[m].store(false, std::memory_order_relaxed);
            }
        }
    }
}

void SamplerPatchManager::allNotesOff(SamplerVoiceManager& vm, float sr,
                                       float release_ms) noexcept {
    vm.releaseAll(sr, release_ms);
    for (int m = 0; m < SAMPLER_MAX_VOICES; m++)
        delayed_offs_[m].store(false, std::memory_order_relaxed);
    sustain_.store(false, std::memory_order_relaxed);
}

// -- VoiceManager -------------------------------------------------------------

bool SamplerVoiceManager::processBlock(float* out_l, float* out_r,
                                        int n_samples, float sr) noexcept {
    bool any = false;
    for (int m = 0; m < SAMPLER_MAX_VOICES; m++) {
        if (!voices_[m].active) continue;
        voices_[m].process(out_l, out_r, n_samples, sr);
        any = true;
    }
    return any;
}

void SamplerVoiceManager::initVoice(int midi, uint8_t velocity,
                                     const SampleBuffer* lo,
                                     const SampleBuffer* hi,
                                     float vel_blend,
                                     float sr, float keyboard_spread) noexcept {
    SamplerVoice& v = voices_[midi];

    // If voice is active, capture damping buffer for click-free retrigger
    if (v.active && v.sample_lo && v.position < v.sample_lo->frames) {
        int damp_frames = (std::min)((int)(SAMPLER_DAMPING_MS * 0.001f * sr), 2048);
        int avail = (std::min)(damp_frames, v.sample_lo->frames - v.position);
        if (avail > 0) {
            const float* src = v.sample_lo->data.data() + v.position * 2;
            // Apply current envelope to damping buffer
            float env = v.vel_gain;
            if (v.releasing) env *= v.rel_gain;
            float fade_step = 1.f / (float)avail;
            for (int i = 0; i < avail; i++) {
                float fade = 1.f - (float)i * fade_step;  // linear fadeout
                v.damp_buf[i * 2]     = src[i * 2]     * env * fade * v.pan_l;
                v.damp_buf[i * 2 + 1] = src[i * 2 + 1] * env * fade * v.pan_r;
            }
            v.damp_len = avail;
            v.damp_pos = 0;
            v.damping  = true;
        }
    }

    v.active    = true;
    v.releasing = false;
    v.in_onset  = true;
    v.midi      = midi;
    v.velocity  = velocity;
    v.sample_lo = lo;
    v.sample_hi = hi;
    v.vel_blend = vel_blend;
    v.position  = 0;

    // Velocity gain: power curve for natural dynamic response
    // Combines layer crossfade (timbral) + continuous gain (dynamic)
    float vel_norm = (float)velocity / 127.f;
    v.vel_gain = vel_norm * vel_norm;  // quadratic gain curve

    // Onset ramp
    v.onset_gain = 0.f;
    v.onset_step = 1.f / (SAMPLER_ATTACK_MS * 0.001f * sr);
    v.rel_gain   = 1.f;
    v.rel_step   = 0.f;

    // Panning
    float angle = (dsp::PI / 4.f)
                + ((float)midi - 64.5f) / 87.0f * keyboard_spread * 0.5f;
    v.pan_l = std::cos(angle);
    v.pan_r = std::sin(angle);
}

void SamplerVoiceManager::releaseVoice(int midi, float sr, float release_ms) noexcept {
    SamplerVoice& v = voices_[midi];
    if (!v.active) return;
    v.releasing = true;
    v.rel_gain  = v.in_onset ? v.onset_gain : 1.f;
    v.rel_step  = -v.rel_gain / (release_ms * 0.001f * sr);
}

void SamplerVoiceManager::releaseAll(float sr, float release_ms) noexcept {
    for (int m = 0; m < SAMPLER_MAX_VOICES; m++)
        if (voices_[m].active) releaseVoice(m, sr, release_ms);
}

// -- Voice::process -----------------------------------------------------------

bool SamplerVoice::process(float* out_l, float* out_r, int n_samples,
                            float sample_rate) noexcept {
    if (!sample_lo || !sample_lo->loaded) { active = false; return false; }

    // Precompute blend weights
    const float w_lo = 1.f - vel_blend;
    const float w_hi = vel_blend;
    const bool  do_blend = (sample_hi && sample_hi != sample_lo
                            && sample_hi->loaded && w_hi > 0.001f);

    for (int i = 0; i < n_samples; i++) {
        // Damping buffer (retrigger crossfade from previous note)
        if (damping && damp_pos < damp_len) {
            out_l[i] += damp_buf[damp_pos * 2];
            out_r[i] += damp_buf[damp_pos * 2 + 1];
            damp_pos++;
            if (damp_pos >= damp_len) damping = false;
        }

        // End of sample (use shorter of the two layers)
        int max_frames = sample_lo->frames;
        if (do_blend && sample_hi->frames < max_frames)
            max_frames = sample_hi->frames;
        if (position >= max_frames) {
            active = false;
            break;
        }

        // Read + crossfade stereo samples between velocity layers
        float sL = sample_lo->data[position * 2]     * w_lo;
        float sR = sample_lo->data[position * 2 + 1] * w_lo;
        if (do_blend) {
            sL += sample_hi->data[position * 2]     * w_hi;
            sR += sample_hi->data[position * 2 + 1] * w_hi;
        }
        position++;

        // Onset ramp
        float env = 1.f;
        if (in_onset) {
            onset_gain += onset_step;
            if (onset_gain >= 1.f) { onset_gain = 1.f; in_onset = false; }
            env = onset_gain;
        }

        // Release ramp
        if (releasing) {
            env *= rel_gain;
            rel_gain += rel_step;
            if (rel_gain <= 0.f) {
                rel_gain = 0.f;
                active = false;
            }
        }

        // Output with velocity gain and pan
        float g = vel_gain * env;
        out_l[i] += sL * g * pan_l;
        out_r[i] += sR * g * pan_r;

        if (!active) break;
    }
    return active;
}

// -- processBlock (RT) --------------------------------------------------------

bool SamplerCore::processBlock(float* out_l, float* out_r,
                                int n_samples) noexcept {
    bool any = voice_mgr_.processBlock(out_l, out_r, n_samples, sample_rate_);
    // Apply master gain (was not connected before — bug fix)
    float g = gain_.load(std::memory_order_relaxed);
    if (any && std::abs(g - 1.f) > 0.001f) {
        for (int i = 0; i < n_samples; i++) {
            out_l[i] *= g;
            out_r[i] *= g;
        }
    }
    return any;
}

// -- Parameters ---------------------------------------------------------------

bool SamplerCore::setParam(const std::string& key, float value) {
    if (key == "gain") {
        gain_.store((std::max)(0.f, (std::min)(2.f, value)),
                    std::memory_order_relaxed);
        return true;
    }
    if (key == "keyboard_spread") {
        keyboard_spread_.store((std::max)(0.f, (std::min)(3.14159f, value)),
                               std::memory_order_relaxed);
        return true;
    }
    if (key == "release_time") {
        release_time_.store((std::max)(0.1f, (std::min)(4.f, value)),
                            std::memory_order_relaxed);
        return true;
    }
    return false;
}

bool SamplerCore::getParam(const std::string& key, float& out) const {
    if (key == "gain")            { out = gain_.load(std::memory_order_relaxed);            return true; }
    if (key == "keyboard_spread") { out = keyboard_spread_.load(std::memory_order_relaxed); return true; }
    if (key == "release_time")    { out = release_time_.load(std::memory_order_relaxed);    return true; }
    return false;
}

std::vector<CoreParamDesc> SamplerCore::describeParams() const {
    return {
        { "gain",            "Gain",            "Output",  "",
          gain_.load(),            0.f, 2.f, false },
        { "keyboard_spread", "Keyboard Spread", "Stereo",  "rad",
          keyboard_spread_.load(), 0.f, 3.14159f, false },
        { "release_time",    "Release Time",    "Envelope", "",
          release_time_.load(),    0.1f, 4.f, false },
    };
}

// -- Visualization ------------------------------------------------------------

CoreVizState SamplerCore::getVizState() const {
    CoreVizState vs;

    for (int m = 0; m < SAMPLER_MAX_VOICES; m++) {
        if (voice_mgr_.voice(m).active) {
            vs.active_midi_notes.push_back(m);
            vs.active_voice_count++;
        }
    }

    int last_midi = patch_mgr_.lastMidi();
    int last_vel  = patch_mgr_.lastVel();
    if (last_midi >= 0 && last_midi < 128) {
        CoreVoiceViz vv;
        vv.midi = last_midi;
        vv.vel  = last_vel;
        vv.f0_hz = 440.f * std::pow(2.f, (float)(last_midi - 69) / 12.f);
        vs.last_note       = std::move(vv);
        vs.last_note_valid = true;
    }

    return vs;
}
