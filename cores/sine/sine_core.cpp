/*
 * cores/sine/sine_core.cpp
 * ────────────────────────
 * Referenční implementace 3-vrstvé Ithaca Core architektury.
 * Každá vrstva má jasně definovanou odpovědnost:
 *
 *   Voice:        nezávislý výpočet zvuku (distribuce na HW)
 *   VoiceManager: životní cyklus hlasů (init, release, process)
 *   PatchManager:  MIDI → nativní překlad (velocity, sustain pedál)
 *   SineCore:     ISynthCore adaptér (propojení s CoreEngine)
 */
#include "sine_core.h"
#include "dsp/dsp_math.h"
#include "engine/synth_core_registry.h"

// Registrace do globálního registru — CoreEngine najde SineCore podle jména
REGISTER_SYNTH_CORE("SineCore", SineCore)

static constexpr float TAU = 2.f * 3.14159265358979f;

// ═════════════════════════════════════════════════════════════════════════════
// Voice — nezávislá výpočetní jednotka
// ═════════════════════════════════════════════════════════════════════════════

bool SineVoice::process(float* out_l, float* out_r, int n_samples) noexcept {
    for (int i = 0; i < n_samples; i++) {
        // Obálka: onset rampa (click prevention 0→1)
        float env = 1.f;
        if (in_onset) {
            onset_gain += onset_step;
            if (onset_gain >= 1.f) { onset_gain = 1.f; in_onset = false; }
            env = onset_gain;
        }

        // Obálka: release rampa (fade-out 1→0)
        if (releasing) {
            rel_gain += rel_step;
            if (rel_gain <= 0.f) {
                active = releasing = false;
                rel_gain = 0.f;
            }
            env *= rel_gain;
        }

        // Syntéza: sinusový oscilátor
        float s = amp * env * std::sin(phase);
        out_l[i] += s * pan_l;
        out_r[i] += s * pan_r;

        // Posun fáze (akumulace)
        phase += omega;
        if (phase >= TAU) phase -= TAU;

        if (!active) break;
    }
    return active;
}

// ═════════════════════════════════════════════════════════════════════════════
// VoiceManager — životní cyklus hlasů
// ═════════════════════════════════════════════════════════════════════════════

bool SineVoiceManager::processBlock(float* out_l, float* out_r,
                                     int n_samples) noexcept {
    bool any = false;
    for (int m = 0; m < SINE_N_VOICES; m++) {
        if (!voices_[m].active) continue;
        voices_[m].process(out_l, out_r, n_samples);
        any = true;
    }
    return any;
}

void SineVoiceManager::initVoice(int midi, float omega, float amp,
                                  float sample_rate,
                                  float keyboard_spread) noexcept {
    SineVoice& v = voices_[midi];
    v.omega      = omega;
    v.amp        = amp;
    v.phase      = 0.f;
    v.in_onset   = true;
    v.onset_gain = 0.f;
    v.onset_step = 1.f / (SINE_ONSET_MS * 0.001f * sample_rate);
    v.releasing  = false;
    v.rel_gain   = 1.f;
    v.active     = true;

    // Keyboard spread pan
    float angle = dsp::keyboard_pan_angle(midi, keyboard_spread);
    dsp::constant_power_pan(angle, v.pan_l, v.pan_r);
}

void SineVoiceManager::releaseVoice(int midi, float sample_rate) noexcept {
    SineVoice& v = voices_[midi];
    if (!v.active) return;
    v.releasing = true;
    v.rel_step  = -1.f / (SINE_RELEASE_MS * 0.001f * sample_rate);
    v.rel_gain  = v.in_onset ? v.onset_gain : 1.f;
}

void SineVoiceManager::releaseAll(float sample_rate) noexcept {
    for (int m = 0; m < SINE_N_VOICES; m++)
        if (voices_[m].active) releaseVoice(m, sample_rate);
}

// ═════════════════════════════════════════════════════════════════════════════
// PatchManager — MIDI → nativní překlad
// ═════════════════════════════════════════════════════════════════════════════

void SinePatchManager::noteOn(uint8_t midi, uint8_t velocity,
                               SineVoiceManager& vm,
                               float sample_rate, float gain,
                               float detune_cents,
                               float keyboard_spread) noexcept {
    float amp   = (velocity / 127.f) * gain;
    float f     = midiToHz(midi, detune_cents);
    float omega = TAU * f / sample_rate;

    vm.initVoice(midi, omega, amp, sample_rate, keyboard_spread);

    last_midi_.store(midi,     std::memory_order_relaxed);
    last_vel_ .store(velocity, std::memory_order_relaxed);
}

void SinePatchManager::noteOff(uint8_t midi, SineVoiceManager& vm,
                                float sample_rate) noexcept {
    // Sustain pedál: odloží note-off
    if (sustain_.load(std::memory_order_relaxed))
        delayed_offs_[midi].store(true, std::memory_order_relaxed);
    else
        vm.releaseVoice(midi, sample_rate);
}

void SinePatchManager::sustainPedal(bool down, SineVoiceManager& vm,
                                     float sample_rate) noexcept {
    sustain_.store(down, std::memory_order_relaxed);
    // Při uvolnění pedálu: zpracuj všechny odložené note-off
    if (!down) {
        for (int m = 0; m < SINE_N_VOICES; m++) {
            if (delayed_offs_[m].load(std::memory_order_relaxed)) {
                vm.releaseVoice(m, sample_rate);
                delayed_offs_[m].store(false, std::memory_order_relaxed);
            }
        }
    }
}

void SinePatchManager::allNotesOff(SineVoiceManager& vm,
                                    float sample_rate) noexcept {
    vm.releaseAll(sample_rate);
    for (int m = 0; m < SINE_N_VOICES; m++)
        delayed_offs_[m].store(false, std::memory_order_relaxed);
    sustain_.store(false, std::memory_order_relaxed);
}

// ═════════════════════════════════════════════════════════════════════════════
// SineCore — ISynthCore adaptér
// ═════════════════════════════════════════════════════════════════════════════
//
// Tenká vrstva: přijímá ISynthCore volání a deleguje na 3-vrstvou architekturu.
// Drží GUI-nastavitelné parametry (atomic) a sample_rate.

SineCore::SineCore() {}

bool SineCore::load(const std::string& /*params_path*/, float sr, Logger& logger,
                    int /*midi_from*/, int /*midi_to*/) {
    sample_rate_ = sr;
    loaded_      = true;
    logger.log("SineCore", LogSeverity::Info,
               "Ready. SR=" + std::to_string((int)sr));
    return true;
}

void SineCore::setSampleRate(float sr) {
    sample_rate_ = sr;
}

// MIDI → PatchManager
void SineCore::noteOn(uint8_t midi, uint8_t velocity) {
    if (midi >= SINE_N_VOICES) return;
    if (velocity == 0) { noteOff(midi); return; }
    patch_mgr_.noteOn(midi, velocity, voice_mgr_, sample_rate_,
                      gain_.load(std::memory_order_relaxed),
                      detune_cents_.load(std::memory_order_relaxed),
                      keyboard_spread_.load(std::memory_order_relaxed));
}

void SineCore::noteOff(uint8_t midi) {
    if (midi >= SINE_N_VOICES) return;
    patch_mgr_.noteOff(midi, voice_mgr_, sample_rate_);
}

void SineCore::sustainPedal(bool down) {
    patch_mgr_.sustainPedal(down, voice_mgr_, sample_rate_);
}

void SineCore::allNotesOff() {
    patch_mgr_.allNotesOff(voice_mgr_, sample_rate_);
}

// Audio rendering → VoiceManager → Voice::process()
bool SineCore::processBlock(float* out_l, float* out_r, int n_samples) noexcept {
    return voice_mgr_.processBlock(out_l, out_r, n_samples);
}

// GUI parametry
bool SineCore::setParam(const std::string& key, float value) {
    if (key == "gain") {
        gain_.store(std::max(0.f, std::min(2.f, value)), std::memory_order_relaxed);
        return true;
    }
    if (key == "detune_cents") {
        detune_cents_.store(std::max(-100.f, std::min(100.f, value)),
                            std::memory_order_relaxed);
        return true;
    }
    if (key == "keyboard_spread") {
        keyboard_spread_.store(std::max(0.f, std::min(3.14159f, value)),
                               std::memory_order_relaxed);
        return true;
    }
    return false;
}

bool SineCore::getParam(const std::string& key, float& out) const {
    if (key == "gain")            { out = gain_.load(std::memory_order_relaxed);            return true; }
    if (key == "detune_cents")    { out = detune_cents_.load(std::memory_order_relaxed);    return true; }
    if (key == "keyboard_spread") { out = keyboard_spread_.load(std::memory_order_relaxed); return true; }
    return false;
}

std::vector<CoreParamDesc> SineCore::describeParams() const {
    return {
        { "gain",            "Gain",            "Output", "",    gain_.load(),            0.f,    2.f,     false },
        { "detune_cents",    "Detune",          "Tuning", "ct",  detune_cents_.load(),    -100.f, 100.f,   false },
        { "keyboard_spread", "Keyboard Spread", "Stereo", "rad", keyboard_spread_.load(), 0.f,    3.14159f, false },
    };
}

// GUI vizualizace
CoreVizState SineCore::getVizState() const {
    CoreVizState vs;

    for (int m = 0; m < SINE_N_VOICES; m++) {
        if (voice_mgr_.voice(m).active) {
            vs.active_midi_notes.push_back(m);
            vs.active_voice_count++;
        }
    }

    int last_midi = patch_mgr_.lastMidi();
    int last_vel  = patch_mgr_.lastVel();
    if (last_midi >= 0) {
        CoreVoiceViz vv;
        vv.midi  = last_midi;
        vv.vel   = last_vel;
        vv.f0_hz = SinePatchManager::midiToHz(
            last_midi, detune_cents_.load(std::memory_order_relaxed));
        vs.last_note       = std::move(vv);
        vs.last_note_valid = true;
    }

    return vs;
}
