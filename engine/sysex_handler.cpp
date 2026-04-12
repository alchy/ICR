/*
 * sysex_handler.cpp
 * ──────────────────
 * ICR SysEx protocol: command parsing and dispatch.
 *
 * Supported commands:
 *   0x70  PING  → returns PONG (0x71)
 *   0x01  SET_NOTE_PARAM      — per-note scalar parameter
 *   0x02  SET_NOTE_PARTIAL    — per-partial parameter
 *   0x03  SET_BANK            — chunked JSON bank replace
 *   0x10  SET_MASTER           — engine/core/DSP global parameters
 *   0x72  EXPORT_BANK         — export bank JSON to file
 *
 * See docs/engine/SYSEX_PROTOCOL.md for full protocol specification.
 */

#include "sysex_handler.h"
#include "engine.h"

#include <cstring>
#include <algorithm>

// ── Helpers ──────────────────────────────────────────────────────────────────

static float decodeSysExFloat(const uint8_t* b) {
    uint32_t bits = 0;
    for (int i = 0; i < 5; ++i)
        bits |= (uint32_t)(b[i] & 0x7F) << ((4 - i) * 7);
    float v;
    std::memcpy(&v, &bits, sizeof(v));
    return v;
}

static const char* noteParamKey(uint8_t id) {
    switch (id) {
        // Shared (additive + physical)
        case 0x01: return "f0_hz";
        case 0x02: return "B";
        // Additive-specific (0x03-0x06)
        case 0x03: return "attack_tau";
        case 0x04: return "A_noise";
        case 0x05: return "rms_gain";
        case 0x06: return "phi_diff";
        // Physical-specific (0x10-0x1D)
        case 0x10: return "gauge";
        case 0x11: return "T60_fund";
        case 0x12: return "T60_nyq";
        case 0x13: return "exc_x0";
        case 0x14: return "K_hardening";
        case 0x15: return "p_hardening";
        case 0x16: return "n_disp_stages";
        case 0x17: return "disp_coeff";
        case 0x18: return "n_strings";
        case 0x19: return "detune_cents";
        case 0x1A: return "hammer_mass";
        case 0x1B: return "string_mass";
        case 0x1C: return "output_scale";
        case 0x1D: return "bridge_refl";
        default:   return nullptr;
    }
}

static const char* partialParamKey(uint8_t id) {
    switch (id) {
        case 0x10: return "f_hz";
        case 0x11: return "A0";
        case 0x12: return "tau1";
        case 0x13: return "tau2";
        case 0x14: return "a1";
        case 0x15: return "beat_hz";
        case 0x16: return "phi";
        default:   return nullptr;
    }
}

static const char* masterCoreParamKey(uint8_t id) {
    switch (id) {
        case 0x01: return "beat_scale";
        case 0x02: return "noise_level";
        case 0x03: return "pan_spread";
        case 0x04: return "stereo_decorr";
        case 0x05: return "keyboard_spread";
        case 0x06: return "eq_strength";
        case 0x07: return "rng_seed";
        default:   return nullptr;
    }
}

static const char* coreIdToName(uint8_t core_id) {
    switch (core_id) {
        case 0x01: return "AdditiveSynthesisPianoCore";
        case 0x02: return "PhysicalModelingPianoCore";
        case 0x03: return "SamplerCore";
        case 0x04: return "SineCore";
        default:   return nullptr;
    }
}

// ── Main dispatch ────────────────────────────────────────────────────────────

std::vector<uint8_t> SysExHandler::handle(const uint8_t* data, int len) {
    if (len < 3) return {};
    if (data[0] != 0x7D || data[1] != 0x01) return {};  // not ICR SysEx

    uint8_t cmd = data[2];

    // PING/PONG — no core_id needed
    if (cmd == 0x70) return { 0xF0, 0x7D, 0x01, 0x71, 0xF7 };

    // All other commands: next byte is core_id
    if (len < 4) return {};
    uint8_t        core_id    = data[3];
    const uint8_t* payload    = data + 4;
    int            payloadLen = len - 4;

    // Resolve target core from core_id
    ISynthCore* target = nullptr;
    bool engine_level = (core_id == 0x7F);

    if (!engine_level) {
        if (core_id == 0x00) {
            target = engine_.core();  // active core
        } else {
            const char* name = coreIdToName(core_id);
            if (name) target = engine_.coreByName(name);
        }
    }

    MasterBus& bus = engine_.masterBus();
    DspChain*  dsp = engine_.getDspChain();
    Logger&    log = engine_.getLogger();

    switch (cmd) {

    case 0x01: {  // SET_NOTE_PARAM
        if (payloadLen < 8 || !target) break;
        int         midi  = payload[0];
        int         vel   = payload[1];
        uint8_t     pid   = payload[2];
        float       value = decodeSysExFloat(payload + 3);
        const char* key   = noteParamKey(pid);
        if (key) target->setNoteParam(midi, vel, key, value);
        break;
    }

    case 0x02: {  // SET_NOTE_PARTIAL
        if (payloadLen < 9 || !target) break;
        int         midi  = payload[0];
        int         vel   = payload[1];
        int         k     = payload[2];
        uint8_t     pid   = payload[3];
        float       value = decodeSysExFloat(payload + 4);
        const char* key   = partialParamKey(pid);
        if (key) target->setNotePartialParam(midi, vel, k, key, value);
        break;
    }

    case 0x03: {  // SET_BANK — chunked JSON
        if (payloadLen < 6 || !target) break;
        int chunk_idx    = ((int)payload[0] << 14) | ((int)payload[1] << 7) | payload[2];
        int total_chunks = ((int)payload[3] << 14) | ((int)payload[4] << 7) | payload[5];
        const uint8_t* chunk_data = payload + 6;
        int chunk_len = payloadLen - 6;

        if (chunk_idx == 0) {
            bank_chunk_buf_.clear();
            bank_chunk_buf_.reserve((size_t)total_chunks * 240);
            bank_chunk_total_ = total_chunks;
            bank_chunk_recv_  = 0;
        }
        bank_chunk_buf_.append(reinterpret_cast<const char*>(chunk_data),
                               (size_t)chunk_len);
        ++bank_chunk_recv_;

        if (bank_chunk_recv_ >= bank_chunk_total_) {
            if (target->loadBankJson(bank_chunk_buf_))
                log.log("SysEx", LogSeverity::Info,
                        "SET_BANK: applied ("
                        + std::to_string(bank_chunk_buf_.size()) + " bytes)"
                        + " core_id=0x" + std::to_string((int)core_id));
            else
                log.log("SysEx", LogSeverity::Warning,
                        "SET_BANK: loadBankJson failed");
            bank_chunk_buf_.clear();
            bank_chunk_total_ = 0;
            bank_chunk_recv_  = 0;
        }
        break;
    }

    case 0x10: {  // SET_MASTER
        if (payloadLen < 6) break;
        uint8_t pid   = payload[0];
        float   value = decodeSysExFloat(payload + 1);

        if (engine_level || pid >= 0x10) {
            if (pid >= 0x10 && pid <= 0x13) {
                switch (pid) {
                case 0x10:
                    bus.setGain((std::max)(0.f, (std::min)(2.f, value)));
                    break;
                case 0x11: {
                    float n = (std::max)(-1.f, (std::min)(1.f, value));
                    if (n <= 0.f) bus.setPan(1.f, 1.f + n);
                    else          bus.setPan(1.f - n, 1.f);
                    break;
                }
                case 0x12:
                    bus.setLfoSpeed((std::max)(0.f, (std::min)(2.f, value)));
                    break;
                case 0x13:
                    bus.setLfoDepth((std::max)(0.f, (std::min)(1.f, value)));
                    break;
                }
            } else if (pid >= 0x20 && pid <= 0x26 && dsp) {
                auto u = (uint8_t)((std::max)(0.f, (std::min)(1.f, value)) * 127.f);
                switch (pid) {
                case 0x20: dsp->setLimiterThreshold(u); break;
                case 0x21: dsp->setLimiterRelease(u);   break;
                case 0x22: dsp->setLimiterEnabled(u);   break;
                case 0x23: dsp->setBBEDefinition(u);    break;
                case 0x24: dsp->setBBEBassBoost(u);     break;
                case 0x25: dsp->setConvolverEnabled(value >= 0.5f); break;
                case 0x26: dsp->setConvolverMix(value);              break;
                }
            }
        }

        if (!engine_level && pid <= 0x07 && target) {
            const char* key = masterCoreParamKey(pid);
            if (key) target->setParam(key, value);
        }
        break;
    }

    case 0x72: {  // EXPORT_BANK
        if (payloadLen < 1 || !target) break;
        std::string export_path(reinterpret_cast<const char*>(payload),
                                (size_t)payloadLen);
        if (target->exportBankJson(export_path))
            log.log("SysEx", LogSeverity::Info,
                    "EXPORT_BANK: wrote " + export_path);
        else
            log.log("SysEx", LogSeverity::Warning,
                    "EXPORT_BANK: failed to write " + export_path);
        break;
    }

    default:
        log.log("SysEx", LogSeverity::Info,
                "Unknown cmd 0x" + std::to_string((int)cmd));
        break;
    }
    return {};
}
