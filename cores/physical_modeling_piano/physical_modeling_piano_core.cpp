/*
 * cores/physical_modeling_piano/physical_modeling_piano_core.cpp
 * --------------------------------------------------------------
 * Dual-rail waveguide piano with Chaigne-Askenfelt hammer model.
 *
 * v1.0: Complete rewrite from single-rail Fourier excitation to
 * dual-rail (Teng 2012 / Smith 1992) with physics-based FD hammer.
 * Multi-string (1-3) with detuning and stereo panning.
 *
 * Validated against Python prototype (tools-physical/generate_teng_v2.py).
 */

#include "physical_modeling_piano_core.h"
#include "physical_modeling_piano_math.h"
#include "engine/synth_core_registry.h"
#include "third_party/json.hpp"

#include <fstream>
#include <algorithm>
#include <cstdio>
#include <cstring>
#include <cmath>

using json = nlohmann::json;

REGISTER_SYNTH_CORE("PhysicalModelingPianoCore", PhysicalModelingPianoCore)

static constexpr float PI = dsp::PI;

// ── Constructor ──────────────────────────────────────────────────────────

PhysicalModelingPianoCore::PhysicalModelingPianoCore() {}

// ── Defaults (physics-based, matching Python _default_note_params) ───────

void PhysicalModelingPianoCore::populateDefaults(int midi_from, int midi_to) {
    for (int m = midi_from; m <= midi_to; m++) {
        PhysicsNoteParam& np = note_params_[m];
        np.valid        = true;
        np.midi         = m;
        np.f0_hz        = 440.f * std::pow(2.f, (float)(m - 69) / 12.f);
        float t = (float)(m - 21) / 87.f;

        np.B            = physics::default_B(m);
        np.gauge        = physics::default_gauge(m);
        np.T60_fund     = physics::hammer::lerp(12.f, 1.5f, t);
        np.T60_nyq      = physics::hammer::lerp(0.30f, 0.15f, t);
        np.exc_x0       = 1.f / 7.f;
        np.n_strings    = physics::default_n_strings(m);
        np.detune_cents = physics::default_detune_cents(m);

        float N = sample_rate_ / np.f0_hz;
        float beta = np.B * N * N;
        int n_raw = (int)(beta * 0.5f);
        np.n_disp_stages = (n_raw < 3) ? 0 : (std::min)(n_raw, 16);
        np.disp_coeff    = -0.15f;
    }
}

// ── JSON loading ─────────────────────────────────────────────────────────

bool PhysicalModelingPianoCore::load(const std::string& params_path, float sr,
                                      Logger& logger, int midi_from, int midi_to) {
    sample_rate_ = sr;
    int from = (std::max)(0, midi_from);
    int to = (std::min)(127, midi_to);

    for (int m = 0; m < 128; m++) note_params_[m] = PhysicsNoteParam{};
    populateDefaults(from, to);

    if (!params_path.empty()) {
        std::ifstream f(params_path);
        if (f.is_open()) {
            std::string json_str((std::istreambuf_iterator<char>(f)),
                                  std::istreambuf_iterator<char>());
            if (loadBankFromJson(json_str, logger))
                logger.log("PhysicalModelingPianoCore", LogSeverity::Info,
                           "Bank loaded: " + params_path);
        } else {
            logger.log("PhysicalModelingPianoCore", LogSeverity::Info,
                       "No bank file, using defaults");
        }
    }

    loaded_ = true;
    logger.log("PhysicalModelingPianoCore", LogSeverity::Info,
               "Ready (v1.0 dual-rail). SR=" + std::to_string((int)sr));
    return true;
}

bool PhysicalModelingPianoCore::loadBankFromJson(const std::string& json_str,
                                                  Logger& logger) {
    json root;
    try { root = json::parse(json_str); }
    catch (...) { return false; }

    if (!root.contains("notes")) return false;

    int count = 0;
    json notes_j = root["notes"];
    for (json::iterator it = notes_j.begin(); it != notes_j.end(); ++it) {
        json s = it.value();
        int midi = s.value("midi", -1);
        if (midi < 0 || midi > 127) continue;

        PhysicsNoteParam& np = note_params_[midi];
        np.valid         = true;
        np.midi          = midi;
        np.f0_hz         = s.value("f0_hz", np.f0_hz);
        np.B             = s.value("B", np.B);
        np.gauge         = s.value("gauge", np.gauge);
        np.T60_fund      = s.value("T60_fund", np.T60_fund);
        np.T60_nyq       = s.value("T60_nyq", np.T60_nyq);
        np.exc_x0        = s.value("exc_x0", np.exc_x0);
        np.n_disp_stages = s.value("n_disp_stages", np.n_disp_stages);
        np.disp_coeff    = s.value("disp_coeff", np.disp_coeff);
        np.n_strings     = s.value("n_strings", np.n_strings);
        np.detune_cents  = s.value("detune_cents", np.detune_cents);
        count++;
    }
    return count > 0;
}

bool PhysicalModelingPianoCore::loadBankJson(const std::string& json_str) {
    Logger dummy;
    std::lock_guard<std::mutex> lk(bank_mutex_);
    return loadBankFromJson(json_str, dummy);
}

void PhysicalModelingPianoCore::setSampleRate(float sr) { sample_rate_ = sr; }

// ── MIDI ─────────────────────────────────────────────────────────────────

void PhysicalModelingPianoCore::noteOn(uint8_t midi, uint8_t velocity) {
    if (midi >= PHYS_MAX_VOICES) return;
    if (velocity == 0) { noteOff(midi); return; }
    patch_mgr_.noteOn(midi, velocity, note_params_, voice_mgr_,
                      sample_rate_,
                      keyboard_spread_.load(std::memory_order_relaxed),
                      stereo_spread_.load(std::memory_order_relaxed),
                      bank_mutex_);
}

void PhysicalModelingPianoCore::noteOff(uint8_t midi) {
    if (midi >= PHYS_MAX_VOICES) return;
    patch_mgr_.noteOff(midi, voice_mgr_, sample_rate_);
}

void PhysicalModelingPianoCore::sustainPedal(bool down) {
    patch_mgr_.sustainPedal(down, voice_mgr_, sample_rate_);
}

void PhysicalModelingPianoCore::allNotesOff() {
    patch_mgr_.allNotesOff(voice_mgr_, sample_rate_);
}

// ── PatchManager ─────────────────────────────────────────────────────────

void PhysicsPatchManager::noteOn(uint8_t midi, uint8_t velocity,
                                  PhysicsNoteParam note_params[],
                                  PhysicsVoiceManager& vm,
                                  float sr, float keyboard_spread,
                                  float stereo_spread,
                                  std::mutex& bank_mutex) noexcept {
    std::unique_lock<std::mutex> lk(bank_mutex, std::try_to_lock);
    if (!lk.owns_lock()) return;
    if (!note_params[midi].valid) return;

    vm.initVoice(midi, velocity, note_params[midi], sr,
                 keyboard_spread, stereo_spread);
    last_midi_.store(midi,     std::memory_order_relaxed);
    last_vel_ .store(velocity, std::memory_order_relaxed);
}

void PhysicsPatchManager::noteOff(uint8_t midi, PhysicsVoiceManager& vm,
                                   float sr) noexcept {
    if (sustain_.load(std::memory_order_relaxed))
        delayed_offs_[midi].store(true, std::memory_order_relaxed);
    else
        vm.releaseVoice(midi, sr);
}

void PhysicsPatchManager::sustainPedal(bool down, PhysicsVoiceManager& vm,
                                        float sr) noexcept {
    sustain_.store(down, std::memory_order_relaxed);
    if (!down) {
        for (int m = 0; m < PHYS_MAX_VOICES; m++) {
            if (delayed_offs_[m].load(std::memory_order_relaxed)) {
                vm.releaseVoice(m, sr);
                delayed_offs_[m].store(false, std::memory_order_relaxed);
            }
        }
    }
}

void PhysicsPatchManager::allNotesOff(PhysicsVoiceManager& vm, float sr) noexcept {
    vm.releaseAll(sr);
    for (int m = 0; m < PHYS_MAX_VOICES; m++)
        delayed_offs_[m].store(false, std::memory_order_relaxed);
    sustain_.store(false, std::memory_order_relaxed);
}

// ── VoiceManager ─────────────────────────────────────────────────────────

bool PhysicsVoiceManager::processBlock(float* out_l, float* out_r,
                                        int n_samples) noexcept {
    bool any = false;
    for (int m = 0; m < PHYS_MAX_VOICES; m++) {
        if (!voices_[m].active) continue;
        voices_[m].process(out_l, out_r, n_samples);
        any = true;
    }
    return any;
}

void PhysicsVoiceManager::releaseVoice(int midi, float sr) noexcept {
    PhysicsVoice& v = voices_[midi];
    if (!v.active) return;
    v.releasing = true;
    v.rel_gain  = v.in_onset ? v.onset_gain : 1.f;
    v.rel_step  = -v.rel_gain / (PHYS_RELEASE_MS * 0.001f * sr);
}

void PhysicsVoiceManager::releaseAll(float sr) noexcept {
    for (int m = 0; m < PHYS_MAX_VOICES; m++)
        if (voices_[m].active) releaseVoice(m, sr);
}

// ── initVoice — Chaigne hammer + dual-rail setup ─────────────────────────

void PhysicsVoiceManager::initVoice(int midi, uint8_t velocity,
                                     const PhysicsNoteParam& np, float sr,
                                     float keyboard_spread,
                                     float stereo_spread) noexcept {
    PhysicsVoice& v = voices_[midi];

    v.active    = true;
    v.releasing = false;
    v.in_onset  = true;
    v.midi      = midi;
    v.t_samples = 0;

    float vel_norm = (float)velocity / 127.f;

    // Max duration
    float dur_s = (std::min)(10.f * np.T60_fund, 30.f);
    if (dur_s < 2.f) dur_s = 2.f;
    v.max_t_samp = (uint64_t)(dur_s * sr);

    // Onset/release
    v.onset_gain = 0.f;
    v.onset_step = 1.f / (PHYS_ONSET_MS * 0.001f * sr);
    v.rel_gain   = 1.f;
    v.rel_step   = 0.f;

    // Output scale — must guarantee no clipping on a single note.
    // Chaigne hammer produces peak amplitude ~11 * vel_norm at full velocity.
    // Target: peak ≈ -3 dB (0.7) at vel_idx=7.
    // Scale = 0.065 gives: 11 * 0.953 * 0.065 ≈ 0.68 → -3.3 dB headroom.
    v.output_scale = 0.065f;

    // ── Chaigne-Askenfelt hammer ─────────────────────────────────────
    float v0 = physics::velocity_to_v0(vel_norm);
    v.hammer_len = physics::hammer::compute_force(
        midi, v0, np.exc_x0, sr, v.hammer_v_in);

    // ── Multi-string setup ──────────────────────────────────────────
    v.n_strings = np.n_strings;

    // Compute detuned frequencies and panning
    physics::StringDetuning sd = physics::compute_detuning(
        np.n_strings, np.detune_cents, np.f0_hz, stereo_spread);

    // Dispersion
    int   n_disp = np.n_disp_stages;
    float a_disp = np.disp_coeff;
    if (n_disp == 0) a_disp = 0.f;

    for (int si = 0; si < sd.count; si++) {
        // Initialize each dual-rail string
        physics::dual_rail_init(v.strings[si], sd.f0s[si], sr,
                                n_disp, a_disp, np.exc_x0,
                                np.T60_fund, np.T60_nyq, np.gauge);

        // Keyboard panning (base angle) + multi-string spread
        float kb_angle = (PI / 4.f) +
            ((float)midi - 64.5f) / 87.f * keyboard_spread * 0.5f;
        float kb_pan_l = std::cos(kb_angle);
        float kb_pan_r = std::sin(kb_angle);

        // Combine keyboard pan with multi-string pan
        v.str_pan_l[si] = kb_pan_l * sd.pan_l[si] / 0.707f;
        v.str_pan_r[si] = kb_pan_r * sd.pan_r[si] / 0.707f;
    }

    // ── Hammer noise (percussive attack) ─────────────────────────────
    v.noise_amp   = 0.3f * vel_norm * vel_norm;
    v.noise_env   = 1.f;
    float noise_tau_ms = 12.f - (float)(midi - 21) / 87.f * 8.f;
    v.noise_decay = dsp::decay_coeff(noise_tau_ms * 0.001f, sr);
    float centroid = 1500.f + (float)(midi - 21) / 87.f * 3500.f;
    v.noise_bpf = dsp::rbj_bandpass(centroid, 1.5f, sr);
    v.noise_wL[0] = v.noise_wL[1] = 0.f;
    v.noise_wR[0] = v.noise_wR[1] = 0.f;
    v.rng.seed((unsigned)(midi * 1000 + velocity));
}

// ── Voice::process — dual-rail synthesis loop ────────────────────────────

bool PhysicsVoice::process(float* out_l, float* out_r, int n_samples) noexcept {
    for (int i = 0; i < n_samples; i++) {
        // Onset gate
        float gate = 1.f;
        if (in_onset) {
            onset_gain += onset_step;
            if (onset_gain >= 1.f) { onset_gain = 1.f; in_onset = false; }
            gate = onset_gain;
        }

        // Hammer input for this sample
        float h_in = (t_samples < (uint32_t)hammer_len) ? hammer_v_in[t_samples] : 0.f;

        // Sum all strings through dual-rail waveguide
        float sum_l = 0.f, sum_r = 0.f;
        for (int si = 0; si < n_strings; si++) {
            float sample = physics::dual_rail_tick(strings[si], h_in);
            sum_l += sample * str_pan_l[si];
            sum_r += sample * str_pan_r[si];
        }

        float out_val_l = sum_l * output_scale * gate;
        float out_val_r = sum_r * output_scale * gate;

        // Release
        if (releasing) {
            out_val_l *= rel_gain;
            out_val_r *= rel_gain;
            rel_gain += rel_step;
            if (rel_gain <= 0.f) { rel_gain = 0.f; active = false; }
        }

        // Hammer noise (bandpass-filtered white noise)
        if (noise_env > 1e-5f) {
            float white = noise_amp * noise_env * ndist(rng);
            noise_env *= noise_decay;
            float nL = dsp::biquad_df2_tick(white, noise_bpf, noise_wL);
            float nR = dsp::biquad_df2_tick(white, noise_bpf, noise_wR);
            out_val_l += nL * gate;
            out_val_r += nR * gate;
        }

        out_l[i] += out_val_l;
        out_r[i] += out_val_r;

        t_samples++;
        if (!active) break;
        if ((uint64_t)t_samples >= max_t_samp) { active = false; break; }
    }
    return active;
}

// ── processBlock (RT) ────────────────────────────────────────────────────

bool PhysicalModelingPianoCore::processBlock(float* out_l, float* out_r,
                                              int n_samples) noexcept {
    return voice_mgr_.processBlock(out_l, out_r, n_samples);
}

// ── Parameters ───────────────────────────────────────────────────────────

bool PhysicalModelingPianoCore::setParam(const std::string& key, float value) {
    if (key == "brightness") {
        brightness_.store((std::max)(0.1f, (std::min)(4.f, value)), std::memory_order_relaxed);
        return true;
    }
    if (key == "stiffness_scale") {
        stiffness_scale_.store((std::max)(0.1f, (std::min)(4.f, value)), std::memory_order_relaxed);
        return true;
    }
    if (key == "sustain_scale") {
        sustain_scale_.store((std::max)(0.1f, (std::min)(4.f, value)), std::memory_order_relaxed);
        return true;
    }
    if (key == "keyboard_spread") {
        keyboard_spread_.store((std::max)(0.f, (std::min)(3.14159f, value)), std::memory_order_relaxed);
        return true;
    }
    if (key == "stereo_spread") {
        stereo_spread_.store((std::max)(0.f, (std::min)(1.f, value)), std::memory_order_relaxed);
        return true;
    }
    if (key == "gauge_scale") {
        gauge_scale_.store((std::max)(0.5f, (std::min)(4.f, value)), std::memory_order_relaxed);
        return true;
    }
    return false;
}

bool PhysicalModelingPianoCore::getParam(const std::string& key, float& out) const {
    if (key == "brightness")      { out = brightness_.load(std::memory_order_relaxed);      return true; }
    if (key == "stiffness_scale") { out = stiffness_scale_.load(std::memory_order_relaxed); return true; }
    if (key == "sustain_scale")   { out = sustain_scale_.load(std::memory_order_relaxed);   return true; }
    if (key == "keyboard_spread") { out = keyboard_spread_.load(std::memory_order_relaxed); return true; }
    if (key == "stereo_spread")   { out = stereo_spread_.load(std::memory_order_relaxed);   return true; }
    if (key == "gauge_scale")     { out = gauge_scale_.load(std::memory_order_relaxed);     return true; }
    return false;
}

std::vector<CoreParamDesc> PhysicalModelingPianoCore::describeParams() const {
    return {
        { "brightness",      "Brightness",        "Timbre",  "",
          brightness_.load(),      0.1f, 4.f, false },
        { "stiffness_scale", "Stiffness",         "Timbre",  "",
          stiffness_scale_.load(), 0.1f, 4.f, false },
        { "sustain_scale",   "Sustain",           "Timbre",  "",
          sustain_scale_.load(),   0.1f, 4.f, false },
        { "gauge_scale",     "Gauge Scale",       "String",  "",
          gauge_scale_.load(),     0.5f, 4.f, false },
        { "keyboard_spread", "Keyboard Spread",   "Stereo",  "rad",
          keyboard_spread_.load(), 0.f,  3.14159f, false },
        { "stereo_spread",   "Multi-String Spread","Stereo",  "",
          stereo_spread_.load(),   0.f,  1.f, false },
    };
}

// ── Visualization ────────────────────────────────────────────────────────

CoreVizState PhysicalModelingPianoCore::getVizState() const {
    CoreVizState vs;
    for (int m = 0; m < PHYS_MAX_VOICES; m++) {
        if (voice_mgr_.voice(m).active) {
            vs.active_midi_notes.push_back(m);
            vs.active_voice_count++;
        }
    }

    int last_midi = patch_mgr_.lastMidi();
    int last_vel  = patch_mgr_.lastVel();
    if (last_midi >= 0 && last_midi < 128) {
        const PhysicsNoteParam& np = note_params_[last_midi];
        CoreVoiceViz vv;
        vv.midi      = last_midi;
        vv.vel       = last_vel;
        vv.f0_hz     = np.f0_hz;
        vv.B         = np.B;
        vv.n_strings = np.n_strings;
        vs.last_note       = std::move(vv);
        vs.last_note_valid = true;
    }
    return vs;
}
