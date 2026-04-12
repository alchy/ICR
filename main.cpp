/*
 * main.cpp — ICR headless real-time CLI
 * ──────────────────────────────────────
 * Argument parsing and engine init are handled by AppConfig.
 * This file owns the interactive keyboard/MIDI loop and batch render mode.
 */

#include "engine/app_config.h"
#include "engine/midi_input.h"

// Core registrations (static-init side effects)
#include "cores/sine/sine_core.h"
#include "cores/additive_synthesis_piano/additive_synthesis_piano_core.h"
#include "cores/physical_modeling_piano/physical_modeling_piano_core.h"
#include "cores/sampler/sampler_core.h"

#include <memory>
#include <cstdio>
#include <thread>
#include <chrono>

#ifdef _WIN32
  #include <conio.h>
#else
  #include <termios.h>
  #include <unistd.h>
  #include <fcntl.h>
#endif

static void sleepMs(int ms) {
    std::this_thread::sleep_for(std::chrono::milliseconds(ms));
}

int main(int argc, char* argv[]) {
    ICR_ENABLE_FTZ();
    setvbuf(stdout, nullptr, _IONBF, 0);

    AppConfig cfg;
    int rc = cfg.parse(argc, argv, /*gui_mode=*/false);
    if (rc != 0) return (rc == 2) ? 0 : 1;

    Logger logger(stdout, stdout);

    try {
        auto engine = std::make_unique<Engine>();
        if (!cfg.initEngine(*engine, logger)) return 1;

        // ── Offline batch render mode ────────────────────────────────────
        if (!cfg.renderBatch().empty()) {
            if (cfg.renderOutDir().empty()) {
                logger.log("main", LogSeverity::Error,
                           "--render-batch requires --out-dir");
                return 1;
            }
            int n = engine->renderBatch(cfg.renderBatch(), cfg.renderOutDir(),
                                        cfg.renderSr());
            return (n > 0) ? 0 : 1;
        }

        if (!engine->start()) return 1;

        // ── MIDI input ───────────────────────────────────────────────────
        MidiInput midi;
        auto ports = MidiInput::listPorts();
        if (!ports.empty()) {
            for (int i = 0; i < (int)ports.size(); i++)
                logger.log("MIDI", LogSeverity::Info,
                           "port [" + std::to_string(i) + "] " + ports[i]);
            midi.open(*engine, cfg.midiPort());
        }
#ifndef _WIN32
        if (!midi.isOpen()) midi.openVirtual(*engine);
#endif

        // ── Keyboard fallback ────────────────────────────────────────────
        const char keys[] = "asdfghjk";
        const int  midis[] = { 60, 62, 64, 65, 67, 69, 71, 72 };
        bool       sus = false;
        logger.log("main", LogSeverity::Info,
                   "Keyboard: a-k = C4-C5  |  z = sustain  |  q = quit");

#ifdef _WIN32
        while (true) {
            if (_kbhit()) {
                int ch = _getch();
                if (ch == 'q' || ch == 'Q') break;
                if (ch == 'z') {
                    sus = !sus;
                    engine->sustainPedal(sus ? 127 : 0);
                    continue;
                }
                for (int i = 0; i < 8; i++) {
                    if (ch == keys[i]) {
                        engine->noteOn((uint8_t)midis[i], 80);
                        sleepMs(300);
                        engine->noteOff((uint8_t)midis[i]);
                    }
                }
            }
            sleepMs(1);
        }
#else
        struct termios oldt, newt;
        tcgetattr(STDIN_FILENO, &oldt);
        newt = oldt;
        newt.c_lflag &= ~(ICANON | ECHO);
        tcsetattr(STDIN_FILENO, TCSANOW, &newt);
        fcntl(STDIN_FILENO, F_SETFL, O_NONBLOCK);
        while (true) {
            char ch;
            if (read(STDIN_FILENO, &ch, 1) == 1) {
                if (ch == 'q' || ch == 'Q') break;
                if (ch == 'z') { sus = !sus; engine->sustainPedal(sus ? 127 : 0); }
                for (int i = 0; i < 8; i++) {
                    if (ch == keys[i]) {
                        engine->noteOn((uint8_t)midis[i], 80);
                        sleepMs(300);
                        engine->noteOff((uint8_t)midis[i]);
                    }
                }
            }
            sleepMs(1);
        }
        tcsetattr(STDIN_FILENO, TCSANOW, &oldt);
#endif

        midi.close();
        engine->stop();
        logger.log("main", LogSeverity::Info, "=== STOPPED ===");
        return 0;

    } catch (const std::exception& e) {
        logger.log("main", LogSeverity::Critical, std::string("FATAL: ") + e.what());
        return 1;
    }
}
