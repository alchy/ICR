# Analýza modularizace ICR — srovnání s IthacaCore architekturou

## Kontext

IthacaCore byl navržen jako knihovna s jasně oddělenými třídami, kde každá
komponenta (Logger, VoiceManager, Envelope, SamplerIO, InstrumentLoader, ...)
je samostatně instanciovatelná a použitelná. Inicializační sekvence je
lineární a dokumentovaná: Logger → EnvelopeStaticData → VoiceManager → prepareToPlay → start.

ICR se od tohoto přístupu odchýlil — třída `Engine` je monolitická
(1036 řádků, 33 member variables, 47+ metod) a kombinuje:

| Oblast               | Řádků | % celku | Složitost |
|----------------------|-------|---------|-----------|
| Config + core switch | 273   | 26%     | Střední   |
| SysEx protokol       | 248   | 24%     | Vysoká    |
| Offline batch render | 167   | 16%     | Střední   |
| RT audio processing  | 135   | 13%     | Nízká     |
| MIDI + DSP control   | 55    | 5%      | Nízká     |
| Ostatní              | 158   | 16%     | Nízká     |

---

## Kandidáti na extrakci

### 1. SysEx handler → `SysExHandler` třída

**Aktuálně:** `handleSysEx()` = 161 řádků + 87 řádků helper funkcí (celkem
~248 řádků, 24% engine.cpp). Zpracovává 6 typů příkazů: SET_NOTE_PARAM,
SET_NOTE_PARTIAL, SET_BANK (chunked reassembly), SET_MASTER, PING/PONG,
EXPORT_BANK.

**Extrakce:** Nová třída `SysExHandler` v `engine/sysex_handler.h/cpp`.
Dostane referenci na Engine (nebo jen na potřebné metody — setMasterGain,
core(), getDspChain...). Engine si jen zavolá `sysex_.handle(data, len)`.

**Přínos:** Vysoký — nejsložitější kus kódu s vlastním state (chunk
reassembly), protokolové změny neovlivní Engine.

**Riziko:** Nízké — čistě jednosměrná závislost (SysExHandler → Engine).

### 2. Batch renderer → `BatchRenderer` free function nebo třída

**Aktuálně:** `renderBatch()` = 121 řádků + `_writeWavStereo16()` helper
= 46 řádků. Nepotřebuje audio device — pracuje přímo s ISynthCore.

**Extrakce:** `engine/batch_renderer.h/cpp`, funkce
`int renderBatch(ISynthCore&, Logger&, json_path, out_dir, sr)`.

**Přínos:** Střední — odstraní závislost na Engine pro offline použití.
Batch render je konceptuálně jiná věc než RT engine.

**Riziko:** Nízké — self-contained, žádný sdílený stav.

### 3. Config manager → `EngineConfig` třída

**Aktuálně:** `loadEngineConfig()` (63 řádků), `saveConfig()` (42 řádků),
`coreConfigValue()`, `setCoreConfigValue()` + 4 member variables
(`config_path_`, `default_core_name_`, `core_config_` map, `log_file_handle_`).

**Extrakce:** `engine/engine_config.h/cpp`. Třída vlastní JSON stav,
poskytuje gettery/settery. Engine drží `EngineConfig cfg_` místo raw map.

**Přínos:** Střední — jasné oddělení persistence od RT enginu.
AppConfig (CLI args) a EngineConfig (JSON stav) mají čistou hierarchii:
AppConfig → EngineConfig → Engine.

**Riziko:** Nízké — čistý datový objekt.

### 4. Master bus → `MasterBus` třída

**Aktuálně:** `applyMasterAndLfo()` (33 řádků) + 6 atomic member variables
(master_gain, pan_l, pan_r, lfo_speed, lfo_depth, lfo_phase) + settery.

**Extrakce:** `engine/master_bus.h` (header-only).
Zapouzdření gain/pan/LFO do jedné třídy s `process(L, R, n)`.

**Přínos:** Nízký-střední — zmenší Engine, ale interface zůstává jednoduchý.

**Riziko:** Nízké — čistě audio processing, žádné vedlejší efekty.

### 5. Audio device → `AudioDevice` wrapper

**Aktuálně:** `start()` (27 řádků), `stop()` (7 řádků), `audioCallback()`
(21 řádků) + `device_`, `running_`, `sample_rate_`, `block_size_`, `buf_l/r_`.

**Extrakce:** `engine/audio_device.h/cpp` — wrapper kolem miniaudio.
Engine poskytuje callback funkci, AudioDevice ji volá.

**Přínos:** Vysoký konceptuálně — umožní Engine používat jako knihovnu
bez vlastního audio device (embedding do JUCE, testování, headless).
Odpovídá IthacaCore vzoru, kde VoiceManager nevlastní audio device.

**Riziko:** Střední — callback vazba (reinterpret_cast na Engine*), lifecycle
managament (kdo vlastní koho).

### 6. MIDI queue — ponechat v Engine

**Aktuálně:** `pushMidiEvt()` (10 řádků), ring buffer (3 vars).

**Doporučení:** Neextrahovat — příliš těsně svázané s processBlock().
Extrakce by přidala složitost bez reálného přínosu.

---

## Srovnání s IthacaCore vzorem

| IthacaCore třída      | ICR ekvivalent        | Stav              |
|-----------------------|-----------------------|-------------------|
| Logger                | Logger (logger.h)     | ✅ Samostatný      |
| SamplerIO             | —                     | Integrováno v cores |
| InstrumentLoader      | —                     | Integrováno v cores |
| Envelope              | Per-core              | ✅ V rámci ISynthCore |
| EnvelopeStaticData    | —                     | Není potřeba (cores mají vlastní) |
| Voice                 | Per-core              | ✅ V rámci ISynthCore |
| VoiceManager          | Engine (monolitický)  | ⚠️ Příliš mnoho zodpovědností |
| WavExporter           | _writeWavStereo16()   | ⚠️ Embedded v Engine |
| runSampler()          | AppConfig.initEngine()| ✅ Extrahováno |

### Hlavní rozdíl

IthacaCore: `VoiceManager` = čistě syntéza + polyphony. Audio device, DSP,
SysEx, config — to jsou externí záležitosti.

ICR: `Engine` = syntéza + audio device + DSP + SysEx + config + batch render.

---

## Doporučený postup refaktoru (od nejvyššího přínosu)

| Priorita | Extrakce           | Řádků | Přínos   | Riziko |
|----------|--------------------|-------|----------|--------|
| 1        | SysExHandler       | ~250  | Vysoký   | Nízké  |
| 2        | BatchRenderer      | ~170  | Střední  | Nízké  |
| 3        | EngineConfig       | ~110  | Střední  | Nízké  |
| 4        | AudioDevice        | ~60   | Vysoký*  | Střední |
| 5        | MasterBus          | ~40   | Nízký    | Nízké  |
| —        | MIDI queue         | ~15   | Žádný    | —      |

*AudioDevice má vysoký konceptuální přínos (knihovní použití), ale středně
složitou implementaci kvůli callback vazbě.

Po těchto extrakcích by Engine měl ~400 řádků a čistě se staral o:
- Multi-core management (core switching, lazy instantiation)
- MIDI queue + processBlock coordination
- Delegace na MasterBus, DspChain, AudioDevice, SysExHandler

To odpovídá roli VoiceManageru z IthacaCore — koordinátor, ne monolith.

---

## Co NEMÁ cenu refaktorovat

- **ISynthCore interface** — už je čistý a modulární
- **DspChain** — už je samostatná třída
- **MidiInput** — už je samostatná třída
- **Jednotlivé cores** — mají vlastní soubory, vlastní testy
- **MIDI queue** — příliš malý, příliš těsně svázaný s processBlock
