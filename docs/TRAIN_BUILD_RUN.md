# ICR — trénink, build a spuštění

Průvodce od WAV banky po zvuk ze syntetizátoru.
Detailní popis modulů → [`docs/TRAINING_MODULES.md`](TRAINING_MODULES.md).
Přehled trénovacích workflows → [`docs/TRAINING_WORKFLOWS.md`](TRAINING_WORKFLOWS.md).
Otevřené priority a hotové kroky → [`docs/ROADMAP.md`](ROADMAP.md).

---

## Obsah

1. [Kde jsme — rychlá orientace](#1-kde-jsme--rychlá-orientace)
2. [Předpoklady](#2-předpoklady)
3. [Dostupné banky a co jsme z nich získali](#3-dostupné-banky-a-co-jsme-z-nich-získali)
4. [Cesta A: Instrument DNA (doporučená pro piano)](#4-cesta-a-instrument-dna-doporučená-pro-piano)
5. [Cesta B: NN pipeline](#5-cesta-b-nn-pipeline)
6. [Diagnostika kvality extrakce](#6-diagnostika-kvality-extrakce)
7. [Generování sample banky (WAV soubory)](#7-generování-sample-banky-wav-soubory)
8. [Parametrické soubory — formát](#8-parametrické-soubory--formát)
9. [Build přes CMake](#9-build-přes-cmake)
10. [Spuštění](#10-spuštění)
11. [Časté problémy](#11-časté-problémy)

---

## 1. Kde jsme — rychlá orientace

**Projekt ICR** extrahuje fyzikální parametry (inharmonicita, decay, beating) z WAV nahrávek
piana nebo Rhodesu a generuje z nich soundbank JSON, který PianoCore syntetizátor přehraje.

**Aktuální stav (duben 2026):**

- Máme dvě kvalitní piano banky: `pl-grand` (88 MIDI × 8 vel, 29s/nota) a `pl-upright`
  (51 MIDI × 8 vel, 18.5s/nota).
- Nový extraktor (v2): bi-exp fit 59 % → 77 % na pl-grand. Každý workflow ho používá automaticky.
- `soundbanks/pl-grand-dna.json` vygenerována (704 not, 5.6 MB). **Čeká na poslechové ověření.**
- NN pipeline je funkční, ale tréninkový loss je MSE na číslech, ne na zvuku — strukturální limit.

**Doporučená cesta pro piano:** Instrument DNA (sekce 4) — bez NN, fyzikální zákony + GP residuals.
**Pro Rhodes:** NN pipeline zatím nelze plně použít (ICR renderuje 3s WAVy → bi-exp vždy selže).

---

## 2. Předpoklady

### Python

```bash
# Instrument DNA + diagnostika (minimální):
pip install numpy scipy soundfile scikit-learn

# NN pipeline — přidej torch:
pip install torch

# Nebo vše najednou:
pip install -r requirements.txt
```

| Balíček | DNA | NN pipeline |
|---------|-----|-------------|
| `numpy` | ✓ | ✓ |
| `scipy` | ✓ | ✓ |
| `soundfile` | ✓ | ✓ |
| `scikit-learn` | ✓ (GP residuals) | — |
| `torch` | — | ✓ |

### WAV banka

Soubory pojmenované `m{midi:03d}-vel{vel}-f{sr}.wav` (vel = 0–7, MIDI 21–108).
Pipeline preferuje 48 kHz varianty (`f48`), automaticky fallbackuje na `f44`.

```
m021-vel0-f48.wav   ← MIDI 21, velocity band 0, 48 kHz (preferováno)
...
m108-vel7-f48.wav
```

Monofonní soubory jsou plně podporovány.

---

## 3. Dostupné banky a co jsme z nich získali

| Banka | Cesta | MIDI | Délka/nota | Stav extrakce |
|---|---|---|---|---|
| `pl-grand` | `C:/SoundBanks/IthacaPlayer/pl-grand` | 21–108 | 29.4s | bi-exp 77 %, beat 100 %, B_ok 92 % |
| `pl-upright` | `C:/SoundBanks/IthacaPlayer/pl-upright` | 21–71 | 18.5s | extrakce dosud neproběhla |

Extrahovaná data pl-grand: `generated/pl-grand-extracted-v2.json` (z ParamExtractor přímo).
Soundbank z InstrumentDNA: `soundbanks/pl-grand-dna.json` — čeká na poslech.

**Proč jsou tyto banky lepší než původní Rhodes:**
Rhodes banky mají tau2 ~30s, ale ICR round-trip renderuje 3s WAVy → tau2_max=2.7s →
bi-exp extrakce vždy selže → 87 % not má degenerované parametry → NN se učí šum.
Pianové banky (pl-grand, pl-upright) mají záznamy 18–29s → tau2 lze spolehlivě extrahovat.

---

## 4. Cesta A: Instrument DNA (doporučená pro piano)

Generuje soundbank bez NN — z fyzikálních zákonů fitovaných na dobrých anchor notách.
Funguje dobře s 10–30 anchor notami, nepotřebuje celých 704 not s dobrou extrakcí.
Detailní popis architektury → [`docs/INSTRUMENT_DNA.md`](INSTRUMENT_DNA.md).

### Krok 1 — Extrakce

```bash
python run-training.py simple \
    --bank "C:/SoundBanks/IthacaPlayer/pl-grand"
# → soundbanks/pl-grand-simple.json  (obsahuje spectral_eq, prošlo outlier filterem)
```

Pro pl-grand alternativně použij hotový výstup (bez EQ, ale s plnou extrakcí):
```
generated/pl-grand-extracted-v2.json
```

### Krok 2 — Diagnostika extrakce (volitelné, doporučené)

```bash
python tools/analyze_extraction.py soundbanks/pl-grand-simple.json
```

Výstup ukáže bi-exp%, beat%, B_ok% po registrech a per-MIDI heatmap.
Cíl: bi-exp > 70 %, beat = 100 %. Pokud je nižší → podívat se na heatmap,
identifikovat problémové MIDI pozice.

### Krok 3 — Volitelně: anotovat anchor noty

```bash
python tools/anchor_helper.py
```

Textový REPL pro ruční úpravu quality score (0.0–1.0). Bez anotace InstrumentDNA
použije auto-quality z extrakčních příznaků (beat=0 → 0.0, a1=1.0 → 0.0, atd.).
Pro pl-grand auto-quality funguje dobře — ruční anotace není nutná pro první běh.

### Krok 4 — Generovat soundbank

```bash
python training/modules/instrument_dna.py \
    soundbanks/pl-grand-simple.json \
    soundbanks/pl-grand-dna.json
```

S vlastními anchor anotacemi:
```bash
python training/modules/instrument_dna.py \
    soundbanks/pl-grand-simple.json \
    soundbanks/pl-grand-dna.json \
    --anchors anchors/pl-grand.json
```

### Krok 5 — Poslech v syntetizéru

```bat
build\bin\Release\ICRGUI.exe --core PianoCore --params soundbanks\pl-grand-dna.json
```

Zkontrolovat: velocity přechody (monotonie A0↑, tau1↓), přechody mezi registry,
charakter beatingu. Porovnat s původními bankami.

### Shrnutí Cesty A

```
WAV banka
  → run-training.py simple          extrakce + EQ + outlier filter
  → analyze_extraction.py           ověření kvality (bi-exp%, beat%)
  → [anchor_helper.py]              volitelná ruční anotace
  → instrument_dna.py               fyzikální fit + GP + velocity enforcer
  → soundbanks/pl-grand-dna.json    hotová soundbanka
  → ICRGUI                          poslech
```

### Naměřené výsledky (pl-grand, 2026-04-04)

- Anchor not: 520/704 (q≥0.5)
- B: 1.24e-4 (MIDI 108) → 5.9e-4 (MIDI 21)
- tau1: 0.028s (MIDI 108, vel7) → 1.45s (MIDI 21, vel0)
- beat_hz: 0.09 → 0.45 Hz

---

## 5. Cesta B: NN pipeline

Trénuje surrogate NN na extrahovaných datech. Hodí se pro nástroje s dostatečnou
extrakcí nebo pro záznamy, kde chceme interpolaci přes celou klaviaturu s vyhlazením.

**Strukturální limit:** Tréninkový loss je MSE na parametrech, ne na zvuku.
ICR-MRSTFT řídí jen early stop, gradient teče přes MSE. Každé zlepšení naráží
na tento strop. Viz [`docs/ROADMAP.md`](ROADMAP.md) — P3.

### Doporučený workflow: `spl-icrtarget-nn-icreval`

```bash
python run-training.py spl-icrtarget-nn-icreval \
    --bank "C:/SoundBanks/IthacaPlayer/pl-grand" \
    --icr-exe build/bin/Release/ICR.exe
# → soundbanks/pl-grand-spl-icrtarget-nn-icreval-hybrid.json
# → soundbanks/pl-grand-spl-icrtarget-nn-icreval-nn.json
```

Proč tento workflow: spline-smooth targety + ICR round-trip korekce. NN konverguje
k tomu, co ICR skutečně produkuje, ne k tomu, co extraktor naměřil z reálného klavíru.

### Přehled všech NN workflows

| Workflow | CLI příkaz | Targety NN | Round-trip | Kdy použít |
|---|---|---|---|---|
| `raw-nn-icreval` | `run-training.py raw-nn-icreval` | raw extrakce | Ne | Rychlý baseline |
| `spl-nn-icreval` | `run-training.py spl-nn-icreval` | spline-smooth | Ne | Produkční banka |
| `spl-ext-nn-icreval` | `run-training.py spl-ext-nn-icreval` | smooth + plné parciály | Ne | Piano s bohatým obsahem |
| `spl-icrtarget-nn-icreval` | `run-training.py spl-icrtarget-nn-icreval` | smooth + RT korekce | **Ano** | Nejpřesnější výstup |
| `spl-ext-icrtarget-nn-icreval` | `run-training.py spl-ext-icrtarget-nn-icreval` | smooth + plné parciály + RT | **Ano** | Maximální přesnost |

Detailní popis každého workflow → [`docs/TRAINING_WORKFLOWS.md`](TRAINING_WORKFLOWS.md).

### Výstupní soubory NN pipeline

```
soundbanks/{banka}-{workflow}-hybrid.json   ← reálná data kde existují + NN pro zbytek
soundbanks/{banka}-{workflow}-nn.json       ← čistý NN výstup pro všech 704 not

generated/{banka}-{workflow}/
  *-pre-smooth.json                         ← raw extrakce (intermediate)
  *-pre-smooth-spline.json                  ← spline-smoothed targety
  *-pre-smooth-rt.json                      ← raw round-trip targety (jen icrtarget)
  *-pre-smooth-rt-spl.json                  ← smooth round-trip targety (jen icrtarget)
```

Logy každého běhu: `training-logs/run-{cmd}-{banka}-{timestamp}.log`

---

## 6. Diagnostika kvality extrakce

Po extrakci (nebo před spuštěním InstrumentDNA) je vhodné ověřit výsledky:

```bash
# Analýza jedné banky
python tools/analyze_extraction.py soundbanks/pl-grand-simple.json

# Srovnání dvou bank
python tools/analyze_extraction.py soundbanks/pl-grand-simple.json \
    --compare generated/pl-grand-extracted-v2.json
```

**Co sledovat:**

| Metrika | Cíl | Problém pokud |
|---|---|---|
| bi-exp% | > 70 % | Nízké → kratší záznamy / beat kontaminace |
| beat% | 100 % | Nízké → beat detection selhává |
| B_ok% | > 85 % | Nízké → B fit clampnutý nebo šum |

**Extractor v2 (aktuální):** Multi-start bi-exp fit — 4 diverse inicializace `(a1, tau1, tau2)`,
`tau1_max=20s` (bylo 5s), relaxed criterion `tau2/tau1>1.3` (bylo 3.0).
Výsledek na pl-grand: bi-exp **59 % → 77 %**. Každý workflow (`simple`, NN pipeline)
používá tento extraktor automaticky — není třeba nic nastavovat.

**Strukturální limit extrakce:**
ICR round-trip renderuje záznamy délky `duration_s=3.0s` → `tau2_max=2.7s`.
Pro Rhodes s tau2~30s bi-exp vždy selže. Řešení: prodloužit render duration (viz ROADMAP P2)
nebo použít Instrument DNA (nevyžaduje dobrou bi-exp extrakci z round-tripu).

---

## 7. Generování sample banky (WAV soubory)

`run-generate.py` renderuje WAV soubory ze soundbanky nebo NN modelu.
Hodí se pro poslech parametrické varianty nebo augmentaci dat.

```bash
# Celá banka z DNA soundbanku
python run-generate.py \
    --source soundbanks/pl-grand-dna.json \
    --full-bank
# → generated/pl-grand-dna/m021-vel0-f48.wav  ...

# Celá banka z NN modelu
python run-generate.py \
    --source training/profile-pl-grand.pt \
    --full-bank

# Jedna nota
python run-generate.py \
    --source soundbanks/pl-grand-dna.json \
    --midi-note 60 --velocity 4

# Rozsah not s vlastní parametrizací
python run-generate.py \
    --source soundbanks/pl-grand-dna.json \
    --midi-range 48-72 --vel-count 4 \
    --beat-scale 2.0 --noise-level 0.5 --duration 4.0
```

### Všechny přepínače

| Přepínač | Výchozí | Popis |
|----------|---------|-------|
| `--source` | povinný | Cesta k `.pt` modelu nebo soundbank `.json` |
| `--out-dir` | auto | Výstupní adresář (default: `generated/{název_souboru}/`) |
| `--full-bank` | — | Celá banka: MIDI 21–108, všechny velocity vrstvy |
| `--midi-note N` | — | Jednotlivá nota (vyžaduje `--velocity`) |
| `--midi-range LO-HI` | — | Rozsah not, např. `48-72` |
| `--vel-count` | `8` | Počet velocity bands |
| `--velocity` | — | Velocity index 0–7 (pro `--midi-note`) |
| `--freq` | `48` | Sample rate: `44` = 44100 Hz, `48` = 48000 Hz |
| `--duration` | `3.0` | Délka každého WAV v sekundách |
| `--beat-scale` | `1.0` | Škálování beat_hz (1.0 = dle banky) |
| `--noise-level` | `1.0` | Škálování amplitudy šumu |
| `--eq-strength` | `1.0` | Blend spektrálního EQ (0 = bypass) |

---

## 8. Parametrické soubory — formát

Soundbanka je JSON soubor kompatibilní s `PianoCore::load()`.
Kompletní reference klíčů → [`docs/JSON_SCHEMA.md`](JSON_SCHEMA.md).

```json
{
  "sr":         48000,
  "target_rms": 0.06,
  "k_max":      60,
  "n_notes":    704,
  "notes": {
    "m060_vel4": {
      "midi":       60,
      "vel":        4,
      "f0_hz":      261.63,
      "B":          0.00041,
      "K_valid":    18,
      "phi_diff":   1.234,
      "attack_tau": 0.008,
      "A_noise":    0.42,
      "rms_gain":   0.06,
      "partials": [
        { "k": 1, "f_hz": 261.63, "A0": 13.7, "tau1": 0.41,
          "tau2": 3.73, "a1": 0.82, "beat_hz": 0.17, "phi": 0.0 }
      ],
      "eq_biquads": [
        { "b": [1.02, -1.94, 0.93], "a": [-1.89, 0.90] }
      ]
    }
  }
}
```

| Klíč | Popis |
|------|-------|
| `B` | Inharmonicita — `f_k = k·f0·√(1+B·k²)` |
| `tau1 / tau2` | Rychlá / pomalá složka bi-exp obálky (s) |
| `a1` | Míšení: `env = a1·e^(-t/τ1) + (1-a1)·e^(-t/τ2)` |
| `beat_hz` | Frekvence beatingu mezi strunami (Hz) |
| `eq_biquads` | 5 biquad sekcí spektrálního EQ (min-phase IIR) |

---

## 9. Build přes CMake

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

### Build targety

| Target | Binárka | Popis |
|--------|---------|-------|
| `ICR` | `build/bin/Release/ICR.exe` | Headless CLI, real-time MIDI |
| `ICRGUI` | `build/bin/Release/ICRGUI.exe` | Dear ImGui frontend |

```bat
cmake --build build --config Release --target ICRGUI
cmake --build build --config Debug
```

---

## 10. Spuštění

### GUI (doporučeno)

```bat
build\bin\Release\ICRGUI.exe --core PianoCore --params soundbanks\pl-grand-dna.json
```

**CLI argumenty:**

| Argument | Popis |
|----------|-------|
| `--core PianoCore` | Syntézní core (PianoCore nebo SineCore) |
| `--params <cesta>` | Cesta k soundbank JSON |

**Runtime parametry (nastavitelné za běhu v GUI):**

| Parametr | Rozsah | Výchozí | Popis |
|----------|--------|---------|-------|
| `beat_scale` | 0–4 × | 1.0 | Škáluje beat_hz všech parciálů |
| `noise_level` | 0–4 × | 1.0 | Škáluje amplitudu šumu |
| `eq_strength` | 0–1 | 1.0 | Blend spektrálního EQ (0 = bypass) |
| `pan_spread` | 0–π | 0.55 | Rozevření strun v panoramě |
| `keyboard_spread` | 0–π | 0.60 | Šířka panoramy přes klávesnici |

### Headless CLI

```bat
build\bin\Release\ICR.exe --core PianoCore --params soundbanks\pl-grand-dna.json [midi_port]
```

---

## 11. Časté problémy

### NaN loss během NN tréninku

`B_net` zdrift → velké `log_B` → `B → ∞` → `f_hz → ∞` → NaN.
Řešení: začni od checkpointu před explozí, nebo spusť pipeline znovu.

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

Sniž počet parciálů nebo zkrať dobu syntézy.
Hlavní zátěž: K×N tenzor (K=60, N=132 300 vzorků pro 3s @ 44.1 kHz ≈ 128 MB/nota).

### UnicodeEncodeError na Windows

Všechny skripty volají `sys.stdout.reconfigure(encoding='utf-8')` automaticky.
