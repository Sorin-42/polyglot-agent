"""Start the app, check that it answers, then quit it the way Settings > Quit does. Used by CI
on Windows, macOS and Linux, for the Mac app and for the Python pack.

Usage:  python tools/smoke_test.py STATE_DIR -- COMMAND [ARGS...]

STATE_DIR is where the app writes running.json (data/ for the pack;
~/Library/Application Support/PolyglotAgent for the Mac app). Exits non-zero on failure.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

START_TIMEOUT = 300        # the pack installs its components on the first start


def fail(msg: str, state: Path, proc: subprocess.Popen) -> None:
    print(f"SMOKE TEST FAILED: {msg}", flush=True)
    log = state / "app.log"
    if log.is_file():
        print("--- app.log (last 40 lines) ---")
        print("\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]))
    if proc.poll() is None:
        proc.kill()
    sys.exit(1)


def call(url: str, token: str, method: str = "GET", body: dict | None = None) -> dict:
    req = urllib.request.Request(url, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Token": token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read() or b"{}")


def main() -> None:
    if "--" not in sys.argv[2:]:
        sys.exit(__doc__)
    state = Path(sys.argv[1]).expanduser()
    cmd = sys.argv[sys.argv.index("--") + 1:]
    running = state / "running.json"
    running.unlink(missing_ok=True)
    print("Starting:", " ".join(cmd), flush=True)
    proc = subprocess.Popen(cmd)
    deadline = time.time() + START_TIMEOUT
    while True:
        if proc.poll() is not None:
            fail(f"the app exited early (code {proc.returncode})", state, proc)
        if time.time() > deadline:
            fail(f"no answer within {START_TIMEOUT}s", state, proc)
        try:
            url = json.loads(running.read_text(encoding="utf-8"))["url"]
            page = urllib.request.urlopen(url, timeout=5).read().decode("utf-8")
            break
        except (OSError, ValueError, KeyError):
            time.sleep(1)
    token = re.search(r'window\.PA_TOKEN = "([^"]+)"', page).group(1)
    meta = call(url + "api/settings", token)["meta"]
    print(f"App answered at {url}: platform {meta['platform']}, "
          f"data folder {meta['data_dir'] or '(not chosen yet)'}, computer control "
          f"{'available' if meta['computer_supported'] else 'not available'}", flush=True)
    if meta["platform"] != sys.platform:
        fail(f"platform is {meta['platform']}, expected {sys.platform}", state, proc)
    if not meta["desktop"]:
        fail("not running as the desktop app (no Quit)", state, proc)
    print("Quit:", call(url + "api/quit", token, "POST", {}), flush=True)
    try:
        code = proc.wait(timeout=40)
    except subprocess.TimeoutExpired:
        fail("the app did not quit within 40s", state, proc)
    print(f"The app quit (exit code {code}). Smoke test passed.")


if __name__ == "__main__":
    main()
