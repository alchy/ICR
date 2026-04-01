# ICR Sound Editor — Installation

## Requirements

| Component | Minimum version |
|-----------|----------------|
| Python    | 3.10           |
| Node.js   | 18             |
| npm       | 9              |

A virtual MIDI loopback driver is required to route SysEx to ICR:

- **Windows**: [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html) (free)
- **macOS**: built-in IAC Driver (Audio MIDI Setup → MIDI Studio)
- **Linux**: `sudo modprobe snd-virmidi` or JACK MIDI

---

## Backend

```bash
cd sound-editor/backend

# (recommended) create a virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

pip install -r requirements.txt
```

### requirements.txt

```
fastapi>=0.111
uvicorn[standard]>=0.29
scipy>=1.13
numpy>=1.26
mido>=1.3
python-rtmidi>=1.5
```

Start the server:

```bash
uvicorn main:app --reload --port 8000
```

The API will be available at `http://localhost:8000`.  
Interactive docs: `http://localhost:8000/docs`

---

## Frontend

```bash
cd sound-editor/frontend

npm install          # installs Three.js and Vite (local, no global install needed)
npm run dev          # starts Vite dev server on http://localhost:5173
```

Open `http://localhost:5173` in a browser.

### Production build

```bash
npm run build        # output in sound-editor/frontend/dist/
npm run preview      # serve the production build locally
```

---

## First run

1. Start `loopMIDI` (Windows) and create a port named e.g. `ICR`.
2. Start ICR.exe and configure it to listen on that MIDI port.
3. Start the backend: `uvicorn main:app --reload --port 8000`
4. Start the frontend: `npm run dev`
5. Open `http://localhost:5173`
6. In the bottom bar enter the soundbank path and click **Load**:
   ```
   C:/Users/jindr/PycharmProjects/ICR/soundbanks/params-ks-grand-simple.json
   ```
7. In the right panel select the loopMIDI port and click **Connect**.
8. Select a layer from the left panel — cards appear in 3D space.
9. Click **Fit spline**, then **Send bank →** to push parameters to the synth.

---

## Troubleshooting

| Problem | Likely cause | Fix |
|---------|--------------|-----|
| `uvicorn: command not found` | venv not activated | activate `.venv` first |
| `mido: no module named rtmidi` | missing C++ extension | `pip install python-rtmidi` |
| No MIDI ports listed | no loopback driver | install loopMIDI / IAC |
| 3D scene is black | browser WebGL disabled | enable hardware acceleration |
| CORS error in browser | backend not running | start `uvicorn main:app …` |
