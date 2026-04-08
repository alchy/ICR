/*
 * gui_main.cpp — IthacaCoreResonatorGUI
 * ───────────────────────────────────────
 * Usage:
 *   IthacaCoreResonatorGUI --core <name> [--params <file.json>]
 *                          [--config <file.json>]
 *                          [--core-param key=value ...]
 *   IthacaCoreResonatorGUI --help
 *
 * Options:
 *   --core <name>          Synthesis core (default: SineCore)
 *   --params <path>        Core parameter JSON
 *   --config <path>        SynthConfig JSON applied via setParam
 *   --core-param key=val   Override a core parameter (repeatable)
 *   --list-cores           List registered cores and exit
 *   --help                 Show this message
 */

#include "engine/core_engine.h"
#include "engine/synth_core_registry.h"
#include "gui/resonator_gui.h"

// Core registrations
#include "cores/sine/sine_core.h"
#include "cores/additive_synthesis_piano/additive_synthesis_piano_core.h"
#include "cores/physical_modeling_piano/physical_modeling_piano_core.h"
#include "cores/sampler/sampler_core.h"

#include <string>
#include <vector>
#include <cstdlib>
#include <cstdio>
#include <memory>

#if defined(__SSE__) || defined(_M_AMD64) || defined(_M_X64)
#  include <immintrin.h>
#  define ICR_ENABLE_FTZ() \
     _MM_SET_FLUSH_ZERO_MODE(_MM_FLUSH_ZERO_ON); \
     _MM_SET_DENORMALS_ZERO_MODE(_MM_DENORMALS_ZERO_ON)
#else
#  define ICR_ENABLE_FTZ() ((void)0)
#endif

static void printHelp(const char* argv0) {
    std::fprintf(stdout,
        "IthacaCoreResonatorGUI — Pluggable Synthesizer (GUI)\n"
        "\n"
        "Usage:\n"
        "  %s --core <name> [--params <file>] [--config <file>]\n"
        "             [--core-param key=value ...]\n"
        "  %s --list-cores\n"
        "  %s --help\n"
        "\n"
        "Options:\n"
        "  --core <name>          Synthesis core (default: SineCore)\n"
        "  --params <path>        Core parameter JSON\n"
        "  --config <path>        SynthConfig JSON applied via setParam\n"
        "  --core-param key=val   Override a core parameter (repeatable)\n"
        "  --midi-range-limit-from <N>  Skip notes with MIDI < N on load (default: 0)\n"
        "  --midi-range-limit-to <N>    Skip notes with MIDI > N on load (default: 127)\n"
        "  --list-cores           List registered cores and exit\n"
        "  --help                 Show this message\n",
        argv0, argv0, argv0);
}

int main(int argc, char* argv[]) {
    ICR_ENABLE_FTZ();  // prevent denormal stalls in biquad / IIR filters
    setvbuf(stdout, nullptr, _IONBF, 0);

    std::string engine_config;
    std::string core_name;
    std::string params_json;
    std::string config_json;
    std::string ir_path;
    int         midi_from = 0;    // --midi-range-limit-from
    int         midi_to   = 127;  // --midi-range-limit-to
    std::vector<std::pair<std::string,float>> core_params;

    for (int i = 1; i < argc; ++i) {
        std::string a(argv[i]);
        if (a == "--help" || a == "-h") {
            printHelp(argv[0]);
            return 0;
        } else if (a == "--list-cores") {
            for (const auto& c : SynthCoreRegistry::instance().availableCores())
                std::fprintf(stdout, "  %s\n", c.c_str());
            return 0;
        } else if (a == "--engine-config" && i + 1 < argc) {
            engine_config = argv[++i];
        } else if (a == "--core" && i + 1 < argc) {
            core_name = argv[++i];
        } else if (a == "--params" && i + 1 < argc) {
            params_json = argv[++i];
        } else if (a == "--config" && i + 1 < argc) {
            config_json = argv[++i];
        } else if (a == "--core-param" && i + 1 < argc) {
            std::string kv(argv[++i]);
            auto eq = kv.find('=');
            if (eq != std::string::npos) {
                core_params.emplace_back(kv.substr(0, eq),
                                         std::stof(kv.substr(eq + 1)));
            } else {
                std::fprintf(stderr, "--core-param: expected key=value, got: %s\n",
                             kv.c_str());
                return 1;
            }
        } else if (a == "--ir" && i + 1 < argc) {
            ir_path = argv[++i];
        } else if (a == "--midi-range-limit-from" && i + 1 < argc) {
            midi_from = std::atoi(argv[++i]);
        } else if (a == "--midi-range-limit-to" && i + 1 < argc) {
            midi_to = std::atoi(argv[++i]);
        } else {
            std::fprintf(stderr, "Unknown option: %s\n\n", a.c_str());
            printHelp(argv[0]);
            return 1;
        }
    }

    Logger logger(stdout, stdout);

    try {
        auto engine = std::make_unique<CoreEngine>();

        // Load engine config (per-core paths, default_core)
        // Auto-detect icr-config.json next to exe if not specified
        if (engine_config.empty())
            engine_config = "icr-config.json";
        engine->loadEngineConfig(engine_config, logger);

        // Resolve core name: CLI > engine config > fallback
        if (core_name.empty())
            core_name = engine->defaultCoreName();
        if (core_name.empty())
            core_name = "SineCore";

        logger.log("main", LogSeverity::Info,
                   "=== IthacaCoreResonatorGUI STARTING — " + core_name + " ===");

        if (!engine->initialize(core_name, params_json, config_json, logger, midi_from, midi_to)) {
            logger.log("main", LogSeverity::Error, "Engine init failed");
            return 1;
        }

        // Load soundboard IR if provided
        if (!ir_path.empty()) {
            DspChain* dsp = engine->getDspChain();
            if (dsp && dsp->loadConvolverIR(ir_path, 0)) {
                dsp->setConvolverEnabled(true);
                logger.log("main", LogSeverity::Info,
                           "Loaded soundboard IR: " + ir_path);
            } else {
                logger.log("main", LogSeverity::Error,
                           "Failed to load IR: " + ir_path);
            }
        }

        // Apply --core-param overrides
        for (const auto& kv : core_params) {
            if (!engine->core()->setParam(kv.first, kv.second))
                logger.log("main", LogSeverity::Warning,
                           "Unknown core param: " + kv.first);
        }

        if (!engine->start()) {
            logger.log("main", LogSeverity::Error, "Audio start failed");
            return 1;
        }

        int ret = runResonatorGui(*engine, logger);

        engine->stop();
        logger.log("main", LogSeverity::Info,
                   "=== IthacaCoreResonatorGUI STOPPED ===");
        return ret;

    } catch (const std::exception& e) {
        logger.log("main", LogSeverity::Critical,
                   std::string("FATAL: ") + e.what());
        return 1;
    }
}
