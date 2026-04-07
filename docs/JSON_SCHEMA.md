# ICR — JSON Soundbank Schema

Referenční přehled všech klíčů v PianoCore soundbank JSON.
Generováno exporterem (`training/modules/exporter.py`), načítáno `piano_core.cpp::loadBankJson()`.

---

## Struktura souboru

```json
{
  "metadata": {
    "instrument_name": "pl-grand",
    "midi_range_from": 21,
    "midi_range_to":   108,
    "source":          "soundbank:params",
    "sr":              48000,
    "target_rms":      0.06,
    "vel_gamma":       0.7,
    "k_max":           60,
    "rng_seed":        0,
    "duration_s":      3.0
  },
  "notes": {
    "m060_vel3": { ... },
    ...
  }
}
```

---

## 1. Bank-level klíče

### 1a. Top-level

| Klíč | Typ | Popis | C++ |
|---|---|---|---|
| `metadata` | object | Metadata banky — viz 1b | ignoruje |
| `notes` | object | Slovník not, klíč = `"m{midi:03d}_vel{vel}"` | ✓ |

### 1b. `metadata` objekt

| Klíč | Typ | Popis |
|---|---|---|
| `instrument_name` | string | Název nástroje; nastavuje uživatel |
| `midi_range_from` | int | Nejnižší MIDI nota s naměřenými vzorky |
| `midi_range_to` | int | Nejvyšší MIDI nota s naměřenými vzorky |
| `source` | string | `"soundbank:params"` / `"nn-hybrid:model"` / `"nn-pure:model"` / `"dna"` |
| `sr` | int | Sample rate (44100 nebo 48000) |
| `target_rms` | float | Cílová RMS úroveň (bake do `rms_gain`) |
| `vel_gamma` | float | Gamma křivka velocity (default 0.7) |
| `k_max` | int | Max počet parciálů (60) |
| `rng_seed` | int | Seed pro generování fází φ a φ_diff |
| `duration_s` | float | Délka renderovaných not v sekundách |

---

## 2. Crosscheck — note-level klíče

Legenda sloupců:

| Sloupec | Význam |
|---|---|
| **Dep** | Závislost: `m` = per-MIDI, `m,v` = per-(midi,vel), `m,v,k` = per-(midi,vel,k) |
| **Extractor** | Zdroj hodnoty před exportem |
| **JSON** | Přítomen v soundbank JSON (`cond.` = jen pokud != false/None) |
| **C++ load** | Načítáno funkcí `loadBankJson()` / `load()` |
| **C++ synth** | Použito v syntézním výpočtu (`processBlock`) |
| **C++ set** | Editovatelné přes `setNoteParam` / `setNotePartialParam` |
| **GUI viz** | Zobrazeno v panelu Last Note (`getVizState`) |
| **GUI edit** | Editovatelné v layer editoru |
| **SysEx** | Přenášeno přes SysEx protokol |

### 2a. Nota — identifikace

| Klíč | Dep | Extractor | JSON | C++ load | C++ synth | C++ set | GUI viz | GUI edit | SysEx | Popis |
|---|---|---|---|---|---|---|---|---|---|---|
| `midi` | — | extr | ✓ | meta | meta | — | ✓ | — | — | MIDI nota 21–108 |
| `vel` | — | extr | ✓ | meta | meta | — | ✓ | — | — | Velocity index 0–7 |
| `is_interpolated` | m,v | exporter | cond. | ✓ | — | — | ✓ badge | — | — | `true` = NN nota; zobrazí `[NN]` / `[MEASURED]` |

### 2b. Nota — fyzika

| Klíč | Dep | Extractor | JSON | C++ load | C++ synth | C++ set | GUI viz | GUI edit | SysEx | Popis |
|---|---|---|---|---|---|---|---|---|---|---|
| `f0_hz` | m,v | extr→NN | ✓ | ✓ | noteOn | ✓ | ✓ | ✓ | ✓ `0x01` | Základní frekvence Hz |
| `B` | **m** | BSplineFitter | ✓ | ✓ | přepočet `f_hz` | ✓ (→ všechny vel) | ✓ | ✓ | ✓ `0x02` | Inharmonicita; `f_k = k·f0·√(1+B·k²)` |

### 2c. Nota — šum

| Klíč | Dep | Extractor | JSON | C++ load | C++ synth | C++ set | GUI viz | GUI edit | SysEx | Popis |
|---|---|---|---|---|---|---|---|---|---|---|
| `attack_tau` | m,v | extr→NN | ✓ | ✓ | noise env decay | ✓ | ✓ | ✓ | ✓ `0x03` | Časová konstanta náběhu šumu (s) |
| `A_noise` | m,v | extr→NN | ✓ | ✓ | noise amplitude | ✓ | — | ✓ | ✓ `0x04` | Amplituda šumové složky |
| `noise_centroid_hz` | m,v | extr | ✓ | ✓ | biquad BPF | ✓ | ✓ | ✓ | ✗ ¹ | Center frequency biquad bandpass (Q=1.5) pro barvení šumu; default 3000 Hz |
| `rise_tau` | m | exporter | ✓ | ✓ | attack rise env | — | — | — | — | Attack rise time (s); -1 = midi-based default. Chabassier: 4ms bass → 0.2ms treble |
| `n_strings` | m | exporter | ✓ | ✓ | string model | — | — | — | — | 1/2/3 string model; -1 = midi-based default (≤27→1, ≤48→2, >48→3) |
| `decor_strength` | m | (future) | ✓ | ✓ | allpass decorr | — | — | — | — | Schroeder decorrelation strength; -1 = midi-based default |

> ¹ `noise_centroid_hz` chybí v `noteParamKey()` v `core_engine.cpp` — SysEx ID není přiřazeno (existující mezera, ID `0x07` volné).

### 2d. Nota — normalizace a stereo

| Klíč | Dep | Extractor | JSON | C++ load | C++ synth | C++ set | GUI viz | GUI edit | SysEx | Popis |
|---|---|---|---|---|---|---|---|---|---|---|
| `rms_gain` | m,v | exporter (RMS kalib.) | ✓ | ✓ | vel-scaled gain | ✓ | — | ✓ | ✓ `0x05` | Zesílení pro dosažení `target_rms`; kalibrováno nad mono M kanálem — invariantní vůči M/S |
| `phi_diff` | m,v | exporter (RNG) | ✓ | ✓ | phase string 2 | ✓ | — | ✗ | ✗ | Fázový offset mezi strunami 1 a 2; `s2 = cos(…+phi+phi_diff)` |
| `stereo_width` | m,v | EQFitter | ✓ | ✓ | M/S post-EQ | ✓ | ✓ | ✓ | ✗ | M/S korekce šíře: `S *= w`, kde `M=(L+R)/2`, `S=(L-R)/2`. Měřeno jako `rms(origS)/rms(origM) ÷ rms(synS)/rms(synM)`; `1.0` = bez korekce (NN noty) |

### 2e. Nota — spektrální EQ

| Klíč | Dep | Extractor | JSON | C++ load | C++ synth | C++ set | GUI viz | GUI edit | SysEx | Popis |
|---|---|---|---|---|---|---|---|---|---|---|
| `eq_biquads` | m,v | EQFitter→exporter | ✓ | ✓ | IIR EQ kaskáda | — | ✓ (mag.) | ✓ (EQ edit) | ✗ | 10 biquad sekcí (Direct Form II), formát viz sekce 4 |
| `spectral_eq` | m,v | EQFitter | ✓ | ✗ | — | — | — | ✓ (re-fit) | ✗ | Surová EQ křivka `{freqs_hz, gains_db, stereo_width_factor}` pro editor re-fit; `stereo_width_factor` → flat `stereo_width` při exportu |

---

## 3. Crosscheck — partial-level klíče

Každá nota má pole `partials` — max 60 objektů.

| Klíč | Dep | Extractor | JSON | C++ load | C++ synth | C++ set | GUI viz | GUI edit | SysEx | Popis |
|---|---|---|---|---|---|---|---|---|---|---|
| `k` | m,v,k | extr | ✓ | meta | B-recompute idx | — | ✓ | — | — | Index parciálu, 1-based |
| `f_hz` | m,v,k | `k·f0·√(1+B·k²)` bake | ✓ | ✓ | oscilátor | ✓ | ✓ | ✓ | ✓ `0x10` | Frekvence parciálu Hz; přepočítána při `setNoteParam("B")` |
| `A0` | m,v,k | extr→NN | ✓ | ✓ | amplituda | ✓ | ✓ | ✓ | ✓ `0x11` | Amplituda parciálu |
| `tau1` | m,v,k | extr→NN | ✓ | ✓ | env fast τ | ✓ | ✓ | ✓ | ✓ `0x12` | Rychlá τ bi-exp obálky (s) |
| `tau2` | m,v,k | extr→NN | ✓ | ✓ | env slow τ | ✓ | ✓ | ✓ | ✓ `0x13` | Pomalá τ (s); `τ2 ≥ τ1` |
| `a1` | m,v,k | extr→NN | ✓ | ✓ | env mix | ✓ | ✓ | ✓ | ✓ `0x14` | Váha rychlé složky; `a1=1.0` = mono-exp |
| `beat_hz` | m,v,k | extr→NN | ✓ | ✓ | detuning PM | ✓ | ✓ | ✓ | ✓ `0x15` | Frekvence beatingu Hz; `0` = mono struna |
| `phi` | m,v,k | exporter (RNG) | ✓ | ✓ | phase string 1 | ✓ | — | ✓ | ✓ `0x16` | Počáteční fáze string 1 (rad) |
| `fit_quality` | m,v,k | extractor | ✓ | ✓ | — | — | ✓ | — | — | Kvalita envelope fitu (0..1, 1=perfektní). Color-coded v GUI: zelená ≥0.9, žlutá ≥0.7, červená <0.7 |
| `damping_derived` | m,v,k | extractor | ✓ | ✓ | — | — | ✓ | — | — | `true` = tau1 nahrazen hodnotou z damping law R+η·f². Zobrazeno jako "d" v GUI |

---

## 3b. Klíče extraktoru (nejsou v soundbank JSON)

Existují ve výstupu `extractor.py`, exporter je do JSON **nezapisuje**.

| Klíč | Úroveň | Extractor | JSON | Proč chybí |
|---|---|---|---|---|
| `n_strings` | note | ✓ | ✗ | Akustický počet strun: 1 (MIDI ≤27 bas), 2 (28–48 tenor), **3 (≥49 treble)**. C++ `initVoice` nastaví `n_model_strings` identicky dle MIDI a renderuje odpovídající model (viz sekce 5). `rms_gain` kalibrován pro stejný model → Python synthesizer a C++ jsou v souladu. |
| `beat_depth` | partial | ✓ | ✗ | C++ neimplementuje AM beating; exporter nastaví `beat_hz=0.0` |
| `mono` | partial | ✓ | ✗ | Exporter → `beat_hz=0.0`; C++ detekuje `beat_hz≈0` automaticky |
| `is_longitudinal` | partial | ✓ | ✗ | C++ bez speciální logiky pro longitudinální parciály |

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

## 5. Syntézní model

```
partial[k]:
  env(t) = a1·exp(−t/τ1) + (1−a1)·exp(−t/τ2)   # bi-exp; a1=1 → mono-exp

  # 1-string (MIDI ≤ 27 — bas)
  out(t) = A0·env(t)·cos(2π·f_hz·t + phi)

  # 2-string (MIDI 28–48 — tenor)
  s1(t) = cos(2π·(f_hz + beat_hz/2)·t + phi)
  s2(t) = cos(2π·(f_hz − beat_hz/2)·t + phi + phi_diff)
  out(t) = A0·env(t)·(s1 + s2) / 2

  # 3-string (MIDI > 48 — treble, symetrický)
  s1(t) = cos(2π·(f_hz − beat_hz)·t + phi)        # vnější levá
  s2(t) = cos(2π·f_hz·t + phi2)                   # centrální (phi2 náhodná per-noteOn)
  s3(t) = cos(2π·(f_hz + beat_hz)·t + phi + phi_diff)  # vnější pravá
  out(t) = A0·env(t)·(s1 + s2 + s3) / 3

  # Stereo pan (konstantní výkon, per-string gain gl/gr)
  L += out · gl_k ;  R += out · gr_k

noise(t):
  white(t) = randn() · A_noise · exp(−t/attack_tau)
  bpf = rbj_bandpass(noise_centroid_hz, Q=1.5, sr)  # biquad bandpass
  y(t) = biquad_tick(white(t), bpf)                  # L, R nezávisle

note(t)  = rms_gain·vel_gain · [Σ partial[k](t) + noise(t)]
         → biquad EQ cascade (eq_biquads)
         → M/S: M=(L+R)/2  S=(L-R)/2·stereo_width  L=M+S  R=M-S

vel_gain = ((vel+1)/8)^vel_gamma
```

> **rms_gain** kalibrován nad mono M kanálem (= (L+R)/2) — invariantní vůči M/S operaci, takže platí bez přepočtu i po přidání `stereo_width`.
>
> **beat_hz sémantika**: u 2-strunného modelu rozteč = `beat_hz/2` na stranu (celková `beat_hz`); u 3-strunného rozteč = `beat_hz` na stranu → 2× větší frekvenční spread, 2 amplitudové nuly za periodu beating.

---

## 6. Přehled závislostí

| Parametr | Per-MIDI | Per-(midi,vel) | Per-(midi,vel,k) |
|---|---|---|---|
| `B` | ✓ (BSplineFitter) | | |
| `f0_hz`, `attack_tau`, `A_noise`, `noise_centroid_hz`, `rms_gain`, `phi_diff`, `stereo_width` | | ✓ | |
| `spectral_eq`, `eq_biquads`, `is_interpolated` | | ✓ | |
| `f_hz`, `A0`, `tau1`, `tau2`, `a1`, `beat_hz`, `phi` | | | ✓ |
