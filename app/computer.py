"""Local desktop controller for the computer_toolset_20260801 member tools.

Windows-first: physical-pixel screenshots via mss (process is DPI-aware),
mouse/keyboard via pyautogui, wheel and Unicode typing via SendInput so any
language can be typed. Coordinates from Claude are in *screenshot* pixels and
are scaled back to the monitor here.
"""
from __future__ import annotations

import base64
import io
import math
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from PIL import Image

from .keys import parse_combo, parse_key_sequence, parse_modifiers

IS_WINDOWS = sys.platform == "win32"
MAX_VISUAL_TOKENS = 4784          # toolset models: ceil(w/28) * ceil(h/28)
MUTATING = {
    "left_click", "right_click", "middle_click", "double_click", "triple_click",
    "left_click_drag", "mouse_move", "left_mouse_down", "left_mouse_up",
    "scroll", "type", "key", "hold_key",
}
ALL_ACTIONS = MUTATING | {"screenshot", "zoom", "cursor_position", "wait"}
PNG_MAX_BYTES = 700_000
ZOOM_MAX_UPSCALE = 4.0


class ComputerError(Exception):
    """An action failed; reported to Claude as is_error."""


class UserAbort(Exception):
    """User aborted (Stop button or pyautogui fail-safe corner)."""


def enable_dpi_awareness() -> None:
    """Must run before pyautogui/mss touch the screen so pixels are physical."""
    if not IS_WINDOWS:
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def compute_scale(width: int, height: int, max_edge: int, upscale_limit: float = 1.0) -> float:
    """Scale factor so the image fits max_edge and the visual-token budget.
    upscale_limit > 1 lets small images (zoom crops) be enlarged."""
    s = min(upscale_limit, max_edge / max(width, height))
    while math.ceil(width * s / 28) * math.ceil(height * s / 28) > MAX_VISUAL_TOKENS:
        s *= 0.98
    return s


@dataclass
class Frame:
    """The physical-pixel rectangle the latest screenshot showed, and the scale
    used to shrink it. Every coordinate from Claude is relative to this."""
    left: int
    top: int
    width: int
    height: int
    scale: float
    title: str = ""        # window title in window mode, "" for the work area

    @property
    def shot_w(self) -> int:
        return max(1, int(self.width * self.scale))

    @property
    def shot_h(self) -> int:
        return max(1, int(self.height * self.scale))


# --------------------------------------------------------------------------- #
# Windows SendInput helpers (wheel + Unicode typing)
# --------------------------------------------------------------------------- #
if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    ULONG_PTR = ctypes.c_size_t

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    _SendInput = ctypes.windll.user32.SendInput
    _SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    _SendInput.restype = wintypes.UINT

    INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
    KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0002, 0x0004
    MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
    VK_RETURN, VK_TAB = 0x0D, 0x09
    WHEEL_DELTA = 120

    def _send(inputs: list) -> None:
        arr = (INPUT * len(inputs))(*inputs)
        sent = _SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
        if sent != len(inputs):
            raise ComputerError("SendInput was blocked (is an elevated/admin window focused?)")

    def _wheel(delta: int, horizontal: bool) -> None:
        inp = INPUT(type=INPUT_MOUSE)
        inp.mi = MOUSEINPUT(0, 0, delta & 0xFFFFFFFF,
                            MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL, 0, 0)
        _send([inp])

    def _vk_down_up(vk: int) -> None:
        down = INPUT(type=INPUT_KEYBOARD)
        down.ki = KEYBDINPUT(vk, 0, 0, 0, 0)
        up = INPUT(type=INPUT_KEYBOARD)
        up.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP, 0, 0)
        _send([down, up])

    def _vk_press(vk: int) -> None:
        down = INPUT(type=INPUT_KEYBOARD)
        down.ki = KEYBDINPUT(vk, 0, 0, 0, 0)
        up = INPUT(type=INPUT_KEYBOARD)
        up.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP, 0, 0)
        _send([down, up])

    def _unicode_chunk(text: str) -> None:
        data = text.encode("utf-16-le")
        inputs = []
        for i in range(0, len(data), 2):
            code = int.from_bytes(data[i:i + 2], "little")
            for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
                inp = INPUT(type=INPUT_KEYBOARD)
                inp.ki = KEYBDINPUT(0, code, flags, 0, 0)
                inputs.append(inp)
        if inputs:
            _send(inputs)


# --------------------------------------------------------------------------- #
# Win32 window helpers: work area (screen minus taskbar), focused window, list/focus
# --------------------------------------------------------------------------- #
SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}

if IS_WINDOWS:
    class RECT(ctypes.Structure):
        _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                    ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT), ("rcWork", RECT),
                    ("dwFlags", wintypes.DWORD)]

    _u32 = ctypes.windll.user32
    _dwm = ctypes.windll.dwmapi
    _u32.MonitorFromPoint.argtypes = (wintypes.POINT, wintypes.DWORD)
    _u32.MonitorFromPoint.restype = wintypes.HMONITOR
    _u32.GetMonitorInfoW.argtypes = (wintypes.HMONITOR, ctypes.POINTER(MONITORINFO))
    _u32.GetForegroundWindow.restype = wintypes.HWND
    _u32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    _u32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    _u32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    _u32.IsIconic.argtypes = (wintypes.HWND,)
    _u32.IsWindowVisible.argtypes = (wintypes.HWND,)
    _u32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(RECT))
    _u32.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
    _u32.GetWindow.restype = wintypes.HWND
    _u32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
    _u32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    _u32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    _u32.BringWindowToTop.argtypes = (wintypes.HWND,)
    _u32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    _dwm.DwmGetWindowAttribute.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _win_title(h) -> str:
        n = _u32.GetWindowTextLengthW(h)
        buf = ctypes.create_unicode_buffer(n + 1)
        _u32.GetWindowTextW(h, buf, n + 1)
        return buf.value

    def _win_class(h) -> str:
        buf = ctypes.create_unicode_buffer(256)
        _u32.GetClassNameW(h, buf, 256)
        return buf.value

    def _win_rect(h) -> Optional[tuple[int, int, int, int]]:
        r = RECT()
        # DWMWA_EXTENDED_FRAME_BOUNDS = 9: the visible frame without drop shadow
        if _dwm.DwmGetWindowAttribute(h, 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:
            if not _u32.GetWindowRect(h, ctypes.byref(r)):
                return None
        return r.left, r.top, r.right, r.bottom

    def _win_cloaked(h) -> bool:
        v = wintypes.DWORD()
        return _dwm.DwmGetWindowAttribute(h, 14, ctypes.byref(v), ctypes.sizeof(v)) == 0 and v.value != 0

    def _win_pid(h) -> int:
        pid = wintypes.DWORD()
        _u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        return pid.value

    def _popups_above(fg) -> list[tuple[int, int, int, int]]:
        """Menus, dropdowns and tooltips are separate windows stacked above the
        focused one; include them so window-only screenshots don't cut them off."""
        pid = _win_pid(fg)
        rects: list[tuple[int, int, int, int]] = []

        def cb(h, _):
            if h == fg:
                return False  # EnumWindows walks top-down: everything after is below
            if _u32.IsWindowVisible(h) and not _win_cloaked(h) and \
                    (_win_class(h) == "#32768" or _win_pid(h) == pid):
                r = _win_rect(h)
                if r and r[2] > r[0] and r[3] > r[1]:
                    rects.append(r)
            return True
        _u32.EnumWindows(_WNDENUMPROC(cb), 0)
        return rects

    def _top_windows() -> list[tuple[int, str]]:
        out: list[tuple[int, str]] = []

        def cb(h, _):
            if not _u32.IsWindowVisible(h) or _u32.GetWindow(h, 4):   # GW_OWNER
                return True
            if _u32.GetWindowLongW(h, -20) & 0x80:                     # WS_EX_TOOLWINDOW
                return True
            title = _win_title(h)
            if title and not _win_cloaked(h) and _win_class(h) not in SHELL_CLASSES:
                out.append((h, title))
            return True
        _u32.EnumWindows(_WNDENUMPROC(cb), 0)
        return out


def _intersect(a: tuple[int, int, int, int], b: tuple[int, int, int, int]):
    l, t = max(a[0], b[0]), max(a[1], b[1])
    r, btm = min(a[2], b[2]), min(a[3], b[3])
    return (l, t, r, btm) if r > l and btm > t else None


# --------------------------------------------------------------------------- #
class LocalComputer:
    def __init__(self, monitor: int = 1, max_edge: int = 1568, settle: float = 0.35,
                 stop_event: Optional[threading.Event] = None, capture_mode: str = "window",
                 own_title: str = ""):
        self.monitor = monitor
        self.max_edge = max_edge
        self.settle = settle
        self.stop_event = stop_event or threading.Event()
        self.capture_mode = capture_mode if capture_mode in ("window", "screen") else "window"
        self.own_title = own_title          # our chat UI's window title marker
        self._pg = None
        self._held_keys: set[str] = set()
        self._mouse_held = False
        self.frame = self._frame_for(self._work_rect())

    # ---------------- setup ----------------
    @property
    def pg(self):
        if self._pg is None:
            import pyautogui  # imported lazily (needs a display)
            pyautogui.FAILSAFE = True
            pyautogui.PAUSE = 0.03
            self._pg = pyautogui
        return self._pg

    def _monitor_rect(self) -> tuple[int, int, int, int]:
        import mss
        with mss.mss() as sct:
            mons = sct.monitors
            idx = self.monitor if 0 < self.monitor < len(mons) else 1
            m = mons[idx]
        return m["left"], m["top"], m["left"] + m["width"], m["top"] + m["height"]

    def _work_rect(self) -> tuple[int, int, int, int]:
        """The monitor minus the taskbar (and other docked app bars)."""
        mon = self._monitor_rect()
        if not IS_WINDOWS:
            return mon
        try:
            pt = wintypes.POINT((mon[0] + mon[2]) // 2, (mon[1] + mon[3]) // 2)
            hmon = _u32.MonitorFromPoint(pt, 2)  # MONITOR_DEFAULTTONEAREST
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            if _u32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                w = mi.rcWork
                return _intersect(mon, (w.left, w.top, w.right, w.bottom)) or mon
        except Exception:  # noqa: BLE001
            pass
        return mon

    def _frame_for(self, rect: tuple[int, int, int, int], title: str = "") -> Frame:
        l, t, r, b = rect
        w, h = max(1, r - l), max(1, b - t)
        return Frame(l, t, w, h, compute_scale(w, h, self.max_edge), title)

    def foreground(self) -> Optional[tuple[str, tuple[int, int, int, int]]]:
        """(title, rect) of the focused window, or None if it's the desktop/taskbar."""
        if not IS_WINDOWS:
            return None
        h = _u32.GetForegroundWindow()
        if not h or _u32.IsIconic(h) or _win_class(h) in SHELL_CLASSES:
            return None
        rect = _win_rect(h)
        if not rect:
            return None
        for r in _popups_above(h):
            rect = (min(rect[0], r[0]), min(rect[1], r[1]), max(rect[2], r[2]), max(rect[3], r[3]))
        return _win_title(h), rect

    def is_own_window(self, title: str) -> bool:
        return bool(self.own_title) and self.own_title in title

    def _choose_frame(self) -> tuple[Frame, bool]:
        """Pick what to capture. Returns (frame, focused_is_own_ui)."""
        work = self._work_rect()
        if self.capture_mode == "window":
            fg = self.foreground()
            if fg:
                title, rect = fg
                if self.is_own_window(title):
                    return self._frame_for(work), True
                clipped = _intersect(rect, work)
                if clipped and (clipped[2] - clipped[0]) >= 160 and (clipped[3] - clipped[1]) >= 120:
                    return self._frame_for(clipped, title), False
        return self._frame_for(work), False

    # ---------------- capture ----------------
    def _grab(self, region: tuple[int, int, int, int]) -> Image.Image:
        import mss
        x0, y0, x1, y1 = region
        box = {"left": x0, "top": y0, "width": max(1, x1 - x0), "height": max(1, y1 - y0)}
        with mss.mss() as sct:
            shot = sct.grab(box)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    def capture(self) -> Image.Image:
        """Screenshot of the current frame, resized to the model's coordinate space."""
        f = self.frame
        img = self._grab((f.left, f.top, f.left + f.width, f.top + f.height))
        if f.scale < 1.0:
            img = img.resize((f.shot_w, f.shot_h), Image.LANCZOS)
        return img

    # ---------------- coordinate mapping ----------------
    def _to_screen(self, coord: Any, field: str = "coordinate") -> tuple[int, int]:
        if (not isinstance(coord, (list, tuple)) or len(coord) != 2
                or not all(isinstance(v, (int, float)) for v in coord)):
            raise ComputerError(f"`{field}` must be [x, y] numbers, got {coord!r}")
        f = self.frame
        x, y = coord
        if not (0 <= x < f.shot_w + 1 and 0 <= y < f.shot_h + 1):
            raise ComputerError(f"{field} {list(coord)} is outside the {f.shot_w}x{f.shot_h} screenshot")
        sx = f.left + min(f.width - 1, int(round(x / f.scale)))
        sy = f.top + min(f.height - 1, int(round(y / f.scale)))
        # pyautogui aborts when the cursor sits on a screen corner (that's the
        # user's emergency stop), so keep the agent's own moves 1px inside.
        ml, mt, mr, mb = self._monitor_rect()
        if sx in (ml, mr - 1) and sy in (mt, mb - 1):
            sx += 1 if sx == ml else -1
            sy += 1 if sy == mt else -1
        return sx, sy

    def _to_shot(self, sx: int, sy: int) -> Optional[tuple[int, int]]:
        f = self.frame
        if not (f.left <= sx < f.left + f.width and f.top <= sy < f.top + f.height):
            return None
        return int(round((sx - f.left) * f.scale)), int(round((sy - f.top) * f.scale))

    # ---------------- windows tool (list / focus / capture mode) ----------------
    def windows_tool(self, inp: dict) -> str:
        action = inp.get("action", "list")
        if action == "capture":
            mode = inp.get("mode")
            if mode not in ("window", "screen"):
                raise ComputerError("`mode` must be window or screen")
            self.capture_mode = mode
            return f"Capture mode is now {mode}. Take a screenshot."
        if not IS_WINDOWS:
            raise ComputerError("window listing is only available on Windows")
        wins = [(h, t) for h, t in _top_windows() if not self.is_own_window(t)]
        if action == "list":
            return "Open windows:\n" + "\n".join(f"- {t}" for _, t in wins[:40]) if wins else "No open windows."
        if action == "focus":
            q = str(inp.get("title") or "").strip().lower()
            if not q:
                raise ComputerError("`title` is required for focus")
            match = next((w for w in wins if w[1].lower() == q), None) or \
                next((w for w in wins if q in w[1].lower()), None)
            if not match:
                raise ComputerError(f"No window title contains {q!r}. Use action=list.")
            h, title = match
            if _u32.IsIconic(h):
                _u32.ShowWindow(h, 9)  # SW_RESTORE
            # Windows only lets the process that last received input steal focus;
            # a synthetic Alt tap satisfies that rule.
            _vk_down_up(0x12)
            _u32.BringWindowToTop(h)
            _u32.SetForegroundWindow(h)
            time.sleep(self.settle)
            return f"Focused: {title}. Take a screenshot."
        raise ComputerError("`action` must be list, focus or capture")

    # ---------------- dispatcher ----------------
    def execute(self, name: str, inp: dict) -> dict:
        """Run one member tool. Returns {"content": [...blocks], "image": PIL|None}."""
        if name not in ALL_ACTIONS:
            raise ComputerError(f"Unsupported computer action: {name}")
        if self.stop_event.is_set():
            raise UserAbort("Stopped by user")
        inp = inp or {}
        try:
            result = getattr(self, f"_do_{name}")(inp)
        except UserAbort:
            self.release_inputs(everything=True)
            raise
        except ComputerError:
            raise
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ == "FailSafeException":
                self.release_inputs(everything=True)
                raise UserAbort("Fail-safe: mouse moved to a screen corner") from e
            raise ComputerError(f"{name} failed: {type(e).__name__}: {e}") from e
        if name in MUTATING and self.settle:
            time.sleep(self.settle)
        return result

    # ---------------- helpers ----------------
    @staticmethod
    def _image_result(img: Image.Image) -> dict:
        """PNG keeps UI text crisp; busy screens (photos, video) fall back to
        JPEG so long sessions don't balloon the request size."""
        buf = io.BytesIO()
        img.save(buf, format="PNG", compress_level=6)
        media = "image/png"
        if buf.tell() > PNG_MAX_BYTES:
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=88)
            media = "image/jpeg"
        data = base64.b64encode(buf.getvalue()).decode()
        return {"content": [{"type": "image",
                             "source": {"type": "base64", "media_type": media, "data": data}}],
                "image": img}

    @staticmethod
    def _ok(text: str = "OK") -> dict:
        return {"content": [{"type": "text", "text": text}], "image": None}

    def _check_abort(self) -> None:
        """Stop button, or the pyautogui fail-safe (mouse parked in a screen corner)."""
        if self.stop_event.is_set():
            raise UserAbort("Stopped by user")
        pg = self.pg
        if pg.FAILSAFE and tuple(pg.position()) in [tuple(pt) for pt in pg.FAILSAFE_POINTS]:
            raise UserAbort("Fail-safe: mouse moved to a screen corner")

    def _release(self, keys=(), buttons=()) -> None:
        """Release keys/buttons even if the fail-safe corner is active
        (pyautogui would otherwise refuse and leave them stuck down)."""
        if self._pg is None:
            return
        pg = self.pg
        old = pg.FAILSAFE
        pg.FAILSAFE = False
        try:
            for k in keys:
                try:
                    pg.keyUp(k)
                except Exception:  # noqa: BLE001
                    pass
            for b in buttons:
                try:
                    pg.mouseUp(button=b)
                except Exception:  # noqa: BLE001
                    pass
        finally:
            pg.FAILSAFE = old
        for k in keys:
            self._held_keys.discard(k)
        if buttons:
            self._mouse_held = False

    def release_inputs(self, everything: bool = False) -> None:
        """Let go of anything the agent may still be holding."""
        keys = set(self._held_keys)
        if everything:
            keys |= {"shift", "ctrl", "alt", "win"}
        buttons = ("left", "right", "middle") if (everything or self._mouse_held) else ()
        if keys or buttons:
            self._release(sorted(keys), buttons)

    def _hold_mods(self, mods: list[str]):
        comp = self

        class _Ctx:
            def __enter__(self_inner):
                for m in mods:
                    comp._held_keys.add(m)
                    comp.pg.keyDown(m)

            def __exit__(self_inner, *exc):
                comp._release(list(reversed(mods)))
        return _Ctx()

    # ---------------- actions ----------------
    def _do_screenshot(self, inp: dict) -> dict:
        self.frame, own_ui = self._choose_frame()
        if own_ui:
            # Don't spend image tokens on our own chat window.
            return self._ok("The focused window is the user's chat with you, so no screenshot was "
                            "taken. Switch windows first: use the `windows` tool (list/focus) or "
                            "press super to search the Start menu.")
        out = self._image_result(self.capture())
        note = f"Window: {self.frame.title}" if self.frame.title else "Full screen"
        out["content"].append({"type": "text", "text": note})
        return out

    def _do_zoom(self, inp: dict) -> dict:
        region = inp.get("region")
        if not (isinstance(region, (list, tuple)) and len(region) == 4):
            raise ComputerError("`region` must be [x0, y0, x1, y1]")
        x0, y0, x1, y1 = region
        if x1 <= x0 or y1 <= y0:
            raise ComputerError("`region` must have x1 > x0 and y1 > y0")
        f = self.frame
        if not (0 <= x0 < f.shot_w and 0 <= y0 < f.shot_h):
            raise ComputerError(f"region start {[x0, y0]} is outside the {f.shot_w}x{f.shot_h} screenshot")
        sx0 = f.left + int(round(x0 / f.scale))
        sy0 = f.top + int(round(y0 / f.scale))
        x1c, y1c = min(x1, f.shot_w), min(y1, f.shot_h)
        sx1 = f.left + min(f.width, int(round(x1c / f.scale)))
        sy1 = f.top + min(f.height, int(round(y1c / f.scale)))
        img = self._grab((sx0, sy0, sx1, sy1))
        s = compute_scale(img.width, img.height, self.max_edge, upscale_limit=ZOOM_MAX_UPSCALE)
        if abs(s - 1.0) > 0.01:
            img = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.LANCZOS)
        return self._image_result(img)

    def _click(self, inp: dict, button: str, clicks: int) -> dict:
        mods = parse_modifiers(inp.get("text"))
        coord = inp.get("coordinate")
        pos = self._to_screen(coord) if coord is not None else None
        with self._hold_mods(mods):
            if pos:
                self.pg.click(pos[0], pos[1], clicks=clicks, interval=0.08, button=button)
            else:
                self.pg.click(clicks=clicks, interval=0.08, button=button)
        return self._ok()

    def _do_left_click(self, inp): return self._click(inp, "left", 1)
    def _do_right_click(self, inp): return self._click(inp, "right", 1)
    def _do_middle_click(self, inp): return self._click(inp, "middle", 1)
    def _do_double_click(self, inp): return self._click(inp, "left", 2)
    def _do_triple_click(self, inp): return self._click(inp, "left", 3)

    def _do_left_click_drag(self, inp: dict) -> dict:
        start = self._to_screen(inp.get("start_coordinate"), "start_coordinate")
        end = self._to_screen(inp.get("coordinate"))
        mods = parse_modifiers(inp.get("text"))
        pg = self.pg
        with self._hold_mods(mods):
            pg.moveTo(*start)
            self._mouse_held = True
            try:
                pg.mouseDown(button="left")
                pg.moveTo(*end, duration=0.4)
            finally:
                self._release(buttons=("left",))
        return self._ok()

    def _do_mouse_move(self, inp: dict) -> dict:
        self.pg.moveTo(*self._to_screen(inp.get("coordinate")), duration=0.1)
        return self._ok()

    def _do_left_mouse_down(self, inp: dict) -> dict:
        self._mouse_held = True
        self.pg.mouseDown(button="left")
        return self._ok()

    def _do_left_mouse_up(self, inp: dict) -> dict:
        self._release(buttons=("left",))
        return self._ok()

    def _do_cursor_position(self, inp: dict) -> dict:
        sx, sy = self.pg.position()
        pos = self._to_shot(sx, sy)
        if pos is None:
            return self._ok("The cursor is outside the area of the last screenshot.")
        return self._ok(f"X={pos[0]}, Y={pos[1]}")

    def _do_scroll(self, inp: dict) -> dict:
        direction = inp.get("scroll_direction")
        if direction not in ("up", "down", "left", "right"):
            raise ComputerError("`scroll_direction` must be up, down, left or right")
        amount = int(inp.get("scroll_amount") or 3)
        amount = max(1, min(amount, 100))
        mods = parse_modifiers(inp.get("text"))
        if inp.get("coordinate") is not None:
            self.pg.moveTo(*self._to_screen(inp["coordinate"]))
        sign = 1 if direction in ("up", "right") else -1
        horizontal = direction in ("left", "right")
        with self._hold_mods(mods):
            for _ in range(amount):
                self._check_abort()
                if IS_WINDOWS:
                    _wheel(sign * WHEEL_DELTA, horizontal)
                elif horizontal:
                    self.pg.hscroll(sign)
                else:
                    self.pg.scroll(sign)
                time.sleep(0.02)
        return self._ok()

    def _do_type(self, inp: dict) -> dict:
        text = inp.get("text")
        if not isinstance(text, str):
            raise ComputerError("`text` is required for type")
        if not IS_WINDOWS:
            self.pg.write(text, interval=0.01)
            return self._ok()
        buf = ""
        self._check_abort()
        for ch in text.replace("\r\n", "\n"):
            if self.stop_event.is_set():
                raise UserAbort("Stopped by user")
            if ch in ("\n", "\t", "\r"):
                if buf:
                    _unicode_chunk(buf)
                    buf = ""
                if ch == "\n":
                    _vk_press(VK_RETURN)
                elif ch == "\t":
                    _vk_press(VK_TAB)
                time.sleep(0.02)
                continue
            buf += ch
            if len(buf) >= 24:
                _unicode_chunk(buf)
                buf = ""
                time.sleep(0.012)
                self._check_abort()
        if buf:
            _unicode_chunk(buf)
        return self._ok()

    def _do_key(self, inp: dict) -> dict:
        text = inp.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ComputerError("`text` is required for key")
        try:
            seq = parse_key_sequence(text)
        except ValueError as e:
            raise ComputerError(str(e)) from e
        repeat = max(1, min(int(inp.get("repeat") or 1), 100))
        for _ in range(repeat):
            for combo in seq:
                if self.stop_event.is_set():
                    raise UserAbort("Stopped by user")
                if len(combo) == 1:
                    self.pg.press(combo[0])
                else:
                    self.pg.hotkey(*combo, interval=0.03)
            if repeat > 1:
                time.sleep(0.03)
        return self._ok()

    def _do_hold_key(self, inp: dict) -> dict:
        try:
            keys = parse_combo(str(inp.get("text") or ""))
        except ValueError as e:
            raise ComputerError(str(e)) from e
        duration = float(inp.get("duration") or 1.0)
        duration = max(0.0, min(duration, 300.0))
        try:
            for k in keys:
                self._held_keys.add(k)
                self.pg.keyDown(k)
            self.stop_event.wait(duration)
        finally:
            self._release(list(reversed(keys)))
        return self._ok()

    def _do_wait(self, inp: dict) -> dict:
        duration = max(0.0, min(float(inp.get("duration") or 1.0), 300.0))
        if self.stop_event.wait(duration):
            raise UserAbort("Stopped by user")
        return self._ok()


def thumbnail_b64(img: Image.Image, width: int = 640) -> str:
    """Small JPEG for the web UI (never sent to the model)."""
    im = img.copy()
    if im.width > width:
        im = im.resize((width, max(1, int(im.height * width / im.width))), Image.BILINEAR)
    buf = io.BytesIO()
    im.convert("RGB").save(buf, format="JPEG", quality=70)
    return base64.b64encode(buf.getvalue()).decode()
