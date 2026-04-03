# Roadmap — ICR Training Pipeline

Items ordered by priority. Updated after workflow renaming to new convention:
`raw-nn-icreval` / `spl-nn-icreval` / `spl-ext-nn-icreval` / `spl-icrtarget-nn-icreval`.

Naming convention: `<target-prep>-nn-<icr-role>`
- `icrtarget` = ICR aktivně generuje trénovací targety (round-trip)
- `icreval` = ICR řídí pouze early stop

---

## Aktivní — čeká na výsledky tréninku (spl-icrtarget-nn-icreval běží)

### 0. spectral_eq — separátní větev tréninku (plánováno)

**Kontext:** EQ hlava tvoří ~45 % val lossu a bude řešena samostatně.
Při ICR round-tripu se používají neutrální (identity) biquady — ICR renderuje
bez EQ aplikace. `spectral_eq` se přenáší z original smooth_params beze změny.

**Plán:**
- Zmrazit EQ hlavu během hlavního NN tréninku (nebo ji oddělit do vlastního optimizeru)
- Po hlavním tréninku: dotrénovat EQ hlavu zvlášť, nebo fitovat post-hoc z ICR-rendered WAVs
- `spl-icrtarget-nn-icreval` připraven pro round-trip bez EQ — základ pro tuto větev

---

### 1. EQ hlavu oddělit od hlavního lossu — střední priorita

**Pozorování (ep. 400–650):** `eq` tvoří ~45 % val lossu (eq≈2.7 vs ostatní ≤1.3).
EQFitter předvýpočítá biquady před tréninkem — NN EQ hlava dolaďuje zbytek, ale
saturuje gradient podobně jako B dřív.

**Návrh:**
- Snížit weight EQ termu v loss (např. 0.3× místo 1.0×), nebo
- Trénovat EQ hlavu separátní optimizer s nižším lr, nebo
- Zmrazit EQ hlavu po prvních N epochách (EQFitter výstup je dostatečný základ)

**Otázka k zodpovězení po A/B poslechu:** přispívá NN EQ hlava k lepšímu zvuku,
nebo jen přetěžuje trénink? Pokud bez ní zní banka stejně → kandidát na odstranění.

---

### 2. tau2 loss ve smooth pipeline — sledovat

**Pozorování (ep.350–650):** smooth/smooth-ext mají tau2≈1.2 vs icr-eval tau2≈0.8.
Spline-smoothed targety mají přísnější tau2 křivku než raw extrakty.

**Akce po tréninku:**
- Pokud tau2 vysoké i v konečné bance → snížit stiffness spline fitu pro tau2 zvlášť
- Nebo snížit loss weight tau2 v smooth pipeline

---

### 3. ICR early-stop averaging — střední priorita

**Pozorování:** ICR-MRSTFT metriky oscilují (~0.02 mezi evaly) kvůli renderovací
variabilitě. Early-stop rozhodnutí na základě jednoho evalu je hlučné.

**Návrh:** Průměrovat poslední 2–3 ICR skóre před early-stop rozhodnutím.

---

### 4. sm_vel penalizaci odstranit — nízká priorita

**Pozorování:** sm_vel ≈ 0 ve všech runech od začátku. NN přirozeně produkuje hladké
velocity křivky → penalizace nedělá nic, jen zatěžuje loss breakdown.

**Návrh:** Odstranit sm_vel weight, nebo přesunout jako diagnostický výpis mimo loss.

---

### 5. Train/val shuffle — diskutováno, odloženo

**Kontext:** Val set (65 sampů, 17 %) je deterministicky fixní split po MIDI pozicích (každá 5. nota).
Zvažovali jsme periodický resplit mezi tréninkovými cykly, aby NN viděla postupně všechna měřená data.

**Proč zatím ne:**
- Split je záměrně po MIDI (ne po samplu) — měří generalizaci do neviděných poloh; resplit by tuto vlastnost zrušil
- `_plateau_count` sleduje val_loss jako konzistentní časovou řadu; po resplitu by čísla nebyla porovnatelná → expanze hlav by se spouštěla na základě artefaktu
- ICR-MRSTFT je stejně pravý soudce; val je jen doplněk pro plateau detekci

**Možné alternativy (pokud bude NN slabě generalizovat do krajů):**
- K-fold přes MIDI (5 foldů) — 5× delší trénink
- `val_frac=0` + plateau řídit přímo z `icr_no_improve`
- Nahrát více not v extrapolační zóně (MIDI 21–32, 90–108)

---

## Dlouhodobé / parkovano

### ICR surrogate model — diferenciabilní loss

**Kontext:** Aktuálně ICR hraje ve workflow dvě role:
1. `icreval` — ICR.exe renderuje zvuk a počítá MRSTFT pro early stop (non-diferenciabilní)
2. `icrtarget` — ICR.exe generuje trénovací targety přes round-trip (pre-processing, non-diferenciabilní)

Žádná z rolí neumožňuje backpropagaci — gradient do NN nepochází z ICR.

**Návrh:** Natrénovat **surrogate model ICR** — druhou NN, která aproximuje přenosovou funkci
ICR.exe (vstup: fyzikální params → výstup: spektrum nebo mel-spektrogram). Surrogate by
umožnil diferenciabilní ICR loss:

```
NN → params → surrogate_ICR → spektrum → MRSTFT loss → backprop do NN
```

Workflow by se jmenovalo `spl-icrtarget-nn-icrloss` (ICR jako loss, ne jen eval/target).

**Předpoklady:**
- Surrogate musí být dostatečně věrný (jinak NN optimalizuje surrogate, ne reálný ICR)
- Trénink surrogate vyžaduje velkou sadu (params, ICR-rendered WAV) párů
- Alternativa: gradient estimation (ES/REINFORCE) bez explicitního surrogate

**Priorita:** nízká — `spl-icrtarget-nn-icreval` je dobrá aproximace; surrogate přináší
hodnotu hlavně pokud round-trip targety nestačí pro konvergenci.

---

- **Convolution IR extraction** — viz `docs/CONVOLUTION_REVERB.md`
- **macOS port** — viz `docs/MAC_OS_CHANGES.md`

---

## Dokončeno

- **Workflow přejmenování** — nová konvence `<target>-nn-<icr-role>`:
  `raw-nn-icreval`, `spl-nn-icreval`, `spl-ext-nn-icreval`, `spl-icrtarget-nn-icreval`.
  `icrtarget` = ICR generuje trénovací targety (round-trip); `icreval` = ICR early stop.
- **ICR round-trip (`spl-icrtarget-nn-icreval`)** — smooth_params → ICR render (neutral EQ)
  → re-extract → params_rt jako trénovací targety. NN konverguje k tomu, co ICR skutečně
  produkuje, ne k tomu, co extrakce naměřila z reálného klavíru. `spectral_eq` vyloučeno
  z round-tripu (neutrální biquady); EQ bude řešeno separátně.
- **B vyčlenit ze NN** — `InstrumentProfileEncExp` je nyní default NoB model;
  B z `BSplneFitter`; všechny workflow používají NoB automaticky.
- **B_g / B_v dead code v smooth penalty** — odstraněno z `profile_trainer_exp.py`.
- **Pipeline konsolidace** — `full-spline-icr-eval` a `b-spline-icr-eval` sloučeny
  do `spl-ext-nn-icreval` resp. odstraněny jako redundantní.
- **Double-spline bug** — smooth pipeline volala `spline_fix` po exportu i přes to,
  že NN trénovala na smooth datech; opraveno — NN výstup je nyní finální autorita.
- **extend_partials přesunuto pre-training** — parciály se rozšiřují před tréninkem
  jako součást spline-smooth kroku, ne post-export via spline_fix.
- **smooth_midi penalty rozšířena na všechny velocity** — dříve se počítala jen pro
  `vel=4`; nyní průměr přes všech 8 velocity vrstev (`profile_trainer_exp.py`).
