# Roadmap — ICR Training Pipeline

Items ordered by priority. Updated after consolidation to 3 workflows
(icr-eval / smooth-icr-eval / smooth-ext-icr-eval).

---

## Aktivní — čeká na výsledky tréninku (smooth-rt-icr-eval běží)

### 0. spectral_eq — separátní větev tréninku (plánováno)

**Kontext:** EQ hlava tvoří ~45 % val lossu a bude řešena samostatně.
Při ICR round-tripu se používají neutrální (identity) biquady — ICR renderuje
bez EQ aplikace. `spectral_eq` se přenáší z original smooth_params beze změny.

**Plán:**
- Zmrazit EQ hlavu během hlavního NN tréninku (nebo ji oddělit do vlastního optimizeru)
- Po hlavním tréninku: dotrénovat EQ hlavu zvlášť, nebo fitovat post-hoc z ICR-rendered WAVs
- `smooth-rt-icr-eval` připraven pro round-trip bez EQ — základ pro tuto větev

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

### 5. Smooth penalty pouze na vel=4 — střední priorita

**Pozorování:** MIDI smoothness se počítá výhradně při `vel=4`. NN se tak učí být
hladká přes klaviaturu jen pro střední velocity; `vel=0` nebo `vel=7` nejsou penalizovány.

**Návrh:** Rozšířit smooth_midi penalty na více velocity (např. 0, 4, 7) nebo
průměrovat přes celý vel grid.

---

### 6. Train/val shuffle — diskutováno, odloženo

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

- **Convolution IR extraction** — viz `docs/CONVOLUTION_REVERB.md`
- **macOS port** — viz `docs/MAC_OS_CHANGES.md`

---

## Dokončeno

- **B vyčlenit ze NN** — `InstrumentProfileEncExp` je nyní default NoB model;
  B z `BSplneFitter`; všechny 3 workflow používají NoB automaticky.
- **B_g / B_v dead code v smooth penalty** — odstraněno z `profile_trainer_exp.py`.
- **Pipeline konsolidace** — `full-spline-icr-eval` a `b-spline-icr-eval` sloučeny
  do `smooth-ext-icr-eval` resp. odstraněny jako redundantní.
- **Double-spline bug** — smooth pipeline volala `spline_fix` po exportu i přes to,
  že NN trénovala na smooth datech; opraveno — NN výstup je nyní finální autorita.
- **extend_partials přesunuto pre-training** — parciály se rozšiřují před tréninkem
  jako součást spline-smooth kroku, ne post-export via spline_fix.
