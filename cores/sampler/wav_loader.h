#pragma once
/*
 * cores/sampler/wav_loader.h
 * --------------------------
 * Minimal WAV file loader.  Reads PCM16 and Float32 mono/stereo WAV files
 * into float32 stereo interleaved buffers.  No external dependencies.
 */

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>
#include <string>

namespace wav {

struct WavData {
    std::vector<float> samples;  // interleaved stereo [L0,R0,L1,R1,...]
    int   sample_rate = 0;
    int   channels    = 0;
    int   frames      = 0;       // number of frames (samples per channel)
    bool  valid       = false;
};

/// Load a WAV file into a stereo float32 buffer.
/// Mono files are duplicated to both channels.
/// Returns WavData with valid=false on error.
inline WavData load(const std::string& path) {
    WavData out;

    FILE* f = std::fopen(path.c_str(), "rb");
    if (!f) return out;

    // Read RIFF header
    char riff[4]; uint32_t file_size; char wave[4];
    if (std::fread(riff, 1, 4, f) != 4 || std::memcmp(riff, "RIFF", 4) != 0) { std::fclose(f); return out; }
    std::fread(&file_size, 4, 1, f);
    if (std::fread(wave, 1, 4, f) != 4 || std::memcmp(wave, "WAVE", 4) != 0) { std::fclose(f); return out; }

    // Find fmt and data chunks
    uint16_t audio_format = 0, num_channels = 0, bits_per_sample = 0;
    uint32_t sample_rate = 0, data_size = 0;
    bool fmt_found = false, data_found = false;

    while (!data_found) {
        char chunk_id[4]; uint32_t chunk_size;
        if (std::fread(chunk_id, 1, 4, f) != 4) break;
        if (std::fread(&chunk_size, 4, 1, f) != 1) break;

        if (std::memcmp(chunk_id, "fmt ", 4) == 0) {
            std::fread(&audio_format, 2, 1, f);
            std::fread(&num_channels, 2, 1, f);
            std::fread(&sample_rate, 4, 1, f);
            uint32_t byte_rate; std::fread(&byte_rate, 4, 1, f);
            uint16_t block_align; std::fread(&block_align, 2, 1, f);
            std::fread(&bits_per_sample, 2, 1, f);
            // Skip any extra fmt bytes
            if (chunk_size > 16) std::fseek(f, chunk_size - 16, SEEK_CUR);
            fmt_found = true;
        } else if (std::memcmp(chunk_id, "data", 4) == 0) {
            data_size = chunk_size;
            data_found = true;
        } else {
            std::fseek(f, chunk_size, SEEK_CUR);
        }
    }

    if (!fmt_found || !data_found || num_channels == 0) { std::fclose(f); return out; }

    // Only support PCM16 (1) and Float32 (3)
    if (audio_format != 1 && audio_format != 3) { std::fclose(f); return out; }

    int bytes_per_sample = bits_per_sample / 8;
    int frames = (int)(data_size / (num_channels * bytes_per_sample));

    // Read raw data
    std::vector<uint8_t> raw(data_size);
    size_t read = std::fread(raw.data(), 1, data_size, f);
    std::fclose(f);
    if ((int)read < (int)data_size) frames = (int)(read / (num_channels * bytes_per_sample));

    // Convert to stereo float32 interleaved
    out.samples.resize(frames * 2);
    out.sample_rate = (int)sample_rate;
    out.channels    = (int)num_channels;
    out.frames      = frames;

    if (audio_format == 1 && bits_per_sample == 16) {
        const int16_t* src = reinterpret_cast<const int16_t*>(raw.data());
        for (int i = 0; i < frames; i++) {
            float L = (float)src[i * num_channels] / 32768.f;
            float R = (num_channels >= 2)
                    ? (float)src[i * num_channels + 1] / 32768.f : L;
            out.samples[i * 2]     = L;
            out.samples[i * 2 + 1] = R;
        }
    } else if (audio_format == 3 && bits_per_sample == 32) {
        const float* src = reinterpret_cast<const float*>(raw.data());
        for (int i = 0; i < frames; i++) {
            float L = src[i * num_channels];
            float R = (num_channels >= 2) ? src[i * num_channels + 1] : L;
            out.samples[i * 2]     = L;
            out.samples[i * 2 + 1] = R;
        }
    } else if (audio_format == 1 && bits_per_sample == 24) {
        const uint8_t* src = raw.data();
        for (int i = 0; i < frames; i++) {
            for (int ch = 0; ch < std::min((int)num_channels, 2); ch++) {
                int idx = (i * num_channels + ch) * 3;
                int32_t s = (int32_t)(src[idx] | (src[idx+1] << 8) | (src[idx+2] << 16));
                if (s & 0x800000) s |= 0xFF000000;  // sign extend
                float val = (float)s / 8388608.f;
                out.samples[i * 2 + ch] = val;
            }
            if (num_channels == 1) out.samples[i * 2 + 1] = out.samples[i * 2];
        }
    } else {
        out.valid = false;
        return out;
    }

    out.valid = true;
    return out;
}

} // namespace wav
