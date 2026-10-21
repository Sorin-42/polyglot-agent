"""Shell (PowerShell on Windows, bash elsewhere) and text-editor tools that run on the
user's computer (approval-gated)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

MAX_OUTPUT = 20_000
EDITOR_NAME = "str_replace_based_edit_tool"
EDITOR_WRITE_COMMANDS = {"create", "str_replace", "insert"}

POWERSHELL_TOOL = {
    "name": "powershell",
    "description": (
        "Run a PowerShell command on the user's Windows PC and get stdout/stderr back. "
        "Use it for inspecting files and folders, running scripts, builds, tests, git, pip/conda, "
        "and reading logs - it is faster and more reliable than clicking through the GUI. "
        "Each call is a fresh process; pass `cwd` to choose the working directory. "
        "Avoid interactive commands (they will hang until the timeout). "
        "The user may be asked to approve each command, so keep commands focused and explain "
        "destructive ones first."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "PowerShell command or script to run."},
            "cwd": {"type": "string", "description": "Working directory (absolute path). Optional."},
            "timeout_sec": {"type": "integer", "description": "Timeout in seconds (default 120, max 1800)."},
        },
        "required": ["command"],
    },
}

# macOS and Linux get a bash tool instead. Same input and handling; only the name and
# description differ, so the agent isn't told to write PowerShell on a Mac.
SHELL_TOOL = {
    "name": "shell",
    "description": (
        "Run a shell command (bash, or sh if bash is missing) on the user's computer and get "
        "stdout/stderr back. Use it for inspecting files and folders, running scripts, builds, "
        "tests, git, pip/conda, and reading logs. "
        "Each call is a fresh process; pass `cwd` to choose the working directory. "
        "Avoid interactive commands (they will hang until the timeout). "
        "The user may be asked to approve each command, so keep commands focused and explain "
        "destructive ones first."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command or script to run."},
            "cwd": {"type": "string", "description": "Working directory (absolute path). Optional."},
            "timeout_sec": {"type": "integer", "description": "Timeout in seconds (default 120, max 1800)."},
        },
        "required": ["command"],
    },
}
SHELL_TOOL_NAMES = ("powershell", "shell")


def shell_tool() -> dict:
    return POWERSHELL_TOOL if sys.platform == "win32" else SHELL_TOOL


WINDOWS_TOOL = {
    "name": "windows",
    "description": (
        "Manage desktop windows without screenshots. action=list returns the titles of open "
        "windows; action=focus brings the window whose title contains `title` to the front; "
        "action=capture switches screenshots between `window` (only the focused window, the "
        "default) and `screen` (the whole screen without the taskbar, e.g. for drag-and-drop "
        "between windows). The user controls this with a switch; switching to `screen` asks "
        "the user for permission."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "focus", "capture"]},
            "title": {"type": "string", "description": "Part of the window title (for focus)."},
            "mode": {"type": "string", "enum": ["window", "screen"], "description": "For capture."},
        },
        "required": ["action"],
    },
}


# ------------------------------------------------------------- folder access
def norm_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.expandvars(os.path.expanduser(path))))


def is_within(path: str, folder: str) -> bool:
    p, f = norm_path(path), norm_path(folder)
    try:
        return os.path.commonpath([p, f]) == f
    except ValueError:  # different drives
        return False


def folder_for(path: str) -> str:
    """The folder a read of `path` should be granted on."""
    full = os.path.abspath(os.path.expandvars(os.path.expanduser(path)))
    return full if os.path.isdir(full) else os.path.dirname(full)


def _truncate(s: str, limit: int = MAX_OUTPUT) -> str:
    if len(s) <= limit:
        return s
    half = limit // 2
    return s[:half] + f"\n... [{len(s) - limit} characters truncated] ...\n" + s[-half:]


def _shell_argv(command: str) -> list[str]:
    if sys.platform == "win32":
        exe = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
        prefix = ("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
                  "$OutputEncoding=[System.Text.Encoding]::UTF8; $ProgressPreference='SilentlyContinue'; ")
        return [exe, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", prefix + command]
    return [shutil.which("bash") or "/bin/sh", "-c", command]


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the shell and everything it started (children can hold our pipes)."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True,
                       creationflags=0x08000000)
    else:  # the shell leads its own process group (start_new_session)
        try:
            os.killpg(proc.pid, 9)
        except OSError:
            pass
    try:
        proc.kill()
    except OSError:
        pass


def run_powershell(inp: dict, stop_event: threading.Event) -> tuple[str, bool]:
    """Returns (output, is_error)."""
    command = inp.get("command")
    if not isinstance(command, str) or not command.strip():
        return "Error: `command` is required", True
    cwd = str(inp.get("cwd") or Path.home())
    if not os.path.isdir(cwd):
        return f"Error: cwd does not exist: {cwd}", True
    try:
        timeout = max(1, min(int(inp.get("timeout_sec") or 120), 1800))
    except (TypeError, ValueError):
        timeout = 120
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    try:
        proc = subprocess.Popen(_shell_argv(command), cwd=cwd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                creationflags=flags, start_new_session=sys.platform != "win32")
    except OSError as e:
        return f"Error: could not start the shell: {e}", True

    chunks: dict[str, list[bytes]] = {"out": [], "err": []}

    def pump(stream, key):
        try:
            for line in iter(stream.readline, b""):
                chunks[key].append(line)
        except (OSError, ValueError):
            pass

    readers = [threading.Thread(target=pump, args=(proc.stdout, "out"), daemon=True),
               threading.Thread(target=pump, args=(proc.stderr, "err"), daemon=True)]
    for r in readers:
        r.start()

    start = time.time()
    why = ""
    while proc.poll() is None:
        if stop_event.is_set():
            why = "stopped by user"
        elif time.time() - start > timeout:
            why = f"timed out after {timeout}s"
        if why:
            _kill_tree(proc)
            break
        time.sleep(0.2)
    for r in readers:
        # a background child may keep the pipe open after PowerShell exits
        r.join(timeout=3)
    note = ""
    if any(r.is_alive() for r in readers):
        note = "\n[note: a background process is still running and holding the output pipe]"
    text = b"".join(chunks["out"]).decode("utf-8", "replace")
    etext = b"".join(chunks["err"]).decode("utf-8", "replace")
    body = text
    if etext.strip():
        body += ("\n" if body else "") + "[stderr]\n" + etext
    body = body.strip() or "(no output)"
    if why:
        return _truncate(f"[process {why}]\n{body}{note}"), True
    return _truncate(f"exit code {proc.returncode}\n{body}{note}"), proc.returncode != 0


# ----------------------------------------------------------------- editor
def describe_editor_write(inp: dict) -> str:
    cmd = inp.get("command")
    path = inp.get("path", "?")
    if cmd == "create":
        txt = str(inp.get("file_text", ""))
        return f"Create/overwrite file {path} ({len(txt)} chars):\n{_truncate(txt, 1500)}"
    if cmd == "str_replace":
        return (f"Edit {path}\n--- replace ---\n{_truncate(str(inp.get('old_str', '')), 800)}\n"
                f"--- with ---\n{_truncate(str(inp.get('new_str', '')), 800)}")
    if cmd == "insert":
        return (f"Insert into {path} after line {inp.get('insert_line')}:\n"
                f"{_truncate(str(inp.get('insert_text', inp.get('new_str', ''))), 1200)}")
    return f"{cmd} {path}"


def _read_keep_eol(p: Path) -> tuple[str, str]:
    """Read text with '\n' line endings and remember the file's own style."""
    with open(p, "r", encoding="utf-8", errors="replace", newline="") as f:
        raw = f.read()
    eol = "\r\n" if "\r\n" in raw else "\n"
    return raw.replace("\r\n", "\n"), eol


def _write_keep_eol(p: Path, text: str, eol: str) -> None:
    text = text.replace("\r\n", "\n")
    if eol != "\n":
        text = text.replace("\n", eol)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def run_editor(inp: dict) -> tuple[str, bool]:
    cmd = inp.get("command")
    path = inp.get("path")
    if not isinstance(path, str) or not path:
        return "Error: `path` is required", True
    p = Path(os.path.expandvars(os.path.expanduser(path)))
    if not p.is_absolute():
        return f"Error: path must be absolute, got {path}", True
    try:
        if cmd == "view":
            if p.is_dir():
                items = []
                for root, dirs, files in os.walk(p):
                    depth = Path(root).relative_to(p).parts
                    dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules"]
                    if len(depth) >= 2:
                        dirs[:] = []
                    for name in sorted(dirs) + sorted(files):
                        if not name.startswith("."):
                            items.append(str(Path(root) / name))
                    if len(items) > 500:
                        break
                return _truncate("\n".join(items[:500]) or "(empty directory)"), False
            if not p.exists():
                return f"Error: The path {path} does not exist.", True
            lines = _read_keep_eol(p)[0].split("\n")
            start, end = 1, len(lines)
            vr = inp.get("view_range")
            if vr:
                if not isinstance(vr, (list, tuple)) or len(vr) != 2:
                    return "Error: view_range must be [start_line, end_line]", True
                start = max(1, int(vr[0]))
                end = len(lines) if int(vr[1]) == -1 else min(len(lines), int(vr[1]))
            body = "\n".join(f"{i:>6}\t{lines[i - 1]}" for i in range(start, end + 1))
            return _truncate(body, 30_000), False
        if cmd == "create":
            p.parent.mkdir(parents=True, exist_ok=True)
            eol = _read_keep_eol(p)[1] if p.is_file() else "\n"
            _write_keep_eol(p, str(inp.get("file_text", "")), eol)
            return f"File created successfully at: {path}", False
        if cmd == "str_replace":
            old, new = inp.get("old_str"), inp.get("new_str") or ""
            if not p.is_file():
                return f"Error: The path {path} does not exist.", True
            if not isinstance(old, str) or not old:
                return "Error: old_str is required", True
            old, new = old.replace("\r\n", "\n"), str(new).replace("\r\n", "\n")
            text, eol = _read_keep_eol(p)
            n = text.count(old)
            if n == 0:
                return f"Error: No replacement was performed, old_str did not appear verbatim in {path}.", True
            if n > 1:
                return f"Error: No replacement was performed. old_str occurs {n} times; make it unique.", True
            _write_keep_eol(p, text.replace(old, new, 1), eol)
            return "The file has been edited.", False
        if cmd == "insert":
            if not p.is_file():
                return f"Error: The path {path} does not exist.", True
            text, eol = _read_keep_eol(p)
            lines = text.split("\n")
            n = int(inp.get("insert_line", -1))
            if n < 0 or n > len(lines):
                return f"Error: insert_line must be in [0, {len(lines)}]", True
            new_text = str(inp.get("insert_text", inp.get("new_str", ""))).replace("\r\n", "\n")
            lines[n:n] = new_text.split("\n")
            _write_keep_eol(p, "\n".join(lines), eol)
            return f"The file {path} has been edited.", False
        return f"Error: unsupported command {cmd}", True
    except (OSError, UnicodeDecodeError, ValueError, TypeError, IndexError) as e:
        return f"Error: {type(e).__name__}: {e}", True
