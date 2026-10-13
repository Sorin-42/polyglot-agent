"""Desktop entry point used by PolyglotAgent.exe.

- no console window (run with pythonw / as a windowed exe); logs go to data/app.log
- one instance at a time; launching again just brings the window to the front
- the UI opens in a window chosen in Settings (window_mode, applied at launch):
  app     built-in window on WebView2, the Chromium engine that ships with
          Windows 10/11 (no browser needed). Falls back to Edge/Chrome if the
          WebView2 runtime or pywebview is missing.
  edge /  that browser's chromeless "--app" window with a private profile.
  chrome
  browser a tab in the default browser (e.g. Firefox); quits after the tab has
          been closed for a while.
  Closing the app window quits the app.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Optional

from . import config
from .computer import enable_dpi_awareness

MUTEX_NAME = "Local\\PolyglotAgent.SingleInstance"
IDLE_EXIT_SECONDS = 120
# uvicorn waits forever for open connections by default; on Windows a dropped
# connection can stay "open" to asyncio, which left a process with no server
# but still holding the single-instance lock, so relaunching opened a dead URL.
SHUTDOWN_GRACE_SECONDS = 3
FORCE_EXIT_SECONDS = 15        # hard stop if shutdown still hangs anywhere
_mutex_handle = None


def _redirect_output() -> None:
    """pythonw / windowed exes have no stdout; uvicorn would crash writing logs."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    log = open(config.DATA_DIR / "app.log", "w", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or log
    sys.stderr = sys.stderr or log


def free_port(preferred: int = 8765) -> int:
    for port in (preferred, 0):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
            return s.getsockname()[1]
        except OSError:
            continue
        finally:
            s.close()
    raise RuntimeError("no free port")


def _first_instance() -> bool:
    global _mutex_handle
    if sys.platform != "win32":
        return True
    import ctypes
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW.restype = ctypes.c_void_p
    _mutex_handle = k32.CreateMutexW(None, False, MUTEX_NAME)
    return k32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def _server_alive() -> bool:
    """True if the running instance's server accepts connections."""
    try:
        info = json.loads((config.DATA_DIR / "running.json").read_text(encoding="utf-8"))
        port = urllib.parse.urlsplit(info["url"]).port
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _wait_for_previous_exit(timeout: float = FORCE_EXIT_SECONDS + 5) -> bool:
    """The other instance holds the lock but its server is gone, so it is still
    shutting down. Wait for it to release the lock; True once we are first."""
    import ctypes
    k32 = ctypes.windll.kernel32
    deadline = time.time() + timeout
    while time.time() < deadline:
        k32.CloseHandle(ctypes.c_void_p(_mutex_handle))
        time.sleep(0.5)
        if _first_instance():
            return True
    return False


def _hard_exit() -> None:
    """End the process now: also stops worker threads that would otherwise keep it
    (and the single-instance lock) alive, e.g. a PowerShell command still running."""
    try:
        (config.DATA_DIR / "running.json").unlink()
    except OSError:
        pass
    for f in (sys.stdout, sys.stderr):
        try:
            f.flush()
        except Exception:  # noqa: BLE001
            pass
    os._exit(0)


def _request_exit(srv) -> None:
    """Stop the server; if shutdown hangs anyway, force the process to end."""
    srv.should_exit = True

    def force() -> None:
        time.sleep(FORCE_EXIT_SECONDS)
        print("Shutdown took too long; forcing exit.", flush=True)
        _hard_exit()
    threading.Thread(target=force, daemon=True).start()


def _focus_existing() -> None:
    if sys.platform == "win32":
        try:
            from . import computer as c
            for h, title in c._top_windows():
                if config.APP_NAME in title:
                    c._u32.ShowWindow(h, 9)
                    c._vk_down_up(0x12)
                    c._u32.SetForegroundWindow(h)
                    return
        except Exception:  # noqa: BLE001
            pass
    try:  # window not found (browser mode): reopen the running instance's URL
        info = json.loads((config.DATA_DIR / "running.json").read_text(encoding="utf-8"))
        webbrowser.open(info["url"])
    except Exception:  # noqa: BLE001
        pass


_BROWSERS = {  # name -> (exe, path under Program Files / LocalAppData)
    "edge": ("msedge.exe", r"Microsoft\Edge\Application\msedge.exe"),
    "chrome": ("chrome.exe", r"Google\Chrome\Application\chrome.exe"),
}


def find_app_browser(prefer: str = "edge") -> Optional[str]:
    """Edge or Chrome for a chromeless app window; `prefer` is tried first."""
    if sys.platform != "win32":
        return None
    for name in [prefer] + [b for b in _BROWSERS if b != prefer]:
        if name not in _BROWSERS:
            continue
        exe, rel = _BROWSERS[name]
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}")
            path = winreg.QueryValue(key, None)
            if path and Path(path).is_file():
                return path
        except OSError:
            pass
        for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base and (Path(base) / rel).is_file():
                return str(Path(base) / rel)
    return None


def _open_app_window(url: str, prefer: str = "edge") -> Optional[subprocess.Popen]:
    exe = find_app_browser(prefer)
    if not exe:
        return None
    # one private profile per browser: Edge and Chrome profiles are not interchangeable
    profile = config.DATA_DIR / ("window-profile-chrome" if exe.lower().endswith("chrome.exe")
                                 else "window-profile")
    args = [exe, f"--app={url}", f"--user-data-dir={profile}", "--no-first-run",
            "--no-default-browser-check", "--disable-background-mode", "--window-size=1320,880"]
    try:
        return subprocess.Popen(args)
    except OSError:
        return None


def _wait_started(srv) -> None:
    for _ in range(300):  # wait for the server to accept connections
        if getattr(srv, "started", False):
            return
        time.sleep(0.05)


def _watch(srv, url: str, mode: str) -> None:
    """Browser-based modes: open the window/tab, quit when it is closed."""
    from . import server
    _wait_started(srv)
    proc = None if mode == "browser" else _open_app_window(url, mode)
    if proc is not None:
        t0 = time.time()
        proc.wait()
        if time.time() - t0 > 5:      # the user closed the app window
            _request_exit(srv)
            return
        # The browser handed off to an existing process; fall back to idle watching.
    else:
        webbrowser.open(url)
    idle_since = time.time()
    while not srv.should_exit:
        time.sleep(5)
        sess = server.state.session
        if server.hub.sockets or (sess is not None and sess.busy):
            idle_since = time.time()
        elif time.time() - idle_since > IDLE_EXIT_SECONDS:
            _request_exit(srv)


APP_ICON = Path(__file__).resolve().parent / "static" / "icons" / "app.ico"


def webview2_ready() -> bool:
    """pywebview is installed and will use WebView2. Without the WebView2 runtime,
    pywebview would silently use the old Internet Explorer engine, which cannot
    run this UI."""
    if sys.platform != "win32":
        return False
    try:
        from webview.platforms import winforms
    except Exception as e:  # noqa: BLE001 - ImportError, pythonnet/.NET problems
        print(f"Built-in window unavailable ({type(e).__name__}: {e}).", flush=True)
        return False
    if getattr(winforms, "renderer", "") != "edgechromium":
        print("Built-in window unavailable: the WebView2 runtime is not installed.", flush=True)
        return False
    return True


def _app_window(srv, url: str) -> None:
    """Built-in app window on WebView2; blocks (on the main thread) until it is closed."""
    import webview

    from . import server
    if sys.platform == "win32":
        try:  # own taskbar button and icon instead of being grouped under pythonw.exe
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PolyglotAgent.App")
        except Exception:  # noqa: BLE001
            pass
    _wait_started(srv)
    window = webview.create_window(config.APP_NAME, url, width=1320, height=880, min_size=(720, 480))

    def quit_app() -> None:   # Settings > Quit: stop the server and close the window
        _request_exit(srv)
        window.destroy()
    server.state.shutdown = quit_app
    webview.start(gui="edgechromium", private_mode=False, storage_path=str(config.DATA_DIR / "webview"),
                  icon=str(APP_ICON) if APP_ICON.is_file() else None)


def _run_server(srv) -> None:
    try:
        srv.run()
    except BaseException:  # noqa: BLE001 - log it; the process ends either way
        traceback.print_exc()


def main() -> None:
    ap = argparse.ArgumentParser(description="Polyglot Agent (desktop)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--browser", action="store_true", help="use the default browser instead of an app window")
    args, _ = ap.parse_known_args()

    enable_dpi_awareness()
    if not _first_instance():
        # Running and reachable: bring it to the front. Server gone: the previous
        # instance is still closing, so wait for it and then start normally.
        if _server_alive() or not _wait_for_previous_exit():
            _focus_existing()
            return
    _redirect_output()   # only the instance that runs may truncate app.log

    import uvicorn

    from . import server
    port = free_port(args.port)
    server.configure(port)
    url = f"http://127.0.0.1:{port}/"
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning",
                                        timeout_graceful_shutdown=SHUTDOWN_GRACE_SECONDS))
    server.state.shutdown = lambda: _request_exit(srv)

    running = config.DATA_DIR / "running.json"
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    running.write_text(json.dumps({"url": url, "pid": os.getpid()}), encoding="utf-8")
    mode = "browser" if args.browser else server.state.settings.window_mode
    print(f"{config.APP_NAME} at {url} (window: {mode})", flush=True)
    try:
        if mode == "app" and webview2_ready():
            # The window needs the main thread, so the server runs beside it.
            server_thread = threading.Thread(target=_run_server, args=(srv,), daemon=True)
            server_thread.start()
            try:
                _app_window(srv, url)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                print("Built-in window failed; opening an Edge/Chrome window instead.", flush=True)
                _watch(srv, url, "edge")
            _request_exit(srv)
            server_thread.join(SHUTDOWN_GRACE_SECONDS + 5)
        else:
            if mode == "app":
                mode = "edge"
            threading.Thread(target=_watch, args=(srv, url, mode), daemon=True).start()
            _run_server(srv)
    finally:
        print(f"{config.APP_NAME} stopped.", flush=True)
        _hard_exit()


if __name__ == "__main__":
    main()
