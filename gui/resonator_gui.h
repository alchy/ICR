#pragma once
#include "../engine/core_engine.h"
#include "../engine/core_logger.h"
#include <string>

// Run the GUI event loop (blocks until window closed).
// engine must already be initialized and started.
int runResonatorGui(CoreEngine& engine, Logger& logger);
