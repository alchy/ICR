# Poznátky z experimentů s physical modeling piano

## Co je allpass kaskáda (a jak se liší od bucket brigade)

### Bucket brigade (BBD) = konstantní delay

Bucket brigade je čistý delay: vzorek vstoupí, projde řadou "kbelíků", a za N
taktů vyleze beze změny. Všechny frekvence se zpozdí stejně. Je to jako
potrubí — všechno projde stejně rychle.

### Allpass filtr = frekvenčně závislý delay

Allpass filtr taky nic nemění na amplitudě (proto "all-pass"), ale různé
frekvence zpozdí různě. Jeden stupeň:

```
vstup x ──┬──────[× a]──────(+)──── výstup y
           │                  ^
           v                  |
         (+)──[z⁻¹]──[× -a]──┘
           ^
           |
         [× 1]
```

Implementace je triviální — dva řádky, jeden vzorek paměti:

```python
y     =  a * x  + state
state =  x  - a * y       # uloží pro příští vzorek
```

Frekvenční odezva má magnitude = 1 všude (nic nezesiluje, nic netlumí), ale fáze
se mění s frekvencí — nízké frekvence projdou pomaleji, vysoké rychleji (nebo
naopak, podle znaménka `a`).

### Kaskáda = řazení stupňů za sebe

Jeden stupeň přidá malý fázový posun. Víc stupňů za sebou:

```
x ──[AP₁]──[AP₂]──[AP₃]──...──[APₙ]── y
```

Fázové posuny se sčítají. A protože každý stupeň posouvá fázi frekvenčně
závisle, efekt se s počtem stupňů zesiluje:

- 1 stupeň: skoro nic neslyšíš
- 6 stupňů: vyšší harmonické se jemně roztáhnou
- 23 stupňů: vyšší harmonické jsou výrazně posunuté

### Srovnání

| | Bucket brigade | Allpass kaskáda |
|---|---|---|
| Delay | konstantní pro všechny frekvence | závisí na frekvenci |
| Paměť | N vzorků (dlouhý buffer) | 1 vzorek na stupeň |
| Amplituda | beze změny | beze změny |
| Účel | echo, chorus, flanger | disperze (stiffness struny) |

BBD je jako potrubí — všechno projde stejně rychle. Allpass kaskáda je jako
sklo — bílé světlo se rozloží na spektrum, protože každá vlnová délka se láme
jinak. Proto se tomu říká **disperze**.

V delay line smyčce waveguidu to znamená, že každý oběh signálu kolem smyčky
trochu víc "natáhne" vyšší parciály — přesně ten efekt, co dělá tuhost reálné
ocelové struny.

---

## Disperzní allpass kaskáda a vliv na barvu tónu

### Princip

Reálná ocelová struna má tuhost (stiffness), která posouvá vyšší parciály nahoru
oproti ideálnímu harmonickému řádu:

```
Ideální struna:  f_k = k × f0
Reálná struna:   f_k = k × f0 × √(1 + B × k²)
```

kde `B` je koeficient inharmonicity (typicky 1e-4 až 1e-3 pro piano).

V waveguide syntéze se tento efekt simuluje kaskádou first-order allpass filtrů
v delay line smyčce. Každý stupeň přidá frekvenčně závislé zpoždění — nízké
frekvence projdou s větším zpožděním než vysoké, čímž se efektivně zkrátí loop
pro vyšší harmonické a ty oscilují rychleji.

### Group delay allpass filtru

Pro allpass s koeficientem `a = -0.15`:

```
τ(ω) = (1 - a²) / (1 + 2a·cos(ω) + a²)

DC     (ω=0):  τ = (1 - a)/(1 + a) = 1.353 vzorků/stupeň
Nyquist (ω=π): τ = (1 + a)/(1 - a) = 0.739 vzorků/stupeň
```

Kompenzace ladění (f0) se provádí odečtením DC group delay od delay line.
Vyšší harmonické ale mají menší delay → vidí kratší loop → jsou ostřejší.

### Experiment: MIDI 64 (E4, 329.6 Hz), B=0.0006

| Stupňů | DC delay  | Nyq delay | Rozdíl | Charakter                        |
|--------|-----------|-----------|--------|----------------------------------|
| 0      | 0         | 0         | 0      | čistá struna, smooth             |
| 6      | 8.1 vz.   | 4.4 vz.  | 3.7    | mírný stretch, piano-like        |
| 12     | 16.2 vz.  | 8.9 vz.  | 7.4    | výrazný stretch, zvonkový        |
| 23     | 31.1 vz.  | 17.0 vz. | 14.1   | extrémní, kovová tyč             |

Pozn: N_total pro MIDI 64 ≈ 145.6 vzorků, takže 23 stupňů zkrátí delay line o ~21%.

### Závěry

- Více allpass stupňů = více inharmonicity = kovově-zvonkový charakter.
- Při příliš mnoha stupních parciály spolu přestanou "bít" harmonicky a tón
  ztrácí hudební kvalitu.
- Reálné piano má silnou inharmonicitu v basu (dlouhé tlusté struny) a slabou
  v diskantu. Proto bank má pro vyšší MIDI noty méně nebo žádné disperzní stupně
  (např. m072 má `n_disp_stages=0`).
- Koeficient `a = -0.15` na stupeň je dobrý kompromis — dostatečně mírný, aby
  jednotlivý stupeň nezpůsobil buzz, ale dostatečný pro akumulaci efektu.

### Kompenzace ladění

Každý disperzní allpass stupeň přidává group delay, který prodlužuje efektivní
delay line a snižuje f0. Kompenzace se provádí odečtením DC group delay od
celočíselné délky delay line:

```
disp_delay = n_stages × (1 - a) / (1 + a)
full_N = SR/f0 - filter_delay - disp_delay
```

Bez kompenzace je tón rozladěný dolů (opraveno v C++ core i Python helperu).

### Generování test souborů

```python
from tests.test_string import make_string_v2, write_wav

params = dict(T60_fund=6.81, T60_nyq=0.251, exc_rolloff=0.1, exc_x0=1/7,
              n_harmonics=80, B=0.0006, odd_boost=1.75, knee_k=10,
              knee_slope=3.7, gauge=1.83)

audio = make_string_v2(64, velocity_01=0.6, duration_s=2.0,
                       n_disp_stages=12, **params)
write_wav("tmp_audio/m064_disp12.wav", audio)
```

---

## Single-rail vs dual-rail waveguide

### Single-rail (make_string_v2)

Jedna kruhová delay line, signál obíhá dokola. Excitace = explicitní
Fourierova řada napsaná do delay line na začátku:

```
excitation → [Delay N] → [Tuning AP] → [Dispersion APs] → [Loss LPF] → zpět
                                                                ↓
                                                             output
```

Parametry excitace (`odd_boost`, `knee_k`, `knee_slope`, `exc_rolloff`)
musí být ručně laděny poslechovými testy.

### Dual-rail (Teng 2012 / Smith 1992)

Dvě paralelní delay lines modelují fyzicky cestující vlny:

```
         n0 (hammer)
          |
  [Nut]   v                           [Bridge]
    |   ←---lower----[+]----upper--->    |
   -1   --->lower----[+]----upper---←   [H]  → output

    H = loss × dispersion × tuning × (-1)
```

- **upper**: right-traveling wave (nut → bridge), délka M = N/2
- **lower**: left-traveling wave (bridge → nut), délka M = N/2
- **hammer force** (half-sine puls) vstříknut na pozici x0 do obou railů

### Proč dual-rail zní líp

1. **Přirozený comb filter** — kladívko vytvoří dvě vlny. Přímá cesta
   (hammer → bridge) a odražená (hammer → nut → bridge) dorazí s různým
   zpožděním. Rozdíl = 2×n0 vzorků → notchy na harmonických k = 1/x0
   (pro x0=1/7: k=7, 14, 21...). Žádné Fourierovy koeficienty — fyzika
   to udělá sama.

2. **Realistický attack** — tvar hammer pulsu přímo řídí spektrum. Kratší
   kontakt = jasnější zvuk. Žádné `odd_boost` / `knee_k` / `knee_slope`.

3. **Multi-string beating** — 2-3 rozladěné struny → přirozený beating +
   two-stage decay (rychlý počáteční útlum z vertikální polarizace, pomalý
   dozvuk z horizontální).

4. **Stereo** — rozladěné struny panované L/C/R s nastavitelným spreadem.

### Tool

```bash
python tools-physical/generate_teng.py \
    --bank soundbanks-physical/physical-piano-04081305.json \
    --midi 60 64 72 --vel 0.3 0.6 0.9
```

Viz `tools-physical/README.md` pro kompletní dokumentaci.

---

## Hammer model — zjednodušení vs fyzika

### Současný stav (v1): half-sine puls

```python
hammer_force = velocity * 0.5 * sin(π * t / n_contact)
```

Jeden hladký hrb. Délka kontaktu závisí na pozici na klávesnici a velocity:

```
contact_ms = max(1.5, 4.0 - 2.0*t_keyboard - velocity*0.5)
```

Bas ≈ 4ms, diskant ≈ 1.5ms. Forte zkracuje kontakt → ostřejší puls →
více vyšších harmonických. Jednoduché, ale chybí fyzikální zpětná vazba.

### Reálné kladívko (Chaigne & Askenfelt 1994 / Teng kap. 4)

Kladívko = nelineární hmota-pružina systém. Plsťový povrch se komprimuje
nelineárně:

```
F = K × |δ|^p        kde δ = y_hammer - y_string(x0)
```

- `K` = tuhost plsti (4.5×10⁹ pro C4)
- `p` = exponent nelinearity (2.5 typicky)
- `δ` = komprese = rozdíl pozice kladívka a struny v bodě úderu

Klíčový efekt: **zpětná vazba struna → kladívko**. Odražená vlna se vrátí
k bodu úderu a zatlačí strunu zpět proti kladívku, čímž vznikne
**vícevrcholový force signal**:

```
Force
  │   ╭╮
  │  ╭╯ ╰╮  ╭╮
  │ ╭╯    ╰╮╭╯╰╮
  │╭╯      ╰╯   ╰──
  └──────────────── time
     1.   2.  3.  pulse
```

Tohle nemůže half-sine nikdy reprodukovat — je to přímý důsledek interakce
kladívka s cestující vlnou na struně.

### Co to mění na zvuku

| Aspekt | Half-sine | Chaigne-Askenfelt |
|---|---|---|
| Attack transient | hladký, generický | realistický "knock" |
| Velocity → spektrum | jen délka pulsu | nelineární komprese → ostřejší |
| Vícenásobné pulsy | ne | ano (wave reflection) |
| Parametry | contact_ms | K, p, M_hammer, v0 (fyzikální) |

### Implementace (Teng str. 40-42)

Finite difference model: v každém časovém kroku se počítá pozice kladívka
i struny, dokud kladívko neopustí strunu (δ < 0):

```python
# Rekurence pro pozici kladívka:
y_h[n] = 2*y_h[n-1] - y_h[n-2] - (dt² * F[n-1]) / M_hammer

# Rekurence pro strunu (zjednodušená):
y_s[n] = 2*y_s[n-1] - y_s[n-2] + (dt² * F[n-1]) / M_string

# Síla:
delta = y_h[n] - y_s[n]
F[n] = K * |delta|^p    pokud delta > 0, jinak 0
```

Počáteční podmínka: `y_h[1] = v0 / SR` (kladívko se rozjede počáteční
rychlostí). Celý výpočet trvá jen ~150-340 vzorků (3-7ms), pak se force
signal předá waveguidu jako excitace.

### Implementace v2 (`tools-physical/generate_teng_v2.py`)

Chaigne-Askenfelt model implementován s plným FD stiff-string schématem.
Fyzikální parametry interpolovány ze 3 anchor notes (C2, C4, C7):

```
             C2 (M36)    C4 (M60)    C7 (M96)
Ms (g)       35.0        3.93        0.467
L  (m)       1.90        0.62        0.09
Mh (g)       4.9         2.97        2.2
T  (N)       750         670         750
p            2.3         2.5         3.0
K            1e8         4.5e9       1e11
```

Stabilita FD schématu: Courant podmínka pro tuhý řetězec je přísnější než
pro ideální: `r ≤ 1/sqrt(1 + 4*epsilon*N²)`. Počet bodů sítě N se
automaticky volí per-note pro splnění stability s 10% marží.

### Výsledky Chaigne vs half-sine

Změřeno z force signálů:

| MIDI | v0   | Peak force | Contact | Peaks | Charakter |
|------|------|------------|---------|-------|-----------|
| 36   | 1.8  | 9 N        | 7.0 ms  | 2     | měkký, basový |
| 36   | 5.4  | 31 N       | 6.3 ms  | 2     | forte bas |
| 60   | 1.8  | 8 N        | 4.4 ms  | 3     | piano, kulatý |
| 60   | 3.6  | 17 N       | 4.3 ms  | 4     | mf, vícepulsní |
| 60   | 5.4  | 26 N       | 4.2 ms  | 4     | ff, ostřejší |
| 72   | 1.8  | 8 N        | 4.1 ms  | 1     | jemný |
| 72   | 5.4  | 28 N       | 3.8 ms  | 4     | jasný, percussive |

Forte (v0=5.4) dává ~3× silnější sílu, kratší kontakt a více force peaků
než piano (v0=1.8). Výsledek: nelineární velocity→spectral mapping, které
half-sine nemůže reprodukovat.

### Generování

```bash
# v1 (half-sine hammer):
python tools-physical/generate_teng.py \
    --bank soundbanks-physical/teng-v2-default.json

# v2 (Chaigne-Askenfelt hammer, s bankou nebo bez):
python tools-physical/generate_teng_v2.py \
    --midi 60 64 72 --vel 0.3 0.6 0.9
```

---

## Multi-string: beating, two-stage decay, stereo

### Jak to řeší Teng

Teng vždy spouští **3 paralelní waveguidy** na jednu notu. Liší se pouze
tuning allpass koeficientem:

```matlab
C = (1-P)/(1+P);                        % base tuning
Hfd1 = (C + z⁻¹) / (1 + C*z⁻¹);        % struna 1: přesné ladění
Hfd2 = (C*(1+offtune) + z⁻¹) / ...      % struna 2: mírně vyšší
Hfd3 = (C*(1-offtune) + z⁻¹) / ...      % struna 3: mírně nižší
```

Všechno ostatní je sdílené — stejný delay line, loss filter, dispersion
cascade, hammer force. Výstupy se prostě sečtou (mono):

```matlab
output = output1 + output2 + output3;
```

Parametr `offtune` závisí na registru:

| Rozsah    | offtune | Beating       |
|-----------|---------|---------------|
| > 3000 Hz | 0.01    | minimální     |
| > 261 Hz  | 0.06    | mírný         |
| > 120 Hz  | 0.18    | výrazný       |
| < 120 Hz  | 0.25    | silný (bas)   |

### Co Teng nedělá

- **Žádná vazba mezi strunami** — žádný feedback ze sumy zpět do waveguidů.
  Zmíní to jako možné vylepšení (Bank 2000), ale neimplementuje.
- **Vždy 3 struny** — nerozlišuje 1/2/3 podle registru.
- **Stejná inharmonicita** — všechny 3 struny sdílejí identickou dispersion
  cascade (reálně by se B mírně lišilo).
- **Mono** — prostý součet, žádné stereo.

### Naše řešení (v2)

V `render_note()` se spouští `_dual_rail_string()` N-krát:

```python
for si in range(n_strings):          # 1, 2 nebo 3
    f0_str = f0 * 2^(offset/1200)    # ±detune_cents
    pan = 0.5 + spread * norm         # stereo L/C/R
    mono = _dual_rail_string(f0_str, hammer, ...)
    output_L += cos(pan) * mono
    output_R += sin(pan) * mono
```

| Aspekt            | Teng              | Naše v2                      |
|-------------------|-------------------|------------------------------|
| Počet strun       | vždy 3            | 1/2/3 podle registru         |
| Detuning metoda   | tuning AP coeff   | přímo f0 ±cents              |
| Detuning jednotky | bezrozměrný coeff | centy (hudebně intuitivní)   |
| Coupling          | žádný             | žádný                        |
| Disperze / struna | sdílená           | sdílená (stejné B)           |
| Výstup            | mono sum          | stereo panning L/C/R         |
| Hammer            | sdílený (1 force) | sdílený (1 Chaigne force)    |

### Proč Teng mění tuning AP a ne f0?

Teng mění jen **frakční delay** (tuning allpass koeficient `C`), ne
celočíselnou délku delay line. To znamená:
- Detuning je velmi malý (zlomek půltónu)
- Delay line `M` je stejná pro všechny 3 struny
- V Matlabu elegantní — celý waveguide je jedna transfer function

My měníme přímo f0 → jiná M → každá struna má vlastní delay line.
Výsledek je ekvivalentní, ale flexibilnější (detuning v centech).

### Two-stage decay — proč multi-string funguje

Beating 3 rozladěných strun přirozeně vytváří dvoustupňový decay:

```
Amplitude
  │╲
  │ ╲         ← fáze 1: struny ve fázi, konstruktivní
  │  ╲           interference → hlasitý attack
  │   ╲╲
  │    ╲ ╲    ← přechod: drift z fáze → rychlý pokles
  │     ╲  ╲
  │      ╲   ────────────  ← fáze 2: struny nezávisle,
  │       ╲                   pomalý dozvuk
  └─────────────────────── time
    0    0.5s    1s    2s
```

1. **Začátek**: struny ve fázi → konstruktivní interference → hlasitý zvuk
2. **Po ~200-500ms**: drift z fáze → destruktivní interference → rychlý pokles
3. **Potom**: struny kmitají nezávisle → pomalý tail ze zbývající energie

To napodobuje reálný efekt vertikální/horizontální polarizace (Bridge
přenáší vertikální vibrace efektivněji → rychlý decay; horizontální
zůstávají a doznívají pomaleji). Fyzikální mechanismus je jiný, ale
výsledný zvukový efekt je velmi podobný.

### Stereo z multi-string

Reálné piano má 3 struny fyzicky vedle sebe na bridge → každá budí
soundboard na mírně jiném místě → mírný stereo efekt. My to modelujeme
panováním strun:

- Struna 0: pan = 0.5 - spread/2 (mírně vlevo)
- Struna 1: pan = 0.5 (střed)
- Struna 2: pan = 0.5 + spread/2 (mírně vpravo)

S `stereo_spread=0.3` (default): subtle stereo šíře. S `1.0`: plný L/R.

---

## Velocity mapping a headroom (C++ core)

### Řetězec velocity → zvuk

MIDI velocity (0-127) prochází třemi stupni, z nichž každý přidá
nelineární závislost:

```
MIDI velocity (1-127)
  │
  ▼
vel_norm = velocity / 127         (0.008 .. 1.0, lineární)
  │
  ▼
v0 = max(0.5, vel_norm * 6.0)    (0.5 .. 6.0 m/s, hammer velocity)
  │
  ▼
Chaigne FD hammer                  nelineární F = K × |δ|^p
  │                                p ≈ 2.5 → forte má proporcionálně
  ▼                                víc HF než piano
raw waveguide output               peak ~ 0.7 (pp) .. 10.7 (ff)
  │
  ▼
output_scale = 0.065               fixní, velocity-independent
  │
  ▼
final peak                         0.05 (pp) .. 0.76 (ff)
```

### Proč fixní output_scale (ne velocity-dependent)

V předchozí single-rail implementaci byl `output_scale = 0.4 * vel_norm`,
protože Fourierova excitace měla lineární amplitude škálování — velocity
neovlivňovala spektrální obsah, jen hlasitost.

V dual-rail s Chaigne hammerem je velocity zakódována přímo ve fyzice:
- Vyšší v0 → silnější komprese plsti → ostřejší force puls → víc HF
- Nelineární exponent p ≈ 2.5 způsobuje, že force roste rychleji než
  lineárně s velocity

Kdyby output_scale závisel na vel_norm, zdvojil by se velocity efekt
(jednou ve fyzice hammeru, podruhé v gain). Proto je output_scale fixní
a veškerá dynamika pochází z Chaigne modelu.

### Headroom tabulka (MIDI 60, 3 struny, C++ stereo output)

| vel_idx | MIDI vel | v0 (m/s) | Peak   | Headroom |
|---------|----------|----------|--------|----------|
| 0 (pp)  | 9        | 0.5      | 0.049  | 26.2 dB  |
| 1       | 25       | 1.2      | 0.131  | 17.7 dB  |
| 2       | 41       | 1.9      | 0.229  | 12.8 dB  |
| 3 (mf)  | 57       | 2.7      | 0.323  | 9.8 dB   |
| 4       | 73       | 3.4      | 0.416  | 7.6 dB   |
| 5 (f)   | 89       | 4.2      | 0.526  | 5.6 dB   |
| 6       | 105      | 5.0      | 0.673  | 3.4 dB   |
| 7 (ff)  | 121      | 5.7      | 0.756  | 2.4 dB   |

Pravidlo: **jedna nota nesmí nikdy clipovat**. Clipping z polyphonie
řeší limiter v DspChain (za soundboard IR konvolucí).

### vel_idx → MIDI velocity mapping

ICR batch render používá 8 velocity vrstev (vel_idx 0-7):

```
MIDI velocity = 9 + vel_idx × 16
```

| vel_idx | MIDI vel | vel_norm | Charakter    |
|---------|----------|----------|--------------|
| 0       | 9        | 0.071    | pianissimo   |
| 1       | 25       | 0.197    | piano        |
| 2       | 41       | 0.323    | mezzo-piano  |
| 3       | 57       | 0.449    | mezzo-forte  |
| 4       | 73       | 0.575    | forte        |
| 5       | 89       | 0.701    | forte+       |
| 6       | 105      | 0.827    | fortissimo   |
| 7       | 121      | 0.953    | fff          |

---

## TODO: další kroky

### Hotové (2026-04-09)

- **C++ dual-rail + Chaigne hammer** — kompletní rewrite core v1.0
- **Soundboard IR konvoluce** — automaticky via DspChain v real-time
- **Stereo batch render** — `m060-v03-f48.wav` formát
- **Headroom** — single nota -2.4 dB i při fff

### Další vylepšení (seřazeno podle dopadu)

1. **Velocity-dependent hammer hardness** — K a p z Chaigne tabulky se
   mění s velocity, ne jen v0. Forte = tvrdší plsť = ostřejší spektrum.
2. **String coupling** — feedback ze sumy zpět do waveguidů (Bank 2000).
   Lepší two-stage decay než jen z detuningu.
3. **Damper modeling** — sympathetic resonance při sustain pedálu.
4. **Per-note output calibration** — basové noty mají vyšší raw peak
   než diskant; kalibrace by vyrovnala perceived loudness.
