"""
sound-editor/backend/sysex_bridge.py
──────────────────────────────────────
SysEx communication with ICRGUI / ICR.exe.

Protocol: F0 7D 01 <cmd> <data...> F7
  7D = non-commercial manufacturer ID
  01 = ICR device ID

Commands:
  01  SET_NOTE_PARAM    midi vel param_id value_f32
  02  SET_NOTE_PARTIAL  midi vel k param_id value_f32
  03  SET_BANK          <chunked JSON>
  10  SET_MASTER        param_id value_f32
  F0  PING
  F1  PONG
"""

import struct
import time
from typing import Optional

try:
    import mido
    MIDO_AVAILABLE = True
except ImportError:
    MIDO_AVAILABLE = False


# ── SysEx constants ───────────────────────────────────────────────────────────

MANUFACTURER_ID = 0x7D   # non-commercial
DEVICE_ID       = 0x01

CMD_SET_NOTE_PARAM   = 0x01
CMD_SET_NOTE_PARTIAL = 0x02
CMD_SET_BANK         = 0x03
CMD_SET_MASTER       = 0x10
CMD_PING             = 0xF0
CMD_PONG             = 0xF1

# Scalar param IDs — per-note fields (commands 0x01 SET_NOTE_PARAM)
PARAM_IDS = {
    "f0_hz":      0x01,
    "B":          0x02,
    "attack_tau": 0x03,
    "A_noise":    0x04,
    "rms_gain":   0x05,
    "phi_diff":   0x06,
}

# Master param IDs (command 0x10 SET_MASTER)
#   0x01–0x07  ISynthCore global params  (physical units matching setParam)
#   0x10–0x13  CoreEngine mix params     (physical units: gain 0–2, pan -1–+1, Hz, 0–1)
#   0x20–0x24  DspChain params           (normalised 0.0–1.0)
MASTER_PARAM_IDS = {
    # ISynthCore global
    "beat_scale":        0x01,   # ×  0.0–4.0
    "noise_level":       0x02,   # ×  0.0–4.0
    "pan_spread":        0x03,   # rad 0.0–π
    "stereo_decorr":     0x04,   # ×  0.0–2.0
    "keyboard_spread":   0x05,   # rad 0.0–π
    "eq_strength":       0x06,   # ×  0.0–1.0
    "rng_seed":          0x07,   # int 0–9999
    # CoreEngine mix
    "master_gain":       0x10,   # 0.0–2.0
    "master_pan":        0x11,   # -1.0–+1.0
    "lfo_speed":         0x12,   # Hz  0.0–2.0
    "lfo_depth":         0x13,   # 0.0–1.0
    # DspChain (normalised 0.0–1.0 → uint8 0–127 on synth side)
    "limiter_threshold": 0x20,   # 0=−40 dB, 1=0 dB
    "limiter_release":   0x21,   # 0=10 ms, 1=2000 ms
    "limiter_enabled":   0x22,   # ≥0.5 = on
    "bbe_definition":    0x23,   # 0=0 dB, 1=12 dB
    "bbe_bass_boost":    0x24,   # 0=0 dB, 1=10 dB
}

# Per-partial param IDs
PARTIAL_PARAM_IDS = {
    "f_hz":    0x10,
    "A0":      0x11,
    "tau1":    0x12,
    "tau2":    0x13,
    "a1":      0x14,
    "beat_hz": 0x15,
    "phi":     0x16,
}

CHUNK_SIZE = 240   # max SysEx data bytes per message (safe MIDI limit)


class SysExBridge:
    """
    Sends SysEx messages to the ICR synthesizer via a MIDI output port.
    """

    def __init__(self, port_name: Optional[str] = None):
        self._port_name = port_name
        self._port = None
        if MIDO_AVAILABLE and port_name:
            self.open(port_name)

    def open(self, port_name: str):
        if not MIDO_AVAILABLE:
            raise RuntimeError("mido not installed — run: pip install mido python-rtmidi")
        self._port = mido.open_output(port_name)
        self._port_name = port_name

    def close(self):
        if self._port:
            self._port.close()
            self._port = None

    def is_open(self) -> bool:
        return self._port is not None

    # ── High-level send methods ───────────────────────────────────────────────

    def set_note_param(self, midi: int, vel: int, param_key: str, value: float):
        """Update one scalar parameter for (midi, vel)."""
        param_id = PARAM_IDS.get(param_key)
        if param_id is None:
            raise ValueError(f"Unknown param key: {param_key}")
        data = [midi, vel, param_id] + _f32_to_sysex_bytes(value)
        self._send(CMD_SET_NOTE_PARAM, data)

    def set_note_partial(self, midi: int, vel: int, k: int,
                         param_key: str, value: float):
        """Update one per-partial parameter for (midi, vel, k)."""
        param_id = PARTIAL_PARAM_IDS.get(param_key)
        if param_id is None:
            raise ValueError(f"Unknown partial param key: {param_key}")
        data = [midi, vel, k, param_id] + _f32_to_sysex_bytes(value)
        self._send(CMD_SET_NOTE_PARTIAL, data)

    def set_bank(self, json_bytes: bytes):
        """Send full soundbank JSON (chunked)."""
        chunks = [json_bytes[i:i+CHUNK_SIZE]
                  for i in range(0, len(json_bytes), CHUNK_SIZE)]
        total = len(chunks)
        for idx, chunk in enumerate(chunks):
            # Header: chunk_index(2), total_chunks(2), data
            header = struct.pack(">HH", idx, total)
            self._send(CMD_SET_BANK, list(header) + list(chunk))
            time.sleep(0.002)   # give ICR time to buffer

    def set_master(self, param_key: str, value: float):
        """Update a master/global parameter (beat_scale, master_gain, limiter_threshold, …)."""
        param_id = MASTER_PARAM_IDS.get(param_key)
        if param_id is None:
            raise ValueError(f"Unknown master param key: {param_key!r}. "
                             f"Valid keys: {list(MASTER_PARAM_IDS)}")
        self._send(CMD_SET_MASTER, [param_id] + _f32_to_sysex_bytes(value))

    def ping(self) -> bool:
        """Send PING; returns True if sent (no ACK over SysEx yet)."""
        self._send(CMD_PING, [])
        return True

    # ── Low-level ─────────────────────────────────────────────────────────────

    def _send(self, cmd: int, data: list[int]):
        if not self._port:
            raise RuntimeError("MIDI port not open")
        payload = [MANUFACTURER_ID, DEVICE_ID, cmd] + data
        msg = mido.Message("sysex", data=payload)
        self._port.send(msg)


# ── Port enumeration ──────────────────────────────────────────────────────────

def list_output_ports() -> list[str]:
    if not MIDO_AVAILABLE:
        return []
    return mido.get_output_names()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _f32_to_sysex_bytes(value: float) -> list[int]:
    """
    Encode float32 as 5 SysEx-safe bytes (7-bit each, no 0x00/0xFF/0xF*).

    We pack as big-endian uint32 then encode 4 bytes as 5×7-bit nibbles.
    """
    raw = struct.pack(">f", value)
    bits = int.from_bytes(raw, "big")
    # 5 × 7 bits = 35 bits; pad to 35 bits (32 + 3 zero padding bits)
    result = []
    for i in range(4, -1, -1):
        result.append((bits >> (i * 7)) & 0x7F)
    return result
