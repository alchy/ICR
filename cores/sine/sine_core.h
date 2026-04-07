#pragma once
/*
 * cores/sine/sine_core.h
 * ──────────────────────
 * Minimální sinusový syntetizér — referenční implementace 3-vrstvé
 * Ithaca Core architektury.
 *
 * Architektura:
 *   SineCore (ISynthCore adaptér)
 *     └── SinePatchManager (MIDI → nativní float překlad)
 *           └── SineVoiceManager (životní cyklus hlasů)
 *                 └── SineVoice[128] (nezávislé výpočetní jednotky)
 *
 * Voice je nezávislá entita — neví o MIDI, nepřistupuje ke globálnímu
 * stavu. Přijímá parametry v nativním formátu (float) a produkuje
 * stereo audio. Může být distribuován na samostatný HW modul.
 *
 * Parametry:
 *   "gain"         (Output):  celkové zesílení, 0..2, default 1.0
 *   "detune_cents" (Tuning):  globální posun ladění v centech, -100..100
 */

#include "engine/i_synth_core.h"
#include <array>
#include <atomic>
#include <cstdint>
#include <cmath>

// ── Konstanty ────────────────────────────────────────────────────────────────

static constexpr int   SINE_N_VOICES   = 128;   // jeden slot per MIDI nota
static constexpr float SINE_ONSET_MS   = 3.f;   // rampa proti klikání (ms)
static constexpr float SINE_RELEASE_MS = 10.f;  // release fade-out (ms)

// ── Voice — nezávislá výpočetní jednotka ─────────────────────────────────────
//
// Voice nemá přístup k MIDI ani ke globálnímu stavu.
// Všechny parametry dostává při inicializaci v nativním float formátu.
// Metoda process() produkuje audio — může běžet na odděleném HW modulu.

class SineVoice {
public:
    /// Zpracuje n_samples, přičte výstup do out_l/out_r.
    /// Vrací false pokud hlas dohasne (lze uvolnit).
    bool process(float* out_l, float* out_r, int n_samples) noexcept;

    // ── Stav (veřejný pro inicializaci — bude zapouzdřen později) ──
    bool  active     = false;    // hlas aktivní (produkuje zvuk)
    bool  releasing  = false;    // ve fázi dohasínání
    bool  in_onset   = false;    // ve fázi náběhu (click prevention)
    float phase      = 0.f;     // aktuální fáze oscilátoru (rad)
    float omega      = 0.f;     // úhlová frekvence per sample (rad/sample)
    float amp        = 0.f;     // cílová amplituda (vel-scaled)
    float onset_gain = 0.f;     // stav onset rampy 0→1
    float onset_step = 0.f;     // per-sample inkrement onset rampy
    float rel_gain   = 1.f;     // stav release rampy 1→0
    float rel_step   = 0.f;     // per-sample dekrement (záporný)
};

// ── VoiceManager — životní cyklus hlasů ──────────────────────────────────────
//
// Spravuje pool 128 hlasů. Inicializuje je s nativními parametry,
// řídí release, procesuje všechny aktivní hlasy.
// Nekomunikuje s MIDI — přijímá již přeložené parametry.

class SineVoiceManager {
public:
    /// Zpracuje všechny aktivní hlasy, přičte do output bufferů.
    bool processBlock(float* out_l, float* out_r, int n_samples) noexcept;

    /// Inicializuje hlas s nativními parametry.
    void initVoice(int midi, float omega, float amp, float sample_rate) noexcept;

    /// Zahájí release fázi hlasu.
    void releaseVoice(int midi, float sample_rate) noexcept;

    /// Uvolní všechny aktivní hlasy.
    void releaseAll(float sample_rate) noexcept;

    SineVoice&       voice(int midi)       { return voices_[midi]; }
    const SineVoice& voice(int midi) const { return voices_[midi]; }

private:
    std::array<SineVoice, SINE_N_VOICES> voices_{};
};

// ── PatchManager — MIDI → nativní překlad ────────────────────────────────────
//
// Vstupní bod systému. Přijímá MIDI (note on/off, pedal) a překládá
// do nativní parametrizace VoiceManageru.
//
// Příklad: MIDI velocity 64 (rozsah 0-127) → 0.504 (float rozsah hlasu).
// Pokud se v budoucnu zvýší přesnost vstupu (10+ bit velocity), patch
// manager pouze přepočítá překlad — zbytek systému se nemění.

class SinePatchManager {
public:
    /// Přeloží MIDI noteOn a aktivuje hlas.
    void noteOn(uint8_t midi, uint8_t velocity,
                SineVoiceManager& vm,
                float sample_rate, float gain, float detune_cents) noexcept;

    /// Přeloží MIDI noteOff (s ohledem na sustain pedál).
    void noteOff(uint8_t midi, SineVoiceManager& vm, float sample_rate) noexcept;

    /// Sustain pedál — odloží note-off dokud není uvolněn.
    void sustainPedal(bool down, SineVoiceManager& vm, float sample_rate) noexcept;

    /// Uvolní všechny hlasy.
    void allNotesOff(SineVoiceManager& vm, float sample_rate) noexcept;

    /// Info o posledním zahraném tónu (pro GUI vizualizaci).
    int lastMidi() const { return last_midi_.load(std::memory_order_relaxed); }
    int lastVel()  const { return last_vel_.load(std::memory_order_relaxed); }

    /// Překlad MIDI nota → frekvence (Hz) s detuning.
    static float midiToHz(int midi, float detune_cents) {
        return 440.f * std::pow(2.f, (midi - 69 + detune_cents / 100.f) / 12.f);
    }

private:
    std::atomic<bool> sustain_          {false};
    std::atomic<bool> delayed_offs_[SINE_N_VOICES] = {};

    std::atomic<int>  last_midi_ {-1};
    std::atomic<int>  last_vel_  {0};
};

// ── SineCore — ISynthCore adaptér ────────────────────────────────────────────
//
// Tenká vrstva propojující 3-vrstvou architekturu s ISynthCore rozhraním.
// Deleguje veškerou logiku na PatchManager → VoiceManager → Voice.

class SineCore final : public ISynthCore {
public:
    SineCore();

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

    CoreVizState getVizState() const override;

    std::string coreName()    const override { return "SineCore"; }
    std::string coreVersion() const override { return "1.0"; }
    bool        isLoaded()    const override { return loaded_; }

private:
    // Tři vrstvy architektury
    SineVoiceManager voice_mgr_;
    SinePatchManager patch_mgr_;

    float sample_rate_ = 44100.f;
    bool  loaded_      = false;

    // GUI-nastavitelné parametry (atomické: GUI vlákno píše, RT vlákno čte)
    std::atomic<float> gain_        {1.0f};
    std::atomic<float> detune_cents_{0.0f};
};
