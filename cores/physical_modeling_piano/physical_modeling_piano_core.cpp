/*
 * cores/physical_modeling_piano/physical_modeling_piano_core.cpp
 * --------------------------------------------------------------
 * Karplus-Strong string synthesis with per-note bank parameters.
 *
 * Validated model from Python prototype (tests/test_string.py):
 *   - Full-period delay, no sign inversion
 *   - Fourier excitation (two-stage knee rolloff, odd_boost, gauge)
 *   - One-pole loss filter from T60 (Smith/Bank design)
 *   - Allpass dispersion cascade (3-16 stages)
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
static constexpr float TAU = dsp::TAU;

// -- Constructor --------------------------------------------------------------

PhysicalModelingPianoCore::PhysicalModelingPianoCore() {}

// -- Defaults -----------------------------------------------------------------

void PhysicalModelingPianoCore::populateDefaults(int midi_from, int midi_to) {
    for (int m = midi_from; m <= midi_to; m++) {
        PhysicsNoteParam& np = note_params_[m];
        np.valid        = true;
        np.midi         = m;
        np.f0_hz        = 440.f * std::pow(2.f, (float)(m - 69) / 12.f);
        float t = (float)(m - 21) / 87.f;

        np.B            = physics::default_B(m);
        np.gauge        = (m <= 48) ? 4.f - t * 27.f / 87.f * 1.5f
                        : (m <= 72) ? 2.5f - (float)(m-48)/24.f * 1.f
                        : 1.5f - (float)(m-72)/36.f * 0.7f;
        np.T60_fund     = 10.f - t * 9.f;
        np.T60_nyq      = 0.35f - t * 0.2f;
        np.exc_rolloff  = 0.1f;
        np.exc_x0       = 1.f / 7.f;
        np.odd_boost    = 2.f - t * 0.5f;
        np.knee_k       = (int)(12.f - t * 4.f);
        np.knee_slope   = 3.5f + t * 0.5f;
        np.n_harmonics  = 80;
        np.n_strings    = 1;  // single string in this iteration
        np.detune_cents = 1.f;

        float N = 48000.f / np.f0_hz;
        float beta = np.B * N * N;
        int n_raw = (int)(beta * 0.5f);
        np.n_disp_stages = (n_raw < 3) ? 0 : (std::min)(n_raw, 16);
        np.disp_coeff    = -0.15f;
    }
}

// -- JSON loading -------------------------------------------------------------

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
               "Ready. SR=" + std::to_string((int)sr));
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
        np.exc_rolloff   = s.value("exc_rolloff", np.exc_rolloff);
        np.exc_x0        = s.value("exc_x0", np.exc_x0);
        np.odd_boost     = s.value("odd_boost", np.odd_boost);
        np.knee_k        = s.value("knee_k", np.knee_k);
        np.knee_slope    = s.value("knee_slope", np.knee_slope);
        np.n_harmonics   = s.value("n_harmonics", np.n_harmonics);
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

// -- MIDI ---------------------------------------------------------------------

void PhysicalModelingPianoCore::noteOn(uint8_t midi, uint8_t velocity) {
    if (midi >= PHYS_MAX_VOICES) return;
    if (velocity == 0) { noteOff(midi); return; }
    patch_mgr_.noteOn(midi, velocity, note_params_, voice_mgr_,
                      sample_rate_,
                      keyboard_spread_.load(std::memory_order_relaxed),
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

// -- PatchManager -------------------------------------------------------------

void PhysicsPatchManager::noteOn(uint8_t midi, uint8_t velocity,
                                  PhysicsNoteParam note_params[],
                                  PhysicsVoiceManager& vm,
                                  float sr, float keyboard_spread,
                                  std::mutex& bank_mutex) noexcept {
    std::unique_lock<std::mutex> lk(bank_mutex, std::try_to_lock);
    if (!lk.owns_lock()) return;
    if (!note_params[midi].valid) return;

    vm.initVoice(midi, velocity, note_params[midi], sr, keyboard_spread);
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

// -- VoiceManager -------------------------------------------------------------

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

void PhysicsVoiceManager::initVoice(int midi, uint8_t velocity,
                                     const PhysicsNoteParam& np, float sr,
                                     float keyboard_spread) noexcept {
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

    // Panning
    float angle = (PI / 4.f) + ((float)midi - 64.5f) / 87.f * keyboard_spread * 0.5f;
    v.pan_l = std::cos(angle);
    v.pan_r = std::sin(angle);

    // Output scale (velocity-dependent)
    v.output_scale = 0.4f * vel_norm;

    // -- Loss filter (Smith/Bank T60 design) ----------------------------------
    float T60_nyq_eff = np.T60_nyq / (std::max)(np.gauge, 0.1f);
    v.loss = physics::compute_loss_filter(np.f0_hz, np.T60_fund, T60_nyq_eff, sr);

    // -- Delay line -----------------------------------------------------------
    float N_total = sr / np.f0_hz;
    float filter_delay = v.loss.b / (std::max)(1.f - v.loss.b * v.loss.b, 0.01f);
    float full_N = N_total - filter_delay;
    int N_int = (std::max)(4, (int)full_N);
    float frac = full_N - (float)N_int;
    if (frac < 0.1f) { N_int -= 1; frac += 1.f; }
    v.ap_frac_a = (1.f - frac) / (1.f + frac);
    v.ap_frac_state = 0.f;

    // Reset delay line
    physics::DelayTuning dt = { N_int, v.ap_frac_a };
    physics::delay_reset(v.delay, dt);

    // -- Dispersion cascade ---------------------------------------------------
    v.n_disp     = np.n_disp_stages;
    v.disp_coeff = np.disp_coeff;
    for (int i = 0; i < PHYS_MAX_DISP; i++) v.disp_states[i] = 0.f;

    // -- Fourier excitation (matching Python make_string_v2) ------------------
    float exc_x0    = np.exc_x0;
    float rolloff   = np.exc_rolloff;
    float odd_b     = np.odd_boost;
    int   knee_k    = np.knee_k;
    float knee_sl   = np.knee_slope;
    float gauge     = np.gauge;
    int   n_harm    = (std::min)(np.n_harmonics, N_int / 2);

    for (int k = 1; k <= n_harm; k++) {
        float modal = std::sin((float)k * PI * exc_x0);
        float ak;
        if (k <= knee_k) {
            ak = (rolloff > 0.f) ? modal / std::pow((float)k, rolloff) : modal;
        } else {
            float ak_knee = (rolloff > 0.f) ? modal / std::pow((float)knee_k, rolloff) : modal;
            ak = ak_knee * std::pow((float)knee_k / (float)k, knee_sl);
        }
        // Odd boost
        if (k % 2 != 0) ak *= odd_b;
        // Gauge effect
        if (gauge != 1.f) ak *= std::pow(gauge, 1.f - (float)k * 0.05f);
        // Inharmonicity in excitation
        float f_k_ratio = (np.B > 0.f) ? (float)k * std::sqrt(1.f + np.B * (float)(k * k))
                                        : (float)k;
        // Write into delay
        for (int i = 0; i < N_int; i++) {
            v.delay.buf[i] += ak * std::sin(TAU * f_k_ratio * (float)i / (float)N_int);
        }
    }

    // Remove DC + normalize
    float dc = 0.f;
    for (int i = 0; i < N_int; i++) dc += v.delay.buf[i];
    dc /= (float)N_int;
    float peak = 0.f;
    for (int i = 0; i < N_int; i++) {
        v.delay.buf[i] -= dc;
        float a = std::abs(v.delay.buf[i]);
        if (a > peak) peak = a;
    }
    if (peak > 0.f) {
        float scale = vel_norm * 0.5f / peak;
        for (int i = 0; i < N_int; i++) v.delay.buf[i] *= scale;
    }

    // -- Hammer noise ---------------------------------------------------------
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

// -- Voice::process -----------------------------------------------------------

bool PhysicsVoice::process(float* out_l, float* out_r, int n_samples) noexcept {
    int N = delay.len;

    for (int i = 0; i < n_samples; i++) {
        // Onset gate
        float gate = 1.f;
        if (in_onset) {
            onset_gain += onset_step;
            if (onset_gain >= 1.f) { onset_gain = 1.f; in_onset = false; }
            gate = onset_gain;
        }

        // Read oldest sample from delay
        int read_ptr = (delay.write_ix + 1) % N;
        float sample = delay.buf[read_ptr];

        // Fractional delay allpass
        float y = ap_frac_a * sample + ap_frac_state;
        ap_frac_state = sample - ap_frac_a * y;

        // Dispersion cascade
        float x = y;
        for (int di = 0; di < n_disp; di++) {
            float dy = disp_coeff * x + disp_states[di];
            disp_states[di] = x - disp_coeff * dy;
            x = dy;
        }

        // Loss filter
        float filtered = loss.g * ((1.f - loss.b) * x + loss.b * loss.s);
        loss.s = filtered;

        // Write back (no sign inversion)
        delay.buf[delay.write_ix] = filtered;
        delay.write_ix = (delay.write_ix + 1) % N;

        // Output = bridge sample * scale * gate
        float out = sample * output_scale * gate;

        // Release
        if (releasing) {
            out *= rel_gain;
            rel_gain += rel_step;
            if (rel_gain <= 0.f) { rel_gain = 0.f; active = false; }
        }

        // Hammer noise
        if (noise_env > 1e-5f) {
            float white = noise_amp * noise_env * ndist(rng);
            noise_env *= noise_decay;
            float nL = dsp::biquad_df2_tick(white, noise_bpf, noise_wL);
            float nR = dsp::biquad_df2_tick(white, noise_bpf, noise_wR);
            out_l[i] += nL * gate;
            out_r[i] += nR * gate;
        }

        out_l[i] += out * pan_l;
        out_r[i] += out * pan_r;

        t_samples++;
        if (!active) break;
        if ((uint64_t)t_samples >= max_t_samp) { active = false; break; }
    }
    return active;
}

// -- processBlock (RT) --------------------------------------------------------

bool PhysicalModelingPianoCore::processBlock(float* out_l, float* out_r,
                                              int n_samples) noexcept {
    return voice_mgr_.processBlock(out_l, out_r, n_samples);
}

// -- Parameters ---------------------------------------------------------------

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
    if (key == "odd_scale") {
        odd_scale_.store((std::max)(0.5f, (std::min)(3.f, value)), std::memory_order_relaxed);
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
    if (key == "odd_scale")       { out = odd_scale_.load(std::memory_order_relaxed);       return true; }
    if (key == "gauge_scale")     { out = gauge_scale_.load(std::memory_order_relaxed);     return true; }
    return false;
}

std::vector<CoreParamDesc> PhysicalModelingPianoCore::describeParams() const {
    return {
        { "brightness",      "Brightness",      "Timbre",  "",
          brightness_.load(),      0.1f, 4.f, false },
        { "stiffness_scale", "Stiffness",       "Timbre",  "",
          stiffness_scale_.load(), 0.1f, 4.f, false },
        { "sustain_scale",   "Sustain",         "Timbre",  "",
          sustain_scale_.load(),   0.1f, 4.f, false },
        { "odd_scale",       "Odd Emphasis",    "Timbre",  "",
          odd_scale_.load(),       0.5f, 3.f, false },
        { "gauge_scale",     "Gauge Scale",     "String",  "",
          gauge_scale_.load(),     0.5f, 4.f, false },
        { "keyboard_spread", "Keyboard Spread", "Stereo",  "rad",
          keyboard_spread_.load(), 0.f,  3.14159f, false },
    };
}

// -- Visualization ------------------------------------------------------------

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
        vv.midi   = last_midi;
        vv.vel    = last_vel;
        vv.f0_hz  = np.f0_hz;
        vv.B      = np.B;
        vv.n_strings = np.n_strings;
        vs.last_note       = std::move(vv);
        vs.last_note_valid = true;
    }
    return vs;
}
