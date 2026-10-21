#!/usr/bin/env python3
"""Start Polyglot Agent on any system with Python 3.10 or newer: macOS, Linux or Windows.

    python3 start.py          (or double-click start.command on macOS, run ./start.sh on Linux)

The first run creates a private environment (.venv) next to this file and installs the
app's components, which takes about a minute. Later runs start the app straight away,
and reinstall only when requirements.txt changes. Your data stays in data/ here.
"""
import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
MARKER = VENV / "polyglot-ready"          # written by tools/bootstrap.py after a good install
REQUIREMENTS = ROOT / "requirements.txt"


def venv_python(windowed: bool = False) -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / ("pythonw.exe" if windowed else "python.exe")
    return VENV / "bin" / "python"


def installed() -> bool:
    try:
        want = hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()
        return venv_python().exists() and MARKER.read_text(encoding="utf-8").strip() == want
    except OSError:
        return False


def main() -> None:
    if sys.version_info < (3, 10):
        sys.exit(f"Polyglot Agent needs Python 3.10 or newer (this is {sys.version.split()[0]}).\n"
                 "Get it from https://www.python.org/downloads/")
    if not installed():
        code = subprocess.call([sys.executable, str(ROOT / "tools" / "bootstrap.py")])
        if code != 0:
            sys.exit(code)
    app = [str(ROOT / "polyglot_agent.py"), *sys.argv[1:]]
    os.chdir(ROOT)
    if os.name == "nt":   # run windowed and let this console close
        subprocess.Popen([str(venv_python(windowed=True)), *app],
                         creationflags=0x00000008 | 0x00000200)   # DETACHED_PROCESS | NEW_PROCESS_GROUP
        return
    print("Starting Polyglot Agent. It opens in a window or browser tab; close that (or press Ctrl+C here) to quit.",
          flush=True)
    py = str(venv_python())
    os.execv(py, [py, *app])


if __name__ == "__main__":
    main()
