#pragma once
/*
 * audio_device.h
 * ───────────────
 * Wrapper around miniaudio playback device.
 *
 * Manages device lifecycle: open, start, stop, reinit with new block size.
 * The Engine provides the audio callback; AudioDevice calls it.
 *
 * Usage:
 *   AudioDevice dev;
 *   dev.start(callback, userdata, 48000, 256);
 *   dev.setBlockSize(512);   // stop → reinit → start
 *   dev.stop();
 */

#include <atomic>
#include <cstdint>
#include <string>

struct ma_device;

// Callback signature matches miniaudio data callback.
using AudioCallback = void(*)(ma_device*, void*, const void*, uint32_t);

class AudioDevice {
public:
    AudioDevice();
    ~AudioDevice();

    // Start audio device.  Returns false on failure.
    bool start(AudioCallback cb, void* userdata, int sample_rate, int block_size);

    // Stop audio device (blocks until callback thread exits).
    void stop();

    // Change block size at runtime: stop → reinit → start.
    // Accepts any positive integer (not limited to powers of 2).
    // Returns false if restart fails.
    bool setBlockSize(int new_size);

    // Change sample rate at runtime: stop → reinit → start.
    bool setSampleRate(int new_sr);

    bool isRunning()  const { return running_.load(); }
    int  sampleRate() const { return sample_rate_; }
    int  blockSize()  const { return block_size_; }

    // Device name (available after start)
    const std::string& deviceName() const { return device_name_; }

private:
    ma_device*       device_      = nullptr;
    std::atomic<bool> running_    {false};
    int              sample_rate_ = 48000;
    int              block_size_  = 256;
    AudioCallback    callback_    = nullptr;
    void*            userdata_    = nullptr;
    std::string      device_name_;
};
