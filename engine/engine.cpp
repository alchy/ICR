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

Engine::Engine() = default;

Engine::~Engine() {
    stop();
    delete[] buf_l_;
    delete[] buf_r_;
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

    // Apply saved block size (if present in config)
    int bs = config_.blockSize();
    if (bs > 0 && bs != audio_.blockSize())
        audio_.setBlockSize(bs);

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
    buf_l_ = new float[audio_.blockSize()];
    buf_r_ = new float[audio_.blockSize()];
    dsp_.prepare((float)sample_rate_, audio_.blockSize());

    float bps = (float)sample_rate_ / (float)audio_.blockSize();
    peak_decay_coeff_ = std::pow(10.f, -1.f / bps);  // -20 dB/s

    // Auto-load soundboard IR from config (if not already loaded via --ir)
    loadIrFromConfig(core_name);
    applyDspDefaults(core_name);

    logger_.log("Engine", LogSeverity::Info,
        std::string("Ready. Core=") + active_core_->coreName() +
        " SR=" + std::to_string(sample_rate_) +
        " block=" + std::to_string(audio_.blockSize()));
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

// ── Per-core parameter serialization ─────────────────────────────────────────

void Engine::saveCoreParams(const std::string& core_name) {
    // DSP bus params (MIDI 0-127 range, stored as string integers)
    auto setI = [&](const std::string& k, int v) {
        config_.setValue(core_name, k, std::to_string(v));
    };

    // Reconstruct MIDI values from physical values in master bus
    // master_gain: physical 0..2 (square law) → MIDI = sqrt(g/2)*127
    float g = master_bus_.gain();
    setI("master_gain", (int)(std::sqrt(g / 2.f) * 127.f + 0.5f));

    // pan: reconstruct from L/R coefficients → MIDI 0-127
    float pl = master_bus_.panL(), pr = master_bus_.panR();
    int pan_midi;
    if (pl < 1.f)      pan_midi = 64 + (int)((1.f - pl) * 64.f + 0.5f);
    else if (pr < 1.f)  pan_midi = 64 - (int)((1.f - pr) * 64.f + 0.5f);
    else                pan_midi = 64;
    setI("master_pan", pan_midi);

    // LFO: physical → MIDI
    setI("lfo_speed", (int)(master_bus_.lfoSpeed() / 2.f * 127.f + 0.5f));
    setI("lfo_depth", (int)(master_bus_.lfoDepth() * 127.f + 0.5f));

    // DSP chain
    setI("limiter_threshold", (int)dsp_.getLimiterThreshold());
    setI("limiter_release",   (int)dsp_.getLimiterRelease());
    setI("limiter_enabled",   dsp_.isLimiterEnabled() ? 64 : 0);
    setI("bbe_definition",    (int)dsp_.getBBEDefinition());
    setI("bbe_bass_boost",    (int)dsp_.getBBEBassBoost());
    setI("convolver_enabled", dsp_.isConvolverEnabled() ? 64 : 0);
    setI("convolver_mix",     (int)(dsp_.getConvolverMix() * 127.f + 0.5f));

    // Core-specific params via describeParams()
    ISynthCore* c = coreByName(core_name);
    if (c) {
        auto params = c->describeParams();
        for (const auto& p : params) {
            // Store as "cp_<key>" to distinguish from DSP keys
            config_.setValue(core_name, "cp_" + p.key,
                            std::to_string(p.value));
        }
    }

    logger_.log("Engine", LogSeverity::Info,
                "Saved params for " + core_name);
}

void Engine::loadCoreParams(const std::string& core_name) {
    // Restore DSP bus params
    applyDspDefaults(core_name);

    // Restore core-specific params via setParam()
    ISynthCore* c = coreByName(core_name);
    if (!c) return;

    auto params = c->describeParams();
    for (const auto& p : params) {
        std::string v = config_.value(core_name, "cp_" + p.key);
        if (v.empty()) continue;
        try {
            float fv = std::stof(v);
            c->setParam(p.key, fv);
        } catch (...) {}
    }
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
        uint32_t chunk = rem < (uint32_t)eng->audio_.blockSize()
                       ? rem : (uint32_t)eng->audio_.blockSize();
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
    if (!audio_.start(audioCallback, this, sample_rate_, audio_.blockSize())) {
        logger_.log("Engine", LogSeverity::Error, "Failed to start audio device");
        return false;
    }

    // Check if audio device negotiated a different sample rate
    int actual_sr = audio_.sampleRate();
    if (actual_sr != sample_rate_) {
        logger_.log("Engine", LogSeverity::Warning,
            "Audio device negotiated SR=" + std::to_string(actual_sr)
            + " (requested " + std::to_string(sample_rate_) + ")");
        sample_rate_ = actual_sr;

        // Propagate new SR to all instantiated cores
        for (auto& [name, core] : cores_) {
            core->setSampleRate((float)sample_rate_);
            logger_.log("Engine", LogSeverity::Info,
                "Core SR updated: " + name + " -> " + std::to_string(sample_rate_));
        }

        // Update DSP chain and peak metering
        dsp_.prepare((float)sample_rate_, audio_.blockSize());
        dsp::agc_init(agc_, (float)sample_rate_);
        float bps = (float)sample_rate_ / (float)audio_.blockSize();
        peak_decay_coeff_ = std::pow(10.f, -1.f / bps);
    }

    logger_.log("Engine", LogSeverity::Info,
        "Audio started: " + audio_.deviceName()
        + " SR=" + std::to_string(sample_rate_)
        + " block=" + std::to_string(audio_.blockSize()));
    return true;
}

void Engine::stop() {
    if (!audio_.isRunning()) return;
    audio_.stop();
    logger_.log("Engine", LogSeverity::Info, "Audio stopped");
}

bool Engine::setBlockSize(int block_size) {
    // Reallocate work buffers
    delete[] buf_l_;
    delete[] buf_r_;
    buf_l_ = new float[block_size];
    buf_r_ = new float[block_size];
    dsp_.prepare((float)sample_rate_, block_size);

    float bps = (float)sample_rate_ / (float)block_size;
    peak_decay_coeff_ = std::pow(10.f, -1.f / bps);

    // Restart audio device with new block size
    if (!audio_.setBlockSize(block_size)) {
        logger_.log("Engine", LogSeverity::Error,
                    "setBlockSize: audio restart failed");
        return false;
    }
    config_.setBlockSize(block_size);
    logger_.log("Engine", LogSeverity::Info,
                "Block size: " + std::to_string(block_size)
                + " (" + std::to_string(1000.f * block_size / sample_rate_)
                .substr(0, 4) + " ms)");
    return true;
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

