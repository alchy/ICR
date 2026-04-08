# ICR — JSON Soundbank Schema

Referenční přehled všech klíčů v AdditiveSynthesisPianoCore soundbank JSON.
Generováno exporterem (`training/modules/exporter.py`), načítáno `additive_synthesis_piano_core.cpp::loadBankJson()`.

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

### 2b. Note-level keys

| Klíč | Kategorie | Fallback | Aprox | Zdroj | C++ use | Popis |
|---|---|---|---|---|---|---|
| `f0_hz` | fyzika | 440.0 | | extractor | oscilátory | Základní frekvence (Hz) |
| `B` | fyzika | 0.0 | | extractor | `f_k=k*f0*sqrt(1+B*k^2)` | Inharmonicita |
| `attack_tau` | šum | 0.05 | | extractor | noise env decay | Noise decay tau (s). Cap 0.10 |
| `A_noise` | šum | 0.04 | | extractor | noise amplitude | Noise amp (0..1). Cap 1.0 |
| `noise_centroid_hz` | šum | 3000 | | extractor | biquad BPF center | Noise BPF (Hz, Q=1.5). Floor 1000 |
| `rise_tau` | attack | **midi-based** | **ano** | exporter | attack rise env | Rise time (s). Fallback: `4ms(MIDI21)->0.2ms(MIDI108)` |
| `n_strings` | model | **midi-based** | **ano** | exporter | 1/2/3-string | Fallback: `<=27->1, <=48->2, >48->3` |
| `decor_strength` | stereo | **midi-based** | **ano** | (future) | allpass decorr | Fallback: `clamp((midi-40)/60)*0.45*decorr` |
| `rms_gain` | level | 1.0 | | exporter | per-note gain | Kalibrován Python biquad BP rendererem |
| `phi_diff` | fáze | 0.0 | | exporter (RNG) | string 2 phase | Fázový offset mezi strunami (rad) |
| `stereo_width` | stereo | 1.0 | | EQ fitter | M/S post-EQ | `S *= width`. Clamped [0.2, 2.0] |
| `eq_biquads` | EQ | [] (bypass) | | EQ fitter | IIR cascade | 10 biquad sekcí (DF-II) |
| `spectral_eq` | EQ | — | | EQ fitter | (editor only) | Surová EQ křivka, nepoužito C++ |
| `fit_quality` | diag | 0.0 | | extractor | (GUI only) | Bi-exp fit kvalita (0..1) |
| `damping_derived` | diag | false | | extractor | (GUI only) | tau1 z damping law R+eta*f^2 |
| `is_interpolated` | diag | false | | exporter | (GUI only) | NN-generated nota |

---

## 3. Crosscheck — partial-level klíče

Každá nota má pole `partials` — max 60 objektů.

| Klíč | Fallback | Aprox | Zdroj | C++ use | SysEx | Popis |
|---|---|---|---|---|---|---|
| `k` | — | | extractor | index | — | Partial index, 1-based |
| `f_hz` | — | | extractor | oscilátor | `0x10` | Frekvence (Hz). Přepočtena při změně B. |
| `A0` | — | | extractor | amplituda | `0x11` | Počáteční amplituda parciálu |
| `tau1` | 0.5 | | extractor | env fast decay | `0x12` | Rychlá tau bi-exp (s). Floor 0.05 v exportéru. |
| `tau2` | =tau1 | | extractor | env slow decay | `0x13` | Pomalá tau (s). Vždy >= tau1. |
| `a1` | 1.0 | | extractor | env mix weight | `0x14` | Váha rychlé složky. 1.0 = mono-exp. |
| `beat_hz` | 0.0 | | extractor | string detuning | `0x15` | Beat frekvence (Hz). 0 = mono struna. |
| `phi` | 0.0 | | exporter (RNG) | phase string 1 | `0x16` | Počáteční fáze (rad) |
| `fit_quality` | 0.0 | | extractor | (GUI only) | — | Kvalita fitu (0..1). GUI color-coded. |
| `damping_derived` | false | | extractor | (GUI only) | — | tau1 z damping law. GUI "d" flag. |

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
