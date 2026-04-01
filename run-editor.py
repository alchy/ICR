"""
run-editor.py  —  ICR Sound Editor launcher
────────────────────────────────────────────
Run from anywhere (repo root, IDE, double-click):

    python run-editor.py

Starts the FastAPI backend on http://localhost:8000.
Open sound-editor/frontend in a browser (or run `npm run dev` separately).
"""

import subprocess
import sys
import os
from pathlib import Path

REPO_ROOT   = Path(__file__).parent.resolve()
BACKEND_DIR = REPO_ROOT / "sound-editor" / "backend"

os.chdir(BACKEND_DIR)          # uvicorn must run from backend/ so imports resolve
sys.path.insert(0, str(BACKEND_DIR))

print(f"ICR Sound Editor backend")
print(f"  backend : {BACKEND_DIR}")
print(f"  banks   : {REPO_ROOT / 'soundbanks'}")
print(f"  API     : http://localhost:8000")
print(f"  docs    : http://localhost:8000/docs")
print()

subprocess.run(
    [sys.executable, "-m", "uvicorn", "main:app", "--reload", "--port", "8000"],
    cwd=str(BACKEND_DIR),
)
