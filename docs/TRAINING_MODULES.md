# ICR — Training modules reference

Dokumentace modulárního training systému v `training/modules/`.
Popis workflows a pipeline logiky → viz [TRAINING_WORKFLOWS.md](TRAINING_WORKFLOWS.md).

---

## Přehled

```
training/
  modules/
    extractor.py                  ParamExtractor              — extrakce fyziky z WAV banky
    structural_outlier_filter.py  StructuralOutlierFilter     — detekce outlierů přes křivkový fit
    eq_fitter.py                  EQFitter                    — LTASE spektrální EQ + biquad fit
    profile_trainer.py            ProfileTrainer              — trénink surrogate NN
    profile_trainer_exp.py        ProfileTrainerEncExp        — sdílené enkodéry, vel na všech hlavách
    mrstft_finetune.py            MRSTFTFinetuner             — closed-loop MRSTFT fine-tuning (legacy)
    icr_evaluator.py              ICRBatchEvaluator           — ICR.exe batch eval, early stop
    icr_round_trip.py             ICRRoundTripProcessor       — round-trip korektury trénovacích targetů
    exporter.py                   SoundbankExporter           — export PianoCore JSON
    synthesizer.py                Synthesizer                 — fyzikální syntéza (stereo, numpy)
                                  DifferentiableRenderer      — diferenciabilní proxy (mono, torch)
    generator.py                  SampleGenerator             — generování WAV sample banky

  pipeline_simple.py              — extract -> structural filter -> EQ -> export
  pipeline_full.py                — extract -> filter -> EQ -> NN -> MRSTFT finetune -> hybrid
  pipeline_nn.py                  — extract -> filter -> EQ -> NN (sdílené enkodéry) -> export
  pipeline_experimental.py        — jako nn + MRSTFTFinetuner (legacy)
  pipeline_icr_eval.py            — extract -> filter -> EQ -> NN (ICR early-stop) -> hybrid
  pipeline_smooth_icr_eval.py     — extract -> filter -> EQ -> spline-smooth -> NN -> hybrid
                                     (volitelně: --extend-partials, --icr-round-trip)
```

Každý modul lze importovat samostatně:

```python
from training.modules.extractor                 import ParamExtractor
from training.modules.structural_outlier_filter import StructuralOutlierFilter
from training.modules.eq_fitter                 import EQFitter
from training.modules.profile_trainer           import ProfileTrainer
from training.modules.profile_trainer_exp       import ProfileTrainerEncExp
from training.modules.mrstft_finetune           import MRSTFTFinetuner
from training.modules.icr_evaluator             import ICRBatchEvaluator
from training.modules.icr_round_trip            import ICRRoundTripProcessor
from training.modules.exporter                  import SoundbankExporter
from training.modules.synthesizer               import Synthesizer, DifferentiableRenderer
from training.modules.generator                 import SampleGenerator
```

---

## ParamExtractor

**Soubor:** `training/modules/extractor.py`

Extrahuje fyzikální parametry (inharmonicita, bi-exponenciální obálky,
beating, šum) z každého WAV souboru banky pomocí FFT peak detection a
STFT envelope tracking.

### API

```python
extractor = ParamExtractor()

# Celá banka paralelně
params = extractor.extract_bank(bank_dir, workers=None, sr_tag="f48")

# Jedna nota
note = extractor.extract_note("m060-vel3-f44.wav")
```

### Výstup `extract_bank`

```python
{
  "bank_dir": "/path/to/ks-grand",
  "n_samples": 704,
  "samples": {

    # ── C3 (MIDI 48) — basová nota, bohatý harmonický obsah ─────────────────
    "m048_vel4": {
      "midi": 48, "vel": 4,
      "f0_hz": 130.81,
      "B": 0.00187,               # vyšší B u bassů (tlustší struna)
      "K_valid": 26,              # bohatý obsah — parciály až ~3.4 kHz
      "partials": [
        # k  f_hz    A0     tau1   tau2    a1     beat_hz  phi
        # tau1 = rychlý decay (úder), tau2 = pomalý (dozvuk)
        # beat_hz výrazné u bassů — 3 struny, větší rozladění
        { "k":  1, "f_hz":  130.9, "A0": 18.4, "tau1": 1.21, "tau2": 9.83, "a1": 0.87, "beat_hz": 0.08, "phi": 0.0 },
        { "k":  2, "f_hz":  261.8, "A0": 12.7, "tau1": 0.84, "tau2": 7.21, "a1": 0.83, "beat_hz": 0.41, "phi": 0.0 },
        { "k":  3, "f_hz":  392.8, "A0":  9.3, "tau1": 0.61, "tau2": 5.44, "a1": 0.79, "beat_hz": 0.19, "phi": 0.0 },
        { "k":  4, "f_hz":  523.9, "A0":  7.1, "tau1": 0.47, "tau2": 4.12, "a1": 0.76, "beat_hz": 0.63, "phi": 0.0 },
        { "k":  5, "f_hz":  655.2, "A0":  5.4, "tau1": 0.38, "tau2": 3.31, "a1": 0.73, "beat_hz": 0.35, "phi": 0.0 },
        { "k":  6, "f_hz":  786.7, "A0":  4.2, "tau1": 0.31, "tau2": 2.73, "a1": 0.70, "beat_hz": 0.82, "phi": 0.0 },
        { "k":  7, "f_hz":  918.4, "A0":  3.1, "tau1": 0.25, "tau2": 2.28, "a1": 0.67, "beat_hz": 0.27, "phi": 0.0 },
        { "k":  8, "f_hz": 1050.3, "A0":  2.4, "tau1": 0.21, "tau2": 1.94, "a1": 0.64, "beat_hz": 0.51, "phi": 0.0 },
        { "k":  9, "f_hz": 1182.4, "A0":  1.8, "tau1": 0.17, "tau2": 1.67, "a1": 0.61, "beat_hz": 0.44, "phi": 0.0 },
        { "k": 10, "f_hz": 1314.7, "A0":  1.4, "tau1": 0.14, "tau2": 1.45, "a1": 0.58, "beat_hz": 0.38, "phi": 0.0 },
        { "k": 11, "f_hz": 1447.2, "A0":  1.1, "tau1": 0.12, "tau2": 1.27, "a1": 0.56, "beat_hz": 0.71, "phi": 0.0 },
        { "k": 12, "f_hz": 1579.9, "A0":  0.8, "tau1": 0.10, "tau2": 1.12, "a1": 0.53, "beat_hz": 0.29, "phi": 0.0 },
        # k=13..26 pokračují s dále klesající amplitudou (A0 < 0.5)
        # k=27..60 alokováno, A0 ≈ 0 (pod extrakčním prahem SNR)
      ],
      "noise": {
        "attack_tau":          0.018,   # o něco delší náběh u bassů
        "A_noise":             1.12,
        "centroid_hz":      1800.0,     # nižší těžiště — bas má méně výškového šumu
        "spectral_slope_db_oct": -4.2
      },
      "rms_gain":   0.071,
      "duration_s": 3.0,
      "_interpolated": False
    },

    # ── C5 (MIDI 72) — výšková nota, méně parciálů (Nyquist omezení) ────────
    "m072_vel4": {
      "midi": 72, "vel": 4,
      "f0_hz": 523.25,
      "B": 0.00028,               # nižší B u výšek (tenčí struna)
      "K_valid": 9,               # parciály k=9 → ~4.7 kHz, dále pod SNR prahem
      "partials": [
        { "k": 1, "f_hz":  523.3, "A0": 11.2, "tau1": 0.31, "tau2": 2.94, "a1": 0.82, "beat_hz": 0.06, "phi": 0.0 },
        { "k": 2, "f_hz": 1046.7, "A0":  6.8, "tau1": 0.22, "tau2": 2.11, "a1": 0.77, "beat_hz": 0.14, "phi": 0.0 },
        { "k": 3, "f_hz": 1570.3, "A0":  3.9, "tau1": 0.16, "tau2": 1.58, "a1": 0.73, "beat_hz": 0.09, "phi": 0.0 },
        { "k": 4, "f_hz": 2094.1, "A0":  2.1, "tau1": 0.12, "tau2": 1.24, "a1": 0.69, "beat_hz": 0.21, "phi": 0.0 },
        { "k": 5, "f_hz": 2618.2, "A0":  1.2, "tau1": 0.09, "tau2": 1.01, "a1": 0.65, "beat_hz": 0.17, "phi": 0.0 },
        { "k": 6, "f_hz": 3142.5, "A0":  0.7, "tau1": 0.07, "tau2": 0.84, "a1": 0.62, "beat_hz": 0.11, "phi": 0.0 },
        { "k": 7, "f_hz": 3667.1, "A0":  0.4, "tau1": 0.06, "tau2": 0.71, "a1": 0.59, "beat_hz": 0.08, "phi": 0.0 },
        { "k": 8, "f_hz": 4192.0, "A0":  0.2, "tau1": 0.05, "tau2": 0.61, "a1": 0.56, "beat_hz": 0.05, "phi": 0.0 },
        { "k": 9, "f_hz": 4717.2, "A0":  0.1, "tau1": 0.04, "tau2": 0.53, "a1": 0.53, "beat_hz": 0.03, "phi": 0.0 },
        # k=10..60 pod SNR prahem — A0 ≈ 0
      ],
      "noise": {
        "attack_tau":          0.008,
        "A_noise":             0.54,
        "centroid_hz":      3600.0,
        "spectral_slope_db_oct": -2.8
      },
      "rms_gain":   0.058,
      "duration_s": 3.0,
      "_interpolated": False
    }

    # ... 702 dalších not
  }
}
```

> **Počet parciálů podle polohy na klaviatuře:**
>
> | Oblast | MIDI | f0 [Hz] | K_valid (typicky) | Omezující faktor |
> |---|---|---|---|---|
> | Hluboký bas | 21–32 | 27–69 | 30–50 | SNR (slabé vyšší harmonické) |
> | Bas | 33–48 | 69–131 | 20–30 | SNR |
> | Střed | 49–72 | 131–523 | 10–20 | SNR + Nyquist |
> | Výšky | 73–96 | 523–2093 | 4–10 | Nyquist (parciály nad 20 kHz) |
> | Pikola | 97–108 | 2093–4186 | 2–5 | Nyquist |
>
> `K_valid` udává počet parciálů s dostatečným SNR při extrakci.
> Sloty k=K_valid+1 až k=60 (`PIANO_MAX_PARTIALS`) jsou alokovány s `A0≈0`
> a exporter je do JSON nezahrnuje.

### Poznámky

- Paralelní zpracování přes `multiprocessing.Pool` — `workers=None` použije `cpu_count - 1`.
- Adaptivní N_FFT podle MIDI noty (nižší noty potřebují delší okno).
- Beat detection z amplitudové modulace STFT obálek.

---

## StructuralOutlierFilter

**Soubor:** `training/modules/structural_outlier_filter.py`

Detekuje a odstraňuje vzorky, jejichž `duration` nebo `n_partials` vybočuje
z hladké křivky napříč klávesnicí nebo velocity vrstvami. Pracuje jako
nezávislý mezikrok — nevstupuje do extrakce ani analýzy.

### Algoritmus

1. Sestaví matici `{feature: {midi: {vel: hodnota}}}` ze všech vzorků.
2. Pro každou MIDI notu fituje polynom 2. stupně přes velocity osu.
3. Pro každou velocity vrstvu fituje polynom 2. stupně přes MIDI osu.
4. Vzorek je označen za outlier, pokud jeho reziduum překročí práh `sigma`
   MAD-sigma v **obou směrech** (příliš vysoko I příliš nízko) na **libovolné ose**.

### API

```python
params = StructuralOutlierFilter().filter(params, sigma=3.0)
```

| Parametr | Výchozí | Popis |
|----------|---------|-------|
| `params` | — | Dict z `ParamExtractor.extract_bank()` |
| `sigma` | 3.0 | MAD-sigma práh (obě strany). Vyšší = méně agresivní. |

### Sledované vlastnosti

| Feature | Zdroj | Prostor fitu |
|---------|-------|--------------|
| `duration` | `duration_s` | Log-prostor |
| `n_partials` | `n_partials` | Lineární |
| `B` | `B` (přeskočeno pokud ≤ 0) | Log-prostor |
| `tau1_mean` | průměr tau1 parciálů k=1–6 | Log-prostor |
| `A0_mean` | průměr A0 parciálů k=1–6 | Log-prostor |

### Příklad — velocity osa pro jednu MIDI notu

```
vel:      0     1     2     3     4     5
dur(s):  20.1  10.3   8.1   5.0   3.1   2.0   ← hladká křivka
outlier:                    ?                  ← např. 15 s na vel=3 → smazáno
```

### Poznámky

- Vstupní dict není modifikován — vrací novou kopii.
- `n_samples` je aktualizováno po filtrování.
- Výpis obsahuje signed z-score (`+` = příliš vysoká, `-` = příliš nízká hodnota).
- `MIN_POINTS = 4` — série s méně body je přeskočena (nefituje se).
- Fyzikální parametry (B, tau1, A0) jsou fitovány nezávisle; jedna chybná extrakce neovlivní ostatní.

---

## EQFitter

**Soubor:** `training/modules/eq_fitter.py`

EQFitter zjistí co chybí syntetizátoru oproti originálu ve frekvenční
doméně a uloží to jako IIR filtr aplikovaný při přehrávání.

Syntetizátor modeluje fyziku strun (parciály, decay, inharmonicita), ale
nezná tělo nástroje — Rhodes má kovovou vidličku a pickup, klavír má
soundboard. EQ zachytí tuto barvovou část, která se nedá popsat pár
parametry. Výsledek: syntéza + EQ zní mnohem blíže originálu než syntéza
samotná.

### Proč je to potřeba

Synth produkuje čisté parciály se správnými frekvencemi a amplitudami.
Originální nástroj navíc obsahuje:
- rezonance těla / soundboardu
- charakteristiku snímače (pickup)
- akustický prostor

Tyto efekty se projevují jako frekvenčně závislé zesílení/útlum — přesně
to co EQ dokáže korigovat.

### Algoritmus krok po kroku

**1. LTASE (Long-Term Average Spectral Envelope)**

Pro každou notu se spustí syntetizátor bez EQ a výsledek se porovná s
originálním WAV přes STFT. Průměrováním přes všechny časové snímky
vznikne spektrální obálka každého signálu:

```
LTASE_orig(f)   — jak nota zní v originálním nástroji
LTASE_synth(f)  — jak nota zní ze syntetizátoru (čisté parciály)
```

N_FFT se volí adaptivně podle MIDI výšky (cíl: 20 binů na jednu
harmonickou), rozsah 8192–32768 vzorků.

**2. Přenosová funkce H**

```
H(f) = LTASE_orig(f) / LTASE_synth(f)
```

Podělením se vyruší co je v obou stejné (parciální struktura) a zbyde
jen rozdíl — rezonance těla nástroje. Poté se aplikuje 1/6-oktávové
vyhlazení a normalizace průměru nad 100 Hz na 0 dB.

**3. 64 bodů na log-škále**

Křivka H se vzorkuje na 64 log-rovnoměrně rozložených frekvencích
(20 Hz–20 kHz) → `spectral_eq: {freqs_hz, gains_db}`. Tato data jdou
do JSONu a jsou editovatelná v sound editoru.

**4. Konverze na biquad kaskádu (5 sekcí)**

Při exportu se křivka fituje na 5 IIR biquad filtrů:
1. Interpolace EQ křivky na 2048-bodový FFT grid
2. Cepstrální minimum-phase rekonstrukce z magnitudy
3. Least-squares IIR design (rovnicová chyba) → `_invfreqz()`
4. Stabilizace pólů do jednotkového kruhu → `_stabilize()`
5. `tf2sos()` → 5 biquad sekcí (Direct Form II)

C++ PianoCore aplikuje těchto 5 filtrů za sebou na výstupní audio každé
noty.

### API

```python
fitter = EQFitter()

# Přidá 'spectral_eq' ke každému vzorku v params (paralelně)
params = fitter.fit_bank(params, bank_dir, workers=None)

# Standalone: převod EQ křivky na biquad koeficienty
biquads = fitter.params_to_biquads(freqs_hz, gains_db, sr=44100)
```

### Výstup `fit_bank` (přidané klíče do každého vzorku)

```python
sample["spectral_eq"] = {
    "freqs_hz":            [20.0, 25.1, ...],   # 64 log-spaced bodů
    "gains_db":            [-1.2, 0.3, ...],    # dB, průměr ~0 dB
    "stereo_width_factor": 1.05                 # poměr stereo šířky orig/synth
}
```

### Výstup `params_to_biquads`

```python
biquads = fitter.params_to_biquads(freqs_hz, gains_db, sr=44100)
# [{"b": [b0, b1, b2], "a": [a1, a2]}, ...]   délka 5
```

### Parametry

| Konstanta | Hodnota | Popis |
|---|---|---|
| `N_EQ_POINTS` | 64 | Počet log-spaced bodů EQ křivky |
| `EQ_F_MIN/MAX` | 20–20000 Hz | Frekvenční rozsah |
| `NFFT_EXP_MIN/MAX` | 13–15 | N_FFT = 2^exp (8192–32768) |
| `NFFT_BINS_TARGET` | 20 | Cíl: 20 FFT binů na harmonickou |
| `SMOOTH_OCT` | 1/12 oct | Polo-šířka oktávového vyhlazení |
| `PIANO_N_BIQUAD` | 5 | Počet biquad sekcí v exportu |

---

## ProfileTrainer

**Soubor:** `training/modules/profile_trainer.py`

Trénuje surrogate NN model `InstrumentProfile` na extrahovaných
parametrech. NN vyhlazuje fyzikální parametry přes klávesnici a
interpoluje pro MIDI noty, kde chybí reálná data.

### API

```python
trainer = ProfileTrainer()

# Trénink
model = trainer.train(params, epochs=1800, hidden=64, lr=0.003)

# Uložení / načtení
torch.save(model.state_dict(), "training/profile.pt")
model = trainer.load("training/profile.pt")

# Inference pro celou klávesnici
predicted_params = trainer.predict_all(model)
# → dict stejného formátu jako params["samples"]
```

### Architektura InstrumentProfile

Faktorizovaný MLP — každý fyzikální parametr má vlastní síť:

| Sub-síť | Vstup | Výstup |
|---------|-------|--------|
| `B_net` | midi | inharmonicita B |
| `dur_net` | midi | délka tónu |
| `tau1_k1_net` | midi, vel | τ1 pro k=1 |
| `tau_ratio_net` | midi, k | škálování τ1 přes parciály |
| `A0_net` | midi, k, vel | amplituda parciálu |
| `df_net` | midi, k | beat_hz |
| `biexp_net` | midi, k, vel | a1, τ2/τ1 |
| `noise_net` | midi, vel | A_noise, τ_noise, centroid |
| `phi_net` | midi, vel | φ_diff (pro torch_synth) |

Feature kódování: `midi_feat(m)` — 6D embedding s harmonickými funkcemi
pro register awareness; `vel_feat(v)` — 3D; `k_feat(k)` — 3D.

### Trénink

- Optimizér: Adam, LR=0.003, cos-annealing do `lr×0.01`
- Loss: MSE extrahovaných params vs. NN predikce (viz `_compute_data_loss`)
- Smoothness penalty na MIDI gridu každých 5 epoch (B, τ1, A0, noise)
- Epochy: 3000 default

### Validační set

Každá N-tá MIDI nota je deterministicky vyčleněna jako validační set
(`val_frac=0.15` → ~15 % not, rovnoměrně rozložených přes celý rozsah).

```
train: m033 m034 m035 m036 m037 m038 m039 m040 …
val:              ↑                   ↑           každá ~7. nota
```

Val loss se počítá každých 100 epoch ze stejných data-fit termů jako train
(bez smoothness penalty). Ukládá se nejlepší checkpoint a na konci se restoruje.

**Poznámka:** Val loss měří fit na zašumělá extrahovaná data — není to
perceptuální kvalita. Hlavní role je detekce divergence a nestability tréninku.

---

## ProfileTrainerEncExp

**Soubor:** `training/modules/profile_trainer_exp.py`

Rozšíření `ProfileTrainer` — sdílené per-axis enkodéry a velocity na
všech hlavách kromě `B_head`. Používá se ve všech aktivních pipeline:
`icr-eval`, `smooth-icr-eval`, `smooth-ext-icr-eval`, `smooth-rt-icr-eval`.

### Architektura `InstrumentProfileEncExp`

```
Sdílené enkodéry (gradient z každé hlavy prochází zpět):
  midi_enc   MLP(MIDI_DIM  → 16d, 3 vrstvy)   — sdílen všemi 11 hlavami
  vel_enc    MLP(VEL_DIM   →  8d, 2 vrstvy)   — sdílen všemi hlavami kromě B_head
  k_enc      MLP(K_DIM     →  8d, 2 vrstvy)   — sdílen k-závislými hlavami
  freq_enc   MLP(FREQ_DIM  →  8d, 2 vrstvy)   — sdílen eq_head

Hlavy (2-vrstvé MLP z konkatenovaných enkodérů):
  B_head         [midi]                → log(B)           ← vel záměrně vynecháno
  dur_head       [midi, vel]           → log(duration)
  tau1_k1_head   [midi, vel]           → log(τ₁ k=1)
  tau_ratio_head [midi, k, vel]        → log(τ_k / τ₁)
  A0_head        [midi, k, vel]        → log(A_k / A₁)
  df_head        [midi, k, vel]        → log(beat_hz)
  eq_head        [midi, freq, vel]     → gain_db
  wf_head        [midi, vel]           → log(stereo_width_factor)
  noise_head     [midi, vel]           → log(τ_atk), log(centroid), log(A_noise)
  biexp_head     [midi, k, vel]        → logit(a1), log(τ₂/τ₁)
  phi_head       [midi, vel]           → φ_diff
```

### Proč B_head nedostává velocity

Inharmonicita B je fyzikální vlastnost struny — závisí na tuhosti, napětí
a délce, nikoli na dynamice:

```
B = (π² · E · I) / (T · L²)
```

Per-velocity variace v extrakci B je měřicí šum, nikoliv fyzikální signál.
`BSplneFitter` průměruje log(B) přes velocity a fituje 1D spline přes MIDI 21–108.
`B_head` dostává pouze `midi_enc`.

### Srovnání s `InstrumentProfile` (full)

| Vlastnost | `full` | `experimental` |
|-----------|--------|----------------|
| Velocity na dur, df, eq, wf, tau_ratio | Ne | Ano |
| Velocity na B | Ne | Ne (fyzikálně správně) |
| Enkodéry | Každá síť samostatně | Sdílené per-axis |
| EQ křivka závisí na velocity | Ne | Ano |
| Parametrů (hidden=64) | ~55 k | ~37 k |

### API

```python
from training.modules.profile_trainer_exp import ProfileTrainerEncExp

model = ProfileTrainerEncExp().train(params, epochs=5000)
model = ProfileTrainerEncExp().load("profile-exp.pt")
```

API je identické s `ProfileTrainer`.

### Trénink

- Smoothness penalty na **MIDI ose**: tau, A0, noise každých 5 epoch
- Smoothness penalty na **velocity ose** (MIDI=60, lambda=0.3) pro B a dur

### Poznatky z tréninku (vv-rhodes, 2026-04-02)

Při prvním tréninku byla `B_head` velocity-aware — výsledky ukázaly divergenci:

| epoch | B val loss | best val |
|-------|-----------|----------|
| 100 | 3.84 | 1.006 ✓ |
| 500 | 6.38 | — |
| 1000 | 11.99 | — |

**Příčina:** `B` per velocity je šum; model přeučoval na náhodné variace.
**Oprava:** `B_head` dostává pouze `midi_enc`; `sm_vel` lambda zvýšena 0.05 → 0.3.

---

## MRSTFTFinetuner

**Soubor:** `training/modules/mrstft_finetune.py`

Closed-loop fine-tuning: renderuje noty přes `DifferentiableRenderer`,
počítá MRSTFT loss vůči originálním WAV souborům a backpropaguje do vah
`InstrumentProfile`.

> **Status:** legacy — nové pipeline (`icr-eval` a varianty) používají
> `ICRBatchEvaluator` pro early stop a nepotřebují fine-tuner.

### API

```python
model = MRSTFTFinetuner().finetune(
    model,
    bank_dir = "C:/SoundBanks/IthacaPlayer/ks-grand",
    epochs   = 200
)
```

### MRSTFT loss

Multi-Resolution STFT (Yamamoto et al., Parallel WaveGAN 2020):

| Scale | N_FFT | Hop | Účel |
|-------|-------|-----|------|
| 1 | 256 | 64 | Attack transienty (~6 ms) |
| 2 | 1024 | 256 | Vyvážený (~23 ms) |
| 3 | 4096 | 1024 | Sustain, ladění (~93 ms) |

Per scale: Spectral Convergence + Log-Magnitude loss.

### Poznámky

- `DifferentiableRenderer` je mono proxy — stereo dekorelace není
  diferenciabilní, proto probíhá fine-tuning na mono signálu.
- Gradient clipping: max_norm=1.0.
- Ukládá best checkpoint (nejnižší průměrná MRSTFT loss).
- Paměť: ~128 MB/nota (K=60 × N=132 300 vzorků × gradient tape).

---

## ICRBatchEvaluator

**Soubor:** `training/modules/icr_evaluator.py`

Evaluátor pro ICR-based early stopping. Spouští C++ `ICR.exe --render-batch`,
načítá vygenerované WAV soubory a počítá MRSTFT loss — na zvuku přesně tak,
jak ho PianoCore syntetizuje v produkci.

**Důležité:** ICRBatchEvaluator řídí **pouze early stop** — není trénovací
loss a gradient přes něj nepochází. Tréninkový loss je vždy MSE na parametrech
(NN výstup vs. targety).

### Přenos banky do ICR: soubor, ne SysEx

Banka se předá ICR.exe přes **JSON soubor** (temp dir):

```
ProfileTrainerEncExp
  → SoundbankExporter → temp/icr_bank_xxxx.json
  → ICR.exe --params temp/icr_bank_xxxx.json --render-batch eval_notes.json
  → WAV soubory v temp/
  → MRSTFT výpočet
```

### API

```python
evaluator = ICRBatchEvaluator(
    icr_exe   = "build/bin/Release/ICR.exe",
    bank_dir  = "C:/SoundBanks/IthacaPlayer/vv-rhodes",
    sr        = 48000,
    eval_midi = None,   # None = auto: 24 not rovnoměrně přes rozsah banky
    eval_vels = None,   # None = auto: [0, 2, 4, 6, 7]  (ppp / pp / mp / mf / ff)
    note_dur  = 3.0,
    out_dir   = None,   # None = auto temp dir
)

score = evaluator.eval(model, params)   # float, nižší = lepší
evaluator.close()
```

### Vnitřní průběh `eval()`

```
1. SoundbankExporter().hybrid(model, params, tmp.json)
2. Zapsat eval_notes.json   [{midi, vel_idx, duration_s}, ...]
3. subprocess: ICR.exe --core PianoCore --params tmp.json
                        --render-batch eval_notes.json --out-dir tmp_dir/
4. Načíst m{midi:03d}_vel{vel}.wav z tmp_dir/
5. Mono, ořez/pad na note_dur*sr vzorků
6. mrstft(rendered_wav, reference_wav) → průměr přes všechny eval noty
```

Timeout ICR.exe: **120 s**. Při selhání vrátí `float('inf')` a pokračuje dál.

### Selekce eval not

**MIDI:** `N_EVAL_MIDI = 24` not rovnoměrně přes rozsah dostupných not banky
(průměrný rozestup ~2.7 půltónu).

**Velocity:** `eval_vels = [0, 2, 4, 6, 7]` pokrývá celou dynamiku:

| Index | Dynamika |
|-------|----------|
| 0 | ppp |
| 2 | pp |
| 4 | mp |
| 6 | mf |
| 7 | ff |

Celkový počet eval not: **24 × 5 = 120** (~80 s na eval).

### MRSTFT scales

| Scale | N_FFT | Hop |
|-------|-------|-----|
| 1 | 256 | 64 |
| 2 | 1024 | 256 |
| 3 | 4096 | 1024 |

---

## ICRRoundTripProcessor

**Soubor:** `training/modules/icr_round_trip.py`

Opravuje trénovací targety o systematické offsety ICR syntezátoru.
Extrakce měří reálný klavír; ICR má vlastní přenosovou funkci — stejné
parametry renderuje jako zvuk, který extraktor změří jinak. Round-trip
tento offset zachytí automaticky.

```
smooth_params  →  ICR render  →  WAV  →  extract  →  params_rt
                                                          ↕ MSE
                                                      (trénovací target)
```

NN se naučí "co ICR potřebuje na vstupu, aby produkoval zamýšlený zvuk",
nikoliv "co jsme naměřili z reálného klavíru".

### EQ v round-tripu

`spectral_eq` je z round-tripu **vyloučeno** — ICR renderuje s neutrálním
EQ (prázdné biquady). Výstup round-tripu zachovává `spectral_eq` z
`smooth_params` beze změny. EQ bude řešeno jako separátní trénovací větev.

### API

```python
from training.modules.icr_round_trip import ICRRoundTripProcessor

rt = ICRRoundTripProcessor(
    icr_exe  = "build/bin/Release/ICR.exe",
    sr       = 48000,
    sr_tag   = "f48",
    note_dur = 3.0,
)
params_rt = rt.process(params_smooth, workers=8)
# params_rt má stejnou strukturu jako params_smooth;
# fyzikální params nahrazeny round-trip hodnotami, spectral_eq zachováno
```

### Průběh `process()`

```
1. Export measured not s neutrálním EQ → temp bank JSON
2. ICR.exe --render-batch  → WAV soubory (m060_vel3.wav, ...)
3. Přejmenování: m060_vel3.wav → m060-vel3-f48.wav  (formát ParamExtractor)
4. ParamExtractor.extract_bank() → params_rt_raw
5. Merge: rt fyzikální params + spectral_eq z smooth_params
   (noty kde extrakce selhala → fallback na smooth_params)
```

### Fallback chování

Noty u nichž extrakce z round-trip WAV selže (velmi tiché noty v krajích
rozsahu) se zachovají z `smooth_params`. Round-trip počet je vypsán:
`Round-trip complete: N_rt/N notes extracted`.

---

## SoundbankExporter

**Soubor:** `training/modules/exporter.py`

Exportuje soundbank JSON načitatelný C++ `PianoCore::load()`.

### API

```python
exporter = SoundbankExporter()

# Simple: jen reálná extrahovaná data
exporter.from_params(params, "soundbanks/out.json", sr=44100)

# Hybrid: reálná data + NN predikce pro chybějící pozice
exporter.hybrid(model, params, "soundbanks/out.json", sr=44100)

# Pure-NN: všech 704 not z NN (pro A/B srovnání)
exporter.pure_nn(model, params, "soundbanks/out-pure-nn.json")
```

### Rozdíl `from_params` vs `hybrid` vs `pure_nn`

| Metoda | Zdroj dat |
|--------|-----------|
| `from_params` | 100 % extrahovaná fyzika |
| `hybrid` | Extrahovaná kde existuje, NN predikce pro zbytek |
| `pure_nn` | 100 % NN predikce (pro A/B) |

### Zpracování při exportu

Pro každou (midi, vel) kombinaci:
1. Sanitizace parciálů (ořez τ, validace beat_hz)
2. Výpočet RMS gain z amplitud parciálů + noise power
3. Převod `spectral_eq` křivky → 5 biquad sekcí (`EQFitter.params_to_biquads`)
4. Generování φ (phase) per parciál — seedováno midi+vel pro reprodukovatelnost
5. Zápis do `notes["m{midi:03d}_vel{vel}"]`

### Soundbank — uložené klíče

```json
{
  "midi": 60, "vel": 3,
  "f0_hz": 261.63,
  "B": 0.00041,
  "K_valid": 48,
  "partials": [
    { "k": 1, "f_hz": 261.6, "A0": 13.7, "tau1": 0.41, "tau2": 3.73,
      "a1": 0.82, "beat_hz": 0.17, "phi": 1.23 },
    ...
  ],
  "eq_biquads": [...],
  "spectral_eq": { "freqs_hz": [...], "gains_db": [...] }
}
```

| Klíč | Účel |
|------|------|
| `B` | inharmonicita; změna via SysEx přepočítá `f_hz[k]` |
| `k` v parciálu | skutečný index parciálu (1-based), nutný pro SysEx B |
| `spectral_eq` | zdrojová EQ křivka; editor ji může znovu fitovat |

---

## Synthesizer

**Soubor:** `training/modules/synthesizer.py`

Fyzikální syntéza jedné noty — stereo, numpy-based.

### API

```python
audio = Synthesizer().render(
    params,
    midi        = 60,
    vel         = 3,
    sr          = 44_100,
    duration    = 3.0,
    beat_scale  = 1.0,
    noise_level = 1.0,
    eq_strength = 1.0,   # 0 = bypass EQ, 1 = plný EQ
    pan_spread  = 0.55,
    target_rms  = 0.06,
)
# audio.shape == (N, 2), dtype float32
```

### Syntézní řetězec

```
pro každý parcial k (K_valid parciálů):
  f_k = k · f0 · √(1 + B·k²)                  inharmonicita
  env = a1·e^(-t/τ1) + (1-a1)·e^(-t/τ2)       bi-exponenciální obálka

  string 1: cos(2π·(f_k + beat/2)·t + φ_L)
  string 2: cos(2π·(f_k - beat/2)·t + φ_R)    φ_R = φ_L + φ_diff

  pan: konstantní výkon (keyboard spread)
  suma přes K parciálů → L/R

post-processing:
  + attack noise (envelope-shaped, spectral centroid přes 1st-order IIR)
  → Schroeder all-pass dekorelace (5 stupňů)
  → spektrální EQ biquad kaskáda (5 sekcí, DF-II, independent L/R state)
  → RMS normalizace + onset ramp
```

---

## DifferentiableRenderer

**Soubor:** `training/modules/synthesizer.py`

Mono diferenciabilní proxy pro fine-tuning. Zachovává gradient flow přes
parametry `InstrumentProfile`.

### API

```python
from training.modules.synthesizer     import DifferentiableRenderer
from training.modules.profile_trainer import ProfileTrainer

model = ProfileTrainer().load("training/profile.pt")
audio = DifferentiableRenderer().render(model, midi=60, vel=3)
# audio: torch.Tensor, shape (N,), requires_grad=True
```

### Zjednodušení oproti `Synthesizer`

| Feature | Synthesizer | DifferentiableRenderer |
|---------|-------------|----------------------|
| Kanály | Stereo | Mono |
| Strings | 1–3 (dle MIDI) | 2 (fixed) |
| Spectral EQ | Ano | Ne |
| Schroeder dekorelace | Ano | Ne |
| Diferenciabilní | Ne | Ano |
| Backend | numpy | PyTorch |

Zjednodušení jsou záměrná — stereo dekorelace a EQ nejsou diferenciabilní.

---

## SampleGenerator

**Soubor:** `training/modules/generator.py`

Generuje WAV soubory z modelu nebo params dictu. Používá `Synthesizer`
interně.

### API

```python
gen = SampleGenerator()

# Celá banka (704 souborů pro 88 not × 8 velocity bands)
gen.generate_bank(
    source      = model,            # InstrumentProfile nebo params dict
    out_dir     = "generated/",
    midi_range  = (21, 108),
    vel_count   = 8,
    sr          = 44_100,
    duration    = 3.0,
    beat_scale  = 1.0,
    noise_level = 1.0,
    eq_strength = 1.0,
)

# Jedna nota → ndarray (N, 2) float32
wav = gen.generate_note(source, midi=60, vel=3, beat_scale=1.5)
```

### Formát výstupních souborů

```
generated/
  m021-vel0-f44.wav
  m021-vel1-f44.wav
  ...
  m108-vel7-f44.wav
```

Pojmenování odpovídá vstupnímu formátu `ParamExtractor` — generovaná
banka může být použita jako vstup do dalšího tréninku.

### Zdroj dat

| `source` typ | Chování |
|-------------|---------|
| `InstrumentProfile` | NN predikce pro každou (midi, vel) |
| `dict` (params) | Reálná extrahovaná data; pokud (midi, vel) chybí, vezme nejbližší sousední notu |
