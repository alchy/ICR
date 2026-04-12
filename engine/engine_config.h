#pragma once
/*
 * engine_config.h
 * ────────────────
 * Per-core JSON configuration: load, save, get/set values.
 *
 * Usage:
 *   EngineConfig cfg;
 *   cfg.load("icr-config.json", logger);
 *   std::string path = cfg.value("AdditiveSynthesisPianoCore", "params_path");
 *   cfg.setValue("SineCore", "master_gain", "100");
 *   cfg.save(logger);
 */

#include "logger.h"
#include <string>
#include <unordered_map>

class EngineConfig {
public:
    // Load config from JSON file.  Returns true on success.
    bool load(const std::string& path, Logger& logger);

    // Save current state to the file it was loaded from.
    bool save(Logger& logger) const;

    // Per-core key/value access
    std::string value(const std::string& core_name,
                      const std::string& key) const;
    void setValue(const std::string& core_name,
                 const std::string& key, const std::string& val);

    // ── Getters ──────────────────────────────────────────────────────────────
    const std::string& defaultCoreName() const { return default_core_; }
    void setDefaultCoreName(const std::string& n) { default_core_ = n; }

    const std::string& configPath() const { return path_; }
    const std::string& logFilePath() const { return log_file_; }

    int  blockSize() const { return block_size_; }
    void setBlockSize(int bs) { block_size_ = bs; }

    int  voicePoolSize() const { return voice_pool_size_; }
    void setVoicePoolSize(int n) { voice_pool_size_ = n; }

private:
    std::string path_;
    std::string default_core_;
    std::string log_file_;
    int         block_size_      = 256;
    int         voice_pool_size_ = 32;
    std::unordered_map<std::string,
        std::unordered_map<std::string, std::string>> cores_;
};
