# Roadmap — ICR Training Pipeline

Naming convention: `<target-prep>-nn-<icr-role>`
- `icrtarget` = ICR aktivně generuje trénovací targety (round-trip)
- `icreval` = ICR řídí pouze early stop

---

## Kde jsme

Projekt se vyvíjel ve dvou paralelních liniích:

**Linie A — NN training pipeline:** NN predikuje fyzikální parametry z (midi, vel).
Zásadní strukturální strop: loss je MSE na číslech, ne na zvuku. ICR-MRSTFT
řídí jen early stop. Každé vylepšení naráží na tento strop, dokud nebude
loss diferenciabilní (viz surrogate — dlouhodobé).

**Linie B — Instrument DNA:** Generování soundbank bez NN, přímo z fyzikálních zákonů
fitovaných na dobrých anchor notách. Motivováno diagnostikou Rhodes: 87 % extraktů
je degenerovaných → NN se učí šum. Alternativa: nespoléhat na úplnost extrakce,
ale explicitně oddělit fyzikální prior od nástrojového charakteru.

**Aktuální stav:** pl-grand-dna.json vygenerována (5.6 MB, 704 not). Čeká na
poslechové ověření. NN pipeline stále aktivní pro stávající targety.

---

## Dokončeno

### Instrument DNA — extrakce a generování soundbank

**Diagnóza Rhodes (výchozí bod)**
Analýzou `vv-rhodes-raw-nn-icreval-hybrid.json` zjištěno:
87 % měřených not má degenerované parametry (beat=0, a1=1.0, tau1≈tau2).
Plausibilní noty: pouze 48/704, v pásmu midi 71–94.
*Proč:* ICR renderuje 3s WAVy → `tau2_max=2.7s` → bi-exp podmínka `tau2/tau1>3.0`
nikdy neprošla pro Rhodes (tau2~30s). Beat detection je post-hoc a
oscilace kontaminují decay fit. NN se učila z 87 % šumu — výsledek byl nevyhnutelný.

**`tools/analyze_extraction.py`**
Diagnostický CLI pro hodnocení kvality extrakce z params JSON.
*Proč:* Bez objektivní metriky (bi-exp%, beat%, B_ok%) nebylo možné porovnat
stávající vs. vylepšený extraktor ani cíleně opravit selhání.
Výstup: overall statistiky + per-register + per-velocity + per-MIDI heatmap.

**`tools/anchor_helper.py`**
Textový REPL pro ruční anotaci anchor not s quality score 0.0–1.0.
*Proč:* InstrumentDNA potřebuje explicitní označení dobrých not. Auto-screener
zachytí zjevně degenerované noty, ale hraniční případy (quality 0.5–0.8)
vyžadují lidský úsudek. Helper umožňuje rychlou anotaci bez GUI.
Podporuje multi-bank (více zdrojových bank jednoho nástroje).

**Extractor v2 — multi-start bi-exp fit**
`training/modules/extractor.py`: 4 diverse inicializace `(a1, tau1, tau2)`,
`tau1_max=20s` (bylo 5s), relaxed criterion `tau2/tau1>1.3` (bylo 3.0),
`tau2_max=t[-1]*0.9`, threshold zlepšení residuálu >15 %.
*Proč:* Původní jednobodový start konvergoval k lokálnímu minimu nebo vůbec.
Uvolnění `tau1_max` umožnilo zachytit basové noty s pomalým decayem.
*Výsledek na pl-grand:* bi-exp 59 % → 77 %, beat 100 %, B_ok 92 %.

**`training/modules/instrument_dna.py` + `soundbanks/pl-grand-dna.json`**
InstrumentDNA: 6 fyzikálních zákonů (WLS v log-prostoru), GP residuals
(sklearn RBF kernel, fallback WLS), velocity enforcer (isotonic regression), export JSON.
*Proč:* Namísto interpolace špatných extraktů fitovat pouze na anchor notách
(quality≥0.5) — fyzikální zákony drží globální konzistenci, GP zachytí
specifický charakter nástroje.
*Výsledky:* 520/704 anchor not, fit 4.5s. B: 1.24e-4→5.9e-4, tau1: 0.028→1.45s,
beat: 0.09→0.45 Hz. Výstup: `soundbanks/pl-grand-dna.json` (5.6 MB, 704 not).
Detailní popis → `docs/INSTRUMENT_DNA.md`.

---

### NN training pipeline

**smooth_midi penalty rozšířena na všechny velocity**
Dříve jen `vel=4`; nyní průměr přes všech 8 velocity vrstev.
*Proč:* Penalty na jedné velocity vrstvě nedostatečně potlačovala neplynulosti
v ostatních vrstvách — zvláště viditelné u tau2 v krajích rozsahu.

**Workflow přejmenování**
Nová konvence `<target>-nn-<icr-role>`:
`raw-nn-icreval`, `spl-nn-icreval`, `spl-ext-nn-icreval`,
`spl-icrtarget-nn-icreval`, `spl-ext-icrtarget-nn-icreval`.
*Proč:* Původní názvy (full-spline, b-spline) neodrážely způsob přípravy targetů
ani roli ICR — bylo obtížné porovnat výsledky běhů mezi sebou.

**ICR round-trip (`spl-icrtarget-nn-icreval`)**
`smooth_params → ICR render (neutral EQ) → re-extract → params_rt` jako trénovací targety.
*Proč:* NN optimalizovala čísla, která ICR syntetizovalo jinak než bylo perceptuálně
žádoucí. Round-trip opravil systematický offset — NN nyní konverguje k tomu, co ICR
skutečně produkuje. `spectral_eq` vyloučeno z round-tripu (ICR renderuje s neutrálními biquady).

**B vyčlenit ze NN**
`InstrumentProfileEncExp` je NoB model; B pochází z `BSplneFitter`.
*Proč:* NN predikovala B s přirozenou variabilitou přes velocity, přestože B
je fyzikálně konstantní pro danou MIDI polohu. Oddělení odstranilo zbytečný stupeň
volnosti a zlepšilo stabilitu tréninku.

**Opravy a cleanup**
- `B_g / B_v` dead code odstraněno z `profile_trainer_exp.py`
- `full-spline-icr-eval` a `b-spline-icr-eval` pipeline odstraněny (konsolidace)
- Double-spline bug: smooth pipeline volala `spline_fix` po exportu — opraveno
- `extend_partials` přesunuto pre-training (bylo post-training — pozdě)

---

## Aktuální priority

### P1 — Poslechové ověření a kalibrace pl-grand-dna.json

**Proč teď:** pl-grand-dna.json je první soundbank generovaná čistě z fyzikálních zákonů
bez NN. Poslechové ověření je jedinou objektivní zpětnou vazbou — numerické metriky
(bi-exp%, B range) jsou nutné, ale nestačí.

**Akce:**
- [ ] Načíst `soundbanks/pl-grand-dna.json` v syntetizéru, poslechnout celou klaviaturu
- [ ] Zkontrolovat: velocity přechody (monotonie), přechody mezi registry, beating charakter
- [ ] Pokud rms_gain nesedí: spustit `save_bank(..., calibrate_rms=True)` po ověření synthesizer API
- [ ] Porovnat poslechem s původní bank `vv-rhodes-raw-nn-icreval-hybrid.json` — je piano charakter věrnější?

---

### P2 — Rhodes: Instrument DNA pipeline

**Proč:** Rhodes je primárním cílem projektu. pl-grand sloužil jako prototyp —
nyní je metodologie ověřena a lze ji aplikovat na Rhodes.

**Bloker:** ICR round-trip renderuje 3s WAVy → tau2_max=2.7s → bi-exp vždy selže.
Bez prodloužení render duration nelze získat spolehlivé tau2 z Rhodes extrakce.

**Akce:**
- [ ] Prodloužit ICR render `duration_s=10–15s` pro Rhodes round-trip WAVy
- [ ] Re-extrahovat Rhodes banku s extractor v2 na delších WAVech → změřit bi-exp rate
- [ ] Pokud bi-exp > 50 %: spustit InstrumentDNA na Rhodes, vygenerovat `soundbanks/rhodes-dna.json`
- [ ] Pokud bi-exp < 50 % i na 15s WAVech: implementovat beat-first pipeline (viz P4 níže)
- [ ] Zvážit pl-upright jako doplněk (midi 21–71, 18.5s/nota) pro cross-bank anchor pooling

---

### P3 — NN pipeline: strukturální problémy

#### P3a — Loss ≠ cíl tréninku

**Problém:** Tréninkový loss je MSE na fyzikálních parametrech. Skutečný cíl je dobrý
zvuk (ICR-MRSTFT). Tyto dvě věci nejsou totožné — NN optimalizuje přesnost čísel,
ne perceptuální výsledek. ICR-MRSTFT řídí jen early stop, ne gradient.

**Výhledové řešení:** ICR surrogate model → diferenciabilní loss (viz dlouhodobé).

**Okamžitá akce:**
- [ ] A/B poslech: banka s NN EQ hlavou vs. čistý EQFitter výstup (eq_head zmrazena)
  — `eq` tvoří ~45 % val lossu; pokud rozdíl není slyšet → zmrazit nebo odstranit

#### P3b — Round-trip opravuje jen ~55 % klaviatury

**Problém:** `spl-icrtarget` koriguje ICR offset pouze pro měřené pozice (~384 not).
Pro ~320 interpolovaných pozic NN trénuje na spline-smooth targetech bez round-trip korekce.
NN dostává dvě různé "pravdy" — přechod může produkovat artifact.

**Alternativy:**
- Po round-tripu znovu spustit spline přes všechny noty (measured + interpolated)
  → i interpolované pozice dostanou RT-konzistentní hodnoty
- Round-trip na hustší sadě not (přidat syntetická měření přes SampleGenerator)

#### P3c — K_valid imbalance v lossu

**Problém:** Bass nota (MIDI 33) má K_valid~26, výšková (MIDI 90) ~4. Loss průměruje
přes parciály — gradient incentiv je 6× silnější pro bass než výšky.
Výšky jsou perceptuálně citlivé jinak (méně parciálů, ale každý je důležitý).

**Akce:**
- [ ] Poslechnout výškové noty (MIDI 80–108) v hotové bance
- [ ] Pokud výšky zní hůř → per-partial weighting nebo oddělený loss term pro K_valid ≤ 5

---

### P4 — Extrakce: beat-first pipeline (hlavně pro Rhodes)

**Proč:** Beat detection je aktuálně post-hoc — detekuje se AŽ PO fitování decay.
Beat oscilace kontaminují bi-exp fit → selhání. Pro pl-grand je beat 100 % (bez beat-first),
ale pro Rhodes s beat_hz~0.3 Hz a záznamy 3s je to strukturální selhání.

**Navrhovaný pipeline:**
```
Aktuální:   detect_peaks → STFT envelope → fit_decay (bi-exp) → detect_beat
Navrhovaný: detect_peaks → STFT envelope → detect_beat_coarse →
            beat_detrend_envelope → fit_decay (bi-exp) → refine_beat
```
- Hrubý beat z autocorrelation log-obálky (nevyžaduje mnoho dat)
- Beat-detrended obálka: `A_clean(t) = A(t) / (1 + depth·cos(2π·beat_hz·t))`
- Bi-exp fit na čisté obálce

**Akce:**
- [ ] Implementovat beat-first pipeline v `extractor.py`
- [ ] Testovat na Rhodes bance (15s WAVy) — sledovat změnu bi-exp rate
- [ ] Sekundárně zvážit Hilbert narrowband envelope pokud bi-exp < 70 % i po beat-first

**Nízká priorita pro pl-grand** (77 % bi-exp dosaženo bez beat-first).

---

### P5 — Cleanup a drobná vylepšení

**sm_vel penalizaci odstranit**
`sm_vel ≈ 0` ve všech runech — NN přirozeně produkuje hladké velocity křivky.
Penalizace nedělá nic, jen zatěžuje loss breakdown.

**ICR early-stop averaging**
ICR-MRSTFT metriky oscilují (~0.02 mezi evaly). Průměrovat poslední 2–3 skóre
před early-stop rozhodnutím → méně falešných zastavení.

**B fit bounds rozšířit**
`_fit_B_f0` má `B_upper=5e-3`. Některé bass noty mají B>5e-3 → fit clampován.
Rozšířit na `B_upper=0.05`. Nízká priorita — InstrumentDNA obchází tento problém
fitováním B zákona odspodu.

**Absolutní regularizace parametrů**
Smoothness penalty penalizuje neplynulost křivky, ale ne absolutní posunutí.
Přidat slabý L2 term na odchylku od spline targetu pro interpolované pozice.

---

## Dlouhodobé / parkovano

### ICR surrogate model — diferenciabilní loss

**Kontext:** Dokud loss není diferenciabilní přes syntézu, NN optimalizuje proxy.
Surrogate ICR by umožnil backprop přímo přes zvuk:

```
NN → params → surrogate_ICR → spektrum → MRSTFT loss → backprop do NN
```

**Relevantní práce:**
Simionato et al. (2023) — "Physics-informed differentiable method for piano modeling"
(*Frontiers Signal Processing*, DOI: 10.3389/frsip.2023.1276748) — fyzikální parametry
jako diferencovatelné vstupy do syntézního DSP modulu. Přímý vzor pro ICR surrogate.

**Předpoklady:** surrogate musí být dostatečně věrný; potřebná velká sada
(params, ICR-rendered WAV) párů. Alternativa: gradient estimation (ES/REINFORCE).

---

### Latentní vektorový model (Instrument DNA fáze 2)

Pro případ, kdy máme anchor noty z více bank stejného nástroje:
`z = Encoder(anchor_notes)` → latentní vektor zachycuje "nástrojový charakter".
Generátor: `params = Decoder(z, midi, vel, k)`.
Umožní interpolaci mezi nástroji nebo transfer vlastností.
*Předpoklad:* nejdříve ověřit základní DNA pipeline na Rhodes (P2).

---

### spectral_eq — separátní větev tréninku

EQ hlava tvoří ~45 % val lossu. Plán:
1. Zmrazit EQ hlavu během hlavního NN tréninku
2. Dotrénovat EQ hlavu zvlášť z ICR-rendered WAVs po hlavním tréninku

*Odkládáno* dokud není jasné, zda NN EQ hlava vůbec přidává hodnotu (A/B poslech — P3a).

---

### Ostatní

- **Convolution IR extraction** — viz `docs/CONVOLUTION_REVERB.md`
- **macOS port** — viz `docs/MAC_OS_CHANGES.md`
- **Resampling jako datová augmentace** — dobrá nota na midi k → time-stretch 2× dolů
  → syntetická nota k-12. Použitelné jen s nižším quality score (0.3–0.5),
  B a soundboard coupling se mezi oktávami nemění hladce.
- **NMF-based B + F0 extrakce** (Rigaud et al. 2013, JASA 133:5) — robustnější
  vůči šumovým peakům než aktuální curve_fit. Střední priorita pro budoucí extrakci.

---

## Literatura

**Fyzikální model a extrakce:**
- Chaigne & Askenfelt (1994). "Numerical simulations of piano strings." *JASA* 95(3):1631.
  DOI: 10.1121/1.409849. — fyzikální model struny, základ pro B(midi) zákon.
- Rigaud, David & Daudet (2013). "A parametric model and estimation techniques for
  the inharmonicity and tuning of the piano." *JASA* 133(5):3107. DOI: 10.1121/1.4798457.
  — NMF přístup pro B a F0.
- Giordano & Jiang (2004). *JASA* 115(2). — tau vs. velocity a harmonic order.

**Beating a obálka:**
- Weinreich (1977). *JASA* 62(6). — coupled piano strings, beating model.
- Simionato & Fasciani (2024). "Sines, Transient, Noise Neural Modeling of Piano Notes."
  *Frontiers Signal Processing.* — SIN dekompozice, fyzikálně-informovaná architektura.
- Feldman M. (2011). *Hilbert Transform Applications in Mechanical Vibration.* Wiley.
  — instantaneous amplitude, median filtering.

**Diferenciabilní syntéza:**
- Simionato et al. (2023). "Physics-informed differentiable method for piano modeling."
  *Frontiers Signal Processing.* DOI: 10.3389/frsip.2023.1276748.
  — přímý vzor pro ICR surrogate model.

**Rhodes specificky:**
- Gastinel et al. (JASA 148(5):3052, 2020). "The Rhodes electric piano: Analysis and
  simulation of the inharmonic overtones." — SLDV experimenty na tine, pickup intermodulace.
