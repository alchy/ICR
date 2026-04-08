# AdditiveSynthesisPianoCore -- SysEx Parameter IDs

Core-specific SysEx parameters for AdditiveSynthesisPianoCore.
For protocol framing and engine-level commands, see [SysEx Protocol](../../engine/SYSEX_PROTOCOL.md).

---

## SET_NOTE_PARAM (0x01) -- Scalar Param IDs

```
F0 7D 01  01  <midi>  <vel>  <param_id>  <v0 v1 v2 v3 v4>  F7
```

| id   | key          | Description                                             |
|------|--------------|---------------------------------------------------------|
| 0x01 | `f0_hz`      | Fundamental frequency                                   |
| 0x02 | `B`          | Inharmonicity coefficient -- propagates to all 8 vel layers |
| 0x03 | `attack_tau` | Noise attack time const                                 |
| 0x04 | `A_noise`    | Noise amplitude                                         |
| 0x05 | `rms_gain`   | RMS output gain                                         |
| 0x06 | `phi_diff`   | Phase difference                                        |

Key definitions: [JSON_SCHEMA.md](JSON_SCHEMA.md)

**Note on `B`:** `setNoteParam` returns `false` for this key -- inharmonicity is
baked into partial frequencies at export time.  To change B, re-export the
soundbank and push via SET_BANK (0x03).

---

## SET_NOTE_PARTIAL (0x02) -- Per-Partial Param IDs

```
F0 7D 01  02  <midi>  <vel>  <k>  <param_id>  <v0 v1 v2 v3 v4>  F7
```

| id   | key       | Description                    |
|------|-----------|--------------------------------|
| 0x10 | `f_hz`    | Partial frequency (Hz)         |
| 0x11 | `A0`      | Initial amplitude              |
| 0x12 | `tau1`    | Fast decay time constant       |
| 0x13 | `tau2`    | Slow decay time constant       |
| 0x14 | `a1`      | Fast decay mixing coefficient  |
| 0x15 | `beat_hz` | String beating frequency (Hz)  |
| 0x16 | `phi`     | Initial phase                  |

Key definitions: [JSON_SCHEMA.md](JSON_SCHEMA.md)

---

## SET_MASTER (0x10) -- Core Global Params (0x01-0x07)

These IDs are routed to `ISynthCore::setParam` and are specific to
AdditiveSynthesisPianoCore:

| id   | key               | Range      | Description                    |
|------|-------------------|------------|--------------------------------|
| 0x01 | `beat_scale`      | 0.0-4.0    | Scales all beat_hz values      |
| 0x02 | `noise_level`     | 0.0-4.0    | Noise amplitude multiplier     |
| 0x03 | `pan_spread`      | 0.0-pi rad | Within-note string spread      |
| 0x04 | `stereo_decorr`   | 0.0-2.0    | Schroeder all-pass strength    |
| 0x05 | `keyboard_spread` | 0.0-pi rad | L-R spread across keyboard     |
| 0x06 | `eq_strength`     | 0.0-1.0    | Spectral EQ wet/dry blend      |
| 0x07 | `rng_seed`        | 0-9999     | Base RNG seed (applied at noteOn) |

---

## C++ entry points

| File | Symbol | Role |
|------|--------|------|
| `additive_synthesis_piano_core.cpp` | `setNoteParam` | Writes one scalar field |
| `additive_synthesis_piano_core.cpp` | `setNotePartialParam` | Writes one partial field |
| `additive_synthesis_piano_core.cpp` | `loadBankJson` | Full bank swap (from SET_BANK) |
| `additive_synthesis_piano_core.cpp` | `exportBankJson` | Serialize bank to JSON file |

---

## Python usage (SysExBridge)

```python
from sysex_bridge import SysExBridge

bridge = SysExBridge()
bridge.open("loopMIDI Port 1")

bridge.set_note_param(midi=60, vel=3, param_key="rms_gain", value=0.12)
bridge.set_note_partial(midi=60, vel=3, k=1, param_key="tau1", value=0.35)
bridge.set_bank(json_bytes)
bridge.close()
```
