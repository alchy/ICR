/*
 * core_engine.cpp
 * ────────────────
 * Generic RT engine for any ISynthCore.
 *
 * Audio thread flow:
 *   audioCallback()
 *     → processBlock(L*, R*, n)
 *         → drain MIDI queue → core->noteOn/Off/sustainPedal
 *         → memset buffers to 0
 *         → core->processBlock(L, R, n)     [additive]
 *         → applyMasterAndLfo(L, R, n)
 *         → dsp_.process(L, R, n)
 *         → interleave L+R → float32 stereo output
 *         → update peak meter
 */

// miniaudio implementation — compiled once here
#define MINIAUDIO_IMPLEMENTATION
#include "miniaudio.h"

#include "core_engine.h"
#include "synth_core_registry.h"
#include "midi_input.h"
#include "../third_party/json.hpp"
using json = nlohmann::json;

#include <cstring>
#include <fstream>
#include <cmath>
#include <cstdio>
#include <stdexcept>
#include <algorithm>
#include <memory>
#include <fstream>
#include <chrono>
#include <filesystem>

#ifdef _WIN32
  #include <conio.h>
#else
  #include <termios.h>
  #include <unistd.h>
  #include <fcntl.h>
#endif

// ── MIDI event queue — see pushMidiEvt (now a CoreEngine member) ─────────────

// ── MIDI queue (instance-local, SPSC lock-free) ───────────────────────────────

void CoreEngine::pushMidiEvt(MidiEvt::Type t, uint8_t midi, uint8_t val) noexcept {
    int w    = midi_w_.load(std::memory_order_relaxed);
    int next = (w + 1) % MIDI_Q_SIZE;
    if (next == midi_r_.load(std::memory_order_acquire)) {
        logger_.log("CoreEngine", LogSeverity::Warning, "MIDI queue full, event dropped");
        return;
    }
    midi_q_[w] = {t, midi, val};
    midi_w_.store(next, std::memory_order_release);
}

// ── Constructor / Destructor ──────────────────────────────────────────────────

CoreEngine::CoreEngine()
    : device_(new ma_device{}) {}

CoreEngine::~CoreEngine() {
    stop();
    delete[] buf_l_;
    delete[] buf_r_;
    delete device_;
    if (log_file_handle_) std::fclose(log_file_handle_);
}

// ── Helper: apply a JSON config file to a core via setParam ──────────────────

static void applyConfigJson(const std::string& path, ISynthCore* core,
                             Logger& logger) {
    if (path.empty()) return;
    std::ifstream f(path);
    if (!f) {
        logger.log("CoreEngine", LogSeverity::Warning,
                   "Config file not found: " + path);
        return;
    }
    nlohmann::json j;
    try { f >> j; }
    catch (const std::exception& e) {
        logger.log("CoreEngine", LogSeverity::Warning,
                   std::string("Config parse error: ") + e.what());
        return;
    }
    int applied = 0;
    for (auto it = j.begin(); it != j.end(); ++it) {
        if (it->is_number()) {
            float v = it->get<float>();
            if (core->setParam(it.key(), v)) ++applied;
        }
    }
    logger.log("CoreEngine", LogSeverity::Info,
               "Config loaded: " + path +
               " (" + std::to_string(applied) + " params applied)");
}

// ── loadEngineConfig ──────────────────────────────────────────────────────────

bool CoreEngine::loadEngineConfig(const std::string& config_path, Logger& logger) {
    std::ifstream f(config_path);
    if (!f.is_open()) {
        logger.log("CoreEngine", LogSeverity::Warning,
                   "Cannot open engine config: " + config_path);
        return false;
    }

    json root;
    try { f >> root; }
    catch (const std::exception& e) {
        logger.log("CoreEngine", LogSeverity::Error,
                   std::string("Engine config parse error: ") + e.what());
        return false;
    }

    // Open log file if configured
    if (root.contains("log_file") && root["log_file"].is_string()) {
        std::string log_path = root["log_file"].get<std::string>();
        if (!log_path.empty()) {
            log_file_handle_ = std::fopen(log_path.c_str(), "w");
            if (log_file_handle_) {
                // Redirect logger: file_out -> log file, rt_out -> stdout
                logger = Logger(log_file_handle_, stdout);
                logger_ = logger;
                logger.log("CoreEngine", LogSeverity::Info,
                           "Log file opened: " + log_path);
            } else {
                logger.log("CoreEngine", LogSeverity::Warning,
                           "Cannot open log file: " + log_path);
            }
        }
    }

    if (root.contains("default_core") && root["default_core"].is_string())
        default_core_name_ = root["default_core"].get<std::string>();

    if (root.contains("cores") && root["cores"].is_object()) {
        json cores_j = root["cores"];
        for (json::iterator it = cores_j.begin(); it != cores_j.end(); ++it) {
            std::string cn = it.key();
            json cval = it.value();
            if (!cval.is_object()) continue;
            std::unordered_map<std::string, std::string> cfg;
            for (json::iterator jt = cval.begin(); jt != cval.end(); ++jt) {
                std::string pk = jt.key();
                json pval = jt.value();
                if (pval.is_string())
                    cfg[pk] = pval.get<std::string>();
                else if (pval.is_number())
                    cfg[pk] = std::to_string(pval.get<double>());
            }
            core_config_[cn] = std::move(cfg);
        }
    }

    logger.log("CoreEngine", LogSeverity::Info,
               "Engine config loaded: " + config_path
               + " (default_core=" + default_core_name_
               + ", " + std::to_string(core_config_.size()) + " core configs)");
    return true;
}

std::string CoreEngine::coreConfigValue(const std::string& core_name,
                                         const std::string& key) const {
    auto it = core_config_.find(core_name);
    if (it == core_config_.end()) return "";
    auto jt = it->second.find(key);
    return (jt != it->second.end()) ? jt->second : "";
}

// ── initialize ────────────────────────────────────────────────────────────────

bool CoreEngine::initialize(const std::string& core_name,
                             const std::string& params_path,
                             const std::string& config_json_path,
                             Logger&            logger,
                             int                midi_from,
                             int                midi_to) {
    logger_ = logger;
    // Resolve params_path: CLI override > engine config > empty
    std::string resolved_params = params_path;
    if (resolved_params.empty())
        resolved_params = coreConfigValue(core_name, "params_path");

    logger_.log("CoreEngine", LogSeverity::Info,
                "Initializing core: " + core_name
                + (resolved_params.empty() ? "" : " params=" + resolved_params));

    auto core = SynthCoreRegistry::instance().create(core_name);
    if (!core) {
        logger_.log("CoreEngine", LogSeverity::Error,
                    "Unknown core: '" + core_name + "'. Available:");
        for (const auto& n : SynthCoreRegistry::instance().availableCores())
            logger_.log("CoreEngine", LogSeverity::Info, "  - " + n);
        return false;
    }

    if (!core->load(resolved_params, (float)sample_rate_, logger_, midi_from, midi_to)) {
        logger_.log("CoreEngine", LogSeverity::Error, "Core load failed");
        return false;
    }

    applyConfigJson(config_json_path, core.get(), logger_);

    // Store in multi-core map and set as active
    active_core_name_ = core_name;
    active_core_      = core.get();
    cores_[core_name] = std::move(core);
    core_params_paths_[core_name] = resolved_params;

    delete[] buf_l_;
    delete[] buf_r_;
    buf_l_ = new float[block_size_];
    buf_r_ = new float[block_size_];
    dsp_.prepare((float)sample_rate_, block_size_);

    float bps = (float)sample_rate_ / (float)block_size_;
    peak_decay_coeff_ = std::pow(10.f, -1.f / bps);  // -20 dB/s

    // Auto-load soundboard IR from config (if not already loaded via --ir)
    loadIrFromConfig(core_name);
    applyDspDefaults(core_name);

    logger_.log("CoreEngine", LogSeverity::Info,
        std::string("Ready. Core=") + active_core_->coreName() +
        " SR=" + std::to_string(sample_rate_) +
        " block=" + std::to_string(block_size_));
    return true;
}

void CoreEngine::loadIrFromConfig(const std::string& core_name) {
    std::string ir = coreConfigValue(core_name, "ir_path");
    if (ir.empty()) return;
    // Don't reload if same IR is already loaded
    if (dsp_.isConvolverLoaded() && dsp_.convolver().isEnabled()) return;
    if (dsp_.loadConvolverIR(ir, (float)sample_rate_)) {
        dsp_.setConvolverEnabled(true);
        logger_.log("CoreEngine", LogSeverity::Info,
                    "Auto-loaded IR from config: " + ir);
    }
}

void CoreEngine::applyDspDefaults(const std::string& core_name) {
    // Apply per-core DSP defaults from icr-config.json "dsp_defaults" sub-object.
    // Any key not present keeps its current value.
    auto get = [&](const std::string& key, int fallback) -> int {
        std::string v = coreConfigValue(core_name, key);
        if (v.empty()) return fallback;
        try { return std::stoi(v); } catch (...) { return fallback; }
    };

    int gain = get("master_gain", -1);
    if (gain >= 0) setMasterGain((uint8_t)(std::min)(127, gain), logger_);

    int pan = get("master_pan", -1);
    if (pan >= 0) setMasterPan((uint8_t)(std::min)(127, pan));

    int lfo_spd = get("lfo_speed", -1);
    if (lfo_spd >= 0) setPanSpeed((uint8_t)(std::min)(127, lfo_spd));

    int lfo_dep = get("lfo_depth", -1);
    if (lfo_dep >= 0) setPanDepth((uint8_t)(std::min)(127, lfo_dep));

    int lim_thr = get("limiter_threshold", -1);
    if (lim_thr >= 0) setLimiterThreshold((uint8_t)(std::min)(127, lim_thr));

    int lim_rel = get("limiter_release", -1);
    if (lim_rel >= 0) setLimiterRelease((uint8_t)(std::min)(127, lim_rel));

    int lim_ena = get("limiter_enabled", -1);
    if (lim_ena >= 0) dsp_.setLimiterEnabled((uint8_t)(lim_ena >= 64 ? 127 : 0));

    int bbe_def = get("bbe_definition", -1);
    if (bbe_def >= 0) setBBEDefinition((uint8_t)(std::min)(127, bbe_def));

    int bbe_bas = get("bbe_bass_boost", -1);
    if (bbe_bas >= 0) setBBEBassBoost((uint8_t)(std::min)(127, bbe_bas));
}

// ── switchCore ────────────────────────────────────────────────────────────────

bool CoreEngine::switchCore(const std::string& core_name,
                             const std::string& params_path) {
    if (core_name == active_core_name_) return true;  // already active

    // Lazy-instantiate: create core only on first use
    if (cores_.find(core_name) == cores_.end()) {
        // Resolve params: CLI arg > engine config > empty
        std::string resolved = params_path;
        if (resolved.empty())
            resolved = coreConfigValue(core_name, "params_path");

        logger_.log("CoreEngine", LogSeverity::Info,
                    "Instantiating core: " + core_name
                    + (resolved.empty() ? "" : " params=" + resolved));

        auto new_core = SynthCoreRegistry::instance().create(core_name);
        if (!new_core) {
            logger_.log("CoreEngine", LogSeverity::Error,
                        "Unknown core: '" + core_name + "'");
            return false;
        }

        if (!new_core->load(resolved, (float)sample_rate_, logger_)) {
            logger_.log("CoreEngine", LogSeverity::Error,
                        "Core load failed for " + core_name);
            return false;
        }

        cores_[core_name] = std::move(new_core);
        core_params_paths_[core_name] = resolved;
    }

    // Switch active — no audio interruption. Old core's voices dozvuk naturally.
    active_core_name_ = core_name;
    active_core_      = cores_[core_name].get();

    // Load IR and DSP defaults for the new active core
    loadIrFromConfig(core_name);
    applyDspDefaults(core_name);

    logger_.log("CoreEngine", LogSeverity::Info,
        std::string("Active core: ") + active_core_->coreName()
        + " (" + std::to_string(cores_.size()) + " cores loaded)");
    return true;
}

// ── Audio callback ────────────────────────────────────────────────────────────

void CoreEngine::audioCallback(ma_device*  device,
                                void*       output,
                                const void* /*input*/,
                                uint32_t    frame_count) {
    auto* eng = reinterpret_cast<CoreEngine*>(device->pUserData);
    // Interleave L+R into float32 output
    uint32_t rem = frame_count;
    uint32_t off = 0;
    while (rem > 0) {
        uint32_t chunk = rem < (uint32_t)eng->block_size_
                       ? rem : (uint32_t)eng->block_size_;
        eng->processBlock(eng->buf_l_, eng->buf_r_, (int)chunk);
        float* dst = reinterpret_cast<float*>(output) + off * 2;
        for (uint32_t i = 0; i < chunk; i++) {
            dst[i*2]   = eng->buf_l_[i];
            dst[i*2+1] = eng->buf_r_[i];
        }
        off += chunk;
        rem -= chunk;
    }
}

void CoreEngine::processBlock(float* out_l, float* out_r, int n) noexcept {
    // Drain MIDI queue — route events to ACTIVE core only
    if (active_core_) {
        int r = midi_r_.load(std::memory_order_acquire);
        int w = midi_w_.load(std::memory_order_relaxed);
        while (r != w) {
            const MidiEvt& ev = midi_q_[r];
            switch (ev.type) {
                case MidiEvt::NOTE_ON:       active_core_->noteOn(ev.midi, ev.value);    break;
                case MidiEvt::NOTE_OFF:      active_core_->noteOff(ev.midi);             break;
                case MidiEvt::SUSTAIN:       active_core_->sustainPedal(ev.value >= 64); break;
                case MidiEvt::ALL_NOTES_OFF: active_core_->allNotesOff();                break;
            }
            r = (r + 1) % MIDI_Q_SIZE;
        }
        midi_r_.store(r, std::memory_order_release);
    }

    // Zero buffers (core output is additive)
    std::memset(out_l, 0, n * sizeof(float));
    std::memset(out_r, 0, n * sizeof(float));

    // Process ALL instantiated cores — voices in non-active cores
    // continue to produce audio (dozvuk / release tails).
    for (auto& [name, core] : cores_) {
        core->processBlock(out_l, out_r, n);
    }

    // Progressive voice gain (AGC) — see dsp/agc.h
    dsp::agc_process(agc_, out_l, out_r, n);

    // Master gain / LFO pan / DSP
    applyMasterAndLfo(out_l, out_r, n);
    dsp_.process(out_l, out_r, n);

    // Peak metering
    float peak = 0.f;
    for (int i = 0; i < n; i++) {
        float s = std::abs(out_l[i]) > std::abs(out_r[i])
                ? std::abs(out_l[i]) : std::abs(out_r[i]);
        if (s > peak) peak = s;
    }
    float cur = output_peak_lin_.load(std::memory_order_relaxed);
    cur = cur * peak_decay_coeff_;
    if (peak > cur) cur = peak;
    output_peak_lin_.store(cur, std::memory_order_relaxed);
}

void CoreEngine::applyMasterAndLfo(float* out_l, float* out_r,
                                    int n) noexcept {
    static constexpr float PI  = 3.14159265358979f;
    static constexpr float TAU = 2.f * PI;

    // Load atomics once — avoid repeated atomic reads in inner loop
    const float mg    = master_gain_.load(std::memory_order_relaxed);
    const float pl    = pan_l_      .load(std::memory_order_relaxed);
    const float pr    = pan_r_      .load(std::memory_order_relaxed);
    const float speed = lfo_speed_  .load(std::memory_order_relaxed);
    const float depth = lfo_depth_  .load(std::memory_order_relaxed);

    float mg_l = mg * pl;
    float mg_r = mg * pr;

    if (speed > 0.f && depth > 0.f) {
        float d_phase = TAU * speed / (float)sample_rate_;
        for (int i = 0; i < n; i++) {
            float lfo   = depth * std::sin(lfo_phase_);
            float lm    = mg_l * (1.f - lfo);
            float rm    = mg_r * (1.f + lfo);
            out_l[i] *= lm;
            out_r[i] *= rm;
            lfo_phase_ += d_phase;
            if (lfo_phase_ >= TAU) lfo_phase_ -= TAU;
        }
    } else {
        for (int i = 0; i < n; i++) {
            out_l[i] *= mg_l;
            out_r[i] *= mg_r;
        }
    }
}

// ── start / stop ─────────────────────────────────────────────────────────────

bool CoreEngine::start() {
    if (!isInitialized()) return false;

    dsp::agc_init(agc_, (float)sample_rate_);

    ma_device_config cfg = ma_device_config_init(ma_device_type_playback);
    cfg.playback.format    = ma_format_f32;
    cfg.playback.channels  = 2;
    cfg.sampleRate         = (ma_uint32)sample_rate_;
    cfg.dataCallback       = audioCallback;
    cfg.pUserData          = this;
    cfg.periodSizeInFrames = (ma_uint32)block_size_;

    if (ma_device_init(nullptr, &cfg, device_) != MA_SUCCESS) {
        logger_.log("CoreEngine", LogSeverity::Error, "Failed to open audio device");
        return false;
    }
    if (ma_device_start(device_) != MA_SUCCESS) {
        logger_.log("CoreEngine", LogSeverity::Error, "Failed to start audio device");
        ma_device_uninit(device_);
        return false;
    }
    running_.store(true);
    logger_.log("CoreEngine", LogSeverity::Info,
        "Audio started: " + std::string(device_->playback.name));
    return true;
}

void CoreEngine::stop() {
    if (!running_.load()) return;
    ma_device_stop(device_);
    ma_device_uninit(device_);
    running_.store(false);
    logger_.log("CoreEngine", LogSeverity::Info, "Audio stopped");
}

// ── Thread-safe MIDI ──────────────────────────────────────────────────────────

void CoreEngine::noteOn(uint8_t midi, uint8_t vel) {
    last_note_midi_.store(midi, std::memory_order_relaxed);
    last_note_vel_ .store(vel,  std::memory_order_relaxed);
    pushMidiEvt(MidiEvt::NOTE_ON, midi, vel);
}
void CoreEngine::noteOff(uint8_t midi) {
    pushMidiEvt(MidiEvt::NOTE_OFF, midi, 0);
}
void CoreEngine::sustainPedal(uint8_t val) {
    pushMidiEvt(MidiEvt::SUSTAIN, 0, val);
}

void CoreEngine::allNotesOff() {
    pushMidiEvt(MidiEvt::ALL_NOTES_OFF, 0, 0);
}

// ── Master mix ────────────────────────────────────────────────────────────────

void CoreEngine::setMasterGain(uint8_t v, Logger& logger) {
    master_gain_.store((v / 127.f) * (v / 127.f) * 2.f,  // square law, 0..2
                       std::memory_order_relaxed);
    logger.log("CoreEngine", LogSeverity::Info,
               "Master gain MIDI=" + std::to_string(v));
}

void CoreEngine::setMasterPan(uint8_t v) noexcept {
    float norm = (v - 64) / 64.f;  // -1..+1
    if (norm <= 0.f) {
        pan_l_.store(1.f,          std::memory_order_relaxed);
        pan_r_.store(1.f + norm,   std::memory_order_relaxed);
    } else {
        pan_l_.store(1.f - norm,   std::memory_order_relaxed);
        pan_r_.store(1.f,          std::memory_order_relaxed);
    }
}

void CoreEngine::setPanSpeed(uint8_t v) noexcept {
    lfo_speed_.store(2.f * (v / 127.f), std::memory_order_relaxed);   // 0..2 Hz
}

void CoreEngine::setPanDepth(uint8_t v) noexcept {
    lfo_depth_.store(v / 127.f, std::memory_order_relaxed);
}

// ── DSP chain ─────────────────────────────────────────────────────────────────

void CoreEngine::setLimiterThreshold(uint8_t v) noexcept { dsp_.setLimiterThreshold(v); }
void CoreEngine::setLimiterRelease  (uint8_t v) noexcept { dsp_.setLimiterRelease(v);   }
void CoreEngine::setLimiterEnabled  (uint8_t v) noexcept { dsp_.setLimiterEnabled(v);   }
void CoreEngine::setBBEDefinition   (uint8_t v) noexcept { dsp_.setBBEDefinition(v);    }
void CoreEngine::setBBEBassBoost    (uint8_t v) noexcept { dsp_.setBBEBassBoost(v);     }

// ── Stats ─────────────────────────────────────────────────────────────────────

int CoreEngine::activeVoices() const {
    if (!active_core_) return 0;
    return active_core_->getVizState().active_voice_count;
}

// ── SysEx handling (MIDI callback thread) ────────────────────────────────────

static float decodeSysExFloat(const uint8_t* b) {
    uint32_t bits = 0;
    for (int i = 0; i < 5; ++i)
        bits |= (uint32_t)(b[i] & 0x7F) << ((4 - i) * 7);
    float v;
    std::memcpy(&v, &bits, sizeof(v));
    return v;
}

static const char* noteParamKey(uint8_t id) {
    switch (id) {
        case 0x01: return "f0_hz";
        case 0x02: return "B";          // recomputes all partial f_hz = k*f0*sqrt(1+B*k²)
        case 0x03: return "attack_tau";
        case 0x04: return "A_noise";
        case 0x05: return "rms_gain";
        case 0x06: return "phi_diff";
        default:   return nullptr;
    }
}

static const char* partialParamKey(uint8_t id) {
    switch (id) {
        case 0x10: return "f_hz";
        case 0x11: return "A0";
        case 0x12: return "tau1";
        case 0x13: return "tau2";
        case 0x14: return "a1";
        case 0x15: return "beat_hz";
        case 0x16: return "phi";
        default:   return nullptr;
    }
}

// Master param IDs — ISynthCore global keys (0x01–0x07).
// CoreEngine and DspChain params (0x10+, 0x20+) are handled inline in SET_MASTER.
static const char* masterCoreParamKey(uint8_t id) {
    switch (id) {
        case 0x01: return "beat_scale";
        case 0x02: return "noise_level";
        case 0x03: return "pan_spread";
        case 0x04: return "stereo_decorr";
        case 0x05: return "keyboard_spread";
        case 0x06: return "eq_strength";
        case 0x07: return "rng_seed";
        default:   return nullptr;
    }
}

// ── Core ID resolution ───────────────────────────────────────────────────────
//
// SysEx frame: F0 7D 01 <cmd> <core_id> <data...> F7
//
//   core_id 0x00 = active core (backwards-compatible default)
//   core_id 0x01 = AdditiveSynthesisPianoCore
//   core_id 0x02 = PhysicalModelingPianoCore
//   core_id 0x03 = SamplerCore
//   core_id 0x04 = SineCore
//   core_id 0x7F = engine-level (master/DspChain, not routed to any core)

static const char* coreIdToName(uint8_t core_id) {
    switch (core_id) {
        case 0x01: return "AdditiveSynthesisPianoCore";
        case 0x02: return "PhysicalModelingPianoCore";
        case 0x03: return "SamplerCore";
        case 0x04: return "SineCore";
        default:   return nullptr;
    }
}

std::vector<uint8_t> CoreEngine::handleSysEx(const uint8_t* data, int len) {
    // data is AFTER F0, BEFORE F7
    if (len < 3) return {};
    if (data[0] != 0x7D || data[1] != 0x01) return {};  // not ICR SysEx

    uint8_t cmd = data[2];

    // PING/PONG — no core_id needed
    if (cmd == 0x70) return { 0xF0, 0x7D, 0x01, 0x71, 0xF7 };

    // All other commands: next byte is core_id
    if (len < 4) return {};
    uint8_t        core_id    = data[3];
    const uint8_t* payload    = data + 4;
    int            payloadLen = len - 4;

    // Resolve target core from core_id
    ISynthCore* target = nullptr;
    bool engine_level = (core_id == 0x7F);

    if (!engine_level) {
        if (core_id == 0x00) {
            target = active_core_;  // active core (default / backwards-compatible)
        } else {
            const char* name = coreIdToName(core_id);
            if (name) {
                auto it = cores_.find(name);
                if (it != cores_.end())
                    target = it->second.get();
            }
        }
    }

    switch (cmd) {

    case 0x01: {  // SET_NOTE_PARAM — midi vel param_id value(5)
        if (payloadLen < 8 || !target) break;
        int         midi  = payload[0];
        int         vel   = payload[1];
        uint8_t     pid   = payload[2];
        float       value = decodeSysExFloat(payload + 3);
        const char* key   = noteParamKey(pid);
        if (key) target->setNoteParam(midi, vel, key, value);
        break;
    }

    case 0x02: {  // SET_NOTE_PARTIAL — midi vel k param_id value(5)
        if (payloadLen < 9 || !target) break;
        int         midi  = payload[0];
        int         vel   = payload[1];
        int         k     = payload[2];
        uint8_t     pid   = payload[3];
        float       value = decodeSysExFloat(payload + 4);
        const char* key   = partialParamKey(pid);
        if (key) target->setNotePartialParam(midi, vel, k, key, value);
        break;
    }

    case 0x03: {  // SET_BANK — chunked JSON
        if (payloadLen < 6 || !target) break;
        int chunk_idx    = ((int)payload[0] << 14) | ((int)payload[1] << 7) | payload[2];
        int total_chunks = ((int)payload[3] << 14) | ((int)payload[4] << 7) | payload[5];
        const uint8_t* chunk_data = payload + 6;
        int chunk_len = payloadLen - 6;

        if (chunk_idx == 0) {
            bank_chunk_buf_.clear();
            bank_chunk_buf_.reserve((size_t)total_chunks * 240);
            bank_chunk_total_ = total_chunks;
            bank_chunk_recv_  = 0;
        }
        bank_chunk_buf_.append(reinterpret_cast<const char*>(chunk_data),
                               (size_t)chunk_len);
        ++bank_chunk_recv_;

        if (bank_chunk_recv_ >= bank_chunk_total_) {
            if (target->loadBankJson(bank_chunk_buf_))
                logger_.log("CoreEngine", LogSeverity::Info,
                            "SET_BANK: applied ("
                            + std::to_string(bank_chunk_buf_.size()) + " bytes)"
                            + " core_id=0x" + std::to_string((int)core_id));
            else
                logger_.log("CoreEngine", LogSeverity::Warning,
                            "SET_BANK: loadBankJson failed");
            bank_chunk_buf_.clear();
            bank_chunk_total_ = 0;
            bank_chunk_recv_  = 0;
        }
        break;
    }

    case 0x10: {  // SET_MASTER — param_id value(5)
        if (payloadLen < 6) break;
        uint8_t pid   = payload[0];
        float   value = decodeSysExFloat(payload + 1);

        if (engine_level || pid >= 0x10) {
            // Engine-level and DspChain params (always engine, regardless of core_id)
            if (pid >= 0x10 && pid <= 0x13) {
                switch (pid) {
                case 0x10:
                    master_gain_.store((std::max)(0.f, (std::min)(2.f, value)),
                                       std::memory_order_relaxed);
                    break;
                case 0x11: {
                    float n = (std::max)(-1.f, (std::min)(1.f, value));
                    if (n <= 0.f) { pan_l_.store(1.f);       pan_r_.store(1.f + n); }
                    else          { pan_l_.store(1.f - n);   pan_r_.store(1.f);     }
                    break;
                }
                case 0x12:
                    lfo_speed_.store((std::max)(0.f, (std::min)(2.f, value)),
                                     std::memory_order_relaxed);
                    break;
                case 0x13:
                    lfo_depth_.store((std::max)(0.f, (std::min)(1.f, value)),
                                     std::memory_order_relaxed);
                    break;
                }
            } else if (pid >= 0x20 && pid <= 0x24) {
                auto u = (uint8_t)((std::max)(0.f, (std::min)(1.f, value)) * 127.f);
                switch (pid) {
                case 0x20: dsp_.setLimiterThreshold(u); break;
                case 0x21: dsp_.setLimiterRelease(u);   break;
                case 0x22: dsp_.setLimiterEnabled(u);   break;
                case 0x23: dsp_.setBBEDefinition(u);    break;
                case 0x24: dsp_.setBBEBassBoost(u);     break;
                }
            }
        }

        if (!engine_level && pid <= 0x07 && target) {
            // Core-specific global params — routed to target core's setParam
            const char* key = masterCoreParamKey(pid);
            if (key) target->setParam(key, value);
        }
        break;
    }

    case 0x72: {  // EXPORT_BANK — path as ASCII bytes
        if (payloadLen < 1 || !target) break;
        std::string export_path(reinterpret_cast<const char*>(payload),
                                (size_t)payloadLen);
        if (target->exportBankJson(export_path))
            logger_.log("CoreEngine", LogSeverity::Info,
                        "EXPORT_BANK: wrote " + export_path);
        else
            logger_.log("CoreEngine", LogSeverity::Warning,
                        "EXPORT_BANK: failed to write " + export_path);
        break;
    }

    default:
        logger_.log("CoreEngine", LogSeverity::Info,
                    "SysEx: unknown cmd 0x" + std::to_string((int)cmd));
        break;
    }
    return {};
}

// ── Offline batch render ──────────────────────────────────────────────────────

// Stereo int16 WAV writer — no external dependencies.
static bool _writeWavStereo16(const std::string& path,
                               const std::vector<float>& left,
                               const std::vector<float>& right,
                               int sr)
{
    uint32_t n         = (uint32_t)left.size();
    uint32_t data_size = n * 4u;          // 2 channels × 2 bytes
    uint32_t riff_size = 36u + data_size;
    uint32_t byte_rate = (uint32_t)sr * 4u;
    uint16_t block_al  = 4u;
    uint16_t bits      = 16u;
    uint16_t channels  = 2u;
    uint16_t fmt_type  = 1u;   // PCM
    uint32_t fmt_size  = 16u;
    uint32_t sr_u      = (uint32_t)sr;

    std::FILE* f = std::fopen(path.c_str(), "wb");
    if (!f) return false;

    std::fwrite("RIFF",    1, 4, f);
    std::fwrite(&riff_size, 4, 1, f);
    std::fwrite("WAVE",    1, 4, f);
    std::fwrite("fmt ",    1, 4, f);
    std::fwrite(&fmt_size,  4, 1, f);
    std::fwrite(&fmt_type,  2, 1, f);
    std::fwrite(&channels,  2, 1, f);
    std::fwrite(&sr_u,      4, 1, f);
    std::fwrite(&byte_rate, 4, 1, f);
    std::fwrite(&block_al,  2, 1, f);
    std::fwrite(&bits,      2, 1, f);
    std::fwrite("data",    1, 4, f);
    std::fwrite(&data_size, 4, 1, f);

    for (uint32_t i = 0; i < n; i++) {
        auto clamp16 = [](float s) -> int16_t {
            float c = s > 1.f ? 1.f : (s < -1.f ? -1.f : s);
            return static_cast<int16_t>(c * 32767.f);
        };
        int16_t vl = clamp16(left[i]);
        int16_t vr = clamp16(right[i]);
        std::fwrite(&vl, 2, 1, f);
        std::fwrite(&vr, 2, 1, f);
    }
    std::fclose(f);
    return true;
}

// Map vel_idx 0-7 → MIDI velocity midpoint of that layer.
// midiVelToIdx(v) = min(7, (v-1)/16)  →  inverse midpoint = 9 + idx*16
static inline uint8_t _velIdxToMidi(int vel_idx) {
    return static_cast<uint8_t>(9 + (std::min)(7, (std::max)(0, vel_idx)) * 16);
}

static void _mkdirP(const std::string& dir) {
    std::filesystem::create_directories(dir);
}

int CoreEngine::renderBatch(const std::string& batch_json_path,
                             const std::string& out_dir,
                             int sr)
{
    if (!active_core_ || !active_core_->isLoaded()) {
        logger_.log("ICR", LogSeverity::Error, "renderBatch: core not loaded");
        return 0;
    }

    // ── Parse batch JSON ──────────────────────────────────────────────────────
    nlohmann::json batch;
    {
        std::ifstream f(batch_json_path);
        if (!f.is_open()) {
            logger_.log("ICR", LogSeverity::Error,
                        "renderBatch: cannot open " + batch_json_path);
            return 0;
        }
        try { f >> batch; }
        catch (const std::exception& e) {
            logger_.log("ICR", LogSeverity::Error,
                        std::string("renderBatch: JSON parse error: ") + e.what());
            return 0;
        }
    }
    if (!batch.is_array() || batch.empty()) {
        logger_.log("ICR", LogSeverity::Error, "renderBatch: batch JSON must be a non-empty array");
        return 0;
    }

    _mkdirP(out_dir);

    const int    BLOCK    = 1024;
    const float  TAIL_S   = 0.5f;
    const int    total    = (int)batch.size();

    // Set SR on core (only if different from default — avoids needless recompute)
    sample_rate_ = sr;
    active_core_->setSampleRate((float)sr);

    std::vector<float> buf_l(BLOCK), buf_r(BLOCK);

    logger_.log("ICR", LogSeverity::Info,
                "Render batch: " + std::to_string(total)
                + " notes -> " + out_dir);

    int rendered = 0;
    auto t_start = std::chrono::steady_clock::now();

    for (int ni = 0; ni < total; ++ni) {
        const auto& entry = batch[ni];
        int   midi       = entry.value("midi",       60);
        int   vel_idx    = entry.value("vel_idx",     3);
        float duration_s = entry.value("duration_s", 3.0f);

        uint8_t midi_u = static_cast<uint8_t>((std::max)(0, (std::min)(127, midi)));
        uint8_t vel_u  = _velIdxToMidi(vel_idx);

        int sustain_samples = static_cast<int>(duration_s * (float)sr);
        int tail_samples    = static_cast<int>(TAIL_S * (float)sr);
        int total_samples   = sustain_samples + tail_samples;

        std::vector<float> out_left, out_right;
        out_left.reserve((size_t)total_samples);
        out_right.reserve((size_t)total_samples);

        active_core_->allNotesOff();
        active_core_->noteOn(midi_u, vel_u);

        // Render sustain
        for (int s = 0; s < sustain_samples; ) {
            int n = (std::min)(BLOCK, sustain_samples - s);
            std::fill(buf_l.begin(), buf_l.begin() + n, 0.f);
            std::fill(buf_r.begin(), buf_r.begin() + n, 0.f);
            active_core_->processBlock(buf_l.data(), buf_r.data(), n);
            for (int j = 0; j < n; j++) {
                out_left.push_back(buf_l[j]);
                out_right.push_back(buf_r[j]);
            }
            s += n;
        }

        active_core_->noteOff(midi_u);

        // Render release tail
        for (int s = 0; s < tail_samples; ) {
            int n = (std::min)(BLOCK, tail_samples - s);
            std::fill(buf_l.begin(), buf_l.begin() + n, 0.f);
            std::fill(buf_r.begin(), buf_r.begin() + n, 0.f);
            active_core_->processBlock(buf_l.data(), buf_r.data(), n);
            for (int j = 0; j < n; j++) {
                out_left.push_back(buf_l[j]);
                out_right.push_back(buf_r[j]);
            }
            s += n;
        }

        // Build output filename: m060-v03-f48.wav
        int sr_k = sr / 1000;
        char fname[32];
        std::snprintf(fname, sizeof(fname), "m%03d-v%02d-f%d.wav", midi, vel_idx, sr_k);
        std::string out_path = out_dir + "/" + fname;

        if (_writeWavStereo16(out_path, out_left, out_right, sr)) {
            rendered++;
            logger_.log("ICR", LogSeverity::Info,
                        "  Rendered " + std::string(fname)
                        + "  (" + std::to_string(ni + 1) + "/" + std::to_string(total) + ")");
        } else {
            logger_.log("ICR", LogSeverity::Warning,
                        "  Failed to write " + out_path);
        }
    }

    auto t_end = std::chrono::steady_clock::now();
    float elapsed = std::chrono::duration<float>(t_end - t_start).count();
    logger_.log("ICR", LogSeverity::Info,
                "Render done: " + std::to_string(rendered) + "/" + std::to_string(total)
                + " notes in " + std::to_string((int)(elapsed * 10) / 10.f) + "s");
    return rendered;
}

// ── runCoreEngine — interactive loop ─────────────────────────────────────────

int runCoreEngine(Logger&            logger,
                  const std::string& core_name,
                  const std::string& params_path,
                  int                midi_port,
                  const std::string& config_json_path) {
    logger.log("runCoreEngine", LogSeverity::Info,
               "=== IthacaCoreResonator STARTING ===");

    auto engine = std::make_unique<CoreEngine>();
    if (!engine->initialize(core_name, params_path, config_json_path, logger)) {
        logger.log("runCoreEngine", LogSeverity::Error, "Initialization failed");
        return 1;
    }
    if (!engine->start()) {
        logger.log("runCoreEngine", LogSeverity::Error, "Audio start failed");
        return 1;
    }

    MidiInput midi;
    auto ports = MidiInput::listPorts();
    if (!ports.empty()) {
        for (int i = 0; i < (int)ports.size(); i++)
            logger.log("MIDI", LogSeverity::Info,
                       "port [" + std::to_string(i) + "] " + ports[i]);
        midi.open(*engine, midi_port);
    }
#ifndef _WIN32
    if (!midi.isOpen()) midi.openVirtual(*engine);
#endif

    const char  keys[] = "asdfghjk";
    const int  midis[] = { 60, 62, 64, 65, 67, 69, 71, 72 };
    bool       sustain = false;
    logger.log("runCoreEngine", LogSeverity::Info,
               "Keyboard: a-k = C4-C5  |  z = sustain  |  q = quit");

#ifdef _WIN32
    while (true) {
        if (_kbhit()) {
            int ch = _getch();
            if (ch == 'q' || ch == 'Q') break;
            if (ch == 'z') {
                sustain = !sustain;
                engine->sustainPedal(sustain ? 127 : 0);
                continue;
            }
            for (int i = 0; i < 8; i++) {
                if (ch == keys[i]) {
                    engine->noteOn((uint8_t)midis[i], 80);
                    ma_sleep(300);
                    engine->noteOff((uint8_t)midis[i]);
                }
            }
        }
        ma_sleep(1);
    }
#else
    struct termios oldt, newt;
    tcgetattr(STDIN_FILENO, &oldt);
    newt = oldt;
    newt.c_lflag &= ~(ICANON | ECHO);
    tcsetattr(STDIN_FILENO, TCSANOW, &newt);
    fcntl(STDIN_FILENO, F_SETFL, O_NONBLOCK);
    while (true) {
        char ch;
        if (read(STDIN_FILENO, &ch, 1) == 1) {
            if (ch == 'q' || ch == 'Q') break;
            if (ch == 'z') {
                sustain = !sustain;
                engine->sustainPedal(sustain ? 127 : 0);
            }
            for (int i = 0; i < 8; i++) {
                if (ch == keys[i]) {
                    engine->noteOn((uint8_t)midis[i], 80);
                    ma_sleep(300);
                    engine->noteOff((uint8_t)midis[i]);
                }
            }
        }
        ma_sleep(1);
    }
    tcsetattr(STDIN_FILENO, TCSANOW, &oldt);
#endif

    midi.close();
    engine->stop();
    logger.log("runCoreEngine", LogSeverity::Info,
               "=== IthacaCoreResonator STOPPED ===");
    return 0;
}
