"""First-run setup, started by PolyglotAgent.exe (in a console window) or by start.py.

Creates .venv next to the app, installs requirements, then exits 0. The caller then
starts the app with the .venv's Python (pythonw.exe on Windows, so no console).
"""
import hashlib
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
MARKER = VENV / "polyglot-ready"
AGAIN = "PolyglotAgent.exe" if os.name == "nt" else "the start script"


def fail(msg: str) -> None:
    print("\n" + msg)
    if sys.stdin and sys.stdin.isatty():
        input("\nPress Enter to close...")
    sys.exit(1)


def venv_hint() -> str:
    if sys.platform.startswith("linux") and shutil.which("apt-get"):
        v = f"{sys.version_info[0]}.{sys.version_info[1]}"
        return f"\nOn Ubuntu/Debian, install Python's venv support and try again:  sudo apt install python{v}-venv"
    return ""


def has_pip(py: Path) -> bool:
    return subprocess.call([str(py), "-m", "pip", "--version"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL) == 0


def seed_pip(py: Path) -> bool:
    """Put pip into an environment created without it (e.g. Debian/Ubuntu without
    python3-venv), using this Python's own pip code. Works with any pip version."""
    try:
        import pip
    except ImportError:
        return False
    where = os.path.dirname(os.path.dirname(pip.__file__))
    # --ignore-installed: the borrowed pip is on sys.path and would count as "already installed"
    code = ("import sys; sys.path.insert(0, sys.argv[1]); from pip._internal.cli.main import main; "
            "sys.exit(main(['install', '--disable-pip-version-check', '--quiet', '--ignore-installed', 'pip']))")
    return subprocess.call([str(py), "-c", code, where]) == 0 and has_pip(py)


def main() -> None:
    print("Polyglot Agent - first-time setup\n")
    if sys.version_info < (3, 10):
        fail(f"Python 3.10 or newer is required (found {sys.version.split()[0]} at {sys.executable}).\n"
             f"Install it from https://www.python.org/downloads/ and run {AGAIN} again.")
    print(f"Using Python {sys.version.split()[0]} at {sys.executable}")
    py = VENV / "Scripts" / "python.exe" if os.name == "nt" else VENV / "bin" / "python"
    if not py.exists():
        print("Creating the app's private environment (.venv)...")
        try:
            venv.EnvBuilder(with_pip=True).create(VENV)
        except Exception:  # noqa: BLE001 - e.g. a Python without ensurepip
            try:
                venv.EnvBuilder(with_pip=False, clear=True).create(VENV)
            except Exception as e:  # noqa: BLE001
                fail(f"Could not create the environment: {e}{venv_hint()}")
    if not has_pip(py) and not seed_pip(py):
        fail("The environment has no pip to install the components with." + venv_hint())
    req = ROOT / "requirements.txt"
    print("Installing components (this takes a minute the first time)...\n")
    cmd = [str(py), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(req)]
    if subprocess.call(cmd) != 0:
        fail(f"Installing components failed. Check your internet connection and run {AGAIN} again.")
    MARKER.write_text(hashlib.sha256(req.read_bytes()).hexdigest(), encoding="utf-8")
    print("\nDone. Starting Polyglot Agent...")


if __name__ == "__main__":
    main()
