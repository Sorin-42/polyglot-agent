"""Translate xdotool-style key names (what Claude emits) into pyautogui names."""
from __future__ import annotations

_ALIASES = {
    # editing / navigation
    "return": "enter", "enter": "enter", "kp_enter": "enter", "linefeed": "enter",
    "tab": "tab", "escape": "esc", "esc": "esc", "backspace": "backspace",
    "delete": "delete", "del": "delete", "insert": "insert", "ins": "insert",
    "home": "home", "end": "end", "page_up": "pageup", "pageup": "pageup", "prior": "pageup",
    "page_down": "pagedown", "pagedown": "pagedown", "next": "pagedown",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "space": "space", " ": "space",
    # modifiers
    "ctrl": "ctrl", "control": "ctrl", "control_l": "ctrlleft", "control_r": "ctrlright",
    "ctrl_l": "ctrlleft", "ctrl_r": "ctrlright",
    "alt": "alt", "alt_l": "altleft", "alt_r": "altright", "altgr": "altright", "option": "alt",
    "shift": "shift", "shift_l": "shiftleft", "shift_r": "shiftright",
    "super": "win", "super_l": "winleft", "super_r": "winright", "win": "win", "windows": "win",
    "meta": "win", "meta_l": "winleft", "meta_r": "winright", "cmd": "win", "command": "win",
    "hyper": "win",
    # locks / system
    "caps_lock": "capslock", "capslock": "capslock", "num_lock": "numlock", "numlock": "numlock",
    "scroll_lock": "scrolllock", "pause": "pause", "break": "pause",
    "print": "printscreen", "printscreen": "printscreen", "sys_req": "printscreen",
    "menu": "apps", "apps": "apps",
    # punctuation names
    "plus": "+", "minus": "-", "equal": "=", "comma": ",", "period": ".", "slash": "/",
    "backslash": "\\", "semicolon": ";", "apostrophe": "'", "quoteright": "'", "grave": "`",
    "quoteleft": "`", "bracketleft": "[", "bracketright": "]", "underscore": "_",
    "exclam": "!", "at": "@", "numbersign": "#", "dollar": "$", "percent": "%",
    "asciicircum": "^", "ampersand": "&", "asterisk": "*", "parenleft": "(", "parenright": ")",
    "braceleft": "{", "braceright": "}", "bar": "|", "colon": ":", "quotedbl": '"',
    "less": "<", "greater": ">", "question": "?", "asciitilde": "~",
    # keypad
    "kp_add": "add", "kp_subtract": "subtract", "kp_multiply": "multiply",
    "kp_divide": "divide", "kp_decimal": "decimal",
    # media
    "xf86audiomute": "volumemute", "xf86audioraisevolume": "volumeup",
    "xf86audiolowervolume": "volumedown", "xf86audioplay": "playpause",
    "xf86audionext": "nexttrack", "xf86audioprev": "prevtrack", "xf86audiostop": "stop",
    "xf86back": "browserback", "xf86forward": "browserforward", "xf86refresh": "browserrefresh",
}

MODIFIERS = {"ctrl", "ctrlleft", "ctrlright", "alt", "altleft", "altright", "shift",
             "shiftleft", "shiftright", "win", "winleft", "winright"}


def map_key(name: str) -> str:
    """Map one key name to a pyautogui key name. Raises ValueError if unknown."""
    if name == "":
        raise ValueError("empty key name")
    low = name.lower()
    if low in _ALIASES:
        return _ALIASES[low]
    if len(low) >= 2 and low[0] == "f" and low[1:].isdigit() and 1 <= int(low[1:]) <= 24:
        return low
    if low.startswith("kp_") and low[3:].isdigit() and len(low) == 4:
        return "num" + low[3:]
    if len(name) == 1:
        return low  # letters, digits, punctuation (case handled by caller via shift)
    raise ValueError(f"Unknown key name: {name!r}")


def parse_combo(combo: str) -> list[str]:
    """'ctrl+shift+t' -> ['ctrl', 'shift', 't'];  'ctrl++' -> ['ctrl', '+']."""
    combo = combo.strip()
    if not combo:
        raise ValueError("empty key combination")
    if combo == "+":
        return ["+"]
    trailing_plus = combo.endswith("++")
    if trailing_plus:
        combo = combo[:-2]
    parts = [p for p in combo.split("+") if p != ""]
    keys = [map_key(p) for p in parts]
    if trailing_plus:
        keys.append("+")
    return keys


def parse_key_sequence(text: str) -> list[list[str]]:
    """xdotool allows several combos separated by spaces: 'ctrl+a Delete'."""
    seq = [parse_combo(tok) for tok in text.split()]
    if not seq:
        raise ValueError("empty key text")
    return seq


def parse_modifiers(text: str | None) -> list[str]:
    """Modifier string used with clicks/scroll, e.g. 'shift' or 'ctrl+shift'."""
    if not text:
        return []
    mods = parse_combo(text)
    bad = [m for m in mods if m not in MODIFIERS]
    if bad:
        raise ValueError(f"Not a modifier key: {', '.join(bad)}")
    return mods
