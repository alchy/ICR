# ICR — trénink, build a spuštění

Průvodce od WAV banky po zvuk ze syntetizátoru.

---

## Obsah

1. [Předpoklady](#1-předpoklady)
2. [Training pipeline — přehled](#2-training-pipeline--přehled)
3. [Quickstart](#3-quickstart)
4. [Generování sample banky](#4-generování-sample-banky)
5. [Parametrické soubory — formát](#5-parametrické-soubory--formát)
6. [Build přes CMake](#6-build-přes-cmake)
7. [Spuštění](#7-spuštění)
8. [Časté problémy](#8-časté-problémy)

---

## 1. Předpoklady

### Python

```bash
# Simple pipeline (bez NN) — minimální install:
pip install numpy scipy soundfile

# Full pipeline (NN trénink) — přidej torch:
pip install torch

# Nebo vše najednou (z rootu projektu):
pip install -r requirements.txt
```

| Balíček | Simple | Full |
|---------|--------|------|
| `numpy` | ✓ | ✓ |
| `scipy` | ✓ | ✓ |
| `soundfile` | ✓ | ✓ |
| `torch` | — | ✓ |
| `matplotlib` | — | volitelný |

### WAV banka

Soubory pojmenované `m{midi:03d}-vel{vel}-f44.wav` (vel = 0–7, MIDI 21–108).

```
m021-vel0-f44.wav   ← MIDI 21, velocity band 0
m021-vel1-f44.wav
...
m108-vel7-f44.wav
```

---

## 2. Training pipeline — přehled

Dva módy s různým poměrem rychlosti a přesnosti:

```
                   WAV banka
                      │
               ┌──────┴──────────────┐
               │                     │
          SIMPLE (~15 min)       FULL (~60 min)
               │                     │
          extract              extract
          filter               filter
          fit EQ               fit EQ
          export               train NN
               │               finetune NN
               │               hybrid export
               │                     │
               └──────┬──────────────┘
                      │
              soundbanks/*.json   ← loadable by ICRGUI
```

**Simple** — extrahuje fyziku přímo z WAV, bez NN. Rychlé, přesné,
žádné GPU.

**Full** — navíc trénuje surrogate NN (vyhlazení přes klávesnici,
interpolace chybějících not) a fine-tune přes MRSTFT loss. Výsledná
soundbanka je hybrid: reálná data kde existují, NN predikce pro zbytek.

---

## 3. Quickstart

### Simple pipeline

```bash
python training/train_pipeline.py simple \
    --bank  "C:/SoundBanks/IthacaPlayer/ks-grand" \
    --out   soundbanks/params-ks-grand.json
```

Volitelné přepínače:

| Přepínač | Výchozí | Popis |
|----------|---------|-------|
| `--workers N` | CPU count | Paralelní workery (extrakce + EQ) |
| `--skip-eq` | — | Přeskočit spektrální EQ (rychlejší, bez body resonance) |

### Full pipeline

```bash
python training/train_pipeline.py full \
    --bank      "C:/SoundBanks/IthacaPlayer/ks-grand" \
    --out       soundbanks/params-ks-grand.json \
    --epochs    1800 \
    --ft-epochs 200
```

Volitelné přepínače:

| Přepínač | Výchozí | Popis |
|----------|---------|-------|
| `--workers N` | CPU count | Paralelní workery |
| `--epochs N` | 1800 | NN trénink epoch |
| `--ft-epochs N` | 200 | MRSTFT fine-tuning epoch |

### Spuštění po tréninku

```bat
build\bin\Release\ICRGUI.exe --core PianoCore --params soundbanks\params-ks-grand.json
```

---

## 4. Generování sample banky

`generate.py` renderuje WAV soubory ze soundbanky nebo naučeného modelu.
Hodí se pro poslech parametrické varianty, augmentaci dat nebo export
nástrojové banky.

### Celá banka z modelu (NN predikce)

```bash
python training/generate.py \
    --source  training/profile-ks-grand.pt \
    --out-dir generated/ks-grand/
```

Vygeneruje `m021-vel0-f44.wav` … `m108-vel7-f44.wav` (704 souborů).

### Celá banka ze soundbank JSON (reálná fyzika)

```bash
python training/generate.py \
    --source  soundbanks/params-ks-grand.json \
    --out-dir generated/ks-grand-raw/
```

### Jednotlivá nota

```bash
python training/generate.py \
    --source     soundbanks/params-ks-grand.json \
    --out-dir    generated/single/ \
    --midi-range 60-60 \
    --vel-count  1
```

### Rozsah not s vlastní parametrizací syntézy

```bash
python training/generate.py \
    --source       training/profile-ks-grand.pt \
    --out-dir      generated/bright/ \
    --midi-range   48-72 \
    --vel-count    4 \
    --beat-scale   2.0 \
    --noise-level  0.5 \
    --eq-strength  0.8 \
    --duration     4.0
```

### Všechny přepínače generate.py

| Přepínač | Výchozí | Popis |
|----------|---------|-------|
| `--source` | povinný | Cesta k `.pt` modelu nebo params `.json` |
| `--out-dir` | povinný | Výstupní adresář pro WAV soubory |
| `--midi-range` | `21-108` | Rozsah MIDI not (včetně), formát `lo-hi` |
| `--vel-count` | `8` | Počet velocity bands (1–8) |
| `--duration` | `3.0` | Délka každého WAV v sekundách |
| `--sr` | `44100` | Sample rate |
| `--beat-scale` | `1.0` | Škálování beat_hz (1.0 = dle banky) |
| `--noise-level` | `1.0` | Škálování amplitudy šumu |
| `--eq-strength` | `1.0` | Blend spektrálního EQ (0 = bypass) |

### Použití z Pythonu

```python
from training.modules.generator import SampleGenerator
from training.modules.profile_trainer import ProfileTrainer

model = ProfileTrainer().load("training/profile-ks-grand.pt")
gen   = SampleGenerator()

# Celá banka
gen.generate_bank(model, "generated/ks-grand/", midi_range=(21, 108), vel_count=8)

# Jedna nota
wav = gen.generate_note(model, midi=60, vel=3, beat_scale=1.5)
# wav.shape == (N, 2), float32 stereo
```

---

## 5. Parametrické soubory — formát

Soundbanka je JSON soubor kompatibilní s `PianoCore::load()`:

```json
{
  "format":     "piano-core-v1",
  "sr":         44100,
  "target_rms": 0.06,
  "vel_gamma":  0.7,
  "k_max":      60,
  "n_notes":    704,
  "notes": {
    "m060_vel3": {
      "midi":       60,
      "vel":        3,
      "f0_hz":      261.63,
      "K_valid":    55,
      "phi_diff":   1.234,
      "attack_tau": 0.008,
      "A_noise":    0.42,
      "rms_gain":   0.06,
      "partials": [
        { "f_hz": 261.63, "A0": 13.7, "tau1": 0.41,
          "tau2": 3.73, "a1": 0.82, "beat_hz": 0.17, "phi": 0.0 }
      ],
      "eq_biquads": [
        { "b": [1.02, -1.94, 0.93], "a": [-1.89, 0.90] }
      ]
    }
  }
}
```

Klíčové hodnoty:

| Klíč | Popis |
|------|-------|
| `f0_hz` | Základní frekvence noty (Hz) |
| `K_valid` | Počet platných parciálů |
| `A0` | Amplituda parciálu (normalizovaná) |
| `tau1 / tau2` | Rychlá / pomalá složka bi-exponenciální obálky (s) |
| `a1` | Míšení obálek: `env = a1·e^(-t/τ1) + (1-a1)·e^(-t/τ2)` |
| `beat_hz` | Frekvence beatingu mezi strunami (Hz) |
| `eq_biquads` | 5 biquad sekcí spektrálního EQ (min-phase IIR) |

Podrobná dokumentace modulů → [`docs/TRAINING_MODULES.md`](TRAINING_MODULES.md).

---

## 6. Build přes CMake

### Prerekvizity

| Nástroj | Verze |
|---------|-------|
| Visual Studio 2022 | 17.x (MSVC toolchain + x64) |
| CMake | ≥ 3.16 |
| Git | libovolná (FetchContent stahuje GLFW + ImGui) |

### Konfigurace a build

```bat
cmake -B build -G "Visual Studio 17 2022" -A x64
cmake --build build --config Release
```

FetchContent při prvním `cmake -B` automaticky stáhne GLFW 3.4 a Dear ImGui v1.91.9.
Vyžaduje internet pouze při první konfiguraci.

### Build targety

| Target | Binárka | Popis |
|--------|---------|-------|
| `ICR` | `build/bin/Release/ICR.exe` | Headless CLI, real-time MIDI |
| `ICRGUI` | `build/bin/Release/ICRGUI.exe` | Dear ImGui frontend |

```bat
# Jen GUI
cmake --build build --config Release --target ICRGUI

# Debug build
cmake --build build --config Debug
```

### Kompilační volby (automatické)

| Volba | Platforma | Efekt |
|-------|-----------|-------|
| `/arch:AVX2` | MSVC x86_64 | AVX2 + FMA vektorizace |
| `-mavx2 -mfma` | GCC/Clang x86_64 | totéž |
| `/O2 /DNDEBUG` | MSVC Release | optimalizace |

### Alternativní toolchainy

```bash
# MinGW-w64
cmake -B build-mingw -G "MinGW Makefiles" -DCMAKE_BUILD_TYPE=Release
cmake --build build-mingw
```

---

## 7. Spuštění

### GUI (doporučeno)

```bat
build\bin\Release\ICRGUI.exe --core PianoCore --params soundbanks\params-piano-soundbank.json
```

> Bundled soundbank `soundbanks/params-piano-soundbank.json` je součástí repozitáře.
> Byl fitován z KS Grand nahrávek, obsahuje per-note spektrální EQ.

**CLI argumenty:**

| Argument | Popis |
|----------|-------|
| `--core PianoCore` | Syntézní core (PianoCore nebo SineCore) |
| `--params <cesta>` | Cesta k soundbank JSON |

**Runtime parametry GUI (nastavitelné za běhu):**

| Parametr | Skupina | Rozsah | Výchozí | Popis |
|----------|---------|--------|---------|-------|
| `beat_scale` | Timbre | 0–4 × | 1.0 | Škáluje beat_hz všech parciálů |
| `noise_level` | Timbre | 0–4 × | 1.0 | Škáluje amplitudu šumu |
| `eq_strength` | Timbre | 0–1 | 1.0 | Blend spektrálního EQ (0 = bypass) |
| `pan_spread` | Stereo | 0–π | 0.55 | Rozevření strun v panoramě |
| `keyboard_spread` | Stereo | 0–π | 0.60 | Šířka panoramy přes klávesnici |
| `stereo_decorr` | Stereo | 0–2 × | 1.0 | Síla Schroederova dekorélátoru |

**Vizualizační panel (pravý sloupec):**

Při každém `noteOn` GUI zobrazí detail pro danou (midi, vel) kombinaci:
frekvenční odezva EQ biquad kaskády (5 sekcí × 32 log-spaced frekvencí,
30 Hz–18 kHz), počet parciálů, šumová obálka.

### Headless CLI

```bat
build\bin\Release\ICR.exe --core PianoCore --params soundbanks\params-piano-soundbank.json [midi_port]
```

`midi_port` — index MIDI vstupu (výchozí: 0). Dostupné porty jsou vypsány při startu.

---

## 8. Časté problémy

### NaN loss během MRSTFT fine-tuningu

`B_net` zdrift → velké `log_B` → `B → ∞` → `f_hz → ∞` → `cos(∞) = NaN`.
`B` je interně clampnuté, ale při extrémních váhách se může prosadit.
Řešení: začni od checkpointu před explozí, nebo spusť full pipeline znovu.

### CMake ukazuje na původní adresář po kopírování projektu

```bat
del build\CMakeCache.txt
cmake -B build -G "Visual Studio 17 2022" -A x64
```

### Linker zamkne .exe (GUI spuštěné při buildu)

```bat
taskkill /F /IM ICRGUI.exe
cmake --build build --config Release
```

### OOM nebo příliš pomalý MRSTFT trénink

Sniž počet parciálů v `MRSTFTFinetuner` nebo zkrať dobu syntézy.
Hlavní paměťová zátěž: K×N tenzor (K=60 parciálů, N=132 300 vzorků pro 3 s @ 44,1 kHz ≈ 128 MB/nota).

### UnicodeEncodeError na Windows (cp1252)

Všechny skripty volají `sys.stdout.reconfigure(encoding='utf-8')` automaticky.
Pokud chybí, přidej na začátek:

```python
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
```
