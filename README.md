```bash
cmake -B build
cmake --build build --config Release
```

```bash
# Physical
./build/bin/Release/icrgui --core PhysicalModelingPianoCore

# Headless CLI
./build/bin/Release/icr --core PhysicalModelingPianoCore

# List available cores
./build/bin/Release/icr --list-cores
```

- Python 3.12, numpy, scipy, soundfile
