# macOS Port Notes

Status: **ready to build** (no code changes needed for C++ engine)

---

## What works without changes

### C++ engine (fully portable)
- CMakeLists.txt handles macOS via `icr_platform_setup()` macro
- CoreAudio + CoreMIDI frameworks linked automatically
- GLFW + OpenGL work on macOS (including Apple Silicon via Rosetta/compat)
- All file I/O uses `std::filesystem` (no `#ifdef` for directory scanning)
- All threading uses `std::atomic` / `std::mutex` (POSIX-compatible)
- miniaudio auto-detects CoreAudio backend

### Build

```bash
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
```

Binaries: `build/bin/Release/icr`, `build/bin/Release/icrgui` (no `.exe`)

### Python pipeline
- `pathlib.Path` used throughout (cross-platform)
- `tempfile.mkdtemp()` uses `$TMPDIR` on macOS
- `soundfile` (libsndfile) works on macOS via Homebrew

---

## Known issues / things to verify

### 1. Python `--icr-exe` default has `.exe` extension

Training pipeline scripts hardcode `build/bin/Release/ICR.exe`:

| File | Fix |
|------|-----|
| `training/pipeline_icr_eval.py` | `_resolve_icr_exe()` tries both `.exe` and no-extension |
| `training/pipeline_smooth_icr_eval.py` | Default `icr_exe` parameter |
| `training/pipeline_full_spline_icr_eval.py` | Default `icr_exe` parameter |
| `run-extract-additive.py` | `--icr-exe` default for 3 subcommands |

Fix: pass `--icr-exe build/bin/Release/icr` explicitly, or update defaults:

```python
import platform
_ICR_DEFAULT = ("build/bin/Release/icr.exe"
                if platform.system() == "Windows"
                else "build/bin/Release/icr")
```

### 2. OpenGL deprecation on macOS

Apple deprecated OpenGL in macOS 10.14 but still ships it. GLFW uses
the Compatibility profile which works through at least macOS 14 (Sonoma).
Long-term: ImGui supports Metal backend if needed.

### 3. Executable permission

CMake sets executable bit automatically. If manual build:
```bash
chmod +x build/bin/Release/icr build/bin/Release/icrgui
```

### 4. Homebrew dependencies (if building GUI)

```bash
brew install cmake
# GLFW and ImGui are fetched by CMake (FetchContent), no Homebrew needed
# OpenGL ships with Xcode command line tools
```

### 5. Apple Silicon (M1/M2/M3/M4)

- AVX2 not available on ARM — CMake auto-skips (`ICR_USE_AVX2` only
  activates on `x86_64` processor)
- NEON auto-vectorization available via `-O3` (GCC/Clang)
- Performance is excellent — Apple Silicon is fast enough for full
  polyphony without SIMD intrinsics

---

## Verification checklist

1. `cmake -B build && cmake --build build` completes without errors
2. `./build/bin/Release/icr --list-cores` shows all 4 cores
3. `./build/bin/Release/icrgui --core PhysicalModelingPianoCore` launches GUI
4. MIDI input works (CoreMIDI port appears in dropdown)
5. Audio output works (CoreAudio, speakers/headphones)
6. Soundboard IR loads and convolver enables
7. Batch render produces valid stereo WAV files
