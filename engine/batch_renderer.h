#pragma once
/*
 * batch_renderer.h
 * ─────────────────
 * Offline batch render: given a JSON spec and an ISynthCore, renders
 * each note to a stereo 16-bit WAV file.  No audio device needed.
 *
 * Usage:
 *   int n = renderBatch(*core, logger, "batch.json", "exports/", 48000);
 */

#include "i_synth_core.h"
#include "logger.h"
#include <string>

// Render notes from batch JSON spec to WAV files.
// Returns number of notes successfully rendered.
int renderBatch(ISynthCore& core,
                Logger&     logger,
                const std::string& batch_json_path,
                const std::string& out_dir,
                int                sr = 48000);
