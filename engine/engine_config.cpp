/*
 * engine_config.cpp
 * ──────────────────
 * Per-core JSON configuration persistence.
 */

#include "engine_config.h"
#include "../third_party/json.hpp"
using json = nlohmann::json;

#include <fstream>

bool EngineConfig::load(const std::string& path, Logger& logger) {
    path_ = path;
    std::ifstream f(path);
    if (!f.is_open()) {
        logger.log("EngineConfig", LogSeverity::Warning,
                   "Cannot open: " + path);
        return false;
    }

    json root;
    try { f >> root; }
    catch (const std::exception& e) {
        logger.log("EngineConfig", LogSeverity::Error,
                   std::string("Parse error: ") + e.what());
        return false;
    }

    if (root.contains("log_file") && root["log_file"].is_string())
        log_file_ = root["log_file"].get<std::string>();

    if (root.contains("default_core") && root["default_core"].is_string())
        default_core_ = root["default_core"].get<std::string>();

    if (root.contains("block_size") && root["block_size"].is_number_integer())
        block_size_ = root["block_size"].get<int>();

    if (root.contains("cores") && root["cores"].is_object()) {
        for (auto it = root["cores"].begin(); it != root["cores"].end(); ++it) {
            if (!it.value().is_object()) continue;
            std::unordered_map<std::string, std::string> cfg;
            for (auto jt = it.value().begin(); jt != it.value().end(); ++jt) {
                if (jt.value().is_string())
                    cfg[jt.key()] = jt.value().get<std::string>();
                else if (jt.value().is_number())
                    cfg[jt.key()] = std::to_string(jt.value().get<double>());
            }
            cores_[it.key()] = std::move(cfg);
        }
    }

    logger.log("EngineConfig", LogSeverity::Info,
               "Loaded: " + path + " (default_core=" + default_core_
               + ", " + std::to_string(cores_.size()) + " core configs)");
    return true;
}

bool EngineConfig::save(Logger& logger) const {
    if (path_.empty()) {
        logger.log("EngineConfig", LogSeverity::Warning,
                   "No config path set, skipping save");
        return false;
    }

    // Resolve default_core from _engine pseudo-key
    std::string dc = default_core_;
    auto eng_it = cores_.find("_engine");
    if (eng_it != cores_.end()) {
        auto dc_it = eng_it->second.find("default_core");
        if (dc_it != eng_it->second.end())
            dc = dc_it->second;
    }

    json root;
    root["log_file"] = log_file_.empty() ? "icr.log" : log_file_;
    root["default_core"] = dc;
    root["block_size"] = block_size_;

    json cores_j = json::object();
    for (const auto& [cname, cfg] : cores_) {
        if (cname == "_engine") continue;
        json cj = json::object();
        for (const auto& [k, v] : cfg)
            cj[k] = v;
        cores_j[cname] = cj;
    }
    root["cores"] = cores_j;

    std::ofstream f(path_);
    if (!f.is_open()) {
        logger.log("EngineConfig", LogSeverity::Error,
                   "Cannot open for save: " + path_);
        return false;
    }
    f << root.dump(2) << "\n";

    logger.log("EngineConfig", LogSeverity::Info,
               "Saved: " + path_ + " (default_core=" + dc + ")");
    return f.good();
}

std::string EngineConfig::value(const std::string& core_name,
                                const std::string& key) const {
    auto it = cores_.find(core_name);
    if (it == cores_.end()) return "";
    auto jt = it->second.find(key);
    return (jt != it->second.end()) ? jt->second : "";
}

void EngineConfig::setValue(const std::string& core_name,
                            const std::string& key, const std::string& val) {
    cores_[core_name][key] = val;
}
