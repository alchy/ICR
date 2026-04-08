/*
 * cores/physical_modeling_piano/physical_modeling_piano_core.cpp
 * ──────────────────────────────────────────────────────────────
 * Digital waveguide piano synthesis -- physical model implementation.
 *
 * Energy flow per sample (per string):
 *
 *   1. Hammer force F = K_H * max(0, xi-u)^p  (first ~2-5 ms only)
 *   2. Force injected at strike position into delay_r
 *   3. Right-going wave -> bridge:
 *        reflected = k_r * incoming
 *        transmitted -> soundboard modes
 *   4. Left-going wave -> nut:
 *        reflected = -1 * incoming  (rigid termination)
 *   5. Loss filter in loop: freq-dependent damping
 *   6. Dispersion allpass: inharmonicity
 *   7. Output = sum of soundboard mode radiation + direct string
 *
 * Multi-string coupling: strings share the bridge junction.
 * The bridge force is the sum of all string transmissions,
 * and each string sees the aggregate bridge response as part
 * of its reflection.
 */

#include "physical_modeling_piano_core.h"
#include "physical_modeling_piano_math.h"
#include "engine/synth_core_registry.h"
#include "third_party/json.hpp"

#include <fstream>
#include <algorithm>
#include <cstdio>
#include <cstring>

using json = nlohmann::json;

// Self-register
REGISTER_SYNTH_CORE("PhysicalModelingPianoCore", PhysicalModelingPianoCore)

// -- Constructor --------------------------------------------------------------

PhysicalModelingPianoCore::PhysicalModelingPianoCore() {}

// -- Default physical parameters ----------------------------------------------

void PhysicalModelingPianoCore::populateDefaults(int midi_from, int midi_to) {
    for (int m = midi_from; m <= midi_to; m++) {
        PhysicsNoteParam& np = note_params_[m];
        np.valid           = true;
        np.f0_hz           = 440.f * std::pow(2.f, (float)(m - 69) / 12.f);
        np.B               = physics::default_B(m);
        np.n_strings       = physics::default_n_strings(m);
        np.detune_cents    = physics::default_detune_cents(m);
        np.K_H             = physics::default_K_H(m);
        np.p               = physics::default_p(m);
        np.M_H             = physics::default_M_H(m);
        np.x0_ratio        = 0.125f;  // standard 1/8 strike position
        np.tau_fund         = physics::default_tau_fund(m);
        np.tau_high         = physics::default_tau_high(m);
        np.impedance_ratio  = physics::default_impedance_ratio(m);
        np.gain             = 1.f;
    }
}

// -- JSON loading -------------------------------------------------------------

bool PhysicalModelingPianoCore::load(const std::string& params_path, float sr,
                                      Logger& logger,
                                      int midi_from, int midi_to) {
    sample_rate_ = sr;
    inv_sr_      = 1.f / sr;
    dt_          = 1.f / sr;

    // Clear all
    for (int m = 0; m < 128; m++)
        note_params_[m] = PhysicsNoteParam{};

    // Always populate physical defaults first
    int from = std::max(0,   midi_from);
    int to   = std::min(127, midi_to);
    populateDefaults(from, to);

    int loaded_count = to - from + 1;

    // If a JSON path is given, try to load and override defaults
    if (!params_path.empty()) {
        std::ifstream f(params_path);
        if (f.is_open()) {
            std::string json_str((std::istreambuf_iterator<char>(f)),
                                  std::istreambuf_iterator<char>());
            if (loadFromAdditiveSynthesisJson(json_str, logger, from, to)) {
                logger.log("PhysicalModelingPianoCore", LogSeverity::Info,
                           "Loaded overrides from " + params_path);
            } else {
                logger.log("PhysicalModelingPianoCore", LogSeverity::Warning,
                           "JSON load failed, using physics defaults: " + params_path);
            }
        } else {
            logger.log("PhysicalModelingPianoCore", LogSeverity::Warning,
                       "Cannot open " + params_path + ", using physics defaults");
        }
    }

    loaded_ = (loaded_count > 0);
    if (loaded_) {
        std::string range_info = (midi_from > 0 || midi_to < 127)
            ? ("  MIDI filter: " + std::to_string(from) + "-" + std::to_string(to))
            : "";
        logger.log("PhysicalModelingPianoCore", LogSeverity::Info,
                   "Ready with " + std::to_string(loaded_count) + " notes"
                   + "  SR=" + std::to_string((int)sr) + range_info);
    }
    return loaded_;
}

bool PhysicalModelingPianoCore::loadFromAdditiveSynthesisJson(
        const std::string& json_str, Logger& logger,
        int midi_from, int midi_to) {
    json root;
    try {
        root = json::parse(json_str);
    } catch (const std::exception& e) {
        logger.log("PhysicalModelingPianoCore", LogSeverity::Error,
                   std::string("JSON parse error: ") + e.what());
        return false;
    }

    if (!root.contains("notes")) return false;

    // Extract what we can from AdditiveSynthesisPianoCore format:
    //   f0_hz -> f0_hz (direct)
    //   B -> B (direct)
    //   tau1 -> tau_high (treble decay time)
    //   tau2 -> tau_fund via max partial tau2
    //   A_noise -> scales hammer noise contribution
    //   rms_gain -> output gain

    int count = 0;
    const auto& notes = root["notes"];
    for (auto it = notes.begin(); it != notes.end(); ++it) {
        const auto& s = it.value();
        int midi = s["midi"].get<int>();
        if (midi < midi_from || midi > midi_to) continue;
        // Use only vel_idx 3-4 (mid velocity) for physics defaults
        int vel_idx = s["vel"].get<int>();
        if (vel_idx != 3 && vel_idx != 4) continue;

        PhysicsNoteParam& np = note_params_[midi];
        np.valid  = true;
        np.f0_hz  = s["f0_hz"].get<float>();
        np.B      = s.value("B", np.B);
        np.gain   = s.value("rms_gain", 1.f);

        // Extract tau from partials -- use partial k=1 as representative
        if (s.contains("partials")) {
            const auto& partials = s["partials"];
            if (!partials.empty()) {
                float tau1 = partials[0].value("tau1", np.tau_high);
                float tau2 = partials[0].value("tau2", np.tau_fund);
                // tau1 from additive model ~ fast decay ~ high-freq damping
                // tau2 ~ fundamental decay
                np.tau_high = std::max(0.05f, tau1);
                np.tau_fund = std::max(0.5f, tau2);
            }
        }

        // Override string count if present
        np.n_strings = s.value("n_strings",
                               physics::default_n_strings(midi));

        count++;
    }

    return count > 0;
}

void PhysicalModelingPianoCore::setSampleRate(float sr) {
    sample_rate_ = sr;
    inv_sr_      = 1.f / sr;
    dt_          = 1.f / sr;
}

// -- MIDI (RT thread) ---------------------------------------------------------

void PhysicalModelingPianoCore::noteOn(uint8_t midi, uint8_t velocity) {
    if (midi >= PHYS_MAX_VOICES) return;
    if (velocity == 0) { noteOff(midi); return; }
    patch_mgr_.noteOn(midi, velocity, note_params_, voice_mgr_,
                      sample_rate_,
                      keyboard_spread_.load(std::memory_order_relaxed),
                      hammer_hardness_.load(std::memory_order_relaxed),
                      damping_scale_.load(std::memory_order_relaxed),
                      brightness_.load(std::memory_order_relaxed),
                      detune_scale_.load(std::memory_order_relaxed),
                      soundboard_mix_.load(std::memory_order_relaxed),
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

void PhysicsPatchManager::noteOn(
        uint8_t midi, uint8_t velocity,
        PhysicsNoteParam note_params[],
        PhysicsVoiceManager& vm,
        float sample_rate,
        float keyboard_spread,
        float hammer_hardness,
        float damping_scale,
        float brightness,
        float detune_scale,
        float soundboard_mix,
        std::mutex& bank_mutex) noexcept {
    std::unique_lock<std::mutex> lk(bank_mutex, std::try_to_lock);
    if (!lk.owns_lock()) return;

    if (!note_params[midi].valid) return;

    vm.initVoice(midi, note_params[midi], velocity, sample_rate,
                 keyboard_spread, hammer_hardness, damping_scale,
                 brightness, detune_scale, soundboard_mix);

    last_midi_.store(midi,     std::memory_order_relaxed);
    last_vel_ .store(velocity, std::memory_order_relaxed);
}

void PhysicsPatchManager::noteOff(uint8_t midi, PhysicsVoiceManager& vm,
                                   float sample_rate) noexcept {
    if (sustain_.load(std::memory_order_relaxed))
        delayed_offs_[midi].store(true, std::memory_order_relaxed);
    else
        vm.releaseVoice(midi, sample_rate);
}

void PhysicsPatchManager::sustainPedal(bool down, PhysicsVoiceManager& vm,
                                        float sample_rate) noexcept {
    sustain_.store(down, std::memory_order_relaxed);
    if (!down) {
        for (int m = 0; m < PHYS_MAX_VOICES; m++) {
            if (delayed_offs_[m].load(std::memory_order_relaxed)) {
                vm.releaseVoice((uint8_t)m, sample_rate);
                delayed_offs_[m].store(false, std::memory_order_relaxed);
            }
        }
    }
}

void PhysicsPatchManager::allNotesOff(PhysicsVoiceManager& vm,
                                       float sample_rate) noexcept {
    vm.releaseAll(sample_rate);
    for (int m = 0; m < PHYS_MAX_VOICES; m++)
        delayed_offs_[m].store(false, std::memory_order_relaxed);
    sustain_.store(false, std::memory_order_relaxed);
}

// -- VoiceManager -------------------------------------------------------------

bool PhysicsVoiceManager::processBlock(float* out_l, float* out_r,
                                        int n_samples,
                                        float inv_sr, float dt) noexcept {
    bool any = false;
    for (int m = 0; m < PHYS_MAX_VOICES; m++) {
        if (!voices_[m].active) continue;
        voices_[m].process(out_l, out_r, n_samples, inv_sr, dt);
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

void PhysicsVoiceManager::initVoice(int midi, const PhysicsNoteParam& np,
                                     uint8_t velocity, float sr,
                                     float keyboard_spread,
                                     float hammer_hardness,
                                     float damping_scale,
                                     float brightness,
                                     float detune_scale,
                                     float soundboard_mix) noexcept {
    PhysicsVoice& v = voices_[midi];

    v.active     = true;
    v.releasing  = false;
    v.in_onset   = true;
    v.midi       = midi;
    v.t_samples  = 0;
    v.n_strings  = np.n_strings;
    v.string_mix = 1.f / (float)np.n_strings;
    v.gain       = np.gain;

    // Apply runtime scales to physical parameters
    float eff_tau_fund = np.tau_fund * damping_scale;
    float eff_tau_high = np.tau_high * damping_scale * brightness;
    float eff_K_H      = np.K_H * hammer_hardness;
    float eff_detune   = np.detune_cents * detune_scale;

    // Max duration: 10 x tau_fund or 60 s
    float dur_s = std::min(10.f * eff_tau_fund, 60.f);
    if (dur_s < 3.f) dur_s = 3.f;
    v.max_t_samp = (uint64_t)(dur_s * sr);

    // Onset ramp
    v.onset_gain = 0.f;
    v.onset_step = 1.f / (PHYS_ONSET_MS * 0.001f * sr);
    v.rel_gain   = 1.f;
    v.rel_step   = 0.f;

    // Panning (constant-power, MIDI-dependent)
    float angle = (dsp::PI / 4.f)
                + ((float)midi - 64.5f) / 87.0f * keyboard_spread * 0.5f;
    v.pan_l = std::cos(angle);
    v.pan_r = std::sin(angle);

    // Impedance-matched injection: force → wave variable = F / (2 * Z_string)
    // Z_string ≈ 2 * f0 in normalized waveguide units (proportional to
    // string tension / wave speed).  This replaces the incorrect F*dt*0.5.
    v.injection_scale = 1.f / std::max(2.f * np.f0_hz, 1.f);

    // Output scale: normalize waveguide output to audible range.
    // Empirical: waveguide bridge output peaks around 0.01-0.1;
    // scale to reach ~0.3-0.5 peak for mid-velocity.
    v.output_scale = 0.5f;

    // -- Setup strings --------------------------------------------------------

    auto detuning = physics::compute_detuning(np.n_strings, eff_detune,
                                               np.f0_hz);

    for (int si = 0; si < np.n_strings; si++) {
        PhysicsString& s = v.strings[si];
        float f0 = np.f0_hz + detuning.offsets[si];
        s.f0_hz = f0;

        // Loss filter (uses scaled damping times)
        // Strengthen high-freq damping: use tau_high/4 for more aggressive rolloff.
        // Real piano strings damp high partials much faster than one-pole predicts.
        s.loss = physics::compute_loss_filter(f0, eff_tau_fund,
                                               eff_tau_high * 0.25f, sr);

        // Felt low-pass: cutoff rises with pitch (softer hammer = darker)
        //   Bass (MIDI 21): ~800 Hz,  Treble (MIDI 108): ~6 kHz
        float felt_fc = 800.f + (float)(midi - 21) / 87.f * 5200.f;
        float felt_w = dsp::TAU * felt_fc / sr;
        s.felt_lp_coeff = felt_w / (felt_w + 1.f);  // one-pole alpha
        s.felt_lp_state = 0.f;

        // Dispersion
        s.dispersion = physics::compute_dispersion(np.B, f0, sr);

        // Total filter delay (for tuning compensation)
        float filter_delay = physics::loss_filter_delay(s.loss)
                           + physics::dispersion_delay(s.dispersion);

        // Single-loop delay: total round-trip = sr/f0 - filter_delay.
        // We use delay_r as the main loop and delay_l as a minimal 2-sample
        // buffer for the bridge reflection path.  This avoids the tuning
        // error from splitting the delay into two equal halves.
        auto tuning = physics::compute_delay_tuning(f0, sr, filter_delay);

        // Main loop delay (nut -> bridge -> nut)
        physics::delay_reset(s.delay_r, tuning);

        // Bridge reflection path (minimal, just for topology)
        physics::DelayTuning tl = { 2, 0.f };
        physics::delay_reset(s.delay_l, tl);

        // Bridge junction
        s.junction = physics::compute_junction(np.impedance_ratio);

        // Hammer strike position (as tap in delay_r)
        s.strike_tap = (std::max)(1, (int)(tuning.len * np.x0_ratio));
        s.u_at_hammer = 0.f;
    }

    // -- Setup hammer (uses scaled stiffness) ---------------------------------

    float hammer_vel = physics::midi_to_hammer_velocity(velocity);
    physics::hammer_init(v.hammer, hammer_vel, eff_K_H, np.p, np.M_H);

    // Soundboard coloring: handled by DspChain convolver (soundboard IR),
    // not by per-voice mode bank.  See strategy A in docs.

    // -- Hammer noise: felt-on-steel "thwack" at onset ------------------------
    // Velocity-scaled amplitude, bandpass centroid rises with pitch
    // (harder, lighter hammers in treble → brighter noise).
    // Decay tau: bass ~8 ms, treble ~3 ms (short percussive burst).
    {
        float vel_norm = (float)velocity / 127.f;
        v.noise_amp   = 0.15f * vel_norm * vel_norm;  // quadratic velocity curve
        v.noise_env   = 1.f;
        float noise_tau_ms = 8.f - (float)(midi - 21) / 87.f * 5.f;  // 8→3 ms
        v.noise_decay = dsp::decay_coeff(noise_tau_ms * 0.001f, sr);
        // Bandpass centroid: 1.5 kHz (bass) to 5 kHz (treble)
        float centroid = 1500.f + (float)(midi - 21) / 87.f * 3500.f;
        v.noise_bpf = dsp::rbj_bandpass(centroid, 1.5f, sr);
        v.noise_wL[0] = v.noise_wL[1] = 0.f;
        v.noise_wR[0] = v.noise_wR[1] = 0.f;
        v.rng.seed((unsigned)(midi * 1000 + velocity));
    }
}

// -- Voice::process -- the waveguide heart ------------------------------------

bool PhysicsVoice::process(float* out_l, float* out_r, int n_samples,
                            float inv_sr, float dt) noexcept {
    for (int i = 0; i < n_samples; i++) {

        // -- Onset gate -------------------------------------------------------
        float env_gate = 1.f;
        if (in_onset) {
            onset_gain += onset_step;
            if (onset_gain >= 1.f) {
                onset_gain = 1.f;
                in_onset = false;
            }
            env_gate = onset_gain;
        }

        // -- Per-string waveguide processing ----------------------------------
        //
        // The hammer is a single rigid body that contacts all strings in
        // the unison group simultaneously.  We compute the average string
        // displacement at the strike point, advance the hammer once, then
        // inject the resulting force into each string's waveguide.

        // Step 1: Compute average string displacement at hammer contact
        float u_avg = 0.f;
        if (hammer.in_contact) {
            for (int si = 0; si < n_strings; si++) {
                PhysicsString& s = strings[si];
                float u = physics::delay_read(s.delay_r, s.strike_tap);
                s.u_at_hammer = u;
                u_avg += u;
            }
            u_avg /= (float)n_strings;
        }

        // Step 2: Advance hammer once (single rigid body)
        float hammer_force = physics::hammer_tick(hammer, u_avg, dt);
        // Force per string, impedance-matched to wave variable:
        //   injection = F / n_strings / (2*Z)  ≈  F / n_strings * injection_scale
        float injection_per_string = hammer_force * injection_scale;

        // Step 3: Process each string — single-loop Karplus-Strong topology
        //
        //   delay_r is the full round-trip delay line (sr/f0 samples).
        //   Signal path per sample:
        //     1. Read oldest sample from delay (= string output at bridge)
        //     2. Apply loss filter (frequency-dependent damping)
        //     3. Apply dispersion (inharmonicity allpass)
        //     4. Negate (rigid nut reflection, bridge reflection combined)
        //     5. Add hammer injection
        //     6. Write back into delay
        //     7. Output = bridge sample (pre-filter)
        //
        float bridge_out_total = 0.f;

        for (int si = 0; si < n_strings; si++) {
            PhysicsString& s = strings[si];

            // Read oldest sample (full round-trip delay = len samples)
            float bridge_sample = physics::delay_read(s.delay_r, s.delay_r.len - 1);

            // Output: bridge radiation (before filtering)
            bridge_out_total += bridge_sample;

            // Felt low-pass filter on excitation: shapes hammer force spectrum
            // (softer felt = darker attack, harder felt = brighter)
            float filtered_injection = injection_per_string;
            if (injection_per_string != 0.f) {
                s.felt_lp_state += s.felt_lp_coeff
                                 * (injection_per_string - s.felt_lp_state);
                filtered_injection = s.felt_lp_state;
            }

            // Karplus-Strong loop: loss filter + dispersion + filtered injection
            float looped = physics::loss_filter_tick(bridge_sample, s.loss);
            looped = physics::dispersion_tick(looped, s.dispersion);
            looped += filtered_injection;

            physics::delay_write(s.delay_r, looped);
        }

        // -- Output: direct string radiation via bridge ───────────────────
        float total = bridge_out_total * string_mix * output_scale;

        // Apply gain and gates
        total *= gain * env_gate;

        // Release (damper)
        if (releasing) {
            total *= rel_gain;
            rel_gain += rel_step;
            if (rel_gain <= 0.f) {
                rel_gain = 0.f;
                active = false;
            }
        }

        // -- Hammer noise (percussive attack) ------------------------------------
        // Independent of waveguide — adds the "thwack" missing from pure
        // string simulation.  Bandpass-filtered Gaussian noise with
        // exponential decay.  Bypasses onset gate (noise IS the onset).
        if (noise_env > 1e-5f) {
            float white = noise_amp * noise_env * ndist(rng);
            noise_env *= noise_decay;
            float nL = dsp::biquad_df2_tick(white, noise_bpf, noise_wL);
            float nR = dsp::biquad_df2_tick(white, noise_bpf, noise_wR);
            out_l[i] += nL * gain;
            out_r[i] += nR * gain;
        }

        // Stereo output (waveguide + soundboard)
        out_l[i] += total * pan_l;
        out_r[i] += total * pan_r;

        t_samples++;
        if (!active) break;
        if ((uint64_t)t_samples >= max_t_samp) {
            active = false;
            break;
        }
    }
    return active;
}

// -- Audio (RT thread) --------------------------------------------------------

bool PhysicalModelingPianoCore::processBlock(float* out_l, float* out_r,
                                              int n_samples) noexcept {
    return voice_mgr_.processBlock(out_l, out_r, n_samples, inv_sr_, dt_);
}

// -- Parameters (GUI thread) --------------------------------------------------

bool PhysicalModelingPianoCore::setParam(const std::string& key, float value) {
    if (key == "hammer_hardness") {
        hammer_hardness_.store(std::max(0.1f, std::min(4.f, value)),
                               std::memory_order_relaxed);
        return true;
    }
    if (key == "damping_scale") {
        damping_scale_.store(std::max(0.1f, std::min(4.f, value)),
                              std::memory_order_relaxed);
        return true;
    }
    if (key == "soundboard_mix") {
        soundboard_mix_.store(std::max(0.f, std::min(2.f, value)),
                               std::memory_order_relaxed);
        return true;
    }
    if (key == "brightness") {
        brightness_.store(std::max(0.1f, std::min(4.f, value)),
                           std::memory_order_relaxed);
        return true;
    }
    if (key == "keyboard_spread") {
        keyboard_spread_.store(std::max(0.f, std::min(3.14159f, value)),
                                std::memory_order_relaxed);
        return true;
    }
    if (key == "detune_scale") {
        detune_scale_.store(std::max(0.f, std::min(4.f, value)),
                             std::memory_order_relaxed);
        return true;
    }
    return false;
}

bool PhysicalModelingPianoCore::getParam(const std::string& key, float& out) const {
    if (key == "hammer_hardness")  { out = hammer_hardness_ .load(std::memory_order_relaxed); return true; }
    if (key == "damping_scale")    { out = damping_scale_   .load(std::memory_order_relaxed); return true; }
    if (key == "soundboard_mix")   { out = soundboard_mix_  .load(std::memory_order_relaxed); return true; }
    if (key == "brightness")       { out = brightness_      .load(std::memory_order_relaxed); return true; }
    if (key == "keyboard_spread")  { out = keyboard_spread_ .load(std::memory_order_relaxed); return true; }
    if (key == "detune_scale")     { out = detune_scale_    .load(std::memory_order_relaxed); return true; }
    return false;
}

std::vector<CoreParamDesc> PhysicalModelingPianoCore::describeParams() const {
    return {
        { "hammer_hardness", "Hammer Hardness", "Hammer",     "",
          hammer_hardness_.load(), 0.1f, 4.f, false },
        { "brightness",      "Brightness",      "Timbre",     "",
          brightness_.load(),      0.1f, 4.f, false },
        { "damping_scale",   "Damping Scale",   "Timbre",     "",
          damping_scale_.load(),   0.1f, 4.f, false },
        { "soundboard_mix",  "Soundboard Mix",  "Timbre",     "",
          soundboard_mix_.load(),  0.f,  2.f, false },
        { "detune_scale",    "Detune Scale",    "Strings",    "",
          detune_scale_.load(),    0.f,  4.f, false },
        { "keyboard_spread", "Keyboard Spread", "Stereo",     "rad",
          keyboard_spread_.load(), 0.f,  3.14159f, false },
    };
}

// -- Visualization (GUI thread) -----------------------------------------------

CoreVizState PhysicalModelingPianoCore::getVizState() const {
    CoreVizState vs;
    vs.sustain_active = false;

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
        vv.midi              = last_midi;
        vv.vel               = last_vel;
        vv.vel_idx           = 0;
        vv.vel_idx_requested = 0;
        vv.vel_fallback      = false;
        vv.f0_hz             = np.f0_hz;
        vv.B                 = np.B;
        vv.n_partials        = 0;  // physics model doesn't have explicit partials
        vv.n_strings         = np.n_strings;
        vv.is_interpolated   = false;
        vv.width_factor      = 1.f;
        vv.noise_centroid_hz = 0.f;
        vv.noise_tau_s       = 0.f;
        vv.noise_floor_rms   = 0.f;

        // Populate some "virtual partials" for the GUI -- show the first
        // few harmonics with their expected frequencies from B
        int n_show = std::min(16, (int)(sample_rate_ * 0.5f / np.f0_hz));
        for (int k = 1; k <= n_show; k++) {
            CorePartialViz cpv;
            cpv.k       = k;
            cpv.f_hz    = (float)k * np.f0_hz
                        * std::sqrt(1.f + np.B * (float)(k * k));
            cpv.A0      = physics::hammer_spectral_weight(
                              k, np.x0_ratio, np.p, np.f0_hz, sample_rate_);
            cpv.tau1    = np.tau_high;
            cpv.tau2    = np.tau_fund;
            cpv.a1      = 0.5f;
            cpv.beat_hz = 0.f;
            cpv.mono    = (np.n_strings == 1);
            cpv.fit_quality     = 0.f;
            cpv.damping_derived = false;
            vv.partials.push_back(cpv);
        }

        vs.last_note       = std::move(vv);
        vs.last_note_valid = true;
    }

    return vs;
}
