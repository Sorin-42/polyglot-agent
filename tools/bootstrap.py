"""First-run setup, started by PolyglotAgent.exe in a console window.

Creates .venv next to the app, installs requirements, then exits 0. The exe
then starts the app with .venv\\Scripts\\pythonw.exe (no console).
"""
import hashlib
import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
MARKER = VENV / "polyglot-ready"


def fail(msg: str) -> None:
    print("\n" + msg)
    input("\nPress Enter to close...")
    sys.exit(1)


def main() -> None:
    print("Polyglot Agent - first-time setup\n")
    if sys.version_info < (3, 10):
        fail(f"Python 3.10 or newer is required (found {sys.version.split()[0]} at {sys.executable}).\n"
             "Install it from https://www.python.org/downloads/ and run PolyglotAgent.exe again.")
    print(f"Using Python {sys.version.split()[0]} at {sys.executable}")
    py = VENV / "Scripts" / "python.exe" if os.name == "nt" else VENV / "bin" / "python"
    venv_pip = True
    if not py.exists():
        print("Creating the app's private environment (.venv)...")
        try:
            venv.EnvBuilder(with_pip=True).create(VENV)
        except Exception:  # noqa: BLE001 - e.g. a Python without ensurepip
            try:
                venv.EnvBuilder(with_pip=False, clear=True).create(VENV)
                venv_pip = False
            except Exception as e:  # noqa: BLE001
                fail(f"Could not create the environment: {e}")
    elif subprocess.call([str(py), "-m", "pip", "--version"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL) != 0:
        venv_pip = False
    req = ROOT / "requirements.txt"
    print("Installing components (this takes a minute the first time)...\n")
    if venv_pip:
        cmd = [str(py), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(req)]
    else:  # drive the environment with this Python's own pip
        cmd = [sys.executable, "-m", "pip", "--python", str(py), "install",
               "--disable-pip-version-check", "-r", str(req)]
    if subprocess.call(cmd) != 0:
        fail("Installing components failed. Check your internet connection and run PolyglotAgent.exe again.")
    MARKER.write_text(hashlib.sha256(req.read_bytes()).hexdigest(), encoding="utf-8")
    print("\nDone. Starting Polyglot Agent...")


if __name__ == "__main__":
    main()
