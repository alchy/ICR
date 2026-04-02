# ICR — Training modules reference

Dokumentace modulárního training systému v `training/modules/`.

---

## Přehled

```
training/
  modules/
    extractor.py                  ParamExtractor              — extrakce fyziky z WAV banky
    structural_outlier_filter.py  StructuralOutlierFilter     — detekce outlierů přes křivkový fit
    outlier_filter.py             OutlierFilter               — (legacy) detekce fyzikálních outlierů
    eq_fitter.py                  EQFitter                    — LTASE spektrální EQ + biquad fit
    profile_trainer.py            ProfileTrainer              — trénink surrogate NN
    mrstft_finetune.py            MRSTFTFinetuner             — closed-loop MRSTFT fine-tuning
    exporter.py                   SoundbankExporter           — export PianoCore JSON
    synthesizer.py                Synthesizer                 — fyzikální syntéza (stereo, numpy)
                                  DifferenciableRenderer      — diferenciabilní proxy (mono, torch)
    generator.py                  SampleGenerator             — generování WAV sample banky

  pipeline_simple.py   — extract → structural filter → EQ → export  (~15 min, bez GPU)
  pipeline_full.py     — extract → structural filter → EQ → NN → finetune → hybrid  (~60 min)

  train_pipeline.py    — CLI: `python train_pipeline.py simple|full ...`
  generate.py          — CLI: `python generate.py --source ... --out-dir ...`
```

Každý modul lze importovat samostatně:

```python
from training.modules.extractor                 import ParamExtractor
from training.modules.structural_outlier_filter import StructuralOutlierFilter
from training.modules.eq_fitter                 import EQFitter
from training.modules.profile_trainer           import ProfileTrainer
from training.modules.mrstft_finetune           import MRSTFTFinetuner
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
params = extractor.extract_bank(bank_dir, workers=None)

# Jedna nota
note = extractor.extract_note("m060-vel3-f44.wav")
```

### Výstup `extract_bank`

```python
{
  "bank_dir": "/path/to/ks-grand",
  "n_samples": 704,
  "samples": {
    "m060_vel3": {
      "midi": 60, "vel": 3,
      "f0_hz": 261.63,
      "B": 0.00041,               # inharmonicita
      "partials": [
        { "f_hz": 261.6, "A0": 13.7, "tau1": 0.41, "tau2": 3.73,
          "a1": 0.82, "beat_hz": 0.17, "mono": False }
      ],
      "noise": {
        "attack_tau": 0.012,       # shodný klíč s PianoCore / SYSEX_PROTOCOL
        "A_noise": 0.78,
        "centroid_hz": 2400.0,
        "spectral_slope_db_oct": -3.0
      },
      "duration_s": 3.0
    }
  }
}
```

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

Počítá Long-Term Average Spectral Envelope (LTASE) korekcní křivku pro
každou notu: poměr spektrální obálky originálního WAV vůči syntetizované
notě (bez EQ). Zachycuje rezonanci těla nástroje.

Obsahuje také `params_to_biquads()` — převod EQ křivky na min-phase IIR
biquad kaskádu (5 sekcí), který se používá i v `SoundbankExporter`.

### API

```python
fitter = EQFitter()

# Přidá 'spectral_eq' ke každému vzorku v params
params = fitter.fit_bank(params, bank_dir, workers=None)

# Standalone: převod EQ křivky na biquad koeficienty
biquads = fitter.params_to_biquads(freqs_hz, gains_db, sr=44100)
```

### Princip LTASE

```
H(f) = LTASE_original(f) / LTASE_synth(f)
```

Syntéza pro výpočet LTASE běží s `eq_strength=0` (bypass EQ),
aby se předešlo cirkulární závislosti.

### Výstup `fit_bank` (přidané klíče do každého vzorku)

```python
sample["spectral_eq"] = {
    "freqs_hz":           [20.0, 25.1, ...],   # 64 log-spaced bodů
    "gains_db":           [-1.2, 0.3, ...],
    "stereo_width_factor": 1.05
}
```

### `params_to_biquads` — IIR fitting

Algoritmus:
1. Interpolace EQ křivky na 2048-bodový FFT grid
2. Cepstrální minimum-phase rekonstrukce
3. Least-squares IIR design (rovnicová chyba) → 5 pólů / 5 nul
4. Stabilizace pólů (odraz mimo jednotkovou kružnici)
5. `tf2sos()` → 5 biquad sekcí (Direct Form II)

```python
biquads = fitter.params_to_biquads(freqs_hz, gains_db, sr=44100)
# biquads == [{"b": [b0,b1,b2], "a": [a1,a2]}, ...]  délka 5
```

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
Perceptuální validace probíhá až ve fázi MRSTFT fine-tuning.

---

## MRSTFTFinetuner

**Soubor:** `training/modules/mrstft_finetune.py`

Closed-loop fine-tuning: renderuje noty přes `DifferentiableRenderer`,
počítá MRSTFT loss vůči originálním WAV souborům a backpropaguje do vah
`InstrumentProfile`.

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
```

### Rozdíl `from_params` vs `hybrid`

| Metoda | Zdroj dat | NN nutná? |
|--------|-----------|-----------|
| `from_params` | 100 % extrahovaná fyzika | Ne |
| `hybrid` | Extrahovaná kde existuje, NN predikce pro zbytek | Ano |

`hybrid` je výstupem `pipeline_full` — NN vyplní MIDI noty mimo rozsah banky
nebo noty odstraněné `StructuralOutlierFilter`.

### Zpracování při exportu

Pro každou (midi, vel) kombinaci:
1. Sanitizace parciálů (ořez τ, validace beat_hz)
2. Výpočet RMS gain z amplitud parciálů + noise power
3. Převod `spectral_eq` křivky → 5 biquad sekcí (`EQFitter.params_to_biquads`)
4. Generování φ (phase) per parciál — seedováno midi+vel pro reprodukovatelnost
5. Zápis do `notes["m{midi:03d}_vel{vel}"]`

### Soundbank — uložené klíče

Soundbanka ukládá fyzikální parametry i editovatelné zdrojové hodnoty:

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

Fyzikální syntéza jedné noty — stereo, numpy-based. Jde o přesný
přepis `physics_synth.synthesize_note()` zabalený do třídy.

### API

```python
audio = Synthesizer().render(
    params,                   # dict z extract_bank nebo soundbank JSON
    midi        = 60,
    vel         = 3,
    sr          = 44_100,
    duration    = 3.0,        # None = z params["duration_s"]
    beat_scale  = 1.0,
    noise_level = 1.0,
    eq_strength = 1.0,        # 0 = bypass EQ, 1 = plný EQ
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

## DifferenciableRenderer

**Soubor:** `training/modules/synthesizer.py`

Mono diferenciabilní proxy pro fine-tuning. Zachovává gradient flow přes
parametry `InstrumentProfile`. Jde o přesný přepis `torch_synth.py`.

### API

```python
from training.modules.synthesizer    import DifferentiableRenderer
from training.modules.profile_trainer import ProfileTrainer

model  = ProfileTrainer().load("training/profile.pt")
audio  = DifferentiableRenderer().render(model, midi=60, vel=3)
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

Zjednodušení jsou záměrná — stereo dekorelace a EQ nejsou diferenciabilní;
fine-tuning na mono signálu je dostatečný pro konvergenci.

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

---

## Pipelines

### pipeline_simple.py

```python
from training.pipeline_simple import run

out_path = run(
    bank_dir      = "C:/SoundBanks/IthacaPlayer/ks-grand",
    out_path      = "soundbanks/params-ks-grand.json",
    workers       = 8,
    skip_eq       = False,
    skip_outliers = False,   # True = přeskočit StructuralOutlierFilter
    sr_tag        = "f48",   # preferovat f48, fallback na f44
)
```

Kroky: `extract_bank` → `StructuralOutlierFilter` → `fit_bank` → `from_params`

### pipeline_full.py

```python
from training.pipeline_full import run

model, out_path = run(
    bank_dir      = "C:/SoundBanks/IthacaPlayer/ks-grand",
    out_path      = "soundbanks/params-ks-grand.json",
    epochs        = 1800,
    ft_epochs     = 200,
    workers       = 8,
    skip_outliers = False,
    sr_tag        = "f48",
)
```

Kroky: `extract_bank` → `StructuralOutlierFilter` → `fit_bank` → `train` → `finetune` → `hybrid`

---

## Vlastní workflow (příklady)

### Přeskočit NN, jen vyexportovat s jiným target_rms

```python
from training.modules.extractor  import ParamExtractor
from training.modules.eq_fitter  import EQFitter
from training.modules.exporter   import SoundbankExporter

params = ParamExtractor().extract_bank("C:/SoundBanks/IthacaPlayer/ks-grand")
params = EQFitter().fit_bank(params, "C:/SoundBanks/IthacaPlayer/ks-grand")

exp = SoundbankExporter()
exp.from_params(params, "soundbanks/custom.json", sr=44100)
```

### Použít existující model, přeskočit extrakci

```python
from training.modules.profile_trainer import ProfileTrainer
from training.modules.exporter        import SoundbankExporter
from training.modules.extractor       import ParamExtractor
from training.modules.eq_fitter       import EQFitter

# Načti existující extrakci (přeskočí přepočet)
import json
with open("training/params-ks-grand.json") as f:
    params = json.load(f)

model = ProfileTrainer().load("training/profile-ks-grand.pt")
SoundbankExporter().hybrid(model, params, "soundbanks/new.json")
```

### Generovat jednu notu v různých variantách

```python
import soundfile as sf
from training.modules.generator import SampleGenerator
from training.modules.profile_trainer import ProfileTrainer

model = ProfileTrainer().load("training/profile-ks-grand.pt")
gen   = SampleGenerator()

for beat in [0.5, 1.0, 1.5, 2.0]:
    wav = gen.generate_note(model, midi=60, vel=4, beat_scale=beat)
    sf.write(f"generated/C4_beat{beat}.wav", wav, 44100)
```

### Vygenerovat sample banku z NN pro celý rozsah

```python
from training.modules.generator      import SampleGenerator
from training.modules.profile_trainer import ProfileTrainer

model = ProfileTrainer().load("training/profile-ks-grand.pt")
SampleGenerator().generate_bank(
    model,
    out_dir     = "generated/ks-grand-v2/",
    midi_range  = (21, 108),
    vel_count   = 8,
    beat_scale  = 1.2,
    eq_strength = 0.8,
)
```
