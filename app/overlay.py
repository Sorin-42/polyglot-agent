"""On-screen signal that the agent is controlling the computer (Windows only).

While the agent acts on the desktop:
- an orange border glows around the controlled monitor, with a label at the top;
- a "Claude" pointer marker stays where the agent last pointed and pulses on clicks,
  so its pointer can be told apart from the user's;
- pressing Esc on the physical keyboard stops the agent. Esc keys the agent sends
  itself are injected input and are ignored.

The overlay windows are click-through, never take focus, and are excluded from
screen capture, so the agent's screenshots never show them. All Win32 work happens
on one thread with its own message loop; the public methods only queue commands.
"""
from __future__ import annotations

import queue
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

from PIL import Image, ImageChops, ImageDraw, ImageFont

IS_WINDOWS = sys.platform == "win32"
ACCENT = (217, 119, 87)               # Claude orange, #D97757
VK_ESCAPE = 0x1B
LLKHF_INJECTED = 0x10                 # set for SendInput / keybd_event keys (the agent's own)
PULSE_MS = 320

LABELS = {
    "简体中文": "Claude 正在使用你的电脑 · 按 Esc 停止",
    "繁體中文": "Claude 正在使用你的電腦 · 按 Esc 停止",
}
DEFAULT_LABEL = "Claude is using your computer · Press Esc to stop"


def control_label(language: str) -> str:
    return LABELS.get(language, DEFAULT_LABEL)


def is_physical_escape(vk: int, flags: int) -> bool:
    """Esc pressed on the real keyboard (not injected by the agent)."""
    return vk == VK_ESCAPE and not (flags & LLKHF_INJECTED)


# ------------------------------------------------------------------ drawing
def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    fonts = Path("C:/Windows/Fonts")
    names = ["msyhbd.ttc", "segoeuib.ttf"] if bold else ["msyh.ttc", "segoeui.ttf"]
    for n in names:
        if (fonts / n).is_file():
            try:
                return ImageFont.truetype(str(fonts / n), size)
            except OSError:
                pass
    return ImageFont.load_default(size)


def render_edge(length: int, thickness: int, side: str) -> Image.Image:
    """A glow strip: solid at the screen edge, fading inwards."""
    horizontal = side in ("top", "bottom")
    img = Image.new("RGBA", (length, thickness) if horizontal else (thickness, length), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    solid = max(2, thickness // 3)
    for i in range(thickness):              # i = distance from the screen edge
        a = 235 if i < solid else int(235 * (1 - (i - solid) / (thickness - solid)) ** 2)
        pos = i if side in ("top", "left") else thickness - 1 - i
        if horizontal:
            d.line([(0, pos), (length, pos)], fill=(*ACCENT, a))
        else:
            d.line([(pos, 0), (pos, length)], fill=(*ACCENT, a))
    return img


def render_label(text: str, scale: float) -> Image.Image:
    font = _font(round(14 * scale), bold=True)
    pad_x, pad_y = round(14 * scale), round(7 * scale)
    l, t, r, b = font.getbbox(text)
    w, h = r - l + 2 * pad_x, b - t + 2 * pad_y
    k = 3                                   # draw large, then shrink: smooth edges
    big = Image.new("RGBA", (w * k, h * k), (0, 0, 0, 0))
    ImageDraw.Draw(big).rounded_rectangle([0, 0, w * k - 1, h * k - 1], radius=h * k // 2,
                                          fill=(*ACCENT, 240))
    img = big.resize((w, h), Image.LANCZOS)
    ImageDraw.Draw(img).text((pad_x - l, pad_y - t), text, font=font, fill=(255, 255, 255, 255))
    return img


def render_marker(scale: float, pressed: bool = False, flip_x: bool = False,
                  flip_y: bool = False) -> tuple[Image.Image, tuple[int, int]]:
    """The agent's pointer: a ring with a "Claude" tag, below-right of the ring
    (flipped near the right/bottom screen edge). Returns (image, hotspot)."""
    k = 4
    r = round((15 if pressed else 12) * scale)          # ring radius
    ring = max(2, round(3 * scale))
    font = _font(round(11 * scale), bold=True)
    tl, tt, tr, tb = font.getbbox("Claude")
    tag_w, tag_h = tr - tl + round(12 * scale), tb - tt + round(6 * scale)
    gap = round(8 * scale)
    # symmetric canvas with the hotspot (ring centre) in the middle
    cx, cy = round(16 * scale) + gap + tag_w + 2, round(16 * scale) + gap + tag_h + 2
    w, h = 2 * cx, 2 * cy
    big = Image.new("RGBA", (w * k, h * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    fill = (*ACCENT, 120 if pressed else 55)
    d.ellipse([(cx - r) * k, (cy - r) * k, (cx + r) * k, (cy + r) * k], fill=fill,
              outline=(*ACCENT, 255), width=ring * k)
    dot = 2 * scale
    d.ellipse([(cx - dot) * k, (cy - dot) * k, (cx + dot) * k, (cy + dot) * k], fill=(*ACCENT, 255))
    x0 = cx - gap - tag_w if flip_x else cx + gap
    y0 = cy - gap - tag_h if flip_y else cy + gap
    d.rounded_rectangle([x0 * k, y0 * k, (x0 + tag_w) * k, (y0 + tag_h) * k], radius=tag_h * k // 2,
                        fill=(*ACCENT, 245))
    img = big.resize((w, h), Image.LANCZOS)
    ImageDraw.Draw(img).text((x0 + round(6 * scale) - tl, y0 + round(3 * scale) - tt), "Claude",
                             font=font, fill=(255, 255, 255, 255))
    return img, (cx, cy)


# ------------------------------------------------------------------- Win32
if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                    ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
                    ("hIconSm", wintypes.HICON)]

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class BLENDFUNCTION(ctypes.Structure):
        _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                    ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                    ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 1)]

    _u32.DefWindowProcW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    _u32.DefWindowProcW.restype = LRESULT
    _u32.RegisterClassExW.argtypes = (ctypes.POINTER(WNDCLASSEXW),)
    _u32.RegisterClassExW.restype = wintypes.ATOM
    _u32.CreateWindowExW.argtypes = (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID)
    _u32.CreateWindowExW.restype = wintypes.HWND
    _u32.DestroyWindow.argtypes = (wintypes.HWND,)
    _u32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    _u32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wintypes.UINT)
    _u32.SetWindowDisplayAffinity.argtypes = (wintypes.HWND, wintypes.DWORD)
    _u32.UpdateLayeredWindow.argtypes = (wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                         ctypes.POINTER(wintypes.SIZE), wintypes.HDC,
                                         ctypes.POINTER(wintypes.POINT), wintypes.COLORREF,
                                         ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD)
    _u32.GetDC.argtypes = (wintypes.HWND,)
    _u32.GetDC.restype = wintypes.HDC
    _u32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
    _u32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
    _u32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
    _u32.SetWindowsHookExW.restype = wintypes.HHOOK
    _u32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    _u32.CallNextHookEx.restype = LRESULT
    _u32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
    _u32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
    _u32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
    _u32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
    _u32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    _u32.SetTimer.argtypes = (wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p)
    _u32.SetTimer.restype = ctypes.c_size_t
    _u32.KillTimer.argtypes = (wintypes.HWND, ctypes.c_size_t)
    _u32.MonitorFromPoint.argtypes = (wintypes.POINT, wintypes.DWORD)
    _u32.MonitorFromPoint.restype = wintypes.HMONITOR
    _k32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
    _k32.GetModuleHandleW.restype = wintypes.HMODULE
    _k32.GetCurrentThreadId.restype = wintypes.DWORD
    _gdi.CreateCompatibleDC.argtypes = (wintypes.HDC,)
    _gdi.CreateCompatibleDC.restype = wintypes.HDC
    _gdi.CreateDIBSection.argtypes = (wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
                                      ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD)
    _gdi.CreateDIBSection.restype = wintypes.HBITMAP
    _gdi.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
    _gdi.SelectObject.restype = wintypes.HGDIOBJ
    _gdi.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
    _gdi.DeleteDC.argtypes = (wintypes.HDC,)

    WM_APP, WM_TIMER, WM_QUIT = 0x8000, 0x0113, 0x0012
    WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
    EX_STYLE = (0x80000      # WS_EX_LAYERED
                | 0x20       # WS_EX_TRANSPARENT: clicks pass through
                | 0x8        # WS_EX_TOPMOST
                | 0x80       # WS_EX_TOOLWINDOW: no taskbar button, not in window lists
                | 0x8000000)  # WS_EX_NOACTIVATE: never takes focus
    WS_POPUP = 0x80000000
    WDA_EXCLUDEFROMCAPTURE = 0x11
    HWND_TOPMOST = wintypes.HWND(-1)
    SWP_FLAGS = 0x0001 | 0x0002 | 0x0010   # NOSIZE | NOMOVE | NOACTIVATE

    def _blit(hwnd, img: Image.Image, x: int, y: int) -> None:
        """Show a per-pixel-alpha image as the window's content, at (x, y)."""
        w, h = img.size
        r, g, b, a = img.split()
        r, g, b = (ImageChops.multiply(ch, a) for ch in (r, g, b))   # premultiplied alpha
        data = Image.merge("RGBA", (b, g, r, a)).tobytes()           # BGRA byte order
        screen = _u32.GetDC(None)
        mem = _gdi.CreateCompatibleDC(screen)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth, bmi.bmiHeader.biHeight = w, -h        # top-down rows
        bmi.bmiHeader.biPlanes, bmi.bmiHeader.biBitCount = 1, 32
        bits = ctypes.c_void_p()
        hbmp = _gdi.CreateDIBSection(mem, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        try:
            ctypes.memmove(bits, data, len(data))
            old = _gdi.SelectObject(mem, hbmp)
            blend = BLENDFUNCTION(0, 0, 255, 1)                      # AC_SRC_OVER, AC_SRC_ALPHA
            _u32.UpdateLayeredWindow(hwnd, screen, ctypes.byref(wintypes.POINT(x, y)),
                                     ctypes.byref(wintypes.SIZE(w, h)), mem,
                                     ctypes.byref(wintypes.POINT(0, 0)), 0, ctypes.byref(blend), 2)
            _gdi.SelectObject(mem, old)
        finally:
            _gdi.DeleteObject(hbmp)
            _gdi.DeleteDC(mem)
            _u32.ReleaseDC(None, screen)

    def _monitor_scale(rect: tuple[int, int, int, int]) -> float:
        try:
            pt = wintypes.POINT((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)
            hmon = _u32.MonitorFromPoint(pt, 2)
            dx, dy = wintypes.UINT(), wintypes.UINT()
            ctypes.windll.shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dx), ctypes.byref(dy))
            return max(1.0, dx.value / 96)
        except Exception:  # noqa: BLE001
            return max(1.0, (rect[3] - rect[1]) / 1080)


class ControlOverlay:
    """Border + label + agent pointer + Esc-to-stop. Methods are thread-safe and
    do nothing on other platforms."""

    def __init__(self, on_escape: Callable[[], None], exclude_from_capture: bool = True):
        self.on_escape = on_escape
        self.exclude_from_capture = exclude_from_capture
        self.active = False
        self._q: "queue.Queue[tuple]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._tid = 0
        self._swallow_esc = False

    # ------------------------------------------------------------ public API
    def show(self, rect: Optional[tuple[int, int, int, int]], label: str) -> None:
        """Enter control mode: arm Esc and draw the border and label."""
        self.active = True
        self._send("show", rect, label)

    def pointer(self, click: bool = False) -> None:
        """Move the agent's pointer marker to the current cursor position."""
        if self.active:
            self._send("pointer", click)

    def hide(self) -> None:
        if self.active:
            self.active = False
            self._send("hide")

    def close(self) -> None:
        if self._thread is not None and self._tid:
            _u32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)

    # -------------------------------------------------------------- plumbing
    def _send(self, *cmd) -> None:
        if not IS_WINDOWS:
            return
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="control-overlay", daemon=True)
            self._thread.start()
            self._ready.wait(5)
        self._q.put(cmd)
        if self._tid:
            _u32.PostThreadMessageW(self._tid, WM_APP, 0, 0)

    def _run(self) -> None:
        self._tid = _k32.GetCurrentThreadId()
        self._hook = None
        self._swallow_esc = False
        self._pulse_timer = 0
        self._scale = 1.0
        self._marker_at: Optional[tuple[int, int]] = None
        self._rect = (0, 0, 1 << 30, 1 << 30)
        self._wndproc = WNDPROC(lambda h, m, w, l: _u32.DefWindowProcW(h, m, w, l))
        self._hookproc = HOOKPROC(self._on_key)
        hinst = _k32.GetModuleHandleW(None)
        wc = WNDCLASSEXW(cbSize=ctypes.sizeof(WNDCLASSEXW), lpfnWndProc=self._wndproc,
                         hInstance=hinst, lpszClassName="PolyglotControlOverlay")
        _u32.RegisterClassExW(ctypes.byref(wc))
        self._wins = {}
        for name in ("top", "bottom", "left", "right", "label", "marker"):
            h = _u32.CreateWindowExW(EX_STYLE, "PolyglotControlOverlay", None, WS_POPUP,
                                     0, 0, 1, 1, None, None, hinst, None)
            if self.exclude_from_capture:
                _u32.SetWindowDisplayAffinity(h, WDA_EXCLUDEFROMCAPTURE)
            self._wins[name] = h
        msg = wintypes.MSG()
        self._ready.set()
        while _u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.hWnd is None and msg.message == WM_APP:
                self._drain()
            elif msg.hWnd is None and msg.message == WM_TIMER and msg.wParam == self._pulse_timer:
                _u32.KillTimer(None, self._pulse_timer)
                self._pulse_timer = 0
                self._draw_marker(pressed=False)
            else:
                _u32.TranslateMessage(ctypes.byref(msg))
                _u32.DispatchMessageW(ctypes.byref(msg))
        self._unhook()
        for h in self._wins.values():
            _u32.DestroyWindow(h)

    def _drain(self) -> None:
        while True:
            try:
                cmd = self._q.get_nowait()
            except queue.Empty:
                return
            try:
                getattr(self, "_do_" + cmd[0])(*cmd[1:])
            except Exception as e:  # noqa: BLE001 - the overlay must never break the agent
                print(f"control overlay: {cmd[0]} failed: {e}", flush=True)

    def _do_show(self, rect, label: str) -> None:
        if self._hook is None:
            self._hook = _u32.SetWindowsHookExW(13, self._hookproc, _k32.GetModuleHandleW(None), 0)
        if rect is None:
            rect = (0, 0, _u32.GetSystemMetrics(0), _u32.GetSystemMetrics(1))
        self._rect = rect
        left, top, right, bottom = rect
        w, h = right - left, bottom - top
        s = self._scale = _monitor_scale(rect)
        t = round(7 * s)
        edges = {"top": (left, top, w), "bottom": (left, bottom - t, w),
                 "left": (left, top, h), "right": (right - t, top, h)}
        for side, (x, y, length) in edges.items():
            _blit(self._wins[side], render_edge(length, t, side), x, y)
        img = render_label(label, s)
        _blit(self._wins["label"], img, left + (w - img.width) // 2, top + round(10 * s))
        for name in ("top", "bottom", "left", "right", "label"):
            _u32.ShowWindow(self._wins[name], 4)              # SW_SHOWNOACTIVATE
            _u32.SetWindowPos(self._wins[name], HWND_TOPMOST, 0, 0, 0, 0, SWP_FLAGS)

    def _do_pointer(self, click: bool) -> None:
        pt = wintypes.POINT()
        _u32.GetCursorPos(ctypes.byref(pt))
        self._marker_at = (pt.x, pt.y)
        self._draw_marker(pressed=click)
        _u32.ShowWindow(self._wins["marker"], 4)
        _u32.SetWindowPos(self._wins["marker"], HWND_TOPMOST, 0, 0, 0, 0, SWP_FLAGS)
        if click:
            if self._pulse_timer:
                _u32.KillTimer(None, self._pulse_timer)
            self._pulse_timer = _u32.SetTimer(None, 0, PULSE_MS, None)

    def _draw_marker(self, pressed: bool) -> None:
        if self._marker_at is None:
            return
        x, y = self._marker_at
        s, (_, _, right, bottom) = self._scale, self._rect
        img, (hx, hy) = render_marker(s, pressed, flip_x=x > right - 110 * s, flip_y=y > bottom - 60 * s)
        _blit(self._wins["marker"], img, x - hx, y - hy)

    def _do_hide(self) -> None:
        self._unhook()
        for h in self._wins.values():
            _u32.ShowWindow(h, 0)                             # SW_HIDE
        self._marker_at = None

    def _unhook(self) -> None:
        if self._hook:
            _u32.UnhookWindowsHookEx(self._hook)
            self._hook = None
        self._swallow_esc = False

    def _on_key(self, n: int, wparam: int, lparam: int) -> int:
        """Low-level keyboard hook: a physical Esc stops the agent and is not passed on."""
        if n >= 0:
            kb = ctypes.cast(ctypes.c_void_p(lparam), ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if is_physical_escape(kb.vkCode, kb.flags):
                if wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    if not self._swallow_esc:
                        self._swallow_esc = True
                        try:
                            self.on_escape()
                        except Exception as e:  # noqa: BLE001
                            print(f"control overlay: Esc handler failed: {e}", flush=True)
                    return 1
                if wparam in (WM_KEYUP, WM_SYSKEYUP) and self._swallow_esc:
                    self._swallow_esc = False
                    return 1
        return _u32.CallNextHookEx(None, n, wparam, lparam)
