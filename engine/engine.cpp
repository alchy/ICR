/*
 * engine.cpp
 * ────────────────
 * Generic RT engine for any ISynthCore.
 *
 * Audio thread flow:
 *   audioCallback()
 *     → processBlock(L*, R*, n)
 *         → drain MIDI queue → core->noteOn/Off/sustainPedal
 *         → memset buffers to 0
 *         → core->processBlock(L, R, n)     [additive]
 *         → master_bus_.process(L, R, n, sr)
 *         → dsp_.process(L, R, n)
 *         → interleave L+R → float32 stereo output
 *         → update peak meter
 */

// miniaudio implementation — compiled once here
#define MINIAUDIO_IMPLEMENTATION
#include "miniaudio.h"

#include "engine.h"
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

// ── MIDI event queue — see pushMidiEvt (Engine member) ──────────────────────

// ── MIDI queue (instance-local, SPSC lock-free) ───────────────────────────────

void Engine::pushMidiEvt(MidiEvt::Type t, uint8_t midi, uint8_t val) noexcept {
    int w    = midi_w_.load(std::memory_order_relaxed);
    int next = (w + 1) % MIDI_Q_SIZE;
    if (next == midi_r_.load(std::memory_order_acquire)) {
        logger_.log("Engine", LogSeverity::Warning, "MIDI queue full, event dropped");
        return;
    }
    midi_q_[w] = {t, midi, val};
    midi_w_.store(next, std::memory_order_release);
}

// ── Constructor / Destructor ──────────────────────────────────────────────────

Engine::Engine()
    : device_(new ma_device{}) {}

Engine::~Engine() {
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
        logger.log("Engine", LogSeverity::Warning,
                   "Config file not found: " + path);
        return;
    }
    nlohmann::json j;
    try { f >> j; }
    catch (const std::exception& e) {
        logger.log("Engine", LogSeverity::Warning,
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
    logger.log("Engine", LogSeverity::Info,
               "Config loaded: " + path +
               " (" + std::to_string(applied) + " params applied)");
}

// ── loadEngineConfig ──────────────────────────────────────────────────────────

bool Engine::loadEngineConfig(const std::string& config_path, Logger& logger) {
    if (!config_.load(config_path, logger))
        return false;

    // Open log file if configured
    const std::string& log_path = config_.logFilePath();
    if (!log_path.empty()) {
        log_file_handle_ = std::fopen(log_path.c_str(), "w");
        if (log_file_handle_) {
            logger = Logger(log_file_handle_, stdout);
            logger_ = logger;
            logger.log("Engine", LogSeverity::Info,
                       "Log file opened: " + log_path);
        } else {
            logger.log("Engine", LogSeverity::Warning,
                       "Cannot open log file: " + log_path);
        }
    }
    return true;
}

// ── initialize ────────────────────────────────────────────────────────────────

bool Engine::initialize(const std::string& core_name,
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

    logger_.log("Engine", LogSeverity::Info,
                "Initializing core: " + core_name
                + (resolved_params.empty() ? "" : " params=" + resolved_params));

    auto core = SynthCoreRegistry::instance().create(core_name);
    if (!core) {
        logger_.log("Engine", LogSeverity::Error,
                    "Unknown core: '" + core_name + "'. Available:");
        for (const auto& n : SynthCoreRegistry::instance().availableCores())
            logger_.log("Engine", LogSeverity::Info, "  - " + n);
        return false;
    }

    if (!core->load(resolved_params, (float)sample_rate_, logger_, midi_from, midi_to)) {
        logger_.log("Engine", LogSeverity::Error, "Core load failed");
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

    logger_.log("Engine", LogSeverity::Info,
        std::string("Ready. Core=") + active_core_->coreName() +
        " SR=" + std::to_string(sample_rate_) +
        " block=" + std::to_string(block_size_));
    return true;
}

void Engine::loadIrFromConfig(const std::string& core_name) {
    // Set soundboard directory for GUI IR selector
    std::string sb_dir = coreConfigValue(core_name, "soundboard_dir");
    if (!sb_dir.empty()) {
        dsp_.setSoundboardDir(sb_dir);
        logger_.log("Engine", LogSeverity::Info,
                    "Soundboard directory: " + sb_dir);
    }

    std::string ir = coreConfigValue(core_name, "ir_path");
    if (ir.empty()) {
        logger_.log("Engine", LogSeverity::Info,
                    "No ir_path configured for " + core_name);
        return;
    }
    // Don't reload if same IR is already loaded
    if (dsp_.isConvolverLoaded() && dsp_.convolver().isEnabled()) return;
    if (dsp_.loadConvolverIR(ir, (float)sample_rate_)) {
        dsp_.setConvolverEnabled(true);
        logger_.log("Engine", LogSeverity::Info,
                    "Convolver IR loaded: " + ir + " ("
                    + std::to_string(dsp_.convolver().irLength()) + " samples)");
    } else {
        logger_.log("Engine", LogSeverity::Warning,
                    "Failed to load convolver IR: " + ir);
    }
}

void Engine::applyDspDefaults(const std::string& core_name) {
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

    int conv_ena = get("convolver_enabled", -1);
    if (conv_ena >= 0) dsp_.setConvolverEnabled(conv_ena >= 64);

    int conv_mix = get("convolver_mix", -1);
    if (conv_mix >= 0) dsp_.setConvolverMix((float)conv_mix / 127.f);
}

// ── switchCore ────────────────────────────────────────────────────────────────

bool Engine::switchCore(const std::string& core_name,
                             const std::string& params_path) {
    if (core_name == active_core_name_) return true;  // already active

    // Lazy-instantiate: create core only on first use
    if (cores_.find(core_name) == cores_.end()) {
        // Resolve params: CLI arg > engine config > empty
        std::string resolved = params_path;
        if (resolved.empty())
            resolved = coreConfigValue(core_name, "params_path");

        logger_.log("Engine", LogSeverity::Info,
                    "Instantiating core: " + core_name
                    + (resolved.empty() ? "" : " params=" + resolved));

        auto new_core = SynthCoreRegistry::instance().create(core_name);
        if (!new_core) {
            logger_.log("Engine", LogSeverity::Error,
                        "Unknown core: '" + core_name + "'");
            return false;
        }

        if (!new_core->load(resolved, (float)sample_rate_, logger_)) {
            logger_.log("Engine", LogSeverity::Error,
                        "Core load failed for " + core_name);
            return false;
        }

        cores_[core_name] = std::move(new_core);
        core_params_paths_[core_name] = resolved;
    }

    // Switch active — old core's voices continue to dozvuk naturally
    // (all cores must have finite decay / max duration).
    active_core_name_ = core_name;
    active_core_      = cores_[core_name].get();

    // Load IR and DSP defaults for the new active core
    loadIrFromConfig(core_name);
    applyDspDefaults(core_name);

    logger_.log("Engine", LogSeverity::Info,
        std::string("Active core: ") + active_core_->coreName()
        + " (" + std::to_string(cores_.size()) + " cores loaded)");
    return true;
}

// ── Audio callback ────────────────────────────────────────────────────────────

void Engine::audioCallback(ma_device*  device,
                                void*       output,
                                const void* /*input*/,
                                uint32_t    frame_count) {
    auto* eng = reinterpret_cast<Engine*>(device->pUserData);
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

void Engine::processBlock(float* out_l, float* out_r, int n) noexcept {
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
    master_bus_.process(out_l, out_r, n, sample_rate_);
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


// ── start / stop ─────────────────────────────────────────────────────────────

bool Engine::start() {
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
        logger_.log("Engine", LogSeverity::Error, "Failed to open audio device");
        return false;
    }
    if (ma_device_start(device_) != MA_SUCCESS) {
        logger_.log("Engine", LogSeverity::Error, "Failed to start audio device");
        ma_device_uninit(device_);
        return false;
    }
    running_.store(true);
    logger_.log("Engine", LogSeverity::Info,
        "Audio started: " + std::string(device_->playback.name));
    return true;
}

void Engine::stop() {
    if (!running_.load()) return;
    ma_device_stop(device_);
    ma_device_uninit(device_);
    running_.store(false);
    logger_.log("Engine", LogSeverity::Info, "Audio stopped");
}

// ── Thread-safe MIDI ──────────────────────────────────────────────────────────

void Engine::noteOn(uint8_t midi, uint8_t vel) {
    last_note_midi_.store(midi, std::memory_order_relaxed);
    last_note_vel_ .store(vel,  std::memory_order_relaxed);
    pushMidiEvt(MidiEvt::NOTE_ON, midi, vel);
}
void Engine::noteOff(uint8_t midi) {
    pushMidiEvt(MidiEvt::NOTE_OFF, midi, 0);
}
void Engine::sustainPedal(uint8_t val) {
    pushMidiEvt(MidiEvt::SUSTAIN, 0, val);
}

void Engine::allNotesOff() {
    pushMidiEvt(MidiEvt::ALL_NOTES_OFF, 0, 0);
}

// ── Master mix ────────────────────────────────────────────────────────────────

void Engine::setMasterGain(uint8_t v, Logger& logger) {
    master_bus_.setGainMidi(v);
    logger.log("Engine", LogSeverity::Info,
               "Master gain MIDI=" + std::to_string(v));
}

void Engine::setMasterPan(uint8_t v) noexcept {
    master_bus_.setPanMidi(v);
}

void Engine::setPanSpeed(uint8_t v) noexcept {
    master_bus_.setLfoSpeed(2.f * (v / 127.f));   // 0..2 Hz
}

void Engine::setPanDepth(uint8_t v) noexcept {
    master_bus_.setLfoDepth(v / 127.f);
}

// ── DSP chain ─────────────────────────────────────────────────────────────────

void Engine::setLimiterThreshold(uint8_t v) noexcept { dsp_.setLimiterThreshold(v); }
void Engine::setLimiterRelease  (uint8_t v) noexcept { dsp_.setLimiterRelease(v);   }
void Engine::setLimiterEnabled  (uint8_t v) noexcept { dsp_.setLimiterEnabled(v);   }
void Engine::setBBEDefinition   (uint8_t v) noexcept { dsp_.setBBEDefinition(v);    }
void Engine::setBBEBassBoost    (uint8_t v) noexcept { dsp_.setBBEBassBoost(v);     }

// ── Stats ─────────────────────────────────────────────────────────────────────

int Engine::activeVoices() const {
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
        // Shared (additive + physical)
        case 0x01: return "f0_hz";
        case 0x02: return "B";
        // Additive-specific (0x03-0x06)
        case 0x03: return "attack_tau";
        case 0x04: return "A_noise";
        case 0x05: return "rms_gain";
        case 0x06: return "phi_diff";
        // Physical-specific (0x10-0x1D)
        case 0x10: return "gauge";
        case 0x11: return "T60_fund";
        case 0x12: return "T60_nyq";
        case 0x13: return "exc_x0";
        case 0x14: return "K_hardening";
        case 0x15: return "p_hardening";
        case 0x16: return "n_disp_stages";
        case 0x17: return "disp_coeff";
        case 0x18: return "n_strings";
        case 0x19: return "detune_cents";
        case 0x1A: return "hammer_mass";
        case 0x1B: return "string_mass";
        case 0x1C: return "output_scale";
        case 0x1D: return "bridge_refl";
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
// Engine and DspChain params (0x10+, 0x20+) are handled inline in SET_MASTER.
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

std::vector<uint8_t> Engine::handleSysEx(const uint8_t* data, int len) {
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
                logger_.log("Engine", LogSeverity::Info,
                            "SET_BANK: applied ("
                            + std::to_string(bank_chunk_buf_.size()) + " bytes)"
                            + " core_id=0x" + std::to_string((int)core_id));
            else
                logger_.log("Engine", LogSeverity::Warning,
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
                    master_bus_.setGain((std::max)(0.f, (std::min)(2.f, value)));
                    break;
                case 0x11: {
                    float n = (std::max)(-1.f, (std::min)(1.f, value));
                    if (n <= 0.f) master_bus_.setPan(1.f, 1.f + n);
                    else          master_bus_.setPan(1.f - n, 1.f);
                    break;
                }
                case 0x12:
                    master_bus_.setLfoSpeed((std::max)(0.f, (std::min)(2.f, value)));
                    break;
                case 0x13:
                    master_bus_.setLfoDepth((std::max)(0.f, (std::min)(1.f, value)));
                    break;
                }
            } else if (pid >= 0x20 && pid <= 0x26) {
                auto u = (uint8_t)((std::max)(0.f, (std::min)(1.f, value)) * 127.f);
                switch (pid) {
                case 0x20: dsp_.setLimiterThreshold(u); break;
                case 0x21: dsp_.setLimiterRelease(u);   break;
                case 0x22: dsp_.setLimiterEnabled(u);   break;
                case 0x23: dsp_.setBBEDefinition(u);    break;
                case 0x24: dsp_.setBBEBassBoost(u);     break;
                case 0x25: dsp_.setConvolverEnabled(value >= 0.5f); break;
                case 0x26: dsp_.setConvolverMix(value);              break;
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
            logger_.log("Engine", LogSeverity::Info,
                        "EXPORT_BANK: wrote " + export_path);
        else
            logger_.log("Engine", LogSeverity::Warning,
                        "EXPORT_BANK: failed to write " + export_path);
        break;
    }

    default:
        logger_.log("Engine", LogSeverity::Info,
                    "SysEx: unknown cmd 0x" + std::to_string((int)cmd));
        break;
    }
    return {};
}

// ── Batch render (delegates to batch_renderer.h) ─────────────────────────────

#include "batch_renderer.h"

int Engine::renderBatch(const std::string& batch_json_path,
                        const std::string& out_dir,
                        int sr)
{
    if (!active_core_ || !active_core_->isLoaded()) {
        logger_.log("Engine", LogSeverity::Error, "renderBatch: core not loaded");
        return 0;
    }
    sample_rate_ = sr;
    return ::renderBatch(*active_core_, logger_, batch_json_path, out_dir, sr);
}

