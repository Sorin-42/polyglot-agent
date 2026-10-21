"""Settings: defaults, persistence (data/settings.json) and model price table."""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Optional

APP_NAME = "Polyglot Agent"
ROOT = Path(__file__).resolve().parent.parent
FROZEN = bool(getattr(sys, "frozen", False))   # single-file exe built with PyInstaller
IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
# Computer control and its on-screen signal use Windows APIs; elsewhere they are unavailable.
COMPUTER_SUPPORTED = IS_WINDOWS


def system_name() -> str:
    return "Windows PC" if IS_WINDOWS else "Mac" if IS_MAC else "Linux computer"


def _default_state_dir() -> Path:
    if not FROZEN:      # run from the app folder: everything stays in its data/
        return ROOT / "data"
    # the standalone exe / Mac app can live anywhere; keep its state in the user profile
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    elif IS_MAC:
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "PolyglotAgent"


_ENV_DIR = os.environ.get("POLYGLOT_DATA_DIR")
DATA_DIR_FIXED = bool(_ENV_DIR)          # POLYGLOT_DATA_DIR wins over the folder chosen in the app
# This PC's app state: single-instance lock, log, window cache, and where the data folder is.
STATE_DIR = Path(_ENV_DIR or _default_state_dir())
LOCATION_FILE = STATE_DIR / "data-location.json"
# The user's data. These are copied when the data folder changes; any of them marks a
# folder as already holding this app's data.
DATA_ENTRIES = ("settings.json", "memories", "memories-backups", "chats", "projects")


def saved_data_dir() -> Optional[Path]:
    """The data folder chosen earlier (it may since have gone missing, e.g. an unplugged drive)."""
    try:
        p = json.loads(LOCATION_FILE.read_text(encoding="utf-8"))["data_dir"]
        return Path(p) if p else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _initial_data_dir() -> Optional[Path]:
    if _ENV_DIR:
        return Path(_ENV_DIR)
    saved = saved_data_dir()
    if saved is not None:
        return saved if saved.is_dir() else None     # gone: ask again
    return None if FROZEN else STATE_DIR             # the standalone exe asks on first start


# Settings, memory, chats and projects. None until the user has chosen a folder.
DATA_DIR: Optional[Path] = _initial_data_dir()


def has_app_data(path: Path) -> bool:
    return any((path / name).exists() for name in DATA_ENTRIES)


def same_path(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def data_target(picked: str) -> Path:
    """The folder the data goes in when the user picks `picked`: the folder itself if it
    is new, empty, this PC's app folder or already holds this app's data; otherwise a
    "Polyglot Agent" folder inside it, so the app's files don't mix with others."""
    p = Path(picked.strip()).expanduser() if picked.strip() else None
    if p is None or not p.is_absolute():
        raise ValueError("Choose a folder.")
    if p.exists() and not p.is_dir():
        raise ValueError(f"{p} is a file, not a folder.")
    if not p.exists() or same_path(p, STATE_DIR) or has_app_data(p) or not any(p.iterdir()):
        return p
    return p / APP_NAME


def copy_data(src: Path, dst: Path) -> None:
    """Copy the user's data (not this PC's app state) into another folder."""
    dst.mkdir(parents=True, exist_ok=True)
    for name in DATA_ENTRIES:
        s = src / name
        if s.is_dir():
            shutil.copytree(s, dst / name, dirs_exist_ok=True)
        elif s.is_file():
            shutil.copy2(s, dst / name)


def set_data_dir(path: Path) -> None:
    """Use `path` as the data folder (created if needed) and remember it for later launches."""
    global DATA_DIR
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".write-test"
    probe.write_text("ok", encoding="utf-8")       # fail now, not on the first save
    probe.unlink()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LOCATION_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"data_dir": str(path)}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(LOCATION_FILE)
    DATA_DIR = path

MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"]
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
APPROVAL_MODES = ["ask", "auto", "off"]

# USD per million tokens (input, output). Haiku is listed as "from" on the
# pricing page, so cost shown in the UI is an estimate.
PRICES = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}

# Context window (tokens) per model, for the "context left" meter.
CONTEXT_WINDOWS = {
    "claude-fable-5-1": 1_000_000,
    "claude-opus-5-5": 1_000_000,
    "claude-sonnet-5-5": 1_000_000,
    "claude-haiku-5-5": 1_000_000,
}
DEFAULT_CONTEXT_WINDOW = 200_000
CAPTURE_MODES = ["window", "screen"]
# Where the UI opens (applied at launch): built-in WebView2 window, Edge/Chrome app
# window, or a tab in the default browser (e.g. Firefox).
WINDOW_MODES = ["app", "edge", "chrome", "browser"]

# The user picks one of these; the app never guesses the language from their text.
LANGUAGES = [
    "English", "简体中文", "繁體中文", "日本語", "한국어", "Español", "Français",
    "Deutsch", "Italiano", "Português", "Русский", "العربية", "हिन्दी", "Tiếng Việt",
    "ไทย", "Bahasa Indonesia", "Türkçe", "Polski", "Nederlands", "Українська",
]

# The English name we hand to the translator for each UI label.
LANGUAGE_ENGLISH_NAME = {
    "English": "English", "简体中文": "Simplified Chinese", "繁體中文": "Traditional Chinese",
    "日本語": "Japanese", "한국어": "Korean", "Español": "Spanish", "Français": "French",
    "Deutsch": "German", "Italiano": "Italian", "Português": "Portuguese", "Русский": "Russian",
    "العربية": "Arabic", "हिन्दी": "Hindi", "Tiếng Việt": "Vietnamese", "ไทย": "Thai",
    "Bahasa Indonesia": "Indonesian", "Türkçe": "Turkish", "Polski": "Polish",
    "Nederlands": "Dutch", "Українська": "Ukrainian",
}


@dataclass
class Settings:
    api_key: str = ""                      # falls back to ANTHROPIC_API_KEY
    base_url: str = "https://api.anthropic.com"
    # main agent
    model: str = "claude-opus-5-5"
    effort: str = "high"
    max_tokens: int = 32000
    max_steps: int = 80                    # model calls per user turn
    show_thinking: bool = True             # request summarized thinking
    # translation layer
    language: str = ""                     # chosen by the user; "" = not chosen yet
    translator_model: str = "claude-haiku-5-5"
    translator_effort: str = "low"
    show_original: bool = False            # show English under translations
    # tools
    computer_use: bool = False             # off until the user turns it on (Settings → Tools)
    monitor: int = 0                       # display to control: mss index, 0 = the main display
    capture_mode: str = "window"           # window = focused window only; screen = work area
    max_screenshot_edge: int = 1568        # long edge sent to the model
    confirm_computer_actions: bool = False
    shell_mode: str = "ask"              # PowerShell tool: ask / auto / off
    file_tools_mode: str = "ask"           # text editor writes: ask / auto / off
    web_search: bool = True
    # memory
    memory_enabled: bool = True
    memory_pass: bool = True               # background memory review after each turn
    memory_pass_model: str = "claude-haiku-5-5"
    # desktop window, applied at the next launch
    window_mode: str = "app"               # see WINDOW_MODES
    # context management (server-side tool-result clearing)
    context_clear_trigger: int = 100_000
    context_keep_tool_uses: int = 6
    # auto compact (server-side): summarize older turns once the prompt reaches the trigger
    auto_compact: bool = True
    compact_trigger: int = 150_000

    def effective_api_key(self) -> str:
        return self.api_key.strip() or os.environ.get("ANTHROPIC_API_KEY", "").strip()

    def public_dict(self) -> dict:
        d = asdict(self)
        key = self.effective_api_key()
        d["api_key"] = ""
        d["api_key_set"] = bool(key)
        d["api_key_hint"] = (key[:7] + "…" + key[-4:]) if len(key) > 12 else ""
        return d


_lock = threading.Lock()


def settings_path() -> Path:
    return DATA_DIR / "settings.json"


def load_settings() -> Settings:
    s = Settings()
    if DATA_DIR is None:      # no data folder chosen yet: defaults until there is one
        return s
    p = settings_path()
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            apply_updates(s, raw)
        except Exception:
            pass
    return s


def apply_updates(s: Settings, updates: dict) -> Settings:
    """Apply a partial dict of updates with light type coercion/validation."""
    valid = {f.name: f for f in fields(Settings)}
    for k, v in (updates or {}).items():
        if k not in valid:
            continue
        cur = getattr(s, k)
        try:
            if isinstance(cur, bool):
                v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
            elif isinstance(cur, int):
                v = int(v)
            elif isinstance(cur, str):
                v = str(v)
        except (TypeError, ValueError):
            continue
        if k in ("effort", "translator_effort") and v not in EFFORTS:
            continue
        if k in ("shell_mode", "file_tools_mode") and v not in APPROVAL_MODES:
            continue
        if k == "capture_mode" and v not in CAPTURE_MODES:
            continue
        if k == "window_mode" and v not in WINDOW_MODES:
            continue
        if k == "language" and v not in LANGUAGES:
            continue  # also drops the old "auto" value, so the user is asked to choose
        if k == "api_key" and v == "":
            continue  # empty field in the UI means "keep current"
        setattr(s, k, v)
    s.max_screenshot_edge = max(640, min(s.max_screenshot_edge, 2000))
    s.max_steps = max(1, min(s.max_steps, 500))
    s.max_tokens = max(1024, min(s.max_tokens, 128_000))
    s.compact_trigger = max(50_000, min(s.compact_trigger, 800_000))   # API minimum is 50k
    return s


def save_settings(s: Settings) -> None:
    if DATA_DIR is None:
        raise RuntimeError("No data folder has been chosen yet.")
    with _lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = settings_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(s), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(settings_path())


def estimate_cost(model: str, usage: dict) -> float:
    pin, pout = PRICES.get(model, (0.0, 0.0))
    inp = usage.get("input_tokens", 0) or 0
    cw = usage.get("cache_creation_input_tokens", 0) or 0
    cr = usage.get("cache_read_input_tokens", 0) or 0
    out = usage.get("output_tokens", 0) or 0
    return (inp * pin + cw * pin * 1.25 + cr * pin * 0.1 + out * pout) / 1_000_000
