#pragma once
#include <cstdint>
/*
 * dsp_chain.h — Master bus DSP chain: Convolver → BBE → Limiter.
 *
 * Signal flow:  Core output → Convolver (soundboard IR) → BBE → Limiter → DAC
 *
 * Call order (per block):
 *   prepare()  — call once at init with sample_rate + max_block_size
 *   process()  — called from audio thread (RT-safe after prepare)
 *   reset()    — clear filter states
 *
 * MIDI/SysEx mappings (param_id 0x20-0x26):
 *   0x20  Limiter threshold:  MIDI 0..127 → -40..0 dB
 *   0x21  Limiter release:    MIDI 0..127 → 10..2000 ms
 *   0x22  Limiter enabled:    >= 0.5 = on
 *   0x23  BBE definition:     MIDI 0..127 → 0..12 dB  (5 kHz high shelf)
 *   0x24  BBE bass boost:     MIDI 0..127 → 0..10 dB  (180 Hz low shelf)
 *   0x25  Convolver enabled:  >= 0.5 = on
 *   0x26  Convolver mix:      0.0-1.0 wet/dry mix
 */

#include "limiter/limiter.h"
#include "bbe/bbe.h"
#include "convolver/convolver.h"

class DspChain {
public:
    void prepare(float sample_rate, int max_block_size);
    void reset();

    // Process stereo block in-place (L/R non-interleaved).
    void process(float* L, float* R, int n_samples);

    // ── Limiter controls ──────────────────────────────────────────────────────
    void setLimiterThreshold(uint8_t midi);   // 127=0 dB, 0=-40 dB
    void setLimiterRelease  (uint8_t midi);   // 0=10 ms, 127=2000 ms
    void setLimiterEnabled  (uint8_t midi);   // >= 64 = on

    uint8_t getLimiterThreshold()     const { return lim_thr_midi_;   }
    uint8_t getLimiterRelease()       const { return lim_rel_midi_;   }
    uint8_t getLimiterEnabled()       const { return lim_ena_midi_;   }
    uint8_t getLimiterGainReduction() const;  // 0=no reduction, 127=full (-40 dB)

    // ── BBE controls ──────────────────────────────────────────────────────────
    void setBBEDefinition(uint8_t midi);   // 0..127 → 0..12 dB
    void setBBEBassBoost (uint8_t midi);   // 0..127 → 0..10 dB

    uint8_t getBBEDefinition() const { return bbe_def_midi_; }
    uint8_t getBBEBassBoost()  const { return bbe_bas_midi_; }

    // ── Convolver controls ─────────────────────────────────────────────────
    bool loadConvolverIR(const std::string& path, float sr);
    void setConvolverEnabled(bool on) { convolver_.setEnabled(on); }
    void setConvolverMix(float mix)   { convolver_.setMix(mix); }
    bool isConvolverLoaded() const    { return convolver_.irLength() > 0; }

    void setSoundboardDir(const std::string& dir) { soundboard_dir_ = dir; }
    const std::string& soundboardDir() const { return soundboard_dir_; }
    const std::string& activeIrName() const { return active_ir_name_; }

    Limiter&   limiter()   { return limiter_; }
    BBE&       bbe()       { return bbe_;     }
    Convolver& convolver() { return convolver_; }

    int getEffectCount() const { return 3; }

private:
    Convolver convolver_;
    Limiter   limiter_;
    BBE       bbe_;

    uint8_t lim_thr_midi_ = 127;
    uint8_t lim_rel_midi_ = 64;
    uint8_t lim_ena_midi_ = 0;
    uint8_t bbe_def_midi_ = 0;
    uint8_t bbe_bas_midi_ = 0;

    std::string soundboard_dir_;
    std::string active_ir_name_;
};
