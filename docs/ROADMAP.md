# Roadmap — ICR Training Pipeline

Naming convention: `<target-prep>-nn-<icr-role>`
- `icrtarget` = ICR aktivně generuje trénovací targety (round-trip)
- `icreval` = ICR řídí pouze early stop

---

## P1 — Strukturální slabiny (nejvyšší priorita)

### Loss ≠ cíl tréninku

**Problém:** Tréninkový loss je MSE na fyzikálních parametrech. Skutečný cíl je dobrý
zvuk (ICR-MRSTFT). Tyto dvě věci nejsou totožné — NN optimalizuje přesnost čísel,
ne perceptuální výsledek. ICR-MRSTFT řídí jen early stop, ne gradient. Každé jiné
zlepšení (round-trip, smooth targety, EQ) naráží na tento strop.

**Why:** NN se může naučit parametry, které jsou numericky blízko targetům, ale ICR
ze syntetizuje jinak než je perceptuálně optimální. MSE nereflektuje, které parametry
jsou perceptuálně důležité (tau1 vs. malé A0 u tichého parciálu).

**How to apply:** Každá nová optimalizace by měla být hodnocena primárně poslechem
a ICR-MRSTFT, ne poklesem val data-loss. Val data-loss je proxy, ne cíl.

**Výhledové řešení:** ICR surrogate model → diferenciabilní loss (viz P-long).

---

### EQ hlava: přínos vs. cena nejasné

**Problém:** `eq` tvoří ~45 % val lossu. EQFitter předvýpočítá biquady před tréninkem —
NN EQ hlava dolaďuje zbytek, ale saturuje gradient a zdražuje každý epoch. V round-tripu
EQ vynecháváme úplně (neutrální biquady) — implicitní přiznání, že nevíme, co NN EQ hlava
přidává. Pokud přínos existuje, je skrytý za dominancí EQ v lossu.

**Why:** EQ je komplexní křivka (64 bodů × velocity) — těžký terč pro MSE. EQFitter
výstup je přitom already rozumný základ. NN dolaďuje druhý řád efektu, ale platí za to
44 % gradient kapacity.

**How to apply:** Před dalším tréninkem provést A/B poslech: banka s NN EQ hlavou vs.
banka s čistým EQFitter výstupem (eq_head zmrazena). Pokud rozdíl není slyšet → zmrazit
nebo odstranit.

**Akce:**
- A/B poslech po dokončení běžícího tréninku
- Zmrazit `eq_head` po prvních N epochách (EQFitter výstup jako základ)
- Nebo: separátní optimizer pro `eq_head` s 10× nižším lr
- Nebo: snížit loss weight EQ termu na 0.1–0.3×

---

## P2 — Vysoká priorita (přímo ovlivňuje kvalitu výstupu)

### Round-trip opravuje jen ~55 % klaviatury

**Problém:** `spl-icrtarget` koriguje ICR offset pouze pro měřené pozice (~384 not).
Pro ~320 interpolovaných pozic NN trénuje na spline-smooth targetech bez round-trip
korekce. NN dostává dvě různé "pravdy" — a přechod mezi nimi se může projevit jako
artifact na místech, kde měřená data přecházejí v interpolaci.

**Why:** Round-trip zpracovává jen `measured` not (filtr `not v.get("_interpolated")`).
Spline-interpolované targety v `smooth_params` jsou geometricky hladké, ale neodpovídají
ICR přenosové funkci.

**How to apply:** Sledovat v bankovním exportu artefakty na přechodech measured/interpolated.
Pokud jsou viditelné → zvážit round-trip na rozšířené sadě (viz alternativy níže).

**Alternativy:**
- Po round-tripu znovu aplikovat spline přes *všechny* noty (measured + interpolated)
  tak, aby i interpolované pozice dostaly RT-konzistentní hodnoty
- Nebo: round-trip na hustší sadě not (přidat syntetická měření přes SampleGenerator)

---

### Re-extrakce v round-tripu přidává vlastní šum

**Problém:** Round-trip pipeline: `smooth_params → ICR → WAV → extractor → params_rt`.
Extractor byl kalibrován na reálný klavír — na ICR-rendered WAV (periodický, bez šumu
těla, bez pickup charakteristiky) může dávat systematicky odlišné hodnoty. Compound noise:
ICR offset + extractor artifacts na syntetickém zvuku.

**Why:** Extractor předpokládá typické piano WAV (decay, noise floor, inharmonicita
v přirozených mezích). ICR výstup je čistší — přesnější peak detection, ale jiné
SNR charakteristiky. Výsledné `params_rt` mohou být méně věrné než `smooth_params`
pro některé parametry (noise, tau2 u tichých not).

**How to apply:** Po dokončení `spl-icrtarget` běhu porovnat `params_rt` vs `smooth_params`
per-parametr — hledat systematické odchylky, zejména u noise a tau2 v krajích rozsahu.

---

### K_valid imbalance v lossu

**Problém:** Basová nota (MIDI 33) má K_valid ~26, výšková (MIDI 90) ~4. Loss přes parciály
se průměruje, ale počet parciálů se liší 6×. NN má silnější gradient incentiv být přesná
v basu než ve výškách — přitom výšky jsou perceptuálně citlivé jinak (méně parciálů,
ale každý je důležitý).

**Why:** `_compute_data_loss_exp` průměruje MSE přes dostupné parciály, ale neváhá
podle K_valid ani perceptuální důležitosti.

**How to apply:** Zkontrolovat kvalitu výškových not (MIDI 80–108) v hotové bance poslechem.
Pokud výšky zní hůř než střed → zvážit per-partial weighting nebo oddělený loss term
pro K_valid ≤ 5.

---

## P3 — Střední priorita

### tau2 loss ve smooth pipeline — sledovat

**Pozorování (ep. 350–650):** smooth/spl-ext mají tau2 ≈ 1.2 vs raw-nn-icreval tau2 ≈ 0.8.
Spline-smoothed targety mají přísnější tau2 křivku než raw extrakty.

**Akce po tréninku:**
- Pokud tau2 vysoké i v konečné bance → snížit stiffness spline fitu pro tau2 zvlášť
- Nebo snížit loss weight tau2 v smooth pipeline

---

### ICR early-stop averaging

**Pozorování:** ICR-MRSTFT metriky oscilují (~0.02 mezi evaly) kvůli renderovací
variabilitě. Early-stop rozhodnutí na základě jednoho evalu je hlučné.

**Návrh:** Průměrovat poslední 2–3 ICR skóre před early-stop rozhodnutím.

---

### Chybí absolutní regularizace parametrů

**Problém:** Smoothness penalty penalizuje druhé diference (neplynulost křivky), ale
ne absolutní hodnoty. NN může produkovat hladkou, ale systematicky posunutou křivku
— penalizace to nezachytí. Slabý L2 weight decay na vahách modelu může pomoct, ale
neřeší posun ve výstupním prostoru.

**Návrh:** Přidat slabý L2 term na odchylku NN predikce od spline targetu pro
interpolované pozice (reference anchor regularizace).

---

## P4 — Nízká priorita / cleanup

### sm_vel penalizaci odstranit

**Pozorování:** sm_vel ≈ 0 ve všech runech od začátku. NN přirozeně produkuje hladké
velocity křivky → penalizace nedělá nic, jen zatěžuje loss breakdown.

**Návrh:** Odstranit sm_vel weight, nebo přesunout jako diagnostický výpis mimo loss.

---

### Train/val shuffle — diskutováno, odloženo

**Kontext:** Val set (65 sampů, 17 %) je deterministicky fixní split po MIDI pozicích.
Zvažovali jsme periodický resplit.

**Proč zatím ne:**
- Split je záměrně po MIDI — měří generalizaci do neviděných poloh
- `_plateau_count` sleduje val_loss jako konzistentní časovou řadu
- ICR-MRSTFT je pravý soudce; val je jen doplněk pro plateau detekci

**Možné alternativy (pokud NN slabě generalizuje do krajů):**
- K-fold přes MIDI (5 foldů) — 5× delší trénink
- `val_frac=0` + plateau řídit přímo z `icr_no_improve`
- Nahrát více not v extrapolační zóně (MIDI 21–32, 90–108)

---

## Dlouhodobé / parkovano

### spectral_eq — separátní větev tréninku

**Kontext:** EQ hlava tvoří ~45 % val lossu a bude řešena samostatně po vyřešení P1/P2.
Při ICR round-tripu se používají neutrální biquady — ICR renderuje bez EQ aplikace.
`spectral_eq` se přenáší z `smooth_params` beze změny.

**Plán:**
- Zmrazit EQ hlavu během hlavního NN tréninku
- Po hlavním tréninku: dotrénovat EQ hlavu zvlášť z ICR-rendered WAVs
- Workflow: `spl-icrtarget-nn-icreval` jako základ (round-trip bez EQ hotový)

---

### ICR surrogate model — diferenciabilní loss

**Kontext:** Aktuálně ICR hraje dvě role:
1. `icreval` — ICR.exe počítá MRSTFT pro early stop (non-diferenciabilní)
2. `icrtarget` — ICR.exe generuje trénovací targety přes round-trip (pre-processing)

Žádná z rolí neumožňuje backpropagaci. Tréninkový loss zůstává MSE na parametrech,
ne na zvuku — to je strukturální strop (viz P1).

**Návrh:** Natrénovat surrogate model ICR — druhou NN aproximující přenosovou funkci
ICR.exe (vstup: fyzikální params → výstup: spektrum nebo mel-spektrogram):

```
NN → params → surrogate_ICR → spektrum → MRSTFT loss → backprop do NN
```

Workflow: `spl-icrtarget-nn-icrloss`

**Předpoklady:**
- Surrogate musí být dostatečně věrný
- Trénink surrogate vyžaduje velkou sadu (params, ICR-rendered WAV) párů
- Alternativa: gradient estimation (ES/REINFORCE) bez explicitního surrogate

---

- **Convolution IR extraction** — viz `docs/CONVOLUTION_REVERB.md`
- **macOS port** — viz `docs/MAC_OS_CHANGES.md`

---

## Dokončeno

- **smooth_midi penalty rozšířena na všechny velocity** — dříve jen `vel=4`;
  nyní průměr přes všech 8 velocity vrstev (`profile_trainer_exp.py`).
- **Workflow přejmenování** — nová konvence `<target>-nn-<icr-role>`:
  `raw-nn-icreval`, `spl-nn-icreval`, `spl-ext-nn-icreval`, `spl-icrtarget-nn-icreval`,
  `spl-ext-icrtarget-nn-icreval`.
- **ICR round-trip (`spl-icrtarget-nn-icreval`)** — smooth_params → ICR render (neutral EQ)
  → re-extract → params_rt jako trénovací targety. NN konverguje k tomu, co ICR skutečně
  produkuje. `spectral_eq` vyloučeno z round-tripu.
- **B vyčlenit ze NN** — `InstrumentProfileEncExp` je NoB model; B z `BSplneFitter`.
- **B_g / B_v dead code v smooth penalty** — odstraněno z `profile_trainer_exp.py`.
- **Pipeline konsolidace** — `full-spline-icr-eval` a `b-spline-icr-eval` odstraněny.
- **Double-spline bug** — smooth pipeline volala `spline_fix` po exportu; opraveno.
- **extend_partials přesunuto pre-training** — rozšíření parciálů před tréninkem.
