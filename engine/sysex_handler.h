#pragma once
/*
 * sysex_handler.h
 * ────────────────
 * ICR SysEx protocol handler.
 *
 * Parses incoming SysEx messages and routes them to the appropriate
 * Engine methods and ISynthCore instances.  Owns the SET_BANK chunk
 * reassembly state.
 *
 * Usage:
 *   SysExHandler sysex(engine);
 *   auto response = sysex.handle(data, len);
 *   if (!response.empty()) sendMidi(response);
 */

#include <vector>
#include <string>
#include <cstdint>

class Engine;   // forward declaration — avoids circular include

class SysExHandler {
public:
    explicit SysExHandler(Engine& engine) : engine_(engine) {}

    // Process incoming SysEx payload (bytes AFTER F0, BEFORE F7).
    // Returns PONG response to send, or empty vector if no response needed.
    std::vector<uint8_t> handle(const uint8_t* data, int len);

private:
    Engine& engine_;

    // SET_BANK chunk reassembly state (MIDI callback thread only)
    std::string bank_chunk_buf_;
    int         bank_chunk_total_ = 0;
    int         bank_chunk_recv_  = 0;
};
