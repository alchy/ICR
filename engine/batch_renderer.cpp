/*
 * batch_renderer.cpp
 * ───────────────────
 * Offline batch render: JSON spec → stereo 16-bit WAV files.
 */

#include "batch_renderer.h"
#include "../third_party/json.hpp"

#include <fstream>
#include <vector>
#include <cstdio>
#include <cstdint>
#include <algorithm>
#include <chrono>
#include <filesystem>

// ── WAV writer ───────────────────────────────────────────────────────────────

static bool writeWavStereo16(const std::string& path,
                             const std::vector<float>& left,
                             const std::vector<float>& right,
                             int sr)
{
    uint32_t n         = (uint32_t)left.size();
    uint32_t data_size = n * 4u;          // 2 channels × 2 bytes
    uint32_t riff_size = 36u + data_size;
    uint32_t byte_rate = (uint32_t)sr * 4u;
    uint16_t block_al  = 4u;
    uint16_t bits      = 16u;
    uint16_t channels  = 2u;
    uint16_t fmt_type  = 1u;   // PCM
    uint32_t fmt_size  = 16u;
    uint32_t sr_u      = (uint32_t)sr;

    std::FILE* f = std::fopen(path.c_str(), "wb");
    if (!f) return false;

    std::fwrite("RIFF",    1, 4, f);
    std::fwrite(&riff_size, 4, 1, f);
    std::fwrite("WAVE",    1, 4, f);
    std::fwrite("fmt ",    1, 4, f);
    std::fwrite(&fmt_size,  4, 1, f);
    std::fwrite(&fmt_type,  2, 1, f);
    std::fwrite(&channels,  2, 1, f);
    std::fwrite(&sr_u,      4, 1, f);
    std::fwrite(&byte_rate, 4, 1, f);
    std::fwrite(&block_al,  2, 1, f);
    std::fwrite(&bits,      2, 1, f);
    std::fwrite("data",    1, 4, f);
    std::fwrite(&data_size, 4, 1, f);

    for (uint32_t i = 0; i < n; i++) {
        auto clamp16 = [](float s) -> int16_t {
            float c = s > 1.f ? 1.f : (s < -1.f ? -1.f : s);
            return static_cast<int16_t>(c * 32767.f);
        };
        int16_t vl = clamp16(left[i]);
        int16_t vr = clamp16(right[i]);
        std::fwrite(&vl, 2, 1, f);
        std::fwrite(&vr, 2, 1, f);
    }
    std::fclose(f);
    return true;
}

// Map vel_idx 0-7 → MIDI velocity midpoint of that layer.
static inline uint8_t velIdxToMidi(int vel_idx) {
    return static_cast<uint8_t>(9 + (std::min)(7, (std::max)(0, vel_idx)) * 16);
}

// ── Batch render ─────────────────────────────────────────────────────────────

int renderBatch(ISynthCore& core,
                Logger&     logger,
                const std::string& batch_json_path,
                const std::string& out_dir,
                int                sr)
{
    if (!core.isLoaded()) {
        logger.log("BatchRenderer", LogSeverity::Error, "Core not loaded");
        return 0;
    }

    nlohmann::json batch;
    {
        std::ifstream f(batch_json_path);
        if (!f.is_open()) {
            logger.log("BatchRenderer", LogSeverity::Error,
                       "Cannot open " + batch_json_path);
            return 0;
        }
        try { f >> batch; }
        catch (const std::exception& e) {
            logger.log("BatchRenderer", LogSeverity::Error,
                       std::string("JSON parse error: ") + e.what());
            return 0;
        }
    }
    if (!batch.is_array() || batch.empty()) {
        logger.log("BatchRenderer", LogSeverity::Error,
                   "Batch JSON must be a non-empty array");
        return 0;
    }

    std::filesystem::create_directories(out_dir);

    const int   BLOCK  = 1024;
    const float TAIL_S = 0.5f;
    const int   total  = (int)batch.size();

    core.setSampleRate((float)sr);

    std::vector<float> buf_l(BLOCK), buf_r(BLOCK);

    logger.log("BatchRenderer", LogSeverity::Info,
               "Render batch: " + std::to_string(total)
               + " notes -> " + out_dir);

    int rendered = 0;
    auto t_start = std::chrono::steady_clock::now();

    for (int ni = 0; ni < total; ++ni) {
        const auto& entry = batch[ni];
        int   midi       = entry.value("midi",       60);
        int   vel_idx    = entry.value("vel_idx",     3);
        float duration_s = entry.value("duration_s", 3.0f);

        uint8_t midi_u = static_cast<uint8_t>((std::max)(0, (std::min)(127, midi)));
        uint8_t vel_u  = velIdxToMidi(vel_idx);

        int sustain_samples = static_cast<int>(duration_s * (float)sr);
        int tail_samples    = static_cast<int>(TAIL_S * (float)sr);
        int total_samples   = sustain_samples + tail_samples;

        std::vector<float> out_left, out_right;
        out_left.reserve((size_t)total_samples);
        out_right.reserve((size_t)total_samples);

        core.allNotesOff();
        core.noteOn(midi_u, vel_u);

        for (int s = 0; s < sustain_samples; ) {
            int n = (std::min)(BLOCK, sustain_samples - s);
            std::fill(buf_l.begin(), buf_l.begin() + n, 0.f);
            std::fill(buf_r.begin(), buf_r.begin() + n, 0.f);
            core.processBlock(buf_l.data(), buf_r.data(), n);
            for (int j = 0; j < n; j++) {
                out_left.push_back(buf_l[j]);
                out_right.push_back(buf_r[j]);
            }
            s += n;
        }

        core.noteOff(midi_u);

        for (int s = 0; s < tail_samples; ) {
            int n = (std::min)(BLOCK, tail_samples - s);
            std::fill(buf_l.begin(), buf_l.begin() + n, 0.f);
            std::fill(buf_r.begin(), buf_r.begin() + n, 0.f);
            core.processBlock(buf_l.data(), buf_r.data(), n);
            for (int j = 0; j < n; j++) {
                out_left.push_back(buf_l[j]);
                out_right.push_back(buf_r[j]);
            }
            s += n;
        }

        int sr_k = sr / 1000;
        char fname[32];
        std::snprintf(fname, sizeof(fname), "m%03d-v%02d-f%d.wav", midi, vel_idx, sr_k);
        std::string out_path = out_dir + "/" + fname;

        if (writeWavStereo16(out_path, out_left, out_right, sr)) {
            rendered++;
            logger.log("BatchRenderer", LogSeverity::Info,
                       "  Rendered " + std::string(fname)
                       + "  (" + std::to_string(ni + 1) + "/" + std::to_string(total) + ")");
        } else {
            logger.log("BatchRenderer", LogSeverity::Warning,
                       "  Failed to write " + out_path);
        }
    }

    auto t_end = std::chrono::steady_clock::now();
    float elapsed = std::chrono::duration<float>(t_end - t_start).count();
    logger.log("BatchRenderer", LogSeverity::Info,
               "Render done: " + std::to_string(rendered) + "/" + std::to_string(total)
               + " notes in " + std::to_string((int)(elapsed * 10) / 10.f) + "s");
    return rendered;
}
