"""Client-side backend for the memory tool (memory_20250818).

Claude addresses files under the virtual root /memories; we map that onto a
local folder and refuse anything that would escape it.
"""
from __future__ import annotations

import shutil
import urllib.parse
from pathlib import Path

ROOT = "/memories"
MAX_FILE_BYTES = 256 * 1024
MAX_VIEW_CHARS = 16_000


class MemoryError_(Exception):
    pass


def _human(n: int) -> str:
    for unit in ("", "K", "M", "G"):
        if n < 1024 or unit == "G":
            return f"{n:.1f}{unit}" if unit else f"{n}"
        n /= 1024
    return str(n)


class MemoryStore:
    def __init__(self, base_dir: Path):
        self.base = Path(base_dir).resolve()
        self.base.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ paths
    def resolve(self, vpath: str) -> Path:
        if not isinstance(vpath, str) or not vpath:
            raise MemoryError_("Error: a path is required")
        decoded = urllib.parse.unquote(vpath).replace("\\", "/")
        if decoded != ROOT and not decoded.startswith(ROOT + "/"):
            raise MemoryError_(f"Error: The path {vpath} must be inside {ROOT}")
        rel = decoded[len(ROOT):].lstrip("/")
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts):
            raise MemoryError_(f"Error: The path {vpath} is not allowed")
        full = (self.base.joinpath(*parts)).resolve()
        try:
            full.relative_to(self.base)
        except ValueError:
            raise MemoryError_(f"Error: The path {vpath} is not allowed") from None
        return full

    def vpath(self, p: Path) -> str:
        rel = p.resolve().relative_to(self.base).as_posix()
        return ROOT if rel in ("", ".") else f"{ROOT}/{rel}"

    # --------------------------------------------------------------- dispatch
    def execute(self, inp: dict) -> str:
        cmd = (inp or {}).get("command")
        fn = getattr(self, f"cmd_{cmd}", None)
        if fn is None:
            raise MemoryError_(f"Error: Unknown memory command: {cmd}")
        return fn(inp)

    # ---------------------------------------------------------------- commands
    def cmd_view(self, inp: dict) -> str:
        path = inp.get("path", ROOT)
        p = self.resolve(path)
        if not p.exists():
            raise MemoryError_(f"The path {path} does not exist. Please provide a valid path.")
        if p.is_dir():
            lines = []
            for child in self._walk(p, depth=2):
                size = sum(f.stat().st_size for f in child.rglob("*") if f.is_file()) if child.is_dir() \
                    else child.stat().st_size
                lines.append(f"{_human(size)}\t{self.vpath(child)}")
            own = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            header = (f"Here're the files and directories up to 2 levels deep in {path}, "
                      f"excluding hidden items and node_modules:")
            return "\n".join([header, f"{_human(own)}\t{path}"] + lines)
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.split("\n")
        if len(lines) > 999_999:
            raise MemoryError_(f"File {path} exceeds maximum line limit of 999,999 lines.")
        start, end = 1, len(lines)
        vr = inp.get("view_range")
        if vr:
            try:
                start = max(1, int(vr[0]))
                end = len(lines) if int(vr[1]) == -1 else min(len(lines), int(vr[1]))
            except (TypeError, ValueError, IndexError):
                raise MemoryError_("Error: view_range must be [start_line, end_line]") from None
        body = "\n".join(f"{i:>6}\t{lines[i - 1]}" for i in range(start, end + 1))
        if len(body) > MAX_VIEW_CHARS:
            body = body[:MAX_VIEW_CHARS] + "\n... (truncated; use view_range to see more)"
        return f"Here's the content of {path} with line numbers:\n{body}"

    def _walk(self, p: Path, depth: int):
        if depth == 0:
            return
        for child in sorted(p.iterdir(), key=lambda c: c.name.lower()):
            if child.name.startswith(".") or child.name == "node_modules":
                continue
            yield child
            if child.is_dir():
                yield from self._walk(child, depth - 1)

    def cmd_create(self, inp: dict) -> str:
        path = inp.get("path")
        text = inp.get("file_text", "")
        p = self.resolve(path)
        if p == self.base:
            raise MemoryError_("Error: cannot create the /memories root as a file")
        if p.is_dir():
            raise MemoryError_(f"Error: {path} is a directory")
        data = str(text).encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise MemoryError_(f"Error: file too large ({len(data)} bytes, max {MAX_FILE_BYTES}). "
                               "Split it into smaller files.")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return f"File created successfully at: {path}"

    def _read_file(self, path: str) -> tuple[Path, str]:
        p = self.resolve(path)
        if not p.is_file():
            raise MemoryError_(f"Error: The path {path} does not exist. Please provide a valid path.")
        return p, p.read_text(encoding="utf-8", errors="replace")

    def cmd_str_replace(self, inp: dict) -> str:
        path = inp.get("path")
        old = inp.get("old_str")
        new = inp.get("new_str") or ""
        if not isinstance(old, str) or old == "":
            raise MemoryError_("Error: old_str is required")
        p, text = self._read_file(path)
        count = text.count(old)
        if count == 0:
            raise MemoryError_(f"No replacement was performed, old_str `{old}` did not appear verbatim in {path}.")
        if count > 1:
            line_nos = []
            idx = text.find(old)
            while idx != -1:
                line_nos.append(text.count("\n", 0, idx) + 1)
                idx = text.find(old, idx + 1)
            raise MemoryError_(f"No replacement was performed. Multiple occurrences of old_str `{old}` "
                               f"in lines: {line_nos}. Please ensure it is unique")
        new_text = text.replace(old, new, 1)
        p.write_text(new_text, encoding="utf-8")
        at = new_text[: new_text.find(new) if new else text.find(old)].count("\n")
        lines = new_text.split("\n")
        lo, hi = max(0, at - 3), min(len(lines), at + new.count("\n") + 4)
        snippet = "\n".join(f"{i + 1:>6}\t{lines[i]}" for i in range(lo, hi))
        return f"The memory file has been edited.\n{snippet}"

    def cmd_insert(self, inp: dict) -> str:
        path = inp.get("path")
        p, text = self._read_file(path)
        lines = text.split("\n") if text else []
        try:
            n = int(inp.get("insert_line"))
        except (TypeError, ValueError):
            n = -1
        if n < 0 or n > len(lines):
            raise MemoryError_(f"Error: Invalid `insert_line` parameter: {inp.get('insert_line')}. "
                               f"It should be within the range of lines of the file: [0, {len(lines)}]")
        new_lines = str(inp.get("insert_text", "")).split("\n")
        if new_lines and new_lines[-1] == "":
            new_lines = new_lines[:-1]
        lines[n:n] = new_lines
        p.write_text("\n".join(lines), encoding="utf-8")
        return f"The file {path} has been edited."

    def cmd_delete(self, inp: dict) -> str:
        path = inp.get("path")
        p = self.resolve(path)
        if p == self.base:
            raise MemoryError_("Error: cannot delete the /memories root")
        if not p.exists():
            raise MemoryError_(f"Error: The path {path} does not exist")
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        return f"Successfully deleted {path}"

    def cmd_rename(self, inp: dict) -> str:
        old, new = inp.get("old_path"), inp.get("new_path")
        po, pn = self.resolve(old), self.resolve(new)
        if po == self.base:
            raise MemoryError_("Error: cannot rename the /memories root")
        if not po.exists():
            raise MemoryError_(f"Error: The path {old} does not exist")
        if pn.exists():
            raise MemoryError_(f"Error: The destination {new} already exists")
        pn.parent.mkdir(parents=True, exist_ok=True)
        po.rename(pn)
        return f"Successfully renamed {old} to {new}"

    # ------------------------------------------------------------- UI helpers
    def tree(self) -> list[dict]:
        out = []
        for f in sorted(self.base.rglob("*")):
            if f.is_file() and not any(part.startswith(".") for part in f.relative_to(self.base).parts):
                out.append({"path": self.vpath(f), "size": f.stat().st_size,
                            "mtime": f.stat().st_mtime})
        return out

    def snapshot(self, max_chars: int = 6000) -> str:
        """Compact listing + key files, injected into the system prompt."""
        files = self.tree()
        if not files:
            return "(memory is empty - this may be the first conversation)"
        parts = ["Files:"] + [f"- {f['path']} ({_human(f['size'])})" for f in files]
        budget = max_chars
        for name in ("profile.md", "preferences.md"):
            p = self.base / name
            if p.is_file() and budget > 200:
                txt = p.read_text(encoding="utf-8", errors="replace")[: budget - 100]
                budget -= len(txt)
                parts.append(f"\n--- {ROOT}/{name} ---\n{txt}")
        return "\n".join(parts)
