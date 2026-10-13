"""Settings: defaults, persistence (data/settings.json) and model price table."""
from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import asdict, dataclass, fields
from pathlib import Path

APP_NAME = "Polyglot Agent"
ROOT = Path(__file__).resolve().parent.parent
FROZEN = bool(getattr(sys, "frozen", False))   # single-file exe built with PyInstaller


def _default_data_dir() -> Path:
    if FROZEN:  # the standalone exe can live anywhere; keep data in the user profile
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "PolyglotAgent"
    return ROOT / "data"


DATA_DIR = Path(os.environ.get("POLYGLOT_DATA_DIR") or _default_data_dir())

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
    computer_use: bool = True
    monitor: int = 1                       # mss monitor index (1 = primary)
    capture_mode: str = "window"           # window = focused window only; screen = work area
    max_screenshot_edge: int = 1568        # long edge sent to the model
    confirm_computer_actions: bool = False
    shell_mode: str = "ask"                # PowerShell tool: ask / auto / off
    file_tools_mode: str = "ask"           # text editor writes: ask / auto / off
    web_search: bool = True
    # memory
    memory_enabled: bool = True
    memory_pass: bool = True               # background memory review after each turn
    memory_pass_model: str = "claude-haiku-5-5"
    # context management (server-side tool-result clearing)
    context_clear_trigger: int = 100_000
    context_keep_tool_uses: int = 6

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
    p = settings_path()
    s = Settings()
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
        if k == "language" and v not in LANGUAGES:
            continue  # also drops the old "auto" value, so the user is asked to choose
        if k == "api_key" and v == "":
            continue  # empty field in the UI means "keep current"
        setattr(s, k, v)
    s.max_screenshot_edge = max(640, min(s.max_screenshot_edge, 2000))
    s.max_steps = max(1, min(s.max_steps, 500))
    s.max_tokens = max(1024, min(s.max_tokens, 128_000))
    return s


def save_settings(s: Settings) -> None:
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
