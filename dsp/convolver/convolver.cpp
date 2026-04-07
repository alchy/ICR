#include "convolver.h"
#include <cstring>
#include <cmath>
#include <algorithm>
#include <fstream>

// Minimal WAV header parser for mono float32 or int16
static bool readWavMono(const std::string& path, std::vector<float>& out, int& sr_out) {
    std::ifstream f(path, std::ios::binary);
    if (!f.is_open()) return false;

    char riff[4]; f.read(riff, 4);
    if (std::memcmp(riff, "RIFF", 4) != 0) return false;

    uint32_t file_size; f.read(reinterpret_cast<char*>(&file_size), 4);
    char wave[4]; f.read(wave, 4);
    if (std::memcmp(wave, "WAVE", 4) != 0) return false;

    uint16_t fmt_tag = 0, channels = 0, bits_per_sample = 0;
    uint32_t sample_rate = 0, data_size = 0;

    // Find fmt and data chunks
    while (f.good()) {
        char chunk_id[4]; f.read(chunk_id, 4);
        uint32_t chunk_size; f.read(reinterpret_cast<char*>(&chunk_size), 4);

        if (std::memcmp(chunk_id, "fmt ", 4) == 0) {
            f.read(reinterpret_cast<char*>(&fmt_tag), 2);
            f.read(reinterpret_cast<char*>(&channels), 2);
            f.read(reinterpret_cast<char*>(&sample_rate), 4);
            f.seekg(6, std::ios::cur); // skip byte_rate + block_align
            f.read(reinterpret_cast<char*>(&bits_per_sample), 2);
            if (chunk_size > 16)
                f.seekg(chunk_size - 16, std::ios::cur);
        } else if (std::memcmp(chunk_id, "data", 4) == 0) {
            data_size = chunk_size;
            break;
        } else {
            f.seekg(chunk_size, std::ios::cur);
        }
    }

    if (channels == 0 || sample_rate == 0 || data_size == 0)
        return false;

    sr_out = (int)sample_rate;

    if (fmt_tag == 3 && bits_per_sample == 32) {
        // Float32
        int n_samples = data_size / (4 * channels);
        out.resize(n_samples);
        if (channels == 1) {
            f.read(reinterpret_cast<char*>(out.data()), n_samples * 4);
        } else {
            // Mix to mono
            std::vector<float> buf(n_samples * channels);
            f.read(reinterpret_cast<char*>(buf.data()), data_size);
            for (int i = 0; i < n_samples; i++) {
                float sum = 0;
                for (int c = 0; c < channels; c++)
                    sum += buf[i * channels + c];
                out[i] = sum / channels;
            }
        }
    } else if (fmt_tag == 1 && bits_per_sample == 16) {
        // Int16
        int n_samples = data_size / (2 * channels);
        out.resize(n_samples);
        std::vector<int16_t> buf(n_samples * channels);
        f.read(reinterpret_cast<char*>(buf.data()), data_size);
        for (int i = 0; i < n_samples; i++) {
            float sum = 0;
            for (int c = 0; c < channels; c++)
                sum += buf[i * channels + c] / 32768.f;
            out[i] = sum / channels;
        }
    } else {
        return false;
    }

    return true;
}

bool Convolver::loadIR(const std::string& path, float /*sample_rate*/) {
    std::vector<float> ir_data;
    int ir_sr = 0;
    if (!readWavMono(path, ir_data, ir_sr))
        return false;
    if (ir_data.empty())
        return false;

    // Note: no sample rate conversion — IR must match the engine SR.
    setIR(ir_data.data(), (int)ir_data.size());
    return true;
}

void Convolver::setIR(const float* ir, int length) {
    ir_.assign(ir, ir + length);
    reset();
}

void Convolver::reset() {
    int len = (int)ir_.size();
    if (len == 0) return;
    buf_L_.assign(len, 0.f);
    buf_R_.assign(len, 0.f);
    write_pos_ = 0;
}

void Convolver::process(float* L, float* R, int n_samples) {
    if (!enabled_ || ir_.empty()) return;

    const int ir_len = (int)ir_.size();
    const float wet = mix_;
    const float dry = 1.f - mix_;

    for (int i = 0; i < n_samples; i++) {
        // Write input to ring buffer
        buf_L_[write_pos_] = L[i];
        buf_R_[write_pos_] = R[i];

        // Convolve: output = sum(ir[k] * input[n-k]) for k=0..ir_len-1
        float out_L = 0.f, out_R = 0.f;
        int rp = write_pos_;
        for (int k = 0; k < ir_len; k++) {
            out_L += ir_[k] * buf_L_[rp];
            out_R += ir_[k] * buf_R_[rp];
            if (--rp < 0) rp = ir_len - 1;
        }

        // Wet/dry mix
        L[i] = dry * L[i] + wet * out_L;
        R[i] = dry * R[i] + wet * out_R;

        if (++write_pos_ >= ir_len) write_pos_ = 0;
    }
}
