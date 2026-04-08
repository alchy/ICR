# macOS — Required Changes

Status: **pre-implementation / parking lot**
Target: port ICR training pipeline to macOS

---

## Co funguje bez změn

- `tempfile.mkdtemp()` — cross-platform, na macOS použije `$TMPDIR`
- Python pipeline (extrakce, NN trénink, spline_fix) — čistý Python/PyTorch
- Všechny cesty přes `pathlib.Path` — cross-platform
- CMakeLists.txt — GLFW a ImGui mají macOS support

---

## Potřebné změny

### 1. ICR.exe → ICR (bez přípony)

Na Unixu binárky nemají `.exe`. Default cesta v `_resolve_icr_exe()` je
hardcoded na Windows:

```python
# training/pipeline_icr_eval.py
# training/pipeline_smooth_icr_eval.py
# training/pipeline_full_spline_icr_eval.py

icr_exe = "build/bin/Release/ICR.exe"   # ← Windows only
```

**Fix:**

```python
import platform

def _default_icr_exe() -> str:
    if platform.system() == "Windows":
        return "build/bin/Release/ICR.exe"
    return "build/bin/Release/ICR"
```

Použít místo hardcoded stringu ve všech třech pipeline souborech
a v `run-training.py` defaults.

Totéž v `_resolve_icr_exe()` v `pipeline_icr_eval.py`:

```python
def _resolve_icr_exe(icr_exe: str) -> str:
    p = Path(icr_exe)
    if p.is_absolute():
        return str(p)
    repo_root = Path(__file__).parent.parent
    # Try both .exe and no-extension variants
    for candidate in [repo_root / icr_exe,
                      repo_root / icr_exe.replace(".exe", "")]:
        if candidate.exists():
            return str(candidate)
    return icr_exe
```

### 2. CMake build na macOS

```bash
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --target ICR
# binárka: build/bin/Release/ICR  (bez .exe)
```

Potenciální problémy:
- OpenGL/GLFW na macOS vyžaduje `-framework OpenGL -framework Cocoa`
  — CMakeLists.txt to pravděpodobně řeší přes `find_package(OpenGL)`
- Metal backend (Apple Silicon) — GLFW standardně používá OpenGL přes
  Compatibility profile, mělo by fungovat

### 3. run-training.py — default pro --icr-exe

```python
# Aktuálně v run-training.py:
icr.add_argument("--icr-exe", default="build/bin/Release/ICR.exe", ...)

# Změnit na:
import platform
_ICR_DEFAULT = ("build/bin/Release/ICR.exe"
                if platform.system() == "Windows"
                else "build/bin/Release/ICR")

icr.add_argument("--icr-exe", default=_ICR_DEFAULT, ...)
# totéž pro smooth-icr-eval a full-spline-icr-eval subcommands
```

### 4. Subprocess — ICR.exe spouštění

`subprocess.run([self.icr_exe, ...])` funguje cross-platform bez změn,
pokud je binárka executable (`chmod +x build/bin/Release/ICR` po buildu).

CMake by měl nastavit executable bit automaticky, ale pro jistotu:

```bash
chmod +x build/bin/Release/ICR
```

### 5. Audio knihovny

Pokud ICR používá pro přehrávání PortAudio nebo CoreAudio:
- PortAudio má macOS backend
- CoreAudio je nativní macOS API

Tréninková pipeline WAV soubory jen čte/zapisuje přes `soundfile`
(libsndfile) — cross-platform bez změn.

---

## Soubory které vyžadují změny

| Soubor | Změna |
|---|---|
| `training/pipeline_icr_eval.py` | `_resolve_icr_exe()` + default |
| `training/pipeline_smooth_icr_eval.py` | default `icr_exe` parametr |
| `training/pipeline_full_spline_icr_eval.py` | default `icr_exe` parametr |
| `run-training.py` | `--icr-exe` default pro 3 subcommands |
| `CMakeLists.txt` | ověřit macOS build (OpenGL/Metal) |

---

## Co ověřit po portu

1. `cmake --build` projde bez chyb na Apple Silicon (M1/M2/M3)
2. `ICR --render-batch` renderuje správně (WAV výstup identický s Windows)
3. Celý `icr-eval` pipeline projde end-to-end
4. Temp adresáře se vytváří a mažou správně (`$TMPDIR`)
5. Cesty s mezerami fungují (macOS uživatelské adresáře je mohou obsahovat)
