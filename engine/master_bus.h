#pragma once
/*
 * master_bus.h
 * ─────────────
 * Post-core master bus: gain, stereo pan, and LFO panning.
 *
 * All setters are thread-safe (atomic stores, GUI thread).
 * process() is RT-safe (no alloc, no lock, no IO).
 *
 * Usage:
 *   MasterBus bus;
 *   bus.setGainMidi(100);          // 0-127
 *   bus.setPanMidi(64);            // 64 = center
 *   bus.setLfoSpeed(0.5f);         // Hz
 *   bus.setLfoDepth(0.3f);         // 0..1
 *   bus.process(out_l, out_r, 256, 48000);
 */

#include <atomic>
#include <cmath>

class MasterBus {
public:
    // ── Setters (GUI / MIDI thread) ──────────────────────────────────────

    // MIDI 0-127 → square-law gain 0..2
    void setGainMidi(uint8_t v) noexcept {
        float g = (v / 127.f);
        gain_.store(g * g * 2.f, std::memory_order_relaxed);
    }

    // MIDI 0-127, 64 = center
    void setPanMidi(uint8_t v) noexcept {
        float norm = (v - 64) / 64.f;  // -1..+1
        if (norm <= 0.f) {
            pan_l_.store(1.f,        std::memory_order_relaxed);
            pan_r_.store(1.f + norm, std::memory_order_relaxed);
        } else {
            pan_l_.store(1.f - norm, std::memory_order_relaxed);
            pan_r_.store(1.f,        std::memory_order_relaxed);
        }
    }

    // Direct float setters (used by SysEx)
    void setGain(float g)     noexcept { gain_.store(g, std::memory_order_relaxed); }
    void setPan(float l, float r) noexcept {
        pan_l_.store(l, std::memory_order_relaxed);
        pan_r_.store(r, std::memory_order_relaxed);
    }
    void setLfoSpeed(float hz) noexcept { lfo_speed_.store(hz, std::memory_order_relaxed); }
    void setLfoDepth(float d)  noexcept { lfo_depth_.store(d, std::memory_order_relaxed); }

    // ── Getters (GUI thread) ─────────────────────────────────────────────

    float gain()     const noexcept { return gain_.load(std::memory_order_relaxed); }
    float panL()     const noexcept { return pan_l_.load(std::memory_order_relaxed); }
    float panR()     const noexcept { return pan_r_.load(std::memory_order_relaxed); }
    float lfoSpeed() const noexcept { return lfo_speed_.load(std::memory_order_relaxed); }
    float lfoDepth() const noexcept { return lfo_depth_.load(std::memory_order_relaxed); }

    // ── RT processing ────────────────────────────────────────────────────

    void process(float* out_l, float* out_r, int n, int sample_rate) noexcept {
        static constexpr float TAU = 2.f * 3.14159265358979f;

        const float mg    = gain_.load(std::memory_order_relaxed);
        const float pl    = pan_l_.load(std::memory_order_relaxed);
        const float pr    = pan_r_.load(std::memory_order_relaxed);
        const float speed = lfo_speed_.load(std::memory_order_relaxed);
        const float depth = lfo_depth_.load(std::memory_order_relaxed);

        float mg_l = mg * pl;
        float mg_r = mg * pr;

        if (speed > 0.f && depth > 0.f) {
            float d_phase = TAU * speed / (float)sample_rate;
            for (int i = 0; i < n; i++) {
                float lfo = depth * std::sin(lfo_phase_);
                out_l[i] *= mg_l * (1.f - lfo);
                out_r[i] *= mg_r * (1.f + lfo);
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

private:
    std::atomic<float> gain_      {1.f};
    std::atomic<float> pan_l_     {1.f};
    std::atomic<float> pan_r_     {1.f};
    std::atomic<float> lfo_speed_ {0.f};
    std::atomic<float> lfo_depth_ {0.f};
    float              lfo_phase_ = 0.f;   // RT thread only
};
