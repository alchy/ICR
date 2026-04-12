#pragma once
/*
 * midi_input.h
 * ─────────────
 * Cross-platform MIDI input via RtMidi.
 * Receives note-on/off, sustain pedal, and passes events to Engine.
 *
 * Engine is forward-declared to avoid circular inclusion
 * (engine.cpp includes midi_input.h).
 */

#include "../third_party/RtMidi.h"
#include <string>
#include <vector>
#include <atomic>
#include <cstdint>

class Engine;

// ── MIDI activity timestamps (updated from callback thread, read from GUI) ────
// Each field holds the steady_clock millisecond timestamp of the last event.
// 0 = never received.
struct MidiActivity {
    std::atomic<uint64_t> any_ms     {0};  // any MIDI message
    std::atomic<uint64_t> sysex_ms   {0};  // SysEx applied
    std::atomic<uint64_t> note_on_ms {0};  // Note On
    std::atomic<uint64_t> note_off_ms{0};  // Note Off
    std::atomic<uint64_t> pedal_ms   {0};  // CC 64 sustain pedal
};

class MidiInput {
public:
    MidiInput() = default;
    ~MidiInput() { close(); }

    // List available MIDI input ports (for user selection)
    static std::vector<std::string> listPorts();
    static std::vector<std::string> listOutputPorts();

    // Open port by index (0 = first available). Returns false if none found.
    bool open(Engine& engine, int port_index = 0);

    // Open virtual port (macOS/Linux — allows DAW to send MIDI)
    bool openVirtual(Engine& engine, const std::string& name = "IthacaCoreResonator");

    void close();
    bool isOpen() const { return midi_ && midi_->isPortOpen(); }
    std::string portName() const { return port_name_; }

    // Open a MIDI output port for sending PONG responses.
    // Call after open(). Optional: PONG is silently dropped if no output is open.
    bool openOutput(int port_index = 0);
    void closeOutput();
    bool isOutputOpen() const { return midi_out_ != nullptr; }

    // Activity timestamps — read from any thread (GUI, main)
    const MidiActivity& activity() const { return activity_; }

private:
    static void callback(double /*timestamp*/,
                         std::vector<unsigned char>* msg,
                         void* user_data);

    void sendRaw(const std::vector<uint8_t>& bytes);

    RtMidiIn*   midi_     = nullptr;
    RtMidiOut*  midi_out_ = nullptr;
    Engine* engine_   = nullptr;
    std::string port_name_;
    MidiActivity activity_;
};
