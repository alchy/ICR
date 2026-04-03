# Convolution Reverb Extraction — Design Notes

Status: **theoretical / pre-implementation**
Depends on: EQFitter, SoundbankExporter, PianoCore C++

---

## Motivace

Nahrávka akustického nebo elektroakustického nástroje obsahuje dvě složky
které současný syntetizátor nereprodukuje:

1. **Rezonance těla nástroje** — soundboard klavíru, kovová vidlička
   Rhodes, dřevěná skříň. Projevuje se jako frekvenčně závislé zesílení
   na konkrétních módových frekvencích, s časovým doznívám (ringing).

2. **Reverb místnosti** — odrazy od stěn, podlahy, víka nástroje. Závisí
   na nahrávacím prostoru a nelze ho oddělit od těla bez suché reference.

EQFitter zachytí **magnitudu** tohoto přenosu pomocí 5 biquad filtrů.
Biquady jsou výpočetně levné, ale:
- nezachytí fázi (časové zpoždění rezonancí)
- nezachytí reverb ocas (doznívání po skončení buzení)
- jsou příliš hladké na zachycení ostrých modálních špiček

Konvoluční IR by zachytil všechno výše uvedené — na úkor větší banky
a vyššího výpočetního výkonu.

---

## Teorie — jak se IR extrahuje

### Základní princip

Nahrávku lze modelovat jako konvoluci:

```
original(t) = synthesized(t) * h(t)  +  noise(t)
```

kde `h(t)` je impulsní odezva systému (tělo + pokoj). V frekvenční
doméně:

```
ORIG(f) = SYNTH(f) · H(f)
H(f)    = ORIG(f) / SYNTH(f)
h[n]    = IFFT(H(f))
```

Syntetizátor i originál máme → dekonvoluce je principiálně možná.

### Wiener deconvolution (numericky stabilní)

Prosté dělení `ORIG/SYNTH` zesiluje šum všude kde má synth malou
energii (mezery mezi parciálami). Wiener filter přidá regularizaci:

```
H(f) = SYNTH*(f) · ORIG(f)
       ─────────────────────
       |SYNTH(f)|² + λ · σ²(f)
```

kde:
- `SYNTH*(f)` je komplexní konjugát
- `λ` je regularizační parametr (např. 0.001)
- `σ²(f)` je odhadovaný výkonový šum (konstantní nebo frekvenčně závislý)

Výsledný `h[n] = IFFT(H(f))` je IR délky `N` vzorků.

### Zpracování útoku

Prvních ~50–100 ms nahrávky obsahuje nelineární úder kladívka / plektra.
Dekonvoluce v tomto úseku nedává smysl — lineární model tam neplatí.

Doporučení: ignorovat prvních `skip_ms=80` ms při výpočtu IR, nebo použít
vážené okno které potlačí útok (exponenciálně rostoucí váha od t=80 ms).

---

## Architektura — jak to zapadá do stávajícího systému

### Současný pipeline

```
WAV → ParamExtractor → StructuralOutlierFilter → EQFitter → Exporter → JSON
                                                   ↑
                                              5 biquads / nota
```

### Navrhované rozšíření

```
WAV → ParamExtractor → StructuralOutlierFilter → EQFitter → IRExtractor → Exporter → JSON
                                                              ↑
                                                    IR per nota (nebo průměrný)
```

`IRExtractor` pracuje stejně jako `EQFitter`:
- vstup: `params` dict + `bank_dir`
- výstup: `params` s přidaným `convolution_ir` per sample
- multiprocessing worker, timeout, error handling

### JSON formát (per nota)

```json
"m065_vel4": {
  ...
  "convolution_ir": {
    "samples": [0.0012, -0.0034, ...],   // IR vzorky, délka N
    "sr":      48000,
    "skip_ms": 80,
    "lambda":  0.001
  }
}
```

Alternativa: jeden průměrný IR na celou banku (viz sekce Granularita níže).

### C++ PianoCore — kde by se konvoluce prováděla

Konvoluce by se aplikovala na výstup syntézy každé noty, **za** biquad EQ:

```
synth → partials → noise → biquad EQ → konvoluce s IR → výstup
```

Implementace v C++:
- FFT-based overlap-add nebo overlap-save (efektivní pro dlouhé IR)
- IR délka 1024–4096 vzorků → FFT size 2048–8192
- Per-nota IR: načíst z banky při note-on, uvolnit při note-off
- Sdílený IR: načíst jednou při load banky

---

## Klíčová rozhodnutí (před implementací)

### 1. Granularita IR

| Varianta | IR na | Výhody | Nevýhody |
|---|---|---|---|
| **A** | Celá banka (1 IR) | Malá banka, jednoduché | Průměruje rozdíly mezi notami |
| **B** | Per MIDI (88 IR) | Zachytí výškové rozdíly | 88× větší, neřeší velocity |
| **C** | Per nota (704 IR) | Nejpřesnější | Velká banka, šum u tichých not |
| **D** | Per MIDI, průměr vels | Kompromis | Středně velká banka |

**Doporučení pro první implementaci: varianta B (per MIDI, průměr přes velocity).**
Zachytí nejdůležitější rozdíly (různé struny/snímač po klaviatuře) bez
přílišného šumu z tichých velocit.

### 2. Délka IR

| Délka | Vzorky při 48 kHz | Pokrytí | Výpočetní náklady |
|---|---|---|---|
| 256 | 5.3 ms | Těsné rezonance | Zanedbatelné |
| 1024 | 21 ms | Modální ringing | Malé |
| 4096 | 85 ms | Krátký reverb ocas | Střední |
| 16384 | 341 ms | Plný reverb | Vysoké |

**Doporučení: 2048–4096 vzorků.** Zachytí modální ringing a první
odrazy od těla nástroje bez nutnosti renderovat plný akustický reverb.

### 3. Vztah k EQ biquads

Dvě možnosti koexistence:

- **Nahradit EQ**: IR v frekvenční doméně obsahuje i spektrální barvení
  → biquady jsou redundantní. Čistší architektura, vyšší náklady.

- **Zachovat EQ + přidat IR**: EQ dělá hrubé spektrální srovnání
  (levné), IR přidá časové detaily. EQ se vyřadí z IR výpočtu
  (Wiener dekonvoluce se počítá na signálu bez EQ). Zpětně kompatibilní.

**Doporučení: zachovat EQ + přidat IR** — zpětná kompatibilita, možnost
postupného rollout (IR jako opt-in feature v bance).

### 4. Kdy IR neextrahovat

- nota s příliš krátkým decay (< 500 ms po skip_ms) → IR příliš zašuměný
- nota kde `synth` má abnormálně nízkou energii → dělení nestabilní
- NN-generované noty (žádný originální WAV) → nelze extrahovat

Fallback: použít průměrný IR ze sousedních měřených not (stejný přístup
jako spline_fix pro jiné parametry).

---

## Implementační poznámky

### IRExtractor (Python)

```python
class IRExtractor:
    IR_LENGTH_SAMPLES = 2048
    LAMBDA_WIENER     = 0.001
    SKIP_MS           = 80.0
    NFFT_IR           = 8192    # musí být >= 2 * IR_LENGTH_SAMPLES

    def fit_bank(self, params, bank_dir, workers=None) -> dict:
        """Přidá 'convolution_ir' ke každému vzorku v params."""
        ...

    def _compute_ir(self, orig_mono, synth_mono, sr) -> np.ndarray:
        """Wiener dekonvoluce → IR délky IR_LENGTH_SAMPLES."""
        skip   = int(self.SKIP_MS * 1e-3 * sr)
        o      = orig_mono[skip:]
        s      = synth_mono[skip:]
        n      = min(len(o), len(s), self.NFFT_IR)
        O      = np.fft.rfft(o[:n], n=self.NFFT_IR)
        S      = np.fft.rfft(s[:n], n=self.NFFT_IR)
        noise  = self.LAMBDA_WIENER * np.mean(np.abs(S)**2)
        H      = S.conj() * O / (np.abs(S)**2 + noise)
        h      = np.fft.irfft(H)[:self.IR_LENGTH_SAMPLES]
        # Fade-in okno (potlačí artefakty na začátku IR)
        fade   = np.minimum(np.arange(len(h)) / 32.0, 1.0)
        return (h * fade).astype(np.float32)
```

### Exportér — přidání IR do JSON

```python
# V SoundbankExporter._build_note():
ir_data = sample.get("convolution_ir")
if ir_data is not None:
    note["convolution_ir"] = {
        "samples": [round(float(v), 6) for v in ir_data],
        "sr":      sr,
    }
```

### C++ načítání

```cpp
struct NoteIR {
    std::vector<float> samples;   // délka IR_LENGTH
    int                sr;
};

// V PianoCore::load():
if (note_json.contains("convolution_ir")) {
    note.ir = load_ir(note_json["convolution_ir"]);
    note.ir_fft = compute_ir_fft(note.ir);   // předpočítat FFT
}

// V PianoCore::render_note():
audio = convolve_overlap_add(audio, note.ir_fft);
```

### Velikost banky (odhad)

| Granularita | IR délka | Přidáno k bance |
|---|---|---|
| 1 IR / banka | 2048 float32 | +8 kB |
| 88 IR (per MIDI) | 2048 float32 | +704 kB |
| 704 IR (per nota) | 2048 float32 | +5.6 MB |

Současná banka vv-rhodes: ~6 MB → varianta B přidá ~12 % velikosti.

---

## Otevřené otázky

1. **Separace těla vs. pokoj**: Lze nějak oddělit body IR od room IR?
   Možnost: nahrát krátký impulz v bezodrazové komoře a použít ho jako
   referenci. Pro stávající banky není možné zpětně.

2. **Polyfonie**: Při současném hraní více not se IR sčítají. U konvoluce
   per nota to znamená N paralelních konvolucí. Přijatelné pro 8–16 hlasů?

3. **Pitch-shifting IR**: Při použití per-MIDI IR a pitch-bendingu by se
   měl IR interpolovat mezi sousedními notami. Složité, možná zbytečné.

4. **NN-generované noty v extrapolační zóně** (MIDI 21–32, 90–108):
   Nemají referenční WAV → IR nelze extrahovat. Řešení: spline interpolace
   IR vzorků přes MIDI (stejný přístup jako spline_fix pro tau1/tau2).

5. **Kompatibilita se sound editorem**: Editor by mohl zobrazit IR jako
   waveform nebo jeho spektrum (log-mag). Editace IR zatím není plánována.

---

## Pořadí kroků pro realizaci

Až bude čas implementovat:

1. Napsat `IRExtractor` (Python, stejný vzor jako `EQFitter`)
2. Přidat `fit_bank` do pipeline za `EQFitter`
3. Rozšířit `SoundbankExporter._build_note()` o IR export
4. Implementovat overlap-add konvoluci v C++ PianoCore
5. Přidat spline interpolaci IR pro NN-generované noty (`spline_fix.py`)
6. A/B test: banka s IR vs. bez IR
