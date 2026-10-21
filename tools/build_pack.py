"""Builds dist/PolyglotAgent-<version>-python.zip: the app for any system with Python 3.10+
(macOS, Linux, Windows). Unzip it, then run start.command (macOS), ./start.sh (Linux) or
start.bat (Windows); each runs start.py, which installs the components on first use.

Usage (from the project folder, any OS):  python tools/build_pack.py
"""
from __future__ import annotations

import re
import stat
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ["start.py", "start.command", "start.sh", "start.bat", "polyglot_agent.py",
         "requirements.txt", "README.md", "CHANGELOG.md", "tools/bootstrap.py"]
FOLDERS = ["app", "docs/images"]
EXECUTABLE = {"start.py", "start.command", "start.sh"}
LF_ONLY = {"start.py", "start.command", "start.sh"}      # a CRLF shebang won't run on macOS/Linux
SKIP_PARTS = {"__pycache__"}
SKIP_SUFFIXES = {".pyc", ".pyo"}


def version() -> str:
    text = (ROOT / "app" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)


def pack_files() -> list[str]:
    out = list(FILES)
    for folder in FOLDERS:
        for p in sorted((ROOT / folder).rglob("*")):
            rel = p.relative_to(ROOT)
            if p.is_file() and not SKIP_PARTS & set(rel.parts) and p.suffix not in SKIP_SUFFIXES:
                out.append(rel.as_posix())
    return out


def build(dest_dir: Path | None = None) -> Path:
    ver = version()
    top = f"PolyglotAgent-{ver}"
    dest_dir = dest_dir or ROOT / "dist"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{top}-python.zip"
    tmp = dest.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in pack_files():
            src = ROOT / rel
            data = src.read_bytes()
            if rel in LF_ONLY:
                data = data.replace(b"\r\n", b"\n")
            info = zipfile.ZipInfo(f"{top}/{rel}", time.localtime(src.stat().st_mtime)[:6])
            info.create_system = 3            # Unix, so unzip / Archive Utility keep the mode bits
            mode = 0o755 if rel in EXECUTABLE else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    tmp.replace(dest)
    return dest


if __name__ == "__main__":
    out = build(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
    print(f"Built {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out} "
          f"({out.stat().st_size // 1024} KB)")
