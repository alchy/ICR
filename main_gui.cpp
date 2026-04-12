/*
 * main_gui.cpp — ICR GUI
 * ────────────────────────
 * Argument parsing and engine init are handled by AppConfig.
 * This file just starts the engine and hands off to the GUI event loop.
 */

#include "engine/app_config.h"
#include "gui/engine_gui.h"

// Core registrations (static-init side effects)
#include "cores/sine/sine_core.h"
#include "cores/additive_synthesis_piano/additive_synthesis_piano_core.h"
#include "cores/physical_modeling_piano/physical_modeling_piano_core.h"
#include "cores/sampler/sampler_core.h"

#include <memory>
#include <cstdio>

int main(int argc, char* argv[]) {
    ICR_ENABLE_FTZ();
    setvbuf(stdout, nullptr, _IONBF, 0);

    AppConfig cfg;
    int rc = cfg.parse(argc, argv, /*gui_mode=*/true);
    if (rc != 0) return (rc == 2) ? 0 : 1;

    Logger logger(stdout, stdout);

    try {
        auto engine = std::make_unique<Engine>();
        if (!cfg.initEngine(*engine, logger)) return 1;

        if (!engine->start()) {
            logger.log("main", LogSeverity::Error, "Audio start failed");
            return 1;
        }

        int ret = runEngineGui(*engine, logger);

        engine->stop();
        logger.log("main", LogSeverity::Info, "=== STOPPED ===");
        return ret;

    } catch (const std::exception& e) {
        logger.log("main", LogSeverity::Critical,
                   std::string("FATAL: ") + e.what());
        return 1;
    }
}
