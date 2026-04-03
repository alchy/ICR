# Roadmap — ICR Training Pipeline

Items ordered by priority. Parked here for evaluation after current training experiment concludes.

---

## Po dokončení tréninku (icr-eval / smooth-icr-eval / full-spline-icr-eval)

### 1. B vyčlenit ze NN — vysoká priorita

**Pozorování:** B loss dominuje všechny runy (6–8×), přitom inharmonicita je fyzikálně
velocity-independent — závisí na struně, ne na síle úderu.

**Návrh:**
- Fit B jako 1D spline přes MIDI z naměřených not (před NN tréninkem)
- NN predikuje B vůbec — o 1 výstup méně
- Gradient se přesměruje na tau1/tau2/A0/eq kde velocity hraje skutečnou roli

**Očekávaný efekt:** výrazné snížení celkové val loss, lepší kapacita pro ostatní parametry.

---

### 2. tau2 loss ve spline pipelinech — sledovat

**Pozorování (ep.350):** smooth/full-spline mají tau2=1.26 vs icr-eval tau2=0.84, a roste.
Spline-smoothed targety mají přísnější tau2 křivku než raw extrakty.

**Akce po tréninku:**
- Pokud tau2 do ep.600 neotočí → snížit stiffness spline fitu pro tau2 zvlášť
- Nebo snížit loss weight tau2 v smooth pipeline

---

### 3. sm_vel penalizaci odstranit — nízká priorita

**Pozorování:** sm_vel ≈ 0 ve všech runech od začátku. NN přirozeně produkuje hladké
velocity křivky → penalizace nedělá nic, jen zatěžuje loss breakdown.

**Návrh:** Odstranit sm_vel weight, nebo přesunout jako diagnostický výpis mimo loss.

---

### 4. ICR early-stop averaging — střední priorita

**Pozorování:** ICR-MRSTFT metriky oscilují (~0.02 mezi evaly) kvůli renderovací
variabilitě. Early-stop rozhodnutí na základě jednoho evalu je hlučné.

**Návrh:** Průměrovat poslední 2–3 ICR skóre před early-stop rozhodnutím.

---

### 5. full-spline-icr-eval vs smooth-icr-eval — rozhodnutí

**Pozorování (ep.350):** ICR-MRSTFT rozdíl pouze 0.005 — v rámci šumu.
`extend_partials` nepřináší měřitelný benefit v metrice.

**Akce po tréninku:** A/B poslech extrapolační zóny (MIDI 21–32, 90–108).
Pokud bez auditelného rozdílu → full-spline-icr-eval zrušit, snížit počet workflows na 2.

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
