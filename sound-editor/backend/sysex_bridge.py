"""
sound-editor/backend/sysex_bridge.py
──────────────────────────────────────
SysEx communication with ICRGUI / ICR.exe.

Protocol: F0 7D 01 <cmd> <core_id> <data...> F7
  7D = non-commercial manufacturer ID
  01 = ICR device ID

Core IDs:
  00  Active core (whichever is selected)
  01  AdditiveSynthesisPianoCore
  02  PhysicalModelingPianoCore
  03  SamplerCore
  04  SineCore
  7F  Engine-level (master mix, DspChain)

Commands:
  01  SET_NOTE_PARAM    core_id midi vel param_id value_f32
  02  SET_NOTE_PARTIAL  core_id midi vel k param_id value_f32
  03  SET_BANK          core_id <chunked JSON>
  10  SET_MASTER        core_id param_id value_f32
  70  PING              (no core_id)
  72  EXPORT_BANK       core_id <ASCII path bytes>
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

# Core IDs for per-core SysEx addressing
CORE_ID_ACTIVE       = 0x00   # whichever core is currently selected
CORE_ID_ADDITIVE     = 0x01   # AdditiveSynthesisPianoCore
CORE_ID_PHYSICAL     = 0x02   # PhysicalModelingPianoCore
CORE_ID_SAMPLER      = 0x03   # SamplerCore
CORE_ID_SINE         = 0x04   # SineCore
CORE_ID_ENGINE       = 0x7F   # engine-level (master mix, DspChain)

CMD_SET_NOTE_PARAM   = 0x01
CMD_SET_NOTE_PARTIAL = 0x02
CMD_SET_BANK         = 0x03
CMD_SET_MASTER       = 0x10
CMD_PING             = 0x70
CMD_PONG             = 0x71
CMD_EXPORT_BANK      = 0x72

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

    core_id determines which core receives the messages:
      CORE_ID_ADDITIVE (0x01) — AdditiveSynthesisPianoCore (default for editor)
      CORE_ID_ACTIVE (0x00)   — whichever core is active
      CORE_ID_ENGINE (0x7F)   — engine-level (master mix, DspChain)
    """

    def __init__(self, port_name: Optional[str] = None,
                 core_id: int = CORE_ID_ADDITIVE):
        self._port_name = port_name
        self._port = None
        self._core_id = core_id
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
        """Send full soundbank JSON (chunked).

        Header encoding (6 bytes, all 7-bit safe):
          chunk_idx   as 3 × 7-bit bytes (big-endian, supports up to 2M chunks)
          total_chunks as 3 × 7-bit bytes
        """
        chunks = [json_bytes[i:i+CHUNK_SIZE]
                  for i in range(0, len(json_bytes), CHUNK_SIZE)]
        total = len(chunks)
        for idx, chunk in enumerate(chunks):
            header = _encode_int21(idx) + _encode_int21(total)
            self._send(CMD_SET_BANK, header + list(chunk))
            time.sleep(0.002)   # give ICR time to buffer

    def set_master(self, param_key: str, value: float):
        """Update a master/global parameter (beat_scale, master_gain, limiter_threshold, …).

        Core-specific params (0x01-0x07) are sent with this bridge's core_id.
        Engine params (0x10+) are sent with CORE_ID_ENGINE (0x7F).
        """
        param_id = MASTER_PARAM_IDS.get(param_key)
        if param_id is None:
            raise ValueError(f"Unknown master param key: {param_key!r}. "
                             f"Valid keys: {list(MASTER_PARAM_IDS)}")
        # Engine-level params always go to core_id 0x7F
        cid = CORE_ID_ENGINE if param_id >= 0x10 else None
        self._send(CMD_SET_MASTER, [param_id] + _f32_to_sysex_bytes(value),
                   core_id=cid)

    def ping(self) -> bool:
        """Send PING; returns True if sent (no ACK over SysEx yet)."""
        self._send_raw(CMD_PING, [])  # PING has no core_id
        return True

    def export_bank(self, path: str):
        """
        Ask ICR to export its current in-memory bank to *path* (absolute path
        on the machine running ICR).  ICR writes a JSON file identical in
        format to the soundbank loaded via set_bank().

        The path is sent as raw ASCII bytes — keep it to ASCII characters only.
        """
        self._send(CMD_EXPORT_BANK, list(path.encode("ascii")))

    # ── Low-level ─────────────────────────────────────────────────────────────

    def _send(self, cmd: int, data: list[int], core_id: Optional[int] = None):
        """Send SysEx with core_id: F0 7D 01 <cmd> <core_id> <data...> F7"""
        cid = core_id if core_id is not None else self._core_id
        self._send_raw(cmd, [cid] + data)

    def _send_raw(self, cmd: int, data: list[int]):
        """Send raw SysEx without auto core_id (used for PING)."""
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

def _encode_int21(value: int) -> list[int]:
    """Encode a non-negative integer as 3 × 7-bit SysEx-safe bytes (big-endian)."""
    return [(value >> 14) & 0x7F, (value >> 7) & 0x7F, value & 0x7F]


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
