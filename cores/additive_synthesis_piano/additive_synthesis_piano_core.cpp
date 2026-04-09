/*
 * cores/additive_synthesis_piano/additive_synthesis_piano_core.cpp
 * ─────────────────────────────────
 * C++ port of analysis/torch_synth.py (2-string bi-exponential piano synth).
 *
 * Phase computation matches Python's:
 *   t[i] = float32(i) / sr
 *   phase_carrier = 2π * f_hz * t[i]
 *   phase_beat    = 2π * beat_hz * 0.5 * t[i]
 *   s1 = cos(phase_carrier + phase_beat + phi)
 *   s2 = cos(phase_carrier - phase_beat + phi + phi_diff)
 *
 * Envelope uses a multiplicative decay factor (avoids per-sample exp()):
 *   decay = exp(-1 / (tau * sr))  →  env[n] = env[n-1] * decay
 */
#include "additive_synthesis_piano_core.h"
#include "additive_synthesis_piano_math.h"
#include "engine/synth_core_registry.h"
#include "third_party/json.hpp"

#include <fstream>
#include <algorithm>
#include <cstdio>

using json = nlohmann::json;

// Self-register
REGISTER_SYNTH_CORE("AdditiveSynthesisPianoCore", AdditiveSynthesisPianoCore)

static constexpr float TAU = dsp::TAU;

// ── Constructor ───────────────────────────────────────────────────────────────

AdditiveSynthesisPianoCore::AdditiveSynthesisPianoCore() {}

// ── JSON loading ──────────────────────────────────────────────────────────────

bool AdditiveSynthesisPianoCore::load(const std::string& params_path, float sr, Logger& logger,
                     int midi_from, int midi_to) {
    sample_rate_ = sr;
    inv_sr_      = 1.f / sr;

    if (params_path.empty()) {
        logger.log("AdditiveSynthesisPianoCore", LogSeverity::Info,
                   "No params_path — loaded empty (use loadBankJson or --params)");
        loaded_ = true;
        return true;
    }

    std::ifstream f(params_path);
    if (!f.is_open()) {
        logger.log("AdditiveSynthesisPianoCore", LogSeverity::Error,
                   "Cannot open params: " + params_path);
        return false;
    }

    json root;
    try {
        f >> root;
    } catch (const std::exception& e) {
        logger.log("AdditiveSynthesisPianoCore", LogSeverity::Error,
                   std::string("JSON parse error: ") + e.what());
        return false;
    }

    if (!root.contains("notes")) {
        logger.log("AdditiveSynthesisPianoCore", LogSeverity::Error, "JSON missing 'notes' key");
        return false;
    }

    // Clear existing params
    for (int m = 0; m < 128; m++)
        for (int v = 0; v < 8; v++)
            note_params_[m][v] = PianoNoteParam{};

    int loaded_count = 0;
    const auto& notes = root["notes"];
    for (auto it = notes.begin(); it != notes.end(); ++it) {
        const auto& s = it.value();
        int midi    = s["midi"].get<int>();
        int vel_idx = s["vel"].get<int>();

        if (midi < 0 || midi > 127 || vel_idx < 0 || vel_idx > 7) continue;
        if (midi < midi_from || midi > midi_to) continue;

        PianoNoteParam& np = note_params_[midi][vel_idx];
        np.valid              = true;
        np.is_interpolated    = s.value("is_interpolated", false);
        np.phi_diff           = s["phi_diff"].get<float>();
        np.attack_tau         = s["attack_tau"].get<float>();
        np.A_noise            = s["A_noise"].get<float>();
        np.noise_centroid_hz  = s.value("noise_centroid_hz", 3000.f);
        np.rms_gain           = s["rms_gain"].get<float>();
        np.stereo_width       = s.value("stereo_width", 1.f);
        np.f0_hz              = s["f0_hz"].get<float>();
        np.B                  = s.value("B", 0.f);
        np.rise_tau           = s.value("rise_tau", -1.f);
        np.n_strings          = s.value("n_strings", -1);
        np.decor_strength     = s.value("decor_strength", -1.f);

        const auto& partials = s["partials"];
        int K = std::min((int)partials.size(), PIANO_MAX_PARTIALS);
        np.K  = K;

        for (int ki = 0; ki < K; ki++) {
            const auto& p = partials[ki];
            PianoPartialParam& pp = np.partials[ki];
            pp.k       = p["k"].get<int>();
            pp.f_hz    = p["f_hz"].get<float>();
            pp.A0      = p["A0"].get<float>();
            pp.tau1    = p["tau1"].get<float>();
            pp.tau2    = p["tau2"].get<float>();
            pp.a1      = p["a1"].get<float>();
            pp.beat_hz = p["beat_hz"].get<float>();
            pp.phi     = p["phi"].get<float>();
            pp.fit_quality     = p.value("fit_quality", 0.f);
            pp.damping_derived = p.value("damping_derived", false);
        }

        // Spectral EQ biquad cascade (optional — absent in NN-exported params)
        np.n_biquad = 0;
        if (s.contains("eq_biquads")) {
            const auto& bqs = s["eq_biquads"];
            int nB = std::min((int)bqs.size(), PIANO_N_BIQUAD);
            for (int bi = 0; bi < nB; bi++) {
                const auto& bq = bqs[bi];
                PianoBiquadCoeffs& c = np.eq[bi];
                c.b0 = bq["b"][0].get<float>();
                c.b1 = bq["b"][1].get<float>();
                c.b2 = bq["b"][2].get<float>();
                c.a1 = bq["a"][0].get<float>();
                c.a2 = bq["a"][1].get<float>();
            }
            np.n_biquad = nB;
        }

        ++loaded_count;
    }

    loaded_ = (loaded_count > 0);
    if (!loaded_) {
        logger.log("AdditiveSynthesisPianoCore", LogSeverity::Error, "No valid notes in params file");
        return false;
    }

    std::string range_info = (midi_from > 0 || midi_to < 127)
        ? ("  MIDI filter: " + std::to_string(midi_from) + "-" + std::to_string(midi_to))
        : "";
    logger.log("AdditiveSynthesisPianoCore", LogSeverity::Info,
               "Loaded " + std::to_string(loaded_count) + " notes from " + params_path
               + "  SR=" + std::to_string((int)sr) + range_info);
    return true;
}

void AdditiveSynthesisPianoCore::setSampleRate(float sr) {
    sample_rate_ = sr;
    inv_sr_      = 1.f / sr;
    // Active voices will drift after SR change; not worth re-computing mid-note.
}

// ── MIDI (RT thread) — delegates to PatchManager ────────────────────────────

void AdditiveSynthesisPianoCore::noteOn(uint8_t midi, uint8_t velocity) {
    if (midi >= PIANO_MAX_VOICES) return;
    if (velocity == 0) { noteOff(midi); return; }
    patch_mgr_.noteOn(midi, velocity, note_params_, voice_mgr_,
                      sample_rate_,
                      beat_scale_.load(std::memory_order_relaxed),
                      noise_level_.load(std::memory_order_relaxed),
                      rng_seed_.load(std::memory_order_relaxed),
                      pan_spread_.load(std::memory_order_relaxed),
                      stereo_decorr_.load(std::memory_order_relaxed),
                      keyboard_spread_.load(std::memory_order_relaxed),
                      eq_strength_.load(std::memory_order_relaxed),
                      bank_mutex_);
}

void AdditiveSynthesisPianoCore::noteOff(uint8_t midi) {
    if (midi >= PIANO_MAX_VOICES) return;
    patch_mgr_.noteOff(midi, voice_mgr_, sample_rate_);
}

void AdditiveSynthesisPianoCore::sustainPedal(bool down) {
    patch_mgr_.sustainPedal(down, voice_mgr_, sample_rate_);
}

void AdditiveSynthesisPianoCore::allNotesOff() {
    patch_mgr_.allNotesOff(voice_mgr_, sample_rate_);
}

// ── PatchManager ─────────────────────────────────────────────────────────────

void PianoPatchManager::noteOn(
        uint8_t midi, uint8_t velocity,
        PianoNoteParam note_params[][8],
        PianoVoiceManager& vm,
        float sample_rate, float beat_scale, float noise_level,
        int rng_seed, float pan_spread, float stereo_decorr,
        float keyboard_spread, float eq_strength,
        std::mutex& bank_mutex) noexcept {
    std::unique_lock<std::mutex> lk(bank_mutex, std::try_to_lock);
    if (!lk.owns_lock()) return;

    float vel_f = midiVelToFloat(velocity);
    int   lo    = (int)vel_f;
    int   hi    = std::min(lo + 1, 7);
    float frac  = vel_f - (float)lo;

    auto findValid = [&](int start) -> int {
        if (start >= 0 && start <= 7 && note_params[midi][start].valid) return start;
        for (int dv = 1; dv < 8; dv++) {
            int a = start - dv, b = start + dv;
            if (a >= 0 && note_params[midi][a].valid) return a;
            if (b <= 7 && note_params[midi][b].valid) return b;
        }
        return -1;
    };

    int lo_valid = findValid(lo);
    int hi_valid = findValid(hi);
    if (lo_valid < 0) return;
    if (hi_valid < 0) hi_valid = lo_valid;

    int vel_idx;
    PianoNoteParam np;
    if (lo_valid == hi_valid || frac < 0.001f) {
        vel_idx = lo_valid;
        np      = note_params[midi][lo_valid];
    } else {
        vel_idx = lo_valid;
        np      = lerpNoteParams(note_params[midi][lo_valid],
                                  note_params[midi][hi_valid], frac);
    }

    // Use forte layer (vel=7) as reference for both rms_gain AND
    // spectral shape.  Bank velocity layers have inconsistent A0 ratios
    // (extracted from different recordings with varying SNR).
    // Forte has best SNR → most reliable spectral content.
    // vel_gain curve handles dynamics; layer interpolation is only for
    // the continuous A0(k1) scaling.
    int ref_vel = -1;
    for (int v = 7; v >= 0; v--) {
        if (note_params[midi][v].valid) { ref_vel = v; break; }
    }
    if (ref_vel >= 0) {
        const PianoNoteParam& ref = note_params[midi][ref_vel];
        np.rms_gain = ref.rms_gain;

        // Normalize spectral shape: preserve np.A0(k1) magnitude but
        // use forte's A0 ratios (k>1 relative to k=1).
        if (np.K > 1 && ref.K > 1 && np.partials[0].A0 > 1e-12f
                                   && ref.partials[0].A0 > 1e-12f) {
            float np_k1  = np.partials[0].A0;
            float ref_k1 = ref.partials[0].A0;
            int minK = (std::min)(np.K, ref.K);
            for (int ki = 1; ki < minK; ki++) {
                // ratio_ref = ref.A0(k) / ref.A0(k=1)
                float ref_ratio = ref.partials[ki].A0 / ref_k1;
                float target_a0 = np_k1 * ref_ratio;
                // Only boost — never cut below current value
                if (target_a0 > np.partials[ki].A0)
                    np.partials[ki].A0 = target_a0;
            }
        }
    }

    vm.initVoice(midi, vel_idx, np, beat_scale, noise_level, rng_seed,
                 pan_spread, stereo_decorr, keyboard_spread,
                 sample_rate, eq_strength, vel_f / 7.f);

    last_midi_   .store(midi,     std::memory_order_relaxed);
    last_vel_    .store(velocity, std::memory_order_relaxed);
    last_vel_idx_.store(vel_idx,  std::memory_order_relaxed);
}

void PianoPatchManager::noteOff(uint8_t midi, PianoVoiceManager& vm,
                                 float sample_rate) noexcept {
    if (sustain_.load(std::memory_order_relaxed))
        delayed_offs_[midi].store(true, std::memory_order_relaxed);
    else
        vm.releaseVoice(midi, sample_rate);
}

void PianoPatchManager::sustainPedal(bool down, PianoVoiceManager& vm,
                                      float sample_rate) noexcept {
    sustain_.store(down, std::memory_order_relaxed);
    if (!down) {
        for (int m = 0; m < PIANO_MAX_VOICES; m++) {
            if (delayed_offs_[m].load(std::memory_order_relaxed)) {
                vm.releaseVoice((uint8_t)m, sample_rate);
                delayed_offs_[m].store(false, std::memory_order_relaxed);
            }
        }
    }
}

void PianoPatchManager::allNotesOff(PianoVoiceManager& vm,
                                     float sample_rate) noexcept {
    vm.releaseAll(sample_rate);
    for (int m = 0; m < PIANO_MAX_VOICES; m++)
        delayed_offs_[m].store(false, std::memory_order_relaxed);
    sustain_.store(false, std::memory_order_relaxed);
}

PianoNoteParam PianoPatchManager::lerpNoteParams(const PianoNoteParam& a,
                                          const PianoNoteParam& b,
                                          float t) noexcept {
    PianoNoteParam out = a;  // copy structure from a
    float s = 1.f - t;

    // Note-level params
    out.phi_diff          = s * a.phi_diff          + t * b.phi_diff;
    out.attack_tau        = s * a.attack_tau        + t * b.attack_tau;
    out.A_noise           = s * a.A_noise           + t * b.A_noise;
    out.noise_centroid_hz = s * a.noise_centroid_hz + t * b.noise_centroid_hz;
    out.rms_gain          = s * a.rms_gain          + t * b.rms_gain;
    out.stereo_width      = s * a.stereo_width      + t * b.stereo_width;
    // rise_tau, n_strings, decor_strength: use from layer a (note-level, not velocity-dependent)
    // f0_hz, B: use from layer a (they should be identical across velocities)

    // Interpolate EQ coefficients
    out.n_biquad = std::min(a.n_biquad, b.n_biquad);
    for (int bi = 0; bi < out.n_biquad; bi++) {
        out.eq[bi].b0 = s * a.eq[bi].b0 + t * b.eq[bi].b0;
        out.eq[bi].b1 = s * a.eq[bi].b1 + t * b.eq[bi].b1;
        out.eq[bi].b2 = s * a.eq[bi].b2 + t * b.eq[bi].b2;
        out.eq[bi].a1 = s * a.eq[bi].a1 + t * b.eq[bi].a1;
        out.eq[bi].a2 = s * a.eq[bi].a2 + t * b.eq[bi].a2;
    }

    // Interpolate per-partial params.  Use max(K) so no partials are dropped.
    // Where only one layer has a partial, fade A0 toward 0 using the factor.
    int minK = std::min(a.K, b.K);
    int maxK = std::max(a.K, b.K);
    out.K = maxK;
    for (int ki = 0; ki < maxK; ki++) {
        if (ki < minK) {
            // Both layers have this partial — interpolate
            out.partials[ki].A0      = s * a.partials[ki].A0      + t * b.partials[ki].A0;
            out.partials[ki].tau1    = s * a.partials[ki].tau1    + t * b.partials[ki].tau1;
            out.partials[ki].tau2    = s * a.partials[ki].tau2    + t * b.partials[ki].tau2;
            out.partials[ki].a1      = s * a.partials[ki].a1      + t * b.partials[ki].a1;
            out.partials[ki].beat_hz = s * a.partials[ki].beat_hz + t * b.partials[ki].beat_hz;
        } else if (ki < a.K) {
            // Only layer a has this partial — fade A0 by (1-t)
            out.partials[ki] = a.partials[ki];
            out.partials[ki].A0 *= s;
        } else {
            // Only layer b has this partial — fade A0 by t
            out.partials[ki] = b.partials[ki];
            out.partials[ki].A0 *= t;
        }
        // f_hz, k, phi: use from whichever layer has the partial
    }

    return out;
}

// ── VoiceManager ─────────────────────────────────────────────────────────────

bool PianoVoiceManager::processBlock(float* out_l, float* out_r,
                                      int n_samples, float inv_sr) noexcept {
    bool any = false;
    for (int m = 0; m < PIANO_MAX_VOICES; m++) {
        if (!voices_[m].active) continue;
        voices_[m].process(out_l, out_r, n_samples, inv_sr);
        any = true;
    }
    return any;
}

void PianoVoiceManager::releaseVoice(int midi, float sample_rate) noexcept {
    PianoVoice& v = voices_[midi];
    if (!v.active) return;
    v.releasing = true;
    v.rel_gain  = v.in_onset ? v.onset_gain : 1.f;
    v.rel_step  = -v.rel_gain * piano::ramp_step(PIANO_RELEASE_MS, sample_rate);
}

void PianoVoiceManager::releaseAll(float sample_rate) noexcept {
    for (int m = 0; m < PIANO_MAX_VOICES; m++)
        if (voices_[m].active) releaseVoice(m, sample_rate);
}

void PianoVoiceManager::initVoice(int midi, int vel_idx,
                                   const PianoNoteParam& np,
                                   float beat_scale, float noise_level,
                                   int rng_seed, float pan_spread,
                                   float stereo_decorr,
                                   float keyboard_spread,
                                   float sample_rate,
                                   float eq_strength,
                                   float vel_norm) noexcept {
    PianoVoice& v = voices_[midi];

    v.active     = true;
    v.releasing  = false;
    v.in_onset   = true;
    v.midi       = midi;
    v.vel_idx    = vel_idx;
    v.t_samples  = 0;

    // Velocity dynamics curve — bank rms_gain normalizes layers to
    // similar loudness (for spectral accuracy), so we re-introduce
    // piano dynamics here.  pow(vel, 1.5) gives ~23 dB range.
    float vel_clamped = (std::min)(vel_norm, 1.f);
    float vel_gain    = vel_clamped * std::sqrt(vel_clamped);  // = pow(v, 1.5)

    // Noise — physics-based centroid and attack tau override.
    // Bank values are often contaminated by harmonic leakage (centroid
    // too low → soft thud instead of metallic hit).  Physics floor
    // ensures minimum brightness.
    float phys_centroid = piano::hammer_noise_centroid(midi);
    float phys_tau      = piano::hammer_attack_tau(midi);
    float use_centroid  = (std::max)(np.noise_centroid_hz, phys_centroid);
    float use_tau       = (std::min)(np.attack_tau, phys_tau);

    // Scale noise amplitude down when centroid is raised significantly —
    // higher-frequency noise is perceptually brighter and cuts through more.
    float centroid_ratio = np.noise_centroid_hz / (std::max)(use_centroid, 1.f);
    float noise_scale = std::sqrt(centroid_ratio);   // -3 dB per 2× centroid rise

    v.A_noise_sc  = np.A_noise * np.rms_gain * noise_level * noise_scale * vel_gain;
    v.noise_env   = 1.f;
    v.noise_decay = dsp::decay_coeff(use_tau, sample_rate);
    v.noise_bpf   = dsp::rbj_bandpass(use_centroid, 1.5f, sample_rate);
    v.noise_bpf_L = {};
    v.noise_bpf_R = {};
    v.rng.seed((uint32_t)(rng_seed + midi * 256 + vel_idx));
    v.ndist = std::normal_distribution<float>(0.f, 1.f);

    // Onset ramp (minimal click-prevention gate, 0.5 ms)
    v.onset_gain = 0.f;
    v.onset_step = piano::ramp_step(PIANO_ONSET_MS, sample_rate);
    v.rel_gain   = 1.f;
    v.rel_step   = 0.f;

    // Attack rise envelope — from JSON if available, else midi-based heuristic
    float rise_tau = (np.rise_tau > 0.f) ? np.rise_tau
                                          : piano::rise_tau_from_midi(midi);
    v.rise_coeff  = dsp::decay_coeff(rise_tau, sample_rate);
    v.rise_env    = 0.f;

    // Compute max voice duration: 10× longest tau2 or 60 s, whichever is less
    float max_tau = 0.f;
    for (int ki = 0; ki < np.K; ki++)
        if (np.partials[ki].tau2 > max_tau) max_tau = np.partials[ki].tau2;
    float dur_s = std::min(10.f * max_tau, 60.f);
    if (dur_s < 3.f) dur_s = 3.f;
    v.max_t_samp = (uint64_t)(dur_s * sample_rate);

    // String model: from JSON if available, else midi-based default
    v.n_model_strings = (np.n_strings > 0) ? np.n_strings
                      : (midi <= 27) ? 1 : (midi <= 48) ? 2 : 3;

    std::uniform_real_distribution<float> phi2dist(0.f, TAU);
    v.n_partials = np.K;
    for (int ki = 0; ki < np.K; ki++) {
        const PianoPartialParam& pp = np.partials[ki];
        PianoPartialState& ps       = v.partials[ki];

        ps.env_fast   = 1.f;
        ps.env_slow   = 1.f;
        ps.decay_fast = dsp::decay_coeff(pp.tau1, sample_rate);
        ps.decay_slow = dsp::decay_coeff(pp.tau2, sample_rate);
        ps.A0_scaled  = pp.A0 * np.rms_gain * vel_gain;
        ps.a1         = pp.a1;
        ps.f_hz       = pp.f_hz;
        ps.beat_hz_h  = pp.beat_hz * beat_scale * 0.5f;
        ps.phi        = pp.phi;
        ps.phi2       = phi2dist(v.rng);   // random center-string phase
        ps.phi_diff   = np.phi_diff;       // outer string phase offset (from JSON)
    }

    // Stereo panning: constant-power pan per string, MIDI-dependent center.
    {
        const float center = piano::keyboard_pan_angle(midi, keyboard_spread);
        const float half   = pan_spread * 0.5f;
        if (midi <= 27) {
            piano::constant_power_pan(center, v.gl1, v.gr1);
            v.gl2 = 0.f; v.gr2 = 0.f;
            v.gl3 = 0.f; v.gr3 = 0.f;
        } else if (midi <= 48) {
            piano::constant_power_pan(center - half, v.gl1, v.gr1);
            piano::constant_power_pan(center + half, v.gl2, v.gr2);
            v.gl3 = 0.f; v.gr3 = 0.f;
        } else {
            piano::constant_power_pan(center - half, v.gl1, v.gr1);
            piano::constant_power_pan(center,        v.gl2, v.gr2);
            piano::constant_power_pan(center + half, v.gl3, v.gr3);
        }
    }

    // Schroeder first-order all-pass decorrelation
    // From JSON if available, else midi-based heuristic
    {
        float ds;
        if (np.decor_strength >= 0.f) {
            ds = np.decor_strength * stereo_decorr;
        } else {
            auto dc = piano::compute_decor_coeffs(midi, stereo_decorr);
            ds = dc.decor_str;
        }
        v.decor_str = ds;
        v.ap_g_L    =   0.35f + ds * 0.25f;
        v.ap_g_R    = -(0.35f + ds * 0.20f);
        v.ap_x_L = v.ap_y_L = v.ap_x_R = v.ap_y_R = 0.f;
    }

    // M/S stereo width correction (snapshot at noteOn)
    v.stereo_width = np.stereo_width;

    // Spectral EQ biquad cascade: copy coeffs, zero filter state
    v.n_biquad    = np.n_biquad;
    v.eq_strength = eq_strength;
    for (int bi = 0; bi < np.n_biquad; bi++)
        v.eq_coeffs[bi] = np.eq[bi];
    std::memset(v.eq_wL, 0, sizeof(v.eq_wL));
    std::memset(v.eq_wR, 0, sizeof(v.eq_wR));
}

// ── Voice::process (independent, distributable) ─────────────────────────────

bool PianoVoice::process(float* out_l, float* out_r, int n_samples,
                          float inv_sr) noexcept {
    for (int i = 0; i < n_samples; i++) {
        // ── Onset ramp ──────────────────────────────────────────────────
        float env_gate = 1.f;
        if (in_onset) {
            bool onset_done = false;
            env_gate = piano::onset_ramp_tick(onset_gain, onset_step, onset_done);
            if (onset_done) in_onset = false;
        }

        // ── Phase base ──────────────────────────────────────────────────
        const float t_f  = (float)t_samples * inv_sr;
        const float tpi2 = dsp::TAU * t_f;

        // ── Partials (string model + bi-exp envelope) ────────────────────
        float part_L = 0.f, part_R = 0.f;
        for (int ki = 0; ki < n_partials; ki++) {
            PianoPartialState& ps = partials[ki];

            float env = piano::biexp_envelope_tick(
                ps.a1, ps.env_fast, ps.env_slow,
                ps.decay_fast, ps.decay_slow);

            if (ps.A0_scaled * env < PIANO_SKIP_THRESH) continue;

            const float phase_c = tpi2 * ps.f_hz + ps.phi;
            const float phase_b = tpi2 * ps.beat_hz_h;

            float A0_env = ps.A0_scaled * env;
            piano::StereoSample ss;
            if (n_model_strings == 1) {
                ss = piano::string_model_1(phase_c, A0_env, gl1, gr1);
            } else if (n_model_strings == 2) {
                ss = piano::string_model_2(phase_c, phase_b, ps.phi_diff,
                                           A0_env, gl1, gr1, gl2, gr2);
            } else {
                ss = piano::string_model_3(phase_c, phase_b,
                                           tpi2 * ps.f_hz, ps.phi2,
                                           ps.phi_diff, A0_env,
                                           gl1, gr1, gl2, gr2, gl3, gr3);
            }
            part_L += ss.L;
            part_R += ss.R;
        }

        // ── Attack rise envelope (partials only) ────────────────────────
        float rise = piano::rise_envelope_tick(rise_env, rise_coeff);
        part_L *= rise;
        part_R *= rise;

        // ── Noise (biquad bandpass) ─────────────────────────────────────
        float noise_L = 0.f, noise_R = 0.f;
        {
            float noise_sc = A_noise_sc * noise_env;
            noise_L = dsp::biquad_tick(noise_sc * ndist(rng),
                                       noise_bpf, noise_bpf_L);
            noise_R = dsp::biquad_tick(noise_sc * ndist(rng),
                                       noise_bpf, noise_bpf_R);
            noise_env *= noise_decay;
        }

        // ── Combine partials + noise ────────────────────────────────────
        float samp_L = part_L + noise_L;
        float samp_R = part_R + noise_R;

        // ── Schroeder all-pass decorrelation ────────────────────────────
        piano::allpass_decorrelate(samp_L, samp_R,
                                   ap_g_L, ap_g_R, decor_str,
                                   ap_x_L, ap_y_L, ap_x_R, ap_y_R);

        // ── Spectral EQ biquad cascade ──────────────────────────────────
        piano::eq_cascade_stereo(samp_L, samp_R,
                                 n_biquad, eq_coeffs, eq_wL, eq_wR, eq_strength);

        // ── M/S stereo width correction ─────────────────────────────────
        piano::ms_stereo_width(samp_L, samp_R, stereo_width);

        // ── Onset / release gates ───────────────────────────────────────
        samp_L *= env_gate;
        samp_R *= env_gate;
        if (releasing) {
            samp_L *= rel_gain;
            samp_R *= rel_gain;
            if (piano::release_ramp_tick(rel_gain, rel_step))
                active = false;
        }

        out_l[i] += samp_L;
        out_r[i] += samp_R;

        t_samples++;
        if (!active) break;
        if ((uint64_t)t_samples >= max_t_samp) {
            active = false;
            break;
        }
    }
    return active;
}

// ── Audio (RT thread) — delegates to per-voice process ──────────────────────

bool AdditiveSynthesisPianoCore::processBlock(float* out_l, float* out_r, int n_samples) noexcept {
    return voice_mgr_.processBlock(out_l, out_r, n_samples, inv_sr_);
}

// ── Parameters (GUI thread) ───────────────────────────────────────────────────

bool AdditiveSynthesisPianoCore::setParam(const std::string& key, float value) {
    if (key == "beat_scale") {
        beat_scale_.store(std::max(0.f, std::min(4.f, value)),
                          std::memory_order_relaxed);
        return true;
    }
    if (key == "noise_level") {
        noise_level_.store(std::max(0.f, std::min(4.f, value)),
                           std::memory_order_relaxed);
        return true;
    }
    if (key == "rng_seed") {
        rng_seed_.store((int)value, std::memory_order_relaxed);
        return true;
    }
    if (key == "pan_spread") {
        pan_spread_.store(std::max(0.f, std::min(3.14159f, value)),
                          std::memory_order_relaxed);
        return true;
    }
    if (key == "stereo_decorr") {
        stereo_decorr_.store(std::max(0.f, std::min(2.f, value)),
                             std::memory_order_relaxed);
        return true;
    }
    if (key == "keyboard_spread") {
        keyboard_spread_.store(std::max(0.f, std::min(3.14159f, value)),
                               std::memory_order_relaxed);
        return true;
    }
    if (key == "eq_strength") {
        eq_strength_.store(std::max(0.f, std::min(1.f, value)),
                           std::memory_order_relaxed);
        return true;
    }
    return false;
}

bool AdditiveSynthesisPianoCore::getParam(const std::string& key, float& out) const {
    if (key == "beat_scale")   { out = beat_scale_   .load(std::memory_order_relaxed); return true; }
    if (key == "noise_level")  { out = noise_level_  .load(std::memory_order_relaxed); return true; }
    if (key == "rng_seed")     { out = (float)rng_seed_.load(std::memory_order_relaxed); return true; }
    if (key == "pan_spread")   { out = pan_spread_   .load(std::memory_order_relaxed); return true; }
    if (key == "stereo_decorr")    { out = stereo_decorr_    .load(std::memory_order_relaxed); return true; }
    if (key == "keyboard_spread")  { out = keyboard_spread_  .load(std::memory_order_relaxed); return true; }
    if (key == "eq_strength")      { out = eq_strength_      .load(std::memory_order_relaxed); return true; }
    return false;
}

std::vector<CoreParamDesc> AdditiveSynthesisPianoCore::describeParams() const {
    return {
        { "beat_scale",   "Beat Scale",    "Timbre",  "×",   beat_scale_   .load(), 0.f,    4.f,     false },
        { "noise_level",  "Noise Level",   "Timbre",  "×",   noise_level_  .load(), 0.f,    4.f,     false },
        { "pan_spread",   "Pan Spread",    "Stereo",  "rad", pan_spread_   .load(), 0.f,    3.14159f,false },
        { "stereo_decorr",   "Stereo Decorr",    "Stereo",  "×",   stereo_decorr_  .load(), 0.f,    2.f,      false },
        { "keyboard_spread", "Keyboard Spread",  "Stereo",  "rad", keyboard_spread_.load(), 0.f,    3.14159f, false },
        { "eq_strength",     "EQ Strength",      "Timbre",  "×",   eq_strength_    .load(), 0.f,    1.f,      false },
        { "rng_seed",        "RNG Seed",         "Debug",   "",    (float)rng_seed_.load(), 0.f,    9999.f,   true  },
    };
}

// ── Per-note SysEx updates (MIDI callback thread) ─────────────────────────────

bool AdditiveSynthesisPianoCore::setNoteParam(int midi, int vel,
                              const std::string& key, float value) {
    if (midi < 0 || midi > 127 || vel < 0 || vel > 7) return false;
    PianoNoteParam& np = note_params_[midi][vel];
    if (key == "f0_hz")             { np.f0_hz             = value; return true; }
    if (key == "attack_tau")        { np.attack_tau        = value; return true; }
    if (key == "A_noise")           { np.A_noise           = value; return true; }
    if (key == "noise_centroid_hz") { np.noise_centroid_hz = value; return true; }
    if (key == "rms_gain")          { np.rms_gain          = value; return true; }
    if (key == "phi_diff")          { np.phi_diff          = value; return true; }
    if (key == "stereo_width")      { np.stereo_width      = std::max(0.f, value); return true; }
    if (key == "B") {
        // B is a string property independent of velocity — propagate to all
        // 8 velocity layers for this MIDI note and recompute f_hz in each.
        for (int v = 0; v < 8; v++) {
            PianoNoteParam& nv = note_params_[midi][v];
            if (!nv.valid) continue;
            nv.B = value;
            const float f0 = nv.f0_hz;
            for (int ki = 0; ki < nv.K; ki++) {
                const int k = nv.partials[ki].k;
                nv.partials[ki].f_hz = piano::partial_frequency(k, f0, value);
            }
        }
        return true;
    }
    return false;
}

bool AdditiveSynthesisPianoCore::setNotePartialParam(int midi, int vel, int k,
                                     const std::string& key, float value) {
    if (midi < 0 || midi > 127 || vel < 0 || vel > 7) return false;
    PianoNoteParam& np = note_params_[midi][vel];
    if (k < 1 || k > np.K) return false;
    PianoPartialParam& pp = np.partials[k - 1];  // k is 1-based in protocol
    if (key == "f_hz")    { pp.f_hz    = value; return true; }
    if (key == "A0")      { pp.A0      = value; return true; }
    if (key == "tau1")    { pp.tau1    = value; return true; }
    if (key == "tau2")    { pp.tau2    = value; return true; }
    if (key == "a1")      { pp.a1      = value; return true; }
    if (key == "beat_hz") { pp.beat_hz = value; return true; }
    if (key == "phi")     { pp.phi     = value; return true; }
    return false;
}

bool AdditiveSynthesisPianoCore::loadBankJson(const std::string& json_str) {
    json root;
    try {
        root = json::parse(json_str);
    } catch (const std::exception&) {
        return false;
    }
    if (!root.contains("notes")) return false;

    // Parse into a temporary heap buffer — keeps the lock window minimal.
    auto tmp = std::make_unique<PianoNoteParam[]>(128 * 8);

    const auto& notes = root["notes"];
    for (auto it = notes.begin(); it != notes.end(); ++it) {
        const auto& s = it.value();
        int midi    = s["midi"].get<int>();
        int vel_idx = s["vel"].get<int>();
        if (midi < 0 || midi > 127 || vel_idx < 0 || vel_idx > 7) continue;

        PianoNoteParam& np = tmp[midi * 8 + vel_idx];
        np.valid              = true;
        np.is_interpolated    = s.value("is_interpolated", false);
        np.phi_diff           = s["phi_diff"].get<float>();
        np.attack_tau         = s["attack_tau"].get<float>();
        np.A_noise            = s["A_noise"].get<float>();
        np.noise_centroid_hz  = s.value("noise_centroid_hz", 3000.f);
        np.rms_gain           = s["rms_gain"].get<float>();
        np.stereo_width       = s.value("stereo_width", 1.f);
        np.f0_hz              = s["f0_hz"].get<float>();
        np.B                  = s.value("B", 0.f);
        np.rise_tau           = s.value("rise_tau", -1.f);
        np.n_strings          = s.value("n_strings", -1);
        np.decor_strength     = s.value("decor_strength", -1.f);

        const auto& partials = s["partials"];
        int K = std::min((int)partials.size(), PIANO_MAX_PARTIALS);
        np.K = K;
        for (int ki = 0; ki < K; ki++) {
            const auto& p = partials[ki];
            PianoPartialParam& pp = np.partials[ki];
            pp.k       = p["k"].get<int>();
            pp.f_hz    = p["f_hz"].get<float>();
            pp.A0      = p["A0"].get<float>();
            pp.tau1    = p["tau1"].get<float>();
            pp.tau2    = p["tau2"].get<float>();
            pp.a1      = p["a1"].get<float>();
            pp.beat_hz = p["beat_hz"].get<float>();
            pp.phi     = p["phi"].get<float>();
            pp.fit_quality     = p.value("fit_quality", 0.f);
            pp.damping_derived = p.value("damping_derived", false);
        }

        np.n_biquad = 0;
        if (s.contains("eq_biquads")) {
            const auto& bqs = s["eq_biquads"];
            int nB = std::min((int)bqs.size(), PIANO_N_BIQUAD);
            for (int bi = 0; bi < nB; bi++) {
                const auto& bq = bqs[bi];
                PianoBiquadCoeffs& c = np.eq[bi];
                c.b0 = bq["b"][0].get<float>();
                c.b1 = bq["b"][1].get<float>();
                c.b2 = bq["b"][2].get<float>();
                c.a1 = bq["a"][0].get<float>();
                c.a2 = bq["a"][1].get<float>();
            }
            np.n_biquad = nB;
        }
    }

    // Apply atomically: lock is held only for the memcpy, not during parsing.
    {
        std::lock_guard<std::mutex> lk(bank_mutex_);
        for (int m = 0; m < 128; m++)
            for (int v = 0; v < 8; v++)
                note_params_[m][v] = tmp[m * 8 + v];
    }
    return true;
}

bool AdditiveSynthesisPianoCore::exportBankJson(const std::string& path) {
    json notes = json::array();

    std::lock_guard<std::mutex> lk(bank_mutex_);
    for (int m = 0; m < 128; m++) {
        for (int v = 0; v < 8; v++) {
            const PianoNoteParam& np = note_params_[m][v];
            if (!np.valid) continue;

            json note;
            note["midi"]              = m;
            note["vel"]               = v;
            note["phi_diff"]          = np.phi_diff;
            note["attack_tau"]        = np.attack_tau;
            note["A_noise"]           = np.A_noise;
            note["noise_centroid_hz"] = np.noise_centroid_hz;
            note["rms_gain"]          = np.rms_gain;
            note["stereo_width"]      = np.stereo_width;
            note["f0_hz"]             = np.f0_hz;
            note["B"]                 = np.B;
            if (np.is_interpolated)
                note["is_interpolated"] = true;

            json partials = json::array();
            for (int ki = 0; ki < np.K; ki++) {
                const PianoPartialParam& pp = np.partials[ki];
                json p;
                p["k"]       = pp.k;
                p["f_hz"]    = pp.f_hz;
                p["A0"]      = pp.A0;
                p["tau1"]    = pp.tau1;
                p["tau2"]    = pp.tau2;
                p["a1"]      = pp.a1;
                p["beat_hz"] = pp.beat_hz;
                p["phi"]     = pp.phi;
                partials.push_back(p);
            }
            note["partials"] = partials;

            if (np.n_biquad > 0) {
                json biquads = json::array();
                for (int bi = 0; bi < np.n_biquad; bi++) {
                    const PianoBiquadCoeffs& c = np.eq[bi];
                    json bq;
                    bq["b"] = {c.b0, c.b1, c.b2};
                    bq["a"] = {c.a1, c.a2};
                    biquads.push_back(bq);
                }
                note["eq_biquads"] = biquads;
            }

            notes.push_back(note);
        }
    }

    json root;
    root["notes"] = notes;

    std::ofstream f(path);
    if (!f.is_open()) return false;
    f << root.dump(2);
    return f.good();
}

// ── Visualization (GUI thread) ────────────────────────────────────────────────

CoreVizState AdditiveSynthesisPianoCore::getVizState() const {
    CoreVizState vs;
    vs.sustain_active = false;  // TODO: expose from patch_mgr_

    for (int m = 0; m < PIANO_MAX_VOICES; m++) {
        if (voice_mgr_.voice(m).active) {
            vs.active_midi_notes.push_back(m);
            vs.active_voice_count++;
        }
    }

    int last_midi    = patch_mgr_.lastMidi();
    int last_vel     = patch_mgr_.lastVel();
    int last_vel_idx = patch_mgr_.lastVelIdx();
    if (last_midi >= 0 && last_midi < 128
        && last_vel_idx >= 0 && last_vel_idx < 8) {
        const PianoNoteParam& np = note_params_[last_midi][last_vel_idx];

        int requested_idx = PianoPatchManager::midiVelToIdx(
            (uint8_t)(std::max)(1, (std::min)(127, last_vel)));

        CoreVoiceViz vv;
        vv.midi              = last_midi;
        vv.vel               = last_vel;
        vv.vel_idx           = last_vel_idx;
        vv.vel_idx_requested = requested_idx;
        vv.vel_fallback      = (last_vel_idx != requested_idx);
        vv.f0_hz             = np.f0_hz;
        vv.B                 = np.B;
        vv.n_partials        = np.K;
        // Acoustic string count for this MIDI note (instrument reality, not C++ model).
        // C++ always renders a 2-string model; this shows the original instrument
        // stringing so the GUI can inform the user when the model simplifies (MIDI>48).
        vv.n_strings         = (last_midi <= 27) ? 1 : (last_midi <= 48) ? 2 : 3;
        vv.is_interpolated   = np.is_interpolated;
        vv.width_factor      = np.stereo_width;
        vv.noise_centroid_hz = np.noise_centroid_hz;
        vv.noise_tau_s       = np.attack_tau;
        vv.noise_floor_rms   = np.A_noise * np.rms_gain;  // peak noise amplitude (t=0, noise_level=1)

        for (int ki = 0; ki < np.K && ki < 16; ki++) {   // cap at 16 for GUI
            const PianoPartialParam& pp = np.partials[ki];
            CorePartialViz cpv;
            cpv.k       = ki + 1;
            cpv.f_hz    = pp.f_hz;
            cpv.A0      = pp.A0;
            cpv.tau1    = pp.tau1;
            cpv.tau2    = pp.tau2;
            cpv.a1      = pp.a1;
            cpv.beat_hz = pp.beat_hz;
            cpv.mono            = (pp.a1 >= 0.99f);
            cpv.fit_quality     = pp.fit_quality;
            cpv.damping_derived = pp.damping_derived;
            vv.partials.push_back(cpv);
        }

        // Spectral EQ frequency response (evaluated from biquad coefficients)
        // 32 log-spaced frequencies 30 Hz – 18 kHz, cascade magnitude in dB
        if (np.n_biquad > 0) {
            constexpr int N_EQ = 32;
            const float f_lo = 30.f, f_hi = 18000.f;
            const float log_lo = std::log(f_lo), log_hi = std::log(f_hi);
            vv.eq_freqs_hz.resize(N_EQ);
            vv.eq_gains_db.resize(N_EQ);
            for (int fi = 0; fi < N_EQ; fi++) {
                float f   = std::exp(log_lo + (log_hi - log_lo) * fi / (N_EQ - 1));
                float w   = TAU * f * inv_sr_;
                float cw  = std::cos(w), sw = std::sin(w);
                float c2w = std::cos(2.f * w), s2w = std::sin(2.f * w);
                // Product of biquad section magnitudes²
                float mag2 = 1.f;
                for (int bi = 0; bi < np.n_biquad; bi++) {
                    const PianoBiquadCoeffs& c = np.eq[bi];
                    float nr = c.b0 + c.b1 * cw  + c.b2 * c2w;
                    float ni = -(c.b1 * sw + c.b2 * s2w);
                    float dr = 1.f  + c.a1 * cw  + c.a2 * c2w;
                    float di = -(c.a1 * sw + c.a2 * s2w);
                    mag2 *= (nr*nr + ni*ni) / std::max(dr*dr + di*di, 1e-30f);
                }
                vv.eq_freqs_hz[fi] = f;
                vv.eq_gains_db[fi] = 10.f * std::log10(std::max(mag2, 1e-12f));
            }
        }

        vs.last_note       = std::move(vv);
        vs.last_note_valid = true;
    }

    return vs;
}
