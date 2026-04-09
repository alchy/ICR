# Audit: Naše implementace vs Teng (2012) Matlab kód

Řádek po řádku srovnání Tengova Matlab kódu (Appendix II) s naší
C++ implementací. Identifikuje odchylky a jejich dopad.

## PART I: HAMMER MODEL

### Teng
```matlab
Fs=44100;  N=65;  L=0.62;  Ms=3.93/1000;  Mh=2.97/1000;
K=4.5*10^9;  T=670;  p=2.5;  alpha=0.12;
v=4;  % Initial hammer velocity — FIXED for all notes
```
- Parametry hardcoded pro C4
- **Stejný force signal pro VŠECHNY noty** ("the force signal for the
  C4 string is used as input to be fed into the digital waveguide model")

### My
```cpp
AnchorParams ap = interp_params(midi);  // interpolace C2/C4/C7
ap.K_stiff *= (1 + K_hardening * vel_norm);
ap.p_exp += p_hardening * vel_norm;
ap.Ms_g *= gauge * string_mass_scale;
ap.Mh_g *= hammer_mass_scale;
```
- Per-note interpolace z 3 anchor points
- Velocity-dependent K a p
- Gauge a mass scales z banky

**Odchylka**: My jsme sofistikovanější, ale Teng-ův přístup (jeden force
pro vše) je jednodušší a funguje — force shape se nemění dramaticky mezi
notami, hlavní rozdíl je v waveguide délce (f0).

**Dopad**: Malý. Náš přístup je lepší fyzikálně.

---

## KRITICKÁ ODCHYLKA #1: Konvoluce force × body IR

### Teng
```matlab
v = F/(2*R0);              % force → velocity
ir = wavread('IR.wav');    % soundboard body impulse response
v_new = conv(v, ir);       % ← KONVOLUCE PŘED WAVEGUIDEM
v_in = [v_new' zeros(...)]; % pad to output length
```

### My
```cpp
// force → velocity (přímo do waveguidu, žádná konvoluce)
for (int i = 0; i < actual_len; i++)
    v_in[i] = F[i] * inv_2R0;
// IR konvoluce je až na master bus (DspChain, post-hoc)
```

**Odchylka**: ZÁSADNÍ. Teng konvolvuje hammer force s body IR **před**
injekcí do waveguidu. To znamená:
1. Soundboard rezonance jsou součástí excitace
2. Waveguide cirkuluje signál, který už obsahuje body character
3. Výsledek: bohatší harmonický obsah od prvního oběhu

My injektujeme raw force (+ trochu noise) → waveguide cirkuluje chudý
signál → body IR je až post-hoc na master bus (nemění co je ve smyčce).

**Dopad**: VELKÝ. Toto je pravděpodobně hlavní důvod "nylonového" zvuku.
IR konvoluce před waveguidem přidá:
- Soundboard rezonanční frekvence (200-800 Hz peaks)
- Bohatší harmonický obsah v excitaci
- Bridge character přímo v signálu

---

## PART II: STRING MODEL — Loss Filter

### Teng
```matlab
gl = -0.997;   % DC gain (negative! → sign inversion included)
al = -0.001;   % pole coefficient (very small → almost bypass)
Hl = gl*(1+al)/(1+al*z^-1);
```
- `gl` je ZÁPORNÉ → zahrnuje sign inversion (-1) v sobě
- `al = -0.001` → pole extrémně blízko 0 → skoro žádný spectral tilt
- Loss filter je hlavně DC gain, minimální frekvenční závislost
- `gl` se mění per-register: -0.96 (bas) to -0.997 (diskant)

### My
```cpp
LossFilter lf = compute_loss_filter(f0, T60_fund, T60_nyq_eff, sr);
// g_dc ≈ 0.996, pole ≈ 0.10 (MIDI 60)
// H(z) = g * (1-b) / (1 - b*z^-1)
```
- Samostatná sign inversion (-1) na bridge
- Pole z T60_fund/T60_nyq → výrazná frekvenční závislost
- Pole ≈ 0.10 pro MIDI 60 (vs Teng al = 0.001)

**Odchylka**: VÝZNAMNÁ.
1. Teng má `al = -0.001` → loss filter je **skoro bypass** (jen DC gain)
2. My máme `pole = 0.10` → **100× silnější spectral tilt**
3. Teng: HF decay je řízeno hlavně dispersion cascade delay
4. My: HF decay je řízeno loss filter + dispersion

**Dopad**: STŘEDNÍ-VELKÝ. Náš silnější loss filter pole může příliš
tlumit HF, čímž se zvuk stává "nylonový". Teng spoléhá na dispersion
cascade pro HF charakter, ne na loss filter.

---

## PART II: STRING MODEL — Dispersion

### Teng
```matlab
ad = -0.30;   % allpass coefficient (SILNĚJŠÍ per-stage)
ap_num = 14;  % stages for C4 (261 Hz)
```
- Per-stage koeficient -0.30 (silný)
- Počet stupňů per-register: 0 (>3kHz) to 20 (<120 Hz)
- C4: 14 stupňů × coeff -0.30

### My
```cpp
a_disp = -0.15;  // per-stage coefficient (SLABŠÍ)
n_disp = min(16, int(B * N^2 * 0.5));  // auto from B
// C4: ~11 stages × coeff -0.15
```

**Odchylka**: VÝZNAMNÁ.
- Teng: 14 × (-0.30) = silná disperze, hodně fázového posunu
- My: 11 × (-0.15) = slabší disperze
- Celkový efekt: Teng má ~2× silnější inharmonicity stretching

**Dopad**: STŘEDNÍ. Silnější disperze = výraznější "piano" charakter
(kovový, inharmonický). My máme příliš harmonický zvuk.

---

## PART II: STRING MODEL — Delay Line Length

### Teng
```matlab
N_exact = (2*pi + ap_num*atan(((ad^2-1)*sin(2*pi*f0/Fs))/...
          (2*ad+(ad^2+1)*cos(2*pi*f0/Fs)))) / (2*pi*f0/Fs);
M = floor(N_exact/2);
P = N_exact - 2*M;
C = (1-P)/(1+P);
```
- Kompenzuje group delay dispersion cascade **přesně** (analyticky z atan)
- `N_exact` zahrnuje loss + dispersion + tuning delay

### My
```cpp
float filt_del = loss_delay(lf);        // pole / (1-pole²)
float disp_del = n_disp * allpass_delay_dc(a_disp);  // (1-a)/(1+a) per stage
float N_comp = N_period - filt_del - disp_del;
```
- Kompenzujeme **DC group delay** (přiblížení)
- Teng kompenzuje přesně na fundamentální frekvenci (atan formulace)

**Odchylka**: MALÁ. Oba přístupy dávají podobný výsledek. Tengova
analytická formulace je přesnější pro vyšší frekvence.

**Dopad**: Minimální — ladění je v obou případech správné.

---

## PART II: STRING MODEL — Transfer Function Approach

### Teng
```matlab
DW1 = DL1/(1+H1*DL1*DL1*DL2*DL2) + DL2*DL2*DL1*(-1)/(1+H1*DL1*DL1*DL2*DL2);
output1 = filter(b,a,v_in);  % ← Matlab filter() na celý vstupní signál
```
- Waveguide jako transfer function
- Matlab `filter()` aplikuje celou TF najednou
- Vstup `v_in` je konvolvovaný force (100000 vzorků, padded zeros)
- Vstup trvá celou dobu syntézy (ne jen hammer contact)

### My
```cpp
for (int n = 0; n < n_samples; n++) {
    float h_in = (t < hammer_len) ? hammer_v_in[t] : 0.f;
    float sample = dual_rail_tick(strings[si], h_in);
}
```
- Sample-by-sample simulace
- Hammer force injektováno jen během kontaktu (~200 vzorků)
- Po kontaktu: h_in = 0 → waveguide osciluje volně

**Odchylka**: KRITICKÁ v kontextu IR konvoluce!
- Teng: `v_in = conv(F, IR)` → IR trvá ~100ms → vstup do waveguidu
  dodává energii **i po skončení hammer kontaktu** (soundboard rezonance
  doznívají a přidávají se do waveguidu)
- My: po ~4ms hammer kontaktu přestaneme dodávat energii → waveguide
  má jen to, co zůstalo z prvních pár oběhů

**Dopad**: VELKÝ. Teng kontinuálně dodává IR-obohacený signál po celou
dobu syntézy. My dodáváme jen 4ms raw force pulse.

---

## PART II: Multi-String

### Teng
```matlab
Hfd1 = (C+z^-1)/(1+C*z^-1);
Hfd2 = (C*(1+offtune)+z^-1)/(1+C*(1+offtune)*z^-1);
Hfd3 = (C*(1-offtune)+z^-1)/(1+C*(1-offtune)*z^-1);
output = output1 + output2 + output3;  % mono sum
```
- Vždy 3 strings
- Detuning přes tuning allpass coefficient
- Mono output (prostý součet)

### My
```cpp
for (int si = 0; si < n_strings; si++) {
    // f0_str = f0 * 2^(offset_cents/1200)
    // pan_l, pan_r z keyboard_spread + stereo_spread
    mono = dual_rail_tick(strings[si], h_in);
    output_L += gain_L * mono;
    output_R += gain_R * mono;
}
```
- 1-3 strings per-register
- Detuning přes f0 (jiná delay line délka)
- Stereo panning

**Odchylka**: MALÁ. Funkčně ekvivalentní, my jsme vylepšeni (stereo, per-register).

---

## SUMMARY: Odchylky seřazené podle dopadu

| # | Odchylka | Dopad | Fix |
|---|----------|-------|-----|
| **1** | **Force × IR konvoluce PŘED waveguidem** | KRITICKÝ | Konvolvovat force s IR před injekcí |
| **2** | **Loss filter pole: 0.10 vs 0.001** | VELKÝ | Snížit pole (méně spectral tilt v loss) |
| **3** | **Dispersion: -0.15 vs -0.30, 11 vs 14 stages** | STŘEDNÍ | Zvýšit coeff a/nebo stages |
| **4** | **Force jen 4ms vs celý výstup (IR tail)** | VELKÝ | Vyplývá z #1 — IR konvoluce prodlouží excitaci |
| 5 | Hammer per-note vs fixed C4 | Malý | Naše je lepší |
| 6 | Delay comp: DC approx vs atan | Minimální | OK |
| 7 | Multi-string: stereo vs mono | Pozitivní | Naše je lepší |

## DOPORUČENÍ

### Fix #1: Konvoluce force × IR (PRIORITA 1)
```cpp
// V compute_force nebo initVoice:
// 1. Načíst IR (jednou při load, ne per-note)
// 2. Konvolvovat v_in s IR
// 3. Pad výsledek na n_samples (ne jen hammer_len)
// 4. Injektovat konvolvovaný signál do waveguidu po celou dobu
```

### Fix #2: Snížit loss filter pole (PRIORITA 2)
```cpp
// Teng: al = -0.001 → skoro žádný spectral tilt v loss filtru
// My: pole = 0.10 → příliš silný tilt
// Fix: snížit T60_nyq nebo přidat parametr pro loss pole override
```

### Fix #3: Silnější disperze (PRIORITA 3)
```json
// V bance: zvýšit disp_coeff z -0.15 na -0.25 až -0.30
// A/nebo: zvýšit n_disp_stages
```
