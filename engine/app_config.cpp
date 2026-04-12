/*
 * app_config.cpp
 * ───────────────
 * Shared CLI argument parsing and engine initialization.
 */

#include "app_config.h"
#include "../dsp/dsp_chain.h"
#include <cstdio>
#include <cstdlib>
#include <string>

// ── Help text ────────────────────────────────────────────────────────────────

void AppConfig::printHelp(const char* argv0, bool gui_mode) {
    if (gui_mode) {
        std::fprintf(stdout,
            "ICR — Pluggable Synthesizer (GUI)\n"
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
            "  --ir <path>            Soundboard impulse response WAV\n"
            "  --midi-range-limit-from <N>  Skip notes with MIDI < N (default: 0)\n"
            "  --midi-range-limit-to <N>    Skip notes with MIDI > N (default: 127)\n"
            "  --list-cores           List registered cores and exit\n"
            "  --help                 Show this message\n",
            argv0, argv0, argv0);
    } else {
        std::fprintf(stdout,
            "ICR — Pluggable Synthesizer\n"
            "\n"
            "Usage:\n"
            "  %s --core <name> [--params <file>] [--config <file>]\n"
            "             [--core-param key=value ...] [--port <N>]\n"
            "  %s --core <name> --params <file> --render-batch <batch.json>\n"
            "             --out-dir <dir> [--sr <hz>]\n"
            "  %s --list-cores\n"
            "  %s --help\n"
            "\n"
            "Options:\n"
            "  --core <name>          Synthesis core (default: SineCore)\n"
            "  --params <path>        Core parameter JSON (core-specific)\n"
            "  --config <path>        SynthConfig JSON applied via setParam\n"
            "  --core-param key=val   Override a core parameter (repeatable)\n"
            "  --ir <path>            Soundboard impulse response WAV\n"
            "  --port <N>             MIDI input port index (default: 0)\n"
            "  --midi-range-limit-from <N>  Skip notes with MIDI < N (default: 0)\n"
            "  --midi-range-limit-to <N>    Skip notes with MIDI > N (default: 127)\n"
            "  --list-cores           List registered cores and exit\n"
            "  --help                 Show this message\n"
            "\n"
            "Offline batch render mode (no audio device):\n"
            "  --render-batch <json>  JSON array [{midi,vel_idx,duration_s},...]\n"
            "  --out-dir <dir>        Output directory for WAV files\n"
            "  --sr <hz>              Sample rate (default: 48000)\n"
            "\n"
            "Keyboard fallback (no MIDI hardware):\n"
            "  a s d f g h j k  ->  C4 D4 E4 F4 G4 A4 B4 C5\n"
            "  z                ->  sustain (toggle)\n"
            "  q                ->  quit\n",
            argv0, argv0, argv0, argv0);
    }
}

// ── Argument parsing ─────────────────────────────────────────────────────────

int AppConfig::parse(int argc, char* argv[], bool gui_mode) {
    for (int i = 1; i < argc; ++i) {
        std::string a(argv[i]);

        if (a == "--help" || a == "-h") {
            printHelp(argv[0], gui_mode);
            return 2;
        } else if (a == "--list-cores") {
            auto cores = SynthCoreRegistry::instance().availableCores();
            std::fprintf(stdout, "Available cores:\n");
            for (const auto& c : cores)
                std::fprintf(stdout, "  %s\n", c.c_str());
            return 2;
        } else if (a == "--engine-config" && i + 1 < argc) {
            engine_config_ = argv[++i];
        } else if (a == "--core" && i + 1 < argc) {
            core_name_ = argv[++i];
        } else if (a == "--params" && i + 1 < argc) {
            params_json_ = argv[++i];
        } else if (a == "--config" && i + 1 < argc) {
            config_json_ = argv[++i];
        } else if (a == "--core-param" && i + 1 < argc) {
            std::string kv(argv[++i]);
            auto eq = kv.find('=');
            if (eq != std::string::npos) {
                core_params_.emplace_back(kv.substr(0, eq),
                                          std::stof(kv.substr(eq + 1)));
            } else {
                std::fprintf(stderr, "--core-param: expected key=value, got: %s\n",
                             kv.c_str());
                return 1;
            }
        } else if (a == "--ir" && i + 1 < argc) {
            ir_path_ = argv[++i];
        } else if (a == "--midi-range-limit-from" && i + 1 < argc) {
            midi_from_ = std::atoi(argv[++i]);
        } else if (a == "--midi-range-limit-to" && i + 1 < argc) {
            midi_to_ = std::atoi(argv[++i]);
        }
        // CLI-only options
        else if (!gui_mode && a == "--port" && i + 1 < argc) {
            midi_port_ = std::atoi(argv[++i]);
        } else if (!gui_mode && a == "--render-batch" && i + 1 < argc) {
            render_batch_ = argv[++i];
        } else if (!gui_mode && a == "--out-dir" && i + 1 < argc) {
            render_out_dir_ = argv[++i];
        } else if (!gui_mode && a == "--sr" && i + 1 < argc) {
            render_sr_ = std::atoi(argv[++i]);
        } else {
            std::fprintf(stderr, "Unknown option: %s\n\n", a.c_str());
            printHelp(argv[0], gui_mode);
            return 1;
        }
    }
    return 0;
}

// ── Engine initialization ────────────────────────────────────────────���───────

bool AppConfig::initEngine(Engine& engine, Logger& logger) {
    // Load engine config (per-core paths, default_core)
    std::string cfg = engine_config_.empty() ? "icr-config.json" : engine_config_;
    engine.loadEngineConfig(cfg, logger);

    // Resolve core name: CLI arg > engine config default > SineCore
    if (core_name_.empty())
        core_name_ = engine.defaultCoreName();
    if (core_name_.empty())
        core_name_ = "SineCore";

    logger.log("main", LogSeverity::Info, "ICR — " + core_name_);

    if (!engine.initialize(core_name_, params_json_, config_json_,
                           logger, midi_from_, midi_to_)) {
        logger.log("main", LogSeverity::Error, "Engine init failed");
        return false;
    }

    // Load soundboard IR if provided
    if (!ir_path_.empty()) {
        DspChain* dsp = engine.getDspChain();
        if (dsp && dsp->loadConvolverIR(ir_path_, 0)) {
            dsp->setConvolverEnabled(true);
            logger.log("main", LogSeverity::Info,
                       "Loaded soundboard IR: " + ir_path_);
        } else {
            logger.log("main", LogSeverity::Error,
                       "Failed to load IR: " + ir_path_);
        }
    }

    // Apply --core-param overrides
    for (const auto& kv : core_params_) {
        if (!engine.core()->setParam(kv.first, kv.second))
            logger.log("main", LogSeverity::Warning,
                       "Unknown core param: " + kv.first);
    }

    return true;
}
