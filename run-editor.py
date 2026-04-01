"""
run-editor.py  —  ICR Sound Editor launcher
────────────────────────────────────────────
Run from anywhere (repo root, IDE, double-click):

    python run-editor.py

Starts:
  • FastAPI backend  on http://localhost:8000
  • Vite dev server  on http://localhost:5173  (opens in browser automatically)

Ctrl+C shuts down both.
"""

import subprocess
import sys
import os
import time
import webbrowser
import threading
import shutil
from pathlib import Path

REPO_ROOT    = Path(__file__).parent.resolve()
BACKEND_DIR  = REPO_ROOT / "sound-editor" / "backend"
FRONTEND_DIR = REPO_ROOT / "sound-editor" / "frontend"

# ── Preflight checks ──────────────────────────────────────────────────────────

def check(ok, msg):
    if not ok:
        print(f"  ERROR: {msg}")
        sys.exit(1)

check(BACKEND_DIR.exists(),  f"backend dir not found: {BACKEND_DIR}")
check(FRONTEND_DIR.exists(), f"frontend dir not found: {FRONTEND_DIR}")
check((FRONTEND_DIR / "node_modules").exists(),
      f"node_modules missing — run:  cd {FRONTEND_DIR}  &&  npm install")

# ── Launch ────────────────────────────────────────────────────────────────────

npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"

print("ICR Sound Editor")
print(f"  backend : http://localhost:8000")
print(f"  editor  : http://localhost:5173")
print(f"  banks   : {REPO_ROOT / 'soundbanks'}")
print()

# Backend: uvicorn in a thread
backend_proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "main:app", "--reload", "--port", "8000"],
    cwd=str(BACKEND_DIR),
)

# Frontend: Vite dev server
frontend_proc = subprocess.Popen(
    [npm_cmd, "run", "dev"],
    cwd=str(FRONTEND_DIR),
)

# Open browser after a short delay
def _open():
    time.sleep(2)
    webbrowser.open("http://localhost:5173")

threading.Thread(target=_open, daemon=True).start()

# Wait — Ctrl+C kills both
try:
    backend_proc.wait()
except KeyboardInterrupt:
    pass
finally:
    backend_proc.terminate()
    frontend_proc.terminate()
    print("\nStopped.")
