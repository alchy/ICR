#pragma once
#include "../engine/engine.h"
#include "../engine/logger.h"
#include <string>

// Run the GUI event loop (blocks until window closed).
// engine must already be initialized and started.
int runEngineGui(Engine& engine, Logger& logger);
