#pragma once
/*
 * app_config.h
 * ─────────────
 * Shared CLI argument parsing and engine initialization for both
 * icr (headless) and icrgui (GUI) targets.
 */

#include "engine.h"
#include "logger.h"
#include "synth_core_registry.h"
#include <string>
#include <vector>
#include <utility>

// ── Platform denormal setup — call once at program start ─────────────────────
#if defined(__SSE__) || defined(_M_AMD64) || defined(_M_X64)
#  include <immintrin.h>
#  define ICR_ENABLE_FTZ() \
     _MM_SET_FLUSH_ZERO_MODE(_MM_FLUSH_ZERO_ON); \
     _MM_SET_DENORMALS_ZERO_MODE(_MM_DENORMALS_ZERO_ON)
#else
#  define ICR_ENABLE_FTZ() ((void)0)
#endif

class AppConfig {
public:
    // Parse command-line arguments.
    // gui_mode: true = icrgui (no --port/--render-batch/--out-dir/--sr)
    // Returns: 0 = success, 1 = parse error, 2 = early exit (--help/--list-cores handled)
    int parse(int argc, char* argv[], bool gui_mode = false);

    // Common init sequence: load engine config, resolve core name,
    // initialize core, load IR, apply --core-param overrides.
    // Returns true on success.
    bool initEngine(Engine& engine, Logger& logger);

    // ── Getters ──────────────────────────────────────────────────────────────
    const std::string& coreName()     const { return core_name_; }
    const std::string& paramsPath()   const { return params_json_; }
    const std::string& configPath()   const { return config_json_; }
    const std::string& irPath()       const { return ir_path_; }
    int                midiFrom()     const { return midi_from_; }
    int                midiTo()       const { return midi_to_; }

    // CLI-only (icr)
    int                midiPort()     const { return midi_port_; }
    const std::string& renderBatch()  const { return render_batch_; }
    const std::string& renderOutDir() const { return render_out_dir_; }
    int                renderSr()     const { return render_sr_; }

    // ── Setters ──────────────────────────────────────────────────────────────
    void setCoreName(const std::string& v)   { core_name_ = v; }
    void setParamsPath(const std::string& v) { params_json_ = v; }

private:
    static void printHelp(const char* argv0, bool gui_mode);

    std::string engine_config_;
    std::string core_name_;
    std::string params_json_;
    std::string config_json_;
    std::string ir_path_;
    int         midi_from_ = 0;
    int         midi_to_   = 127;
    std::vector<std::pair<std::string,float>> core_params_;

    // CLI-only
    int         midi_port_    = 0;
    std::string render_batch_;
    std::string render_out_dir_;
    int         render_sr_    = 48000;
};
