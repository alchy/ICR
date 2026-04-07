# ICR — C++ Engine Architecture

## High-Level Overview

```mermaid
graph TD
    MIDI[MIDI Input / GUI Keyboard] --> CE[CoreEngine]
    CE --> |MIDI queue| SC[ISynthCore]
    SC --> DSP[DspChain]
    DSP --> OUT[Audio Output]

    CE --> |master gain/pan/LFO| DSP
    SC --> |processBlock| DSP

    subgraph "ISynthCore (pluggable)"
        SC --> PM[PatchManager]
        PM --> VM[VoiceManager]
        VM --> V1[Voice 0]
        VM --> V2[Voice 1]
        VM --> VN[Voice 127]
    end

    subgraph "DspChain (master bus)"
        DSP --> CONV[Convolver]
        CONV --> BBE[BBE]
        BBE --> LIM[Limiter]
    end
```

## Three-Layer Core Architecture (Ithaca Core)

Každý ISynthCore implementuje 3-vrstvou architekturu:

```mermaid
graph TD
    subgraph "PatchManager"
        PM_IN[MIDI noteOn/Off/Sustain] --> PM_XLAT[Velocity/Note Translation]
        PM_XLAT --> PM_INTERP[Parameter Interpolation]
        PM_INTERP --> PM_SUSTAIN[Sustain Pedal Logic]
    end

    subgraph "VoiceManager"
        VM_INIT[initVoice] --> VM_POOL[Voice Pool 128]
        VM_REL[releaseVoice] --> VM_POOL
        VM_PROC[processBlock] --> VM_POOL
    end

    subgraph "Voice (nezavisla jednotka)"
        V_OSC[Oscilatory] --> V_ENV[Envelope]
        V_ENV --> V_NOISE[Noise]
        V_NOISE --> V_DECOR[Decorrelation]
        V_DECOR --> V_EQ[EQ Cascade]
        V_EQ --> V_MS[M/S Width]
        V_MS --> V_OUT[Stereo Output]
    end

    PM_INTERP --> VM_INIT
    PM_SUSTAIN --> VM_REL
    VM_POOL --> V_OSC
```

---

## Layer Responsibilities

### Voice (SineVoice / PianoVoice)

Nezavisla vypocetni jednotka. Nevi o MIDI, nepristupuje ke globalnimu
stavu. Prijima parametry v nativnim float formatu a produkuje stereo audio.
Muze byt distribuovana na samostatny HW modul.

| Metoda | Popis |
|--------|-------|
| `process(out_l, out_r, n_samples, ...)` | Produkuje audio, vraci false kdyz dohasne |

| Stav (SineVoice) | Typ | Popis |
|---|---|---|
| `active` | bool | Hlas aktivni |
| `releasing` | bool | Ve fazi dohasinani |
| `phase` | float | Faze oscilatoru (rad) |
| `omega` | float | Uhlova frekvence per sample |
| `amp` | float | Cilova amplituda |
| `onset_gain/step` | float | Onset rampa (click prevention) |
| `rel_gain/step` | float | Release rampa |

| Stav (PianoVoice) — navic | Typ | Popis |
|---|---|---|
| `partials[60]` | struct | Per-partial: env_fast/slow, decay, A0, f_hz, beat_hz, phi |
| `noise_bpf` | BiquadCoeffs | Bandpass noise filter |
| `rise_coeff/env` | float | Attack rise envelope |
| `eq_coeffs/wL/wR` | array | EQ biquad cascade state |
| `gl1..gr3` | float | Constant-power pan gains |
| `ap_g_L/R, ap_x/y` | float | Schroeder allpass state |
| `stereo_width` | float | M/S correction factor |

### VoiceManager (SineVoiceManager / PianoVoiceManager)

Spravuje pool hlasu. Inicializuje je s nativnimi parametry,
ridi release, procesuje vsechny aktivni hlasy.

| Metoda | Popis |
|--------|-------|
| `processBlock(out_l, out_r, n_samples, ...)` | Iteruje aktivni hlasy, deleguje na Voice::process |
| `initVoice(midi, ...)` | Inicializuje hlas s nativnimi parametry |
| `releaseVoice(midi, sr)` | Zahaji release fazi |
| `releaseAll(sr)` | Uvolni vsechny hlasy |
| `voice(midi)` | Getter — pristup k hlasu (pro vizualizaci) |

### PatchManager (SinePatchManager / PianoPatchManager)

Vstupni bod systemu. Prijima MIDI a preklada do nativni parametrizace.

| Metoda | Popis |
|--------|-------|
| `noteOn(midi, velocity, vm, ...)` | MIDI velocity → nativni amp/omega, deleguje na VoiceManager |
| `noteOff(midi, vm, sr)` | Sustain-aware release |
| `sustainPedal(down, vm, sr)` | Odlozene note-off pri sustain |
| `allNotesOff(vm, sr)` | Uvolni vse |
| `lastMidi/lastVel/lastVelIdx()` | Info pro GUI |

PianoPatchManager navic:
| Metoda | Popis |
|--------|-------|
| `midiVelToFloat(vel)` | Velocity 1-127 → float 0.0-7.0 |
| `lerpNoteParams(a, b, t)` | Interpolace parametru mezi velocity vrstvami |

---

## Signal Chain

```mermaid
graph LR
    subgraph "Per-Voice (PianoCore)"
        P[Partials 1-60] --> RE[Rise Envelope]
        RE --> PLUS((+))
        N[Noise BPF] --> PLUS
        PLUS --> AP[Allpass Decorr]
        AP --> EQ[EQ 10x Biquad]
        EQ --> MS[M/S Width]
    end

    subgraph "Master Bus (DspChain)"
        MS --> CONV[Convolver IR]
        CONV --> BBE2[BBE]
        BBE2 --> LIM2[Limiter]
        LIM2 --> MG[Master Gain + LFO Pan]
    end

    MG --> AUDIO[Audio Device]
```

## Threading Model

| Vlakno | Pristup | Poznamka |
|--------|---------|----------|
| RT (audio callback) | Voice::process, VoiceManager::processBlock | Zero-allocation, lock-free |
| MIDI callback | PatchManager::noteOn/Off/sustainPedal | Pushuje do MIDI queue |
| GUI | setParam/getParam, getVizState | Atomic reads/writes |

Komunikace RT ← GUI: pres `std::atomic<float>` (relaxed ordering).
Komunikace MIDI → RT: pres lock-free SPSC ring buffer (256 events).
Jedina mutex: `bank_mutex_` pri loadBankJson (try_lock z RT, block z MIDI).

## File Structure

```
cores/
  sine/
    sine_core.h/cpp        SineVoice + SineVoiceManager + SinePatchManager + SineCore
  piano/
    piano_core.h/cpp       PianoVoice + PianoVoiceManager + PianoPatchManager + PianoCore
    piano_math.h           Cista DSP matematika (stateless, inline)
engine/
    core_engine.h/cpp      CoreEngine (audio callback, MIDI queue, master bus)
    i_synth_core.h         ISynthCore interface + viz structs
    synth_core_registry.h  Factory pattern pro pluggable cores
    midi_input.h/cpp       RtMidi wrapper
dsp/
    dsp_math.h             Sdilene DSP primitivy (biquad, RBJ, decay_coeff)
    dsp_chain.h/cpp        Master bus orchestrator
    limiter/               Peak limiter
    bbe/                   BBE Sonic Maximizer
    convolver/             Soundboard IR convolution
gui/
    resonator_gui.h/cpp    ImGui real-time GUI
```
