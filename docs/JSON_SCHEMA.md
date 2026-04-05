# ICR — JSON Soundbank Schema

Referenční přehled všech klíčů v PianoCore soundbank JSON.
Generováno exporterem (`training/modules/exporter.py`), načítáno `piano_core.cpp::loadBankJson()`.

---

## Struktura souboru

```json
{
  "source":     "nn-hybrid:model",
  "sr":         48000,
  "target_rms": 0.06,
  "vel_gamma":  0.7,
  "k_max":      60,
  "rng_seed":   0,
  "duration_s": 3.0,
  "n_notes":    704,
  "notes": {
    "m060_vel3": { ... },
    ...
  }
}
```

---

## 1. Bank-level klíče (top-level)

| Klíč | Typ | Popis | Čte C++ |
|---|---|---|---|
| `source` | string | Označení původu: `"soundbank:params"` / `"nn-hybrid:model"` / `"nn-pure:model"` | ne |
| `sr` | int | Sample rate (44100 nebo 48000) | ne (informativní) |
| `target_rms` | float | Cílová RMS úroveň pro normalizaci | ne (bake do `rms_gain`) |
| `vel_gamma` | float | Gamma křivka velocity (default 0.7) | ne (bake do `rms_gain`) |
| `k_max` | int | Maximální počet parciálů (60) | ne |
| `rng_seed` | int | Základní seed pro generování fází φ a φ_diff | ne (bake do `phi`, `phi_diff`) |
| `duration_s` | float | Délka renderovaných not v sekundách | ne |
| `n_notes` | int | Celkový počet not v bance | ne |
| `notes` | object | Slovník not, klíč = `"m{midi:03d}_vel{vel}"` | ✓ |

---

## 2. Note-level klíče (v každé notě)

### 2a. Identifikace a fyzika (skaláry)

| Klíč | Typ | Závislost | Původ | C++ synth | Editor | SysEx | Popis |
|---|---|---|---|---|---|---|---|
| `midi` | int | — | extrakce | meta | meta | — | MIDI nota 21–108 |
| `vel` | int | — | extrakce | meta | meta | — | Velocity index 0–7 |
| `f0_hz` | float | per-(midi,vel) | extraktor → NN | ✓ (noteOn) | ✓ editovatelný | ✓ | Základní frekvence v Hz |
| `B` | float | **per-MIDI** (stejná hodnota pro všech 8 vel) | BSplneFitter (1D spline přes MIDI) | ✓ (přepočet `f_hz`) | ✓ editovatelný | ✓ (→ všechny vel) | Koeficient inharmonicity; `f_k = k·f0·√(1+B·k²)` |
| `K_valid` | int | per-(midi,vel) | exporter (délka `partials`) | ✗ ignoruje | meta | — | Počet validních parciálů; C++ čte délku `partials` array |

### 2b. Obálka a šum

| Klíč | Typ | Závislost | Původ | C++ synth | Editor | SysEx | Popis |
|---|---|---|---|---|---|---|---|
| `attack_tau` | float | per-(midi,vel) | extraktor → NN | ✓ | ✓ editovatelný | ✓ | Časová konstanta náběhu šumu (s) |
| `A_noise` | float | per-(midi,vel) | extraktor → NN | ✓ | ✓ editovatelný | ✓ | Amplituda šumové složky |
| `noise_centroid_hz` | float | per-(midi,vel) | extraktor (`noise.centroid_hz`) | ✓ (1-pole IIR) | ✓ editovatelný | ✓ | Frekvence přechodu 1-pólového dolnopropustného filtru pro barvení šumu; výchozí 3000 Hz |

### 2c. Normalizace a stereo

| Klíč | Typ | Závislost | Původ | C++ synth | Editor | SysEx | Popis |
|---|---|---|---|---|---|---|---|
| `rms_gain` | float | per-(midi,vel) | exporter (RMS kalibrace) | ✓ | ✓ editovatelný | ✓ | Zesílení pro dosažení `target_rms`; přepočítáno při exportu |
| `phi_diff` | float | per-(midi,vel) | exporter (RNG: `uniform(0, 2π)`) | ✓ | ✗ | ✗ | Fázový offset mezi strunami 1 a 2 v 2-string modelu; generuje stereo dekorelaci |

### 2d. Spektrální EQ

| Klíč | Typ | Závislost | Původ | C++ synth | Editor | SysEx | Popis |
|---|---|---|---|---|---|---|---|
| `eq_biquads` | array | per-(midi,vel) | EQFitter → exporter (biquad fit) | ✓ (IIR filtr) | ✓ (EQ editor) | ✗ | Kaskáda 5 min-phase IIR biquad sekcí; formát viz níže |
| `spectral_eq` | object | per-(midi,vel) | EQFitter (LTASE měření) | ✗ ignoruje | ✓ (EQ editor) | ✗ | Surová EQ křivka `{freqs_hz: [...], gains_db: [...], stereo_width_factor: float}` použitá pro biquad fit; uložena pro re-fit po editaci |

`spectral_eq` obsahuje také klíč `stereo_width_factor` (float, výchozí 1.0) — ratio levého vs. pravého kanálu pro stereo rozšíření EQ. Produkuje ho `eq_fitter.py`, čte `synthesizer.py`; C++ player ho ignoruje (stereo je řešeno 2-string modelem a pan záběrem).

### 2e. Metadata

| Klíč | Typ | Závislost | Původ | C++ synth | Editor | SysEx | Popis |
|---|---|---|---|---|---|---|---|
| `_interpolated` | bool | per-(midi,vel) | exporter (z `generate_profile_exp`) | `s.value(..., false)` | ✗ | ✗ | `true` = nota generována NN; absence klíče = `false` = měřená nota. Zobrazeno v GUI jako `[NN]` / `[MEASURED]` |

---

## 3. Partial-level klíče (v poli `partials`)

Každá nota má pole `partials` — jeden objekt per parciál (max 60).

| Klíč | Typ | Závislost | Původ | C++ synth | Editor | SysEx | Popis |
|---|---|---|---|---|---|---|---|
| `k` | int | per-(midi,vel,k) | extraktor | ✓ (index pro B-recompute) | meta | — | Index parciálu, 1-based; pro longitudinální parciály může být nelineární |
| `f_hz` | float | per-(midi,vel,k) | `k·f0·√(1+B·k²)` bake | ✓ (oscilátor) | ✓ editovatelný | ✓ | Frekvence parciálu v Hz; přepočítána při `setNoteParam("B")` |
| `A0` | float | per-(midi,vel,k) | extraktor → NN | ✓ | ✓ editovatelný | ✓ | Amplituda parciálu |
| `tau1` | float | per-(midi,vel,k) | extraktor → NN | ✓ | ✓ editovatelný | ✓ | Rychlá časová konstanta bi-exp obálky (s) |
| `tau2` | float | per-(midi,vel,k) | extraktor → NN | ✓ | ✓ editovatelný | ✓ | Pomalá časová konstanta bi-exp obálky (s); `tau2 ≥ tau1` |
| `a1` | float | per-(midi,vel,k) | extraktor → NN | ✓ | ✓ editovatelný | ✓ | Váha rychlé složky: `a1·exp(−t/τ1) + (1−a1)·exp(−t/τ2)`; `a1 = 1.0` = mono-exponenciální |
| `beat_hz` | float | per-(midi,vel,k) | extraktor → NN | ✓ | ✓ editovatelný | ✓ | Frekvence beatingu (rozladění strun) v Hz; `0` = mono struna |
| `phi` | float | per-(midi,vel,k) | exporter (RNG: `uniform(0, 2π)`) | ✓ | ✓ editovatelný | ✓ | Počáteční fáze struna 1 (rad) |

---

## 3b. Klíče pouze v extraktoru (nejsou exportovány do soundbank JSON)

Tyto klíče existují ve výstupu `extractor.py` (v souborech `definition.json`), ale exporter je do finálního soundbank JSON **nezapisuje**. Jsou zde dokumentovány, aby bylo jasné, proč tam nejsou.

| Klíč | Úroveň | Popis | Proč se neexportuje |
|---|---|---|---|
| `n_strings` | note | Počet fyzikálních strun pro MIDI notu (1 = basy, 2 = střední, 3 = výšky) | C++ player vždy používá 2-string model bez ohledu na MIDI notu. Exporter to zarovnává tím, že nikdy neexportuje `n_strings`. |
| `beat_depth` | partial | Hloubka amplitudové modulace beatingu (0–1) | C++ neimplementuje AM beating — pouze PM (fázová modulace přes `beat_hz`). Exporter `beat_hz` exportuje a `beat_depth` tichce zahazuje. |
| `mono` | partial | `true` = parciál pochází z jedné struny (žádný beating) | Exporter místo toho při `mono=True` nastaví `beat_hz=0.0`. C++ automaticky detekuje `beat_hz≈0` a přeskočí výpočet druhé struny. |
| `is_longitudinal` | partial | `true` = longitudinální parciál (jiná fyzika rozladění) | C++ nemá speciální logiku pro longitudinální parciály. Exporter je zahrne s jejich `f_hz` a standardními parametry bez příznaků. |

---

## 4. Formát `eq_biquads`

Každá sekce je objekt:

```json
{ "b": [b0, b1, b2], "a": [a1, a2] }
```

- `b0, b1, b2` — čitatel (numerator); jmenovatel `a0 = 1` vždy
- `a1, a2` — jmenovatel (denominator)

Kaskáda 5 sekcí = 10. řád min-phase IIR filtr.

---

## 5. Syntézní model (přehled)

```
partial[k]:
  env(t) = a1·exp(−t/τ1) + (1−a1)·exp(−t/τ2)   # bi-exp; a1=1 → mono-exp
  s1(t)  = cos(2π·f_hz·t + 2π·(beat_hz/2)·t + phi)
  s2(t)  = cos(2π·f_hz·t − 2π·(beat_hz/2)·t + phi + phi_diff)
  out(t) = A0·env(t)·(s1 + s2)/2

noise(t):
  white(t) = randn() · A_noise · exp(−t/attack_tau)
  α = 1 − exp(−2π·noise_centroid_hz/sr)          # 1-pole IIR koef
  y(t) = α·white(t) + (1−α)·y(t−1)              # barevný šum (levý i pravý nezávisle)

note(t)  = rms_gain·vel_gain · [Σ partial[k](t) + y(t)]
         → biquad EQ cascade (eq_biquads)

vel_gain = ((vel+1)/8)^vel_gamma
```

> **rms_gain** je kalibrován nad celou touto signálovou cestou (parciály + IIR šum + EQ) při exportu, aby výstup C++ přesně dosáhl `target_rms`.

---

## 6. Přehled závislostí

| Parametr | Per-MIDI | Per-(midi,vel) | Per-(midi,vel,k) |
|---|---|---|---|
| `B` | ✓ (BSplneFitter) | | |
| `f0_hz`, `attack_tau`, `A_noise`, `noise_centroid_hz`, `rms_gain`, `phi_diff` | | ✓ | |
| `spectral_eq`, `eq_biquads`, `_interpolated` | | ✓ | |
| `f_hz`, `A0`, `tau1`, `tau2`, `a1`, `beat_hz`, `phi` | | | ✓ |

---

## 7. Co čte co

| Komponenta | Čte klíče |
|---|---|
| **C++ `loadBankJson`** | `midi`, `vel`, `phi_diff`, `attack_tau`, `A_noise`, `noise_centroid_hz`, `rms_gain`, `f0_hz`, `B`, `_interpolated`, `partials[k, f_hz, A0, tau1, tau2, a1, beat_hz, phi]`, `eq_biquads[b, a]` |
| **C++ `setNoteParam`** | `f0_hz`, `attack_tau`, `A_noise`, `noise_centroid_hz`, `rms_gain`, `phi_diff`, `B` (→ všechny vel) |
| **C++ `setNotePartialParam`** | `f_hz`, `A0`, `tau1`, `tau2`, `a1`, `beat_hz`, `phi` |
| **Editor (layer registry)** | `f0_hz`, `B`, `attack_tau`, `A_noise`, `rms_gain`; partial: `f_hz`, `A0`, `tau1`, `tau2`, `a1`, `beat_hz`, `phi` |
| **Editor (EQ editor)** | `spectral_eq`, `eq_biquads` |
| **spline_fix** | Všechny skalární note-level + partial parametry kromě `midi`, `vel`, `phi_diff`, `phi`, `spectral_eq`, `eq_biquads` |
| **GUI LAST NOTE** | `midi`, `vel`, `f0_hz`, `B`, `_interpolated`, partial tabulka, EQ křivka |
