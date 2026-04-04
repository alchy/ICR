# Instrument DNA — Concept & Architecture

## 1. Motivation

### Problém

Soundbank je sada parametrů pro 88 MIDI not × 8 velocity vrstev = 704 záznamů. Parametry jsou extrahovány z nahrávek přes detekci harmonických, fitování obálek a analýzu rytů (beating). Detekce **selže v drtivé většině případů**: v testované bance `vv-rhodes-raw-nn-icreval-hybrid.json` má **87 % měřených not** zcela degenerované parametry:

- `beat_hz = 0.0` — detektor rytů selhal, struna "neskáče"
- `a1 = 1.0` + `tau1 = tau2` — bi-exponenciální fit zdegeneroval na jednoduchou exponenciálu
- `B ≈ 0` nebo `B = 1.0` — inharmonicita mimo fyzikální rozsah

Výsledkem je zvuk, který postrádá charakteristické vlastnosti nástroje: chorus/beating efekt, přirozený dlouhý dozvuk (tau2), živou barvu.

### Proč dosavadní přístupy selhaly

**Spline interpolace:** Křivka byla váhově ukotvena na "dobrých" notách, ale nenulová váha špatných not ji kontaminovala. Navíc interpolace probíhala v surovém parametrickém prostoru — tau1 není lineární v MIDI, ale `log(tau1)` vs `log(f0)` ano. Spline mohl poškodit i původně dobré noty.

**Neuronová síť (stávající):** Síť se učila absolutní hodnoty parametrů ze všech not. Se 87 % kontaminovanými trénovacími daty se síť naučila průměr šumu. Neměla žádný mechanismus pro rozlišení "dobrá nota" vs "špatná nota".

---

## 2. Klíčová myšlenka: Instrument DNA

Charakter nástroje není v absolutních hodnotách parametrů, ale ve **fyzikálních zákonitostech a vztazích**, které platí napříč celou klaviaturou. Tyto zákonitosti jsou dány fyzikou — a dobrá nota je taková, která je jim věrná.

```
Špatný přístup:  naučit se f(midi, vel) → tau1_absolutní_hodnota
Správný přístup: naučit se physics_law(midi) + instrument_residual(jen z dobrých not)
```

Tento přístup odděluje:
1. **Fyzikální prior** — co platí pro všechny nástroje daného typu
2. **Instrument DNA** — co je specifické pro tento konkrétní nástroj
3. **Velocity konzistenci** — monotonní chování přes velocity vrstvy

---

## 3. Fyzikální popis a literatura

### 3.1 Akustické piano

**Inharmonicita B:**
```
f_k = k · f₀ · √(1 + B·k²)
```
B závisí na fyzice struny (Young's modulus E, průměr d, délka L, napětí T):
```
B = π²Ed⁴ / (64TL²)
```
Protože L ∝ 1/f₀ pro piano, platí **B ∝ f₀²**, tedy:
```
log(B) ≈ a + b·midi   (lineární v log-prostoru)
```
Reference: Fletcher & Rossing — *Physics of Musical Instruments* (2nd ed., 1998), kap. 1.4; Conklin (1996) *J. Acoust. Soc. Am.* 99(6).

**Doby dozvuku (tau1, tau2):**
Energetické ztráty struny mají tři složky: vzduchové tlumení (∝ f²), vnitřní tření (frekvence-nezávislé), coupling na resonanční desku (dominantní v bassu). Výsledek:
- tau klesá s harmonickým řádem k (vyšší harmonické rychleji ztrácejí energii)
- tau klesá se silou úderu (velocity) — silnější úder = kratší kontaktní čas = rychlejší počáteční pokles
- Bi-exponenciální model `A(t) = a1·e^(-t/τ1) + (1-a1)·e^(-t/τ2)` odpovídá dvěma módům: rychlý (τ1, coupling na desku) a pomalý (τ2, reziduální vibrace struny)

Reference: Giordano & Jiang (2004) *J. Acoust. Soc. Am.* 115(2); Rocchesso & Scalcon (1999) *IEEE Trans. Speech Audio Process.*

**Beat frekvence:**
Moderní piano má 2–3 struny na notu. Záměrné drobné rozladění mezi strunami vytváří amplitudovou modulaci (beating). Pro piano platí přibližně:
```
log(beat_hz) ≈ a + b·log(f₀)   (power law)
```
Reference: Weinreich (1977) *J. Acoust. Soc. Am.* 62(6) — coupled piano strings.

### 3.2 Rhodes (elektromechanické piano)

Rhodes nepoužívá struny, ale **tines** (kovové tyčky/pružiny) s elektromagnetickým snímačem. Fyzika je odlišná:
- Inharmonicita pochází z ohybové tuhosti tinu (jiný zákon než pro struny), ale log(B) vs midi zůstává přibližně lineární
- Doby dozvuku jsou výrazně delší než u akustického piana (chybí coupling na rezonanční desku)
- Beating vzniká ze dvou tinů na notu — charakteristický "chorus" zvuk Rhodesu
- Spektrální profil: charakteristicky silná 1.–3. harmonická, rychlý pokles výše

Reference: Välimäki et al. (2010) *Proc. IEEE* — Physical Modelling of Musical Instruments.

### 3.3 Implikace pro model

Fyzikální zákony jsou **silné prio** informace — bez trénovacích dat víme:
- B(midi) je hladká monotónní funkce
- tau1(midi) je hladká monotónní funkce
- tau klesá s k (přibližně exponenciálně)
- beat_hz je hladká funkce f₀

Dobrá nota tyto zákonitosti respektuje. Špatná nota je od nich daleko.

---

## 4. Architektura Instrument DNA

### 4.1 Přehled

```
┌─────────────────────────────────────────────────────────────────┐
│  VSTUP: anchor notes (uživatelem označené, quality 0.0–1.0)     │
│  + volitelně: více bank stejného typu nástroje                  │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  VRSTVA 1: AUTO-SCREENING                                        │
│  Detekuje objektivně degenerované noty:                         │
│  • beat_hz = 0 (detektor selhal)                                │
│  • a1 = 1.0 + tau1 ≈ tau2 (bi-exp zdegeneroval)                │
│  • B < 1e-15 nebo B > 0.1 (mimo fyzikální rozsah)              │
│  • A0 outlier (> 3σ od sousedních not)                          │
│  Výstup: auto_quality score ∈ {0.0, 0.5, 1.0}                  │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  VRSTVA 2: FYZIKÁLNÍ ZÁKONY  (váhovaný fit, jen anchor noty)    │
│                                                                 │
│  Každý parametr fitován v physics-aligned prostoru:             │
│  • log(B) = a + b·midi          → inharmonicity law             │
│  • log(τ1_k1) = a(v) + b·log(f₀) → decay law (per velocity)   │
│  • log(τ_k/τ_1) = -α·k          → harmonic decay ratio         │
│  • log(beat_hz) = a + b·log(f₀)  → detuning law                │
│  • log(A0_k/A0_1) = f(k, midi)   → spectral shape              │
│  • a1(midi, v, k) = sigmoid(...)  → bi-exp balance              │
│                                                                 │
│  Fit: weighted least squares, weight = anchor quality score     │
│  Špatné noty (quality≈0) mají nulovou váhu → žádná kontaminace │
└────────────────────────┬────────────────────────────────────────┘
                         │ physics_prediction(midi, vel, k)
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  VRSTVA 3: INSTRUMENT RESIDUALS  (Gaussian Process)             │
│                                                                 │
│  residual = log(measured) − log(physics_prediction)            │
│  Trénován POUZE na anchor notách (quality > threshold)          │
│                                                                 │
│  GP kernely:                                                    │
│  • B: RBF(length_scale=12 MIDI) — hladká globální křivka       │
│  • τ1: RBF(l=15 MIDI) × RBF(l=3 vel) — 2D povrch              │
│  • beat_hz: RBF(l=10 MIDI) — regionální charakter              │
│  • A0_shape: RBF(l=12 MIDI) × RBF(l=2 k) — spektrální envelope│
│                                                                 │
│  GP dává: predikci + uncertainty → víme, kde jsme méně jistí   │
└────────────────────────┬────────────────────────────────────────┘
                         │ physics + residual
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  VRSTVA 4: VELOCITY CONSISTENCY ENFORCER                        │
│                                                                 │
│  Pro každou MIDI notu, across vel 0..7:                         │
│  • A0_k(vel) — monotónně rostoucí (isotonic regression)         │
│  • τ1_k(vel) — monotónně klesající (více sily = kratší sustain) │
│  • a1(vel) — hladce přechází (rolling-window smooth)            │
│  • A_noise(vel) — monotónně rostoucí                            │
│                                                                 │
│  Řeší: "vel4 má vyšší A0 než vel6" → výsledek je monotonní     │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
              Final soundbank JSON (704 not, konzistentní)
```

### 4.2 Proč GP místo NN?

| Vlastnost | NN (stávající) | GP (nový) |
|---|---|---|
| Potřebná data | 100+ příkladů | 10–30 anchor not |
| Overfitting | Ano (bez regularizace) | Přirozená smoothness |
| Uncertainty | Ne | Ano — víme kde nevíme |
| Fyzikální prior | Implicitní | Explicitní (residuals jsou malé) |
| Interpretovatelnost | Nízká | Vysoká |
| Kontaminace špatnými daty | Ano | Ne (anchor only) |

---

## 5. Multi-bank přístup (latentní prostor)

Pokud máme více bank **stejného typu nástroje** (např. tři různé nahrávky Rhodesu), každá s různou sadou dobrých not, lze kombinovat anchor noty napříč bankami:

```
Bank A: dobré noty {36, 48, 72, 84}
Bank B: dobré noty {42, 60, 78}
Bank C: dobré noty {33, 51, 69, 90}
```

Výhoda: pokryjeme celý rozsah klaviatury i s různými záznamy. Fyzikální zákonitosti jsou sdílené — residuals z různých bank by měly být konzistentní pokud nahrávky jsou stejný nástroj (nebo nástroj stejného typu/charakteru).

### Formát multi-bank anchor souboru

```json
{
  "instrument_type": "rhodes",
  "description": "Vintage Rhodes 73 — pooled from 3 sessions",
  "banks": [
    {
      "bank": "soundbanks/session-a.json",
      "notes": {
        "m036_vel5": 1.0,
        "m036_vel6": 1.0,
        "m048_vel4": 0.8
      }
    },
    {
      "bank": "soundbanks/session-b.json",
      "notes": {
        "m060_vel3": 1.0,
        "m072_vel5": 0.9
      }
    }
  ]
}
```

### Latentní prostor (pokročilá varianta)

Pro případ kdy máme anchor noty z více různých bank stejného charakteru (typ nástroje, éra, stavitel), lze použít sdílený **latentní vektor nástroje** z:

```
z = Encoder(anchor_notes)   [malý VAE nebo PCA na residuals]
```

Latentní vektor `z` zachycuje "co je specifické pro tento Rhodes" — napříč bankami by měl být podobný pro stejný nástroj. Generátor pak produkuje:

```
params = Decoder(z, midi, vel, k)
```

Toto umožňuje v budoucnu **interpolaci mezi nástroji** (`z = α·z_rhodes + (1-α)·z_piano`) nebo transfer vlastností.

**Kritická poznámka:** Tuto variantu doporučuji jako krok 2, až bude ověřena základní fyzikálně-GP vrstva. Latentní prostor vyžaduje více anchor not (alespoň 20–30 kvalitních pro stabilní fit).

---

## 6. Quality score (0.0–1.0)

Místo binárního označení dobrá/špatná nota používáme spojitou škálu:

| Score | Interpretace |
|---|---|
| 1.0 | Výborná nota — přesně odpovídá charakteru nástroje |
| 0.8–0.9 | Dobrá nota — drobné odchylky ale celkově věrná |
| 0.5–0.7 | Průměrná — lze použít ale nižší váha |
| 0.2–0.4 | Sporná — pravděpodobně špatná extrakce |
| 0.0 | Ignorovat — zjevně degenerovaná |

Quality score odpovídá **váze** ve fitness fyzikálních zákonů (WLS) a rozhoduje, zda nota vstupuje do GP residuals (práh obvykle 0.5).

**Auto-screening** přednastaví quality score automaticky:
- `beat=0 + a1=1.0` → 0.0
- `B < 1e-15 nebo B > 0.1` → 0.0
- `A0` outlier (> 3σ) → 0.2
- Ostatní měřené → 0.5 (k ruční revizi)
- NN-interpolované → 0.3 (mohou být zdrojem ale ne primárním)

---

## 7. Implementační plán

### Fáze 1 — Základní funkčnost ✓ IMPLEMENTOVÁNO
- [x] `tools/anchor_helper.py` — textový helper pro anotaci anchor not
- [x] `training/modules/instrument_dna.py` — PhysicsLaws + GP residuals + VelocityEnforcer
- [x] Auto-screener integrován přímo v `InstrumentDNA._auto_quality()` (samostatný modul nebyl nutný)
- [x] `tools/analyze_extraction.py` — diagnostický nástroj kvality extrakce

### Fáze 2 — Integrace (pending)
- [ ] `training/pipeline_dna.py` — nová pipeline větev (DNA-based generation)
- [ ] Integrace do `SoundbankExporter` — nový export mód `from_dna`

### Fáze 3 — Multi-bank (volitelné)
- [x] Multi-bank anchor formát (podporováno v `anchor_helper.py`)
- [ ] Pooling fyzikálních zákonů přes banky (pending)
- [ ] Latentní vektorový model (VAE/PCA na residuals)

---

---

## 9. Stav implementace

### Moduly

| Soubor | Stav | Popis |
|---|---|---|
| `training/modules/instrument_dna.py` | ✓ implementováno | InstrumentDNA — fit + generate + export |
| `tools/anchor_helper.py` | ✓ implementováno | Textový REPL pro anotaci anchor not |
| `tools/analyze_extraction.py` | ✓ implementováno | Diagnostika kvality extrakce |

### Naměřené výsledky — pl-grand

Extrakce: `generated/pl-grand-extracted-v2.json` (extractor v2, multi-start bi-exp)

| Metrika | Hodnota |
|---|---|
| Celkem not | 704 (88 MIDI × 8 vel) |
| Bi-exp fit | 542/704 (77%) |
| Beat detect | 704/704 (100%) |
| B v rozsahu | 645/704 (92%) |
| Anchor not (q≥0.5) | 520/704 |
| Fit InstrumentDNA | ~4.5s |

Fyzikální rozsahy z fitu:
- B: 1.24e-4 (midi 108) → 5.9e-4 (midi 21) — B roste do bassů, fyzikálně správně
- tau1: 0.028s (midi 108, vel7) → 1.45s (midi 21, vel0)
- beat_hz: 0.09 → 0.45 Hz (power law vs f0)

### Generovaný výstup

`soundbanks/pl-grand-dna.json` — 5.6 MB, 704 not, PianoCore JSON formát.

**Čeká na:**
- Poslechové ověření v syntetizéru
- Kalibraci `rms_gain` (`calibrate_rms=True` v `save_bank()`)

### Kritické zjištění (Rhodes)

Z analýzy `vv-rhodes-raw-nn-icreval-hybrid.json`:
- 87 % měřených not: degenerované parametry (beat=0, a1=1.0, tau1≈tau2)
- Plausibilní noty: pouze 48/704, v pásmu MIDI 71–94
- Příčina: ICR renderuje 3s WAVy → tau2_max=2.7s → bi-exp vždy selže
- Řešení: prodloužit `duration_s=10–15s` pro Rhodes round-trip (viz ROADMAP.md)

---

## 8. Očekávané výsledky vs dosavadní přístupy

| Kritérium | Spline | Orig. NN | Instrument DNA |
|---|---|---|---|
| Kontaminace špatnými notami | Ano | Ano (87% šum) | Ne (anchor only) |
| Fyzikální konzistence | Ne | Částečně | Ano (explicitní) |
| Velocity monotonie | Ne | Ne | Ano (enforcer) |
| Funkce s 10 dobrými notami | OK | Špatně | Dobře (GP) |
| Beating / bi-exp zachováno | Spline může smazat | NN průměruje | Zachováno z anchor |
| Multi-bank pooling | Ne | Ne | Ano |
| Uncertainty estimate | Ne | Ne | Ano (GP) |
