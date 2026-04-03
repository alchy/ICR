# ICR — Training workflows

Popis všech aktivních trénovacích workflows, jejich datového toku a kdy který použít.
Popis jednotlivých modulů → viz [TRAINING_MODULES.md](TRAINING_MODULES.md).

---

## Přehled workflows

Všechny aktivní workflows sdílejí společný základ:

```
WAV banka → ParamExtractor → StructuralOutlierFilter → EQFitter
```

Liší se v přípravě trénovacích targetů a post-exportu:

| Workflow (CLI) | Targety NN | Extend partials | Round-trip | Finální auth. neměřených pozic |
|---|---|---|---|---|
| `raw-nn-icreval` | raw extracted | Ne | Ne | spline_fix (NN je zašuměná) |
| `spl-nn-icreval` | spline-smooth | Ne | Ne | **NN výstup** |
| `spl-ext-nn-icreval` | spline-smooth + plné parciály | Ano (pre-training) | Ne | **NN výstup** |
| `spl-icrtarget-nn-icreval` | spline-smooth + RT korekce | Ne | Ano | **NN výstup** |
| `spl-ext-icrtarget-nn-icreval` | spline-smooth + plné parciály + RT korekce | Ano (pre-training) | Ano | **NN výstup** |

Všechny workflows používají `ProfileTrainerEncExp` a ICR early stop (`ICRBatchEvaluator`).
Tréninkový loss je vždy **MSE na parametrech** (NN výstup vs. targety).

ICR vstupuje do tréninku dvěma způsoby — podle role v názvu:

| Role | Suffix | Co ICR dělá | Gradient přes ICR |
|------|--------|-------------|-------------------|
| `icreval` | všechny WF | renderuje zvuk, počítá MRSTFT → řídí early stop | Ne |
| `icrtarget` | `spl-icrtarget-*` | generuje trénovací targety přes round-trip → definuje, k čemu NN konverguje | Ne |

Gradient přes ICR.exe v žádném případě nepochází (C++ binary, non-diferenciabilní).
V `icrtarget` workflow však ICR zásadně ovlivňuje trénink — ne přes gradient, ale tím,
že mění samotné targety: NN se učí reprodukovat to, co ICR skutečně produkuje,
ne co extrakce naměřila z reálného klavíru.

---

## Klíčový princip: finální autorita pro neměřené pozice

```
icr-eval:         raw → NN → spline_fix(NN)   → hybrid
                  (raw NN zašuměná → spline ji opraví)

smooth-icr-eval:  smooth → NN                 → hybrid
                  (NN trénovaná na smooth datech je důvěryhodná → spline_fix se NEPROVÁDÍ)
```

> **Opravená chyba (historicky):** původní smooth pipeline volala `spline_fix` i po
> tréninku → double-spline, NN výstup zahozen. Spline_fix náleží pouze `icr-eval`.

---

## Workflow: `raw-nn-icreval`

**CLI:** `python run-training.py raw-nn-icreval --bank <dir>`

NN trénuje na surových extrahovaných parametrech. Výstup NN je zašuměný
(tréninkové targety samy obsahují šum měření), proto se po exportu aplikuje
`spline_fix` na interpolované pozice.

### Schéma

```
WAV soubory
    │
    ▼
[1] ParamExtractor          ← extrakce fyzikálních parametrů
    │  (raw, zašuměné)
    ▼
[2] StructuralOutlierFilter
    │
    ▼
[3] EQFitter
    │
    │  params (measured, raw)
    ├────────────────────────────────────────────────┐
    ▼                                                │ orig_samples
[4] ProfileTrainerEncExp.train()                     │
    │  targety: raw extracted params                 │
    │  early stop: ICR-MRSTFT (ICRBatchEvaluator)   │
    ▼                                                │
[5] SoundbankExporter.hybrid()  ◄────────────────────┘
    │  measured: orig_samples (raw)
    │  NN pozice: NN výstup
    ▼
[6] spline_fix(fix_interpolated=True)
    │  NN pozice NAHRAZENY splinem z measured
    │  → eliminuje šum raw-trained NN
    ▼
[7] SoundbankExporter.pure_nn()
    ▼
VÝSTUP:
  params-{bank}-raw-nn-icreval.json          ← hybrid: raw measured + spline(NN pozice)
  params-{bank}-raw-nn-icreval-pure-nn.json  ← čistá NN (pro A/B)
```

### API

```python
from training.pipeline_icr_eval import run

model, out_path = run(
    bank_dir     = "C:/SoundBanks/IthacaPlayer/vv-rhodes",
    out_path     = "soundbanks/params-vv-rhodes-raw-nn-icreval.json",
    epochs       = 5000,
    icr_patience = 15,
    note_dur     = 3.0,
)
```

### Console výstup

```
[1/3] Extracting params...
[2/3] Training NN on raw params (max 5000 epochs)...
  epoch   50/5000  train=X.XXXX  val=X.XXXX  lr=...
    ICR-MRSTFT = X.XXXX  (120/120 notes, 80s)
  Early stop: ICR-MRSTFT no improvement for 15 evals
[3/3] Exporting hybrid + pure-NN banks...
Done -> soundbanks/params-vv-rhodes-icr-eval.json
```

---

## Workflow: `spl-nn-icreval`

**CLI:** `python run-training.py spl-nn-icreval --bank <dir>`

Před tréninkem se measured parametry vyhladí splinem — NN dostane čisté
targety a sama produkuje hladký výstup. `spline_fix` po exportu se neprovádí.

### Schéma

```
WAV soubory
    │
    ▼
[1] ParamExtractor
    │  (raw, zašuměné)
    ▼
[2] StructuralOutlierFilter
    │
    ▼
[3] EQFitter
    │
    │  params (measured, raw)           ← uložit jako -pre-smooth.json
    ├──────────────────────────────────────────────────────────────┐
    ▼                                                              │ orig_samples
[4] spline_fix(smooth_all, auto_anchors)                           │
    │  → smooth_params                  ← uložit jako -pre-smooth-spline.json
    ▼                                                              │
[5] ProfileTrainerEncExp.train(smooth_params)                      │
    │  targety: spline-vyhlazené measured params                   │
    │  early stop: ICR-MRSTFT                                      │
    ▼                                                              │
[6] SoundbankExporter.hybrid(model, params)  ◄─────────────────────┘
    │  measured: orig_samples (RAW — ne smoothed!)
    │  NN pozice: NN výstup (smooth, protože NN trénovala na smooth datech)
    │  !! BEZ spline_fix !! NN je finální autorita
    ▼
[7] SoundbankExporter.pure_nn()
    ▼
VÝSTUP:
  params-{bank}-spl-nn-icreval.json          ← hybrid: raw measured + NN(smooth)
  params-{bank}-spl-nn-icreval-pure-nn.json  ← čistá NN
  (+ intermediate: -pre-smooth.json, -pre-smooth-spline.json)
```

### API

```python
from training.pipeline_smooth_icr_eval import run

model, out_path = run(
    bank_dir     = "C:/SoundBanks/IthacaPlayer/vv-rhodes",
    out_path     = "soundbanks/params-vv-rhodes-spl-nn-icreval.json",
    epochs       = 5000,
    auto_anchors = 12,
    icr_patience = 15,
)
```

---

## Workflow: `spl-ext-nn-icreval`

**CLI:** `python run-training.py spl-ext-nn-icreval --bank <dir>`

Jako `smooth-icr-eval`, ale před tréninkem se každá measured nota rozšíří
na maximální počet parciálů (`extend_partials=True`). Nové parciály jsou
inicializovány splinem z okolních not. NN se tak učí přirozeně predikovat
plný počet parciálů pro všechny pozice.

### Co se mění oproti `spl-nn-icreval`

```
Krok [4] spline_fix dostane navíc extend_partials=True:

    apply_spline_fix_bank(notes,
                          smooth_all      = True,
                          extend_partials = True,   ← přidáno
                          auto_anchors    = N)

Výsledek: každá measured nota má plný počet parciálů (max přes všechny measured).
NN trénuje na kompletních harmonických targetech od prvního epoch.
```

### API

```python
model, out_path = run(
    bank_dir        = "C:/SoundBanks/IthacaPlayer/vv-rhodes",
    out_path        = "soundbanks/params-vv-rhodes-smooth-ext-icr-eval.json",
    epochs          = 5000,
    auto_anchors    = 12,
    icr_patience    = 15,
    extend_partials = True,
)
```

---

## Workflow: `spl-icrtarget-nn-icreval`

**CLI:** `python run-training.py spl-icrtarget-nn-icreval --bank <dir>`

Rozšíření `spl-nn-icreval` o **ICR round-trip korekci targetů**.
Místo spline-smooth measured params dostane NN jako targety hodnoty
re-extrahované z ICR-renderovaných zvuků. NN konverguje k tomu, co ICR
skutečně produkuje — ne k tomu, co extrakce naměřila z reálného klavíru.

### Proč round-trip

Extrakce měří reálný nástroj. ICR má vlastní přenosovou funkci — stejné
parametry renderuje jako zvuk, který extraktor změří jinak (systematický
offset per parametr). Trénink na `params_rt` tento offset koriguje:

```
Bez round-tripu:   NN → params → ICR → zvuk ≠ reálný klavír
                   (NN se naučila kompenzovat ICR offset, ale špatně)

S round-tripem:    NN → params_rt → ICR → zvuk ≈ cílový zvuk
                   (NN se naučila přesně co ICR potřebuje)
```

`spectral_eq` je z round-tripu vyloučeno (ICR renderuje s neutrálním EQ).

### Schéma

```
WAV soubory
    │
    ▼
[1] ParamExtractor
    ▼
[2] StructuralOutlierFilter
    ▼
[3] EQFitter
    │
    │  params (measured, raw)           ← -pre-smooth.json
    ├──────────────────────────────────────────────────────────────┐
    ▼                                                              │ orig_samples
[4] spline_fix(smooth_all, auto_anchors)                           │
    │  → smooth_params                  ← -pre-smooth-spline.json  │
    ▼                                                              │
[5] ICRRoundTripProcessor.process(smooth_params)                   │
    │  měřené noty → ICR render (neutral EQ) → re-extract          │
    │  → params_rt                      ← -pre-smooth-rt.json      │
    │  spectral_eq zachováno z smooth_params                       │
    ▼                                                              │
[6] ProfileTrainerEncExp.train(params_rt)                          │
    │  targety: round-trip extrahované params                      │
    │  early stop: ICR-MRSTFT                                      │
    ▼                                                              │
[7] SoundbankExporter.hybrid(model, params)  ◄─────────────────────┘
    │  measured: orig_samples (RAW)
    │  NN pozice: NN výstup
    ▼
[8] SoundbankExporter.pure_nn()
    ▼
VÝSTUP:
  params-{bank}-spl-icrtarget-nn-icreval.json          ← hybrid
  params-{bank}-spl-icrtarget-nn-icreval-pure-nn.json  ← čistá NN
  (+ intermediate: -pre-smooth.json, -pre-smooth-spline.json, -pre-smooth-rt.json)
```

### API

```python
model, out_path = run(
    bank_dir       = "C:/SoundBanks/IthacaPlayer/vv-rhodes",
    out_path       = "soundbanks/params-vv-rhodes-spl-icrtarget-nn-icreval.json",
    epochs         = 5000,
    auto_anchors   = 12,
    icr_patience   = 15,
    icr_round_trip = True,
)
```

### Časová náročnost

Round-trip přidává před trénink jeden průchod ICR renderem a re-extrakcí
(~5–15 min podle počtu measured not a hardware). Zbytek pipeline identický
se `smooth-icr-eval`.

---

## Workflow: `spl-ext-icrtarget-nn-icreval`

**CLI:** `python run-training.py spl-ext-icrtarget-nn-icreval --bank <dir>`

Kombinace `spl-ext-nn-icreval` a `spl-icrtarget-nn-icreval` — měřené noty jsou
nejprve rozšířeny na plný počet parciálů, vyhlazeny splinem a pak projdou ICR
round-tripem. NN dostane jako targety nejkompletnější a nejpřesnější data:
kompletní harmonická struktura + korekce ICR přenosové funkce.

### Co se mění oproti `spl-icrtarget-nn-icreval`

```
Krok [4] spline_fix s extend_partials=True + round-trip:

    apply_spline_fix_bank(notes,
                          smooth_all      = True,
                          extend_partials = True,   ← měřené noty mají plný počet parciálů
                          auto_anchors    = N)
    → ICRRoundTripProcessor.process(smooth_params)  ← round-trip na rozšířených datech
```

Round-trip tak koriguje ICR offset i pro vyšší parciály, které byly doplněny splinem.

### API

```python
model, out_path = run(
    bank_dir        = "C:/SoundBanks/IthacaPlayer/vv-rhodes",
    out_path        = "soundbanks/params-vv-rhodes-spl-ext-icrtarget-nn-icreval.json",
    epochs          = 5000,
    auto_anchors    = 12,
    icr_patience    = 15,
    extend_partials = True,
    icr_round_trip  = True,
)
```

---

## Legacy workflows

Tyto workflows jsou zachovány pro zpětnou kompatibilitu, ale pro nové banky
se doporučuje použít výše popsané `icr-eval` varianty.

### `simple`

```
extract → filter → EQ → export JSON  (bez NN, ~15 min)
```

```python
from training.pipeline_simple import run
out_path = run(bank_dir, out_path, workers=8, sr_tag="f48")
```

```bash
python run-training.py simple --bank C:/SoundBanks/IthacaPlayer/ks-grand
```

### `nn`

```
extract → filter → EQ → ProfileTrainerEncExp → hybrid  (~30 min)
Early stop: val data-loss (ne ICR-MRSTFT)
```

```bash
python run-training.py nn --bank C:/SoundBanks/IthacaPlayer/vv-rhodes
```

### `full`

```
extract → filter → EQ → ProfileTrainer → MRSTFTFinetuner → hybrid  (~60 min)
```

```bash
python run-training.py full --bank C:/SoundBanks/IthacaPlayer/ks-grand
```

### `experimental`

Jako `nn`, ale za NN tréninkem následuje `MRSTFTFinetuner` (200 epoch,
Python DifferentiableRenderer). Ponecháno jako legacy.

---

## Srovnání: kdy použít který workflow

| Situace | Doporučený workflow |
|---------|---------------------|
| Baseline — rychlé ověření, nová banka | `raw-nn-icreval` |
| Produkční banka — nejhladší interpolace | `spl-nn-icreval` |
| Nástroj s bohatým harmonickým obsahem (piano) | `spl-ext-nn-icreval` |
| Maximální přesnost syntézního výstupu | `spl-icrtarget-nn-icreval` |
| Maximální přesnost + plný harmonický obsah | `spl-ext-icrtarget-nn-icreval` |

---

## Vlastní workflow (příklady)

### Přeskočit NN, jen vyexportovat s jiným target_rms

```python
from training.modules.extractor  import ParamExtractor
from training.modules.eq_fitter  import EQFitter
from training.modules.exporter   import SoundbankExporter

params = ParamExtractor().extract_bank("C:/SoundBanks/IthacaPlayer/ks-grand")
params = EQFitter().fit_bank(params, "C:/SoundBanks/IthacaPlayer/ks-grand")

SoundbankExporter().from_params(params, "soundbanks/custom.json", sr=44100)
```

### Použít existující model, přeskočit extrakci

```python
from training.modules.profile_trainer_exp import ProfileTrainerEncExp
from training.modules.exporter            import SoundbankExporter
import json

with open("soundbanks/params-vv-rhodes-pre-smooth.json") as f:
    params = json.load(f)

model = ProfileTrainerEncExp().load("training/profile-vv-rhodes.pt")
SoundbankExporter().hybrid(model, params, "soundbanks/new-hybrid.json")
```

### Spustit pouze round-trip korekci na existující smooth bance

```python
from training.modules.icr_round_trip import ICRRoundTripProcessor
import json
from pathlib import Path

smooth_params = json.loads(Path("soundbanks/params-vv-rhodes-pre-smooth-spline.json").read_text())
# (smooth_params musí mít strukturu params dict se "samples" klíčem)

rt = ICRRoundTripProcessor(icr_exe="build/bin/Release/ICR.exe", sr=48000)
params_rt = rt.process(smooth_params, workers=8)
```

### Generovat sample banku z NN pro celý rozsah

```python
from training.modules.generator           import SampleGenerator
from training.modules.profile_trainer_exp import ProfileTrainerEncExp

model = ProfileTrainerEncExp().load("training/profile-ks-grand.pt")
SampleGenerator().generate_bank(
    model,
    out_dir    = "generated/ks-grand-v2/",
    midi_range = (21, 108),
    vel_count  = 8,
    beat_scale = 1.2,
)
```
