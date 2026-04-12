/*
 * audio_device.cpp
 * ─────────────────
 * miniaudio playback device wrapper.
 */

#include "audio_device.h"
#include "miniaudio.h"

AudioDevice::AudioDevice()
    : device_(new ma_device{}) {}

AudioDevice::~AudioDevice() {
    stop();
    delete device_;
}

bool AudioDevice::start(AudioCallback cb, void* userdata,
                         int sample_rate, int block_size) {
    callback_    = cb;
    userdata_    = userdata;
    sample_rate_ = sample_rate;
    block_size_  = block_size;

    ma_device_config cfg = ma_device_config_init(ma_device_type_playback);
    cfg.playback.format    = ma_format_f32;
    cfg.playback.channels  = 2;
    cfg.sampleRate         = (ma_uint32)sample_rate_;
    cfg.dataCallback       = cb;
    cfg.pUserData          = userdata;
    cfg.periodSizeInFrames = (ma_uint32)block_size_;

    if (ma_device_init(nullptr, &cfg, device_) != MA_SUCCESS)
        return false;

    if (ma_device_start(device_) != MA_SUCCESS) {
        ma_device_uninit(device_);
        return false;
    }

    running_.store(true);
    device_name_ = device_->playback.name;
    return true;
}

void AudioDevice::stop() {
    if (!running_.load()) return;
    ma_device_stop(device_);
    ma_device_uninit(device_);
    running_.store(false);
}

bool AudioDevice::setBlockSize(int new_size) {
    if (new_size == block_size_) return true;
    bool was_running = running_.load();
    if (was_running) stop();
    block_size_ = new_size;
    if (was_running)
        return start(callback_, userdata_, sample_rate_, block_size_);
    return true;
}

bool AudioDevice::setSampleRate(int new_sr) {
    if (new_sr == sample_rate_) return true;
    bool was_running = running_.load();
    if (was_running) stop();
    sample_rate_ = new_sr;
    if (was_running)
        return start(callback_, userdata_, sample_rate_, block_size_);
    return true;
}
