"""Entry point:  python -m app.main  [--port 8765] [--no-browser]"""
from __future__ import annotations

import argparse
import sys
import threading
import webbrowser

from .computer import enable_dpi_awareness


def main() -> None:
    ap = argparse.ArgumentParser(description="Polyglot computer-use agent")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="don't open the UI automatically")
    args = ap.parse_args()

    # Physical-pixel coordinates on high-DPI screens; must happen before
    # anything touches the display.
    enable_dpi_awareness()

    import uvicorn

    from . import server
    server.configure(args.port)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Polyglot Agent running at {url}  (Ctrl+C to quit)", flush=True)
    if sys.platform == "win32":
        print("Emergency stop: press Stop in the UI, or slam the mouse into a screen corner.", flush=True)
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    # Bound to localhost only: this server can control your mouse and keyboard.
    uvicorn.run(server.app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
