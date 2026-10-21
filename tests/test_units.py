import asyncio
import json
import sys

import httpx
import pytest

from app.api import APIError, StreamAccumulator, stream_message
from app.computer import compute_scale
from app.keys import parse_combo, parse_key_sequence, parse_modifiers
from app.memory_store import MemoryError_, MemoryStore
from app.config import LANGUAGES, Settings, apply_updates
from app.translator import StreamingTranslation, Translator, is_pure_code
from conftest import sse


# ------------------------------------------------------------------ memory
def test_memory_paths_are_confined(tmp_path):
    m = MemoryStore(tmp_path / "mem")
    for bad in ["/memories/../secret.txt", "/memories/%2e%2e/x", "/etc/passwd", "memories/x",
                "/memories/a/../../x", "/memoriesX/a", "/memories\\..\\x"]:
        with pytest.raises(MemoryError_):
            m.resolve(bad)
    assert m.resolve("/memories/a/b.md") == (tmp_path / "mem" / "a" / "b.md").resolve()


def test_memory_commands(tmp_path):
    m = MemoryStore(tmp_path / "mem")
    assert m.execute({"command": "create", "path": "/memories/profile.md",
                      "file_text": "- name: Zed\n- field: chemistry\n"}) == \
        "File created successfully at: /memories/profile.md"
    v = m.execute({"command": "view", "path": "/memories/profile.md"})
    assert v.startswith("Here's the content of /memories/profile.md with line numbers:")
    assert "     1\t- name: Zed" in v
    d = m.execute({"command": "view", "path": "/memories"})
    assert d.startswith("Here're the files and directories up to 2 levels deep in /memories")
    assert "/memories/profile.md" in d
    r = m.execute({"command": "str_replace", "path": "/memories/profile.md",
                   "old_str": "chemistry", "new_str": "electrochemistry"})
    assert r.startswith("The memory file has been edited.")
    with pytest.raises(MemoryError_, match="did not appear verbatim"):
        m.execute({"command": "str_replace", "path": "/memories/profile.md", "old_str": "nope", "new_str": "x"})
    with pytest.raises(MemoryError_, match="Multiple occurrences"):
        m.execute({"command": "str_replace", "path": "/memories/profile.md", "old_str": "- ", "new_str": "* "})
    assert m.execute({"command": "insert", "path": "/memories/profile.md", "insert_line": 0,
                      "insert_text": "# Profile\n"}) == "The file /memories/profile.md has been edited."
    assert (tmp_path / "mem" / "profile.md").read_text().startswith("# Profile\n- name: Zed")
    with pytest.raises(MemoryError_, match="Invalid `insert_line`"):
        m.execute({"command": "insert", "path": "/memories/profile.md", "insert_line": 99, "insert_text": "x"})
    assert m.execute({"command": "rename", "old_path": "/memories/profile.md",
                      "new_path": "/memories/people/me.md"}) == \
        "Successfully renamed /memories/profile.md to /memories/people/me.md"
    with pytest.raises(MemoryError_, match="cannot delete the /memories root"):
        m.execute({"command": "delete", "path": "/memories"})
    assert m.execute({"command": "delete", "path": "/memories/people"}) == "Successfully deleted /memories/people"
    with pytest.raises(MemoryError_, match="does not exist"):
        m.execute({"command": "view", "path": "/memories/people/me.md"})


def test_memory_snapshot(tmp_path):
    m = MemoryStore(tmp_path / "mem")
    assert "empty" in m.snapshot()
    m.execute({"command": "create", "path": "/memories/preferences.md", "file_text": "- concise answers"})
    snap = m.snapshot()
    assert "/memories/preferences.md" in snap and "concise answers" in snap


# -------------------------------------------------------------------- keys
def test_key_parsing():
    assert parse_combo("ctrl+s") == ["ctrl", "s"]
    assert parse_combo("Return") == ["enter"]
    assert parse_combo("alt+Tab") == ["alt", "tab"]
    assert parse_combo("super") == ["win"]
    assert parse_combo("ctrl+shift+Escape") == ["ctrl", "shift", "esc"]
    assert parse_combo("ctrl++") == ["ctrl", "+"]
    assert parse_combo("ctrl+plus") == ["ctrl", "+"]
    assert parse_combo("Page_Down") == ["pagedown"]
    assert parse_combo("KP_5") == ["num5"]
    assert parse_combo("F11") == ["f11"]
    assert parse_key_sequence("ctrl+a Delete") == [["ctrl", "a"], ["delete"]]
    assert parse_modifiers("ctrl+shift") == ["ctrl", "shift"]
    with pytest.raises(ValueError):
        parse_modifiers("ctrl+a")
    with pytest.raises(ValueError):
        parse_combo("NotAKey")


def test_screenshot_scaling_respects_limits():
    import math
    for w, h in [(1920, 1080), (2560, 1600), (3840, 2160), (1366, 768), (2880, 1800), (5120, 1440)]:
        s = compute_scale(w, h, 1568)
        sw, sh = int(w * s), int(h * s)
        assert max(sw, sh) <= 1568
        assert math.ceil(sw / 28) * math.ceil(sh / 28) <= 4784
    assert compute_scale(1280, 720, 1568) == 1.0


# --------------------------------------------------------------------- api
def _events(raw: bytes):
    for chunk in raw.decode().split("\n\n"):
        for line in chunk.splitlines():
            if line.startswith("data:"):
                yield json.loads(line[5:])


def test_accumulator_keeps_blocks_verbatim():
    blocks = [
        {"type": "thinking", "thinking": "Plan: take a screenshot.", "signature": "SIG123=="},
        {"type": "text", "text": "Let me look at the screen.",
         "citations": [{"type": "web_search_result_location", "url": "https://x.org", "title": "X",
                        "encrypted_index": "abc", "cited_text": "hi"}]},
        {"type": "tool_use", "id": "toolu_1", "name": "left_click", "toolset_name": "computer",
         "input": {"coordinate": [512, 742], "text": "shift"}},
    ]
    acc = StreamAccumulator()
    for ev in _events(sse(blocks, stop_reason="tool_use")):
        acc.handle(ev)
    msg = acc.final()
    assert msg["stop_reason"] == "tool_use"
    th, tx, tu = msg["content"]
    assert th == {"type": "thinking", "thinking": "Plan: take a screenshot.", "signature": "SIG123=="}
    assert tx["text"] == "Let me look at the screen." and tx["citations"][0]["url"] == "https://x.org"
    assert tu == {"type": "tool_use", "id": "toolu_1", "name": "left_click", "toolset_name": "computer",
                  "input": {"coordinate": [512, 742], "text": "shift"}}
    assert msg["usage"]["output_tokens"] == 50 and msg["usage"]["input_tokens"] == 100


def test_blocks_are_kept_exactly_as_received():
    """Even null fields stay, so re-sent history is byte-identical to what the
    API produced (cache prefix + thinking binding)."""
    acc = StreamAccumulator()
    for ev in _events(sse([{"type": "text", "text": "hi"}])):
        acc.handle(ev)
    assert acc.final()["content"] == [{"type": "text", "text": "hi", "citations": None}]


def test_stream_message_retries_overloaded(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request):
        calls["n"] += 1
        assert request.headers["anthropic-beta"] == "context-management-2025-06-27"
        assert json.loads(request.content)["stream"] is True
        if calls["n"] == 1:
            return httpx.Response(529, json={"type": "error", "error": {"type": "overloaded_error",
                                                                          "message": "Overloaded"}})
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=sse([{"type": "text", "text": "ok"}]))

    async def fast_sleep(_):
        return None
    monkeypatch.setattr("app.api.asyncio.sleep", fast_sleep)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await stream_message(http, api_key="k", base_url="https://api.test",
                                        body={"model": "m"}, betas=["context-management-2025-06-27"])
    msg = asyncio.run(run())
    assert calls["n"] == 2 and msg["content"][0]["text"] == "ok"


def test_stream_message_auth_error_not_retried():
    def handler(request):
        return httpx.Response(401, json={"type": "error", "error": {"type": "authentication_error",
                                                                      "message": "invalid x-api-key"}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            await stream_message(http, api_key="bad", base_url="https://api.test", body={})
    with pytest.raises(APIError) as ei:
        asyncio.run(run())
    assert ei.value.status == 401


# -------------------------------------------------------------- translator
def test_language_is_chosen_never_detected():
    # no auto-detect option; the old "auto" value and unknown names are rejected
    assert "auto" not in LANGUAGES
    s = Settings()
    assert s.language == ""
    assert apply_updates(s, {"language": "auto"}).language == ""
    assert apply_updates(s, {"language": "Klingon"}).language == ""
    assert apply_updates(s, {"language": "日本語"}).language == "日本語"


def test_to_english_uses_the_chosen_language():
    systems = []

    def handler(request):
        body = json.loads(request.content)
        systems.append(body["system"])
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=sse([{"type": "text", "text": "<english>Open the file</english>"}]))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            tr = Translator(http, lambda: Settings(api_key="k"))
            return (await tr.to_english("打开文件", "简体中文"),
                    await tr.to_english("open the file", "English"),
                    await tr.to_english("12 + 3", "简体中文"),
                    # English typed while Spanish is chosen still goes to the translator
                    await tr.to_english("ok", "Español"))

    out = asyncio.run(run())
    assert out[:3] == ("Open the file", "open the file", "12 + 3")
    assert len(systems) == 2      # English and letter-free input never call the translator
    assert "The user writes in Simplified Chinese." in systems[0]
    assert "The user writes in Spanish." in systems[1]
    assert all("<lang>" not in s for s in systems)


def test_streaming_translation_chunks_in_order_and_skips_code():
    calls = []

    class FakeTranslator:
        async def from_english(self, text, target, on_delta=None, context=""):
            calls.append(text)
            out = f"[{target}]" + text.strip().upper()
            for i in range(0, len(out), 4):
                await on_delta(out[i:i + 4])
            return out

    async def run():
        got = []

        async def on_delta(t):
            got.append(t)
        st = StreamingTranslation(FakeTranslator(), "Chinese", on_delta, min_chunk=20)
        text = ("First paragraph is long enough.\n\n"
                "```python\nx = 1\n\ny = 2\n```\n\n"
                "Second paragraph here.\n\nThird.")
        for i in range(0, len(text), 3):
            st.feed(text[i:i + 3])
            await asyncio.sleep(0)
        st.finish()
        res = await st.result()
        return res, "".join(got)

    res, streamed = asyncio.run(run())
    assert res == streamed
    # code fence with an inner blank line was never split and never translated
    assert "```python\nx = 1\n\ny = 2\n```" in res
    assert all("x = 1" not in c for c in calls)
    assert res.index("FIRST PARAGRAPH") < res.index("x = 1") < res.index("SECOND PARAGRAPH")
    assert is_pure_code("```\ncode\n```\n")


def test_window_frame_coordinate_mapping():
    import threading
    from app.computer import Frame, LocalComputer, ComputerError
    c = object.__new__(LocalComputer)
    c.stop_event = threading.Event()
    c.capture_mode = "window"
    c._monitor_rect = lambda: (0, 0, 1920, 1080)
    # focused window at (300, 200), 1000x600 physical, no scaling
    c.frame = Frame(300, 200, 1000, 600, 1.0, "KiCad")
    assert c._to_screen([0, 0]) == (300, 200)
    assert c._to_screen([999, 599]) == (1299, 799)
    assert c._to_shot(350, 260) == (50, 60) and c._to_shot(10, 10) is None
    with pytest.raises(ComputerError):
        c._to_screen([1200, 10])
    # full work area scaled by 0.5: corners are nudged off the fail-safe points
    c.frame = Frame(0, 0, 1920, 1040, 0.5, "")
    assert c._to_screen([0, 0]) == (1, 1)
    assert c._to_screen([960, 520]) == (1919, 1039)   # bottom is the taskbar edge, not a corner
    assert c.windows_tool({"action": "capture", "mode": "screen"}).startswith("Capture mode is now screen")
    assert c.capture_mode == "screen"


def test_desktop_detects_a_dead_previous_instance(monkeypatch, tmp_path):
    import socket
    from app import desktop
    monkeypatch.setattr(desktop.config, "STATE_DIR", tmp_path)
    assert not desktop._server_alive()                     # no running.json
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    (tmp_path / "running.json").write_text(json.dumps({"url": f"http://127.0.0.1:{port}/", "pid": 1}))
    try:
        assert desktop._server_alive()                     # server answering
    finally:
        s.close()
    assert not desktop._server_alive()                     # process left, server gone


def test_desktop_free_port_falls_back_when_busy():
    import socket
    from app.desktop import free_port
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    busy = s.getsockname()[1]
    try:
        p = free_port(busy)
        assert p != busy and p > 0
    finally:
        s.close()


def test_memory_backup_clear_and_restore(tmp_path):
    m = MemoryStore(tmp_path / "memories")
    assert m.backups == tmp_path / "memories-backups"       # outside /memories
    m.execute({"command": "create", "path": "/memories/profile.md", "file_text": "- likes tea\n"})
    m.execute({"command": "create", "path": "/memories/projects/board.md", "file_text": "- 4 layers\n"})
    assert '<file path="/memories/projects/board.md">' in m.dump()
    assert m.latest_backup() is None and m.restore_latest() is None
    info = m.backup("clear")
    assert info["kind"] == "clear" and info["files"] == 2 and m.latest_backup() == info
    m.clear()
    assert m.tree() == [] and m.base.is_dir()
    assert m.restore_latest() == info                       # undo pops the backup
    assert sorted(f["path"] for f in m.tree()) == ["/memories/profile.md", "/memories/projects/board.md"]
    assert m.latest_backup() is None
    for _ in range(12):
        m.backup("tidy")
    assert len(list(m.backups.iterdir())) == 10             # only the newest 10 are kept


def test_window_mode_setting():
    s = Settings()
    assert s.window_mode == "app"
    assert apply_updates(s, {"window_mode": "firefox"}).window_mode == "app"   # not an app-window mode
    assert apply_updates(s, {"window_mode": "chrome"}).window_mode == "chrome"
    assert apply_updates(s, {"window_mode": "browser"}).window_mode == "browser"


def test_app_browser_preference_and_separate_profiles(monkeypatch, tmp_path):
    import winreg
    from app import desktop
    if desktop.sys.platform != "win32":
        pytest.skip("Windows only")

    def no_registry(*a, **k):
        raise OSError
    monkeypatch.setattr(winreg, "OpenKey", no_registry)
    for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
        (tmp_path / rel).parent.mkdir(parents=True)
        (tmp_path / rel).write_text("")
    for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        monkeypatch.setenv(env, str(tmp_path))
    assert desktop.find_app_browser("chrome").endswith("chrome.exe")
    assert desktop.find_app_browser("edge").endswith("msedge.exe")
    (tmp_path / r"Google\Chrome\Application\chrome.exe").unlink()
    assert desktop.find_app_browser("chrome").endswith("msedge.exe")      # falls back to Edge

    launched = []
    monkeypatch.setattr(desktop.config, "STATE_DIR", tmp_path / "data")
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda args: launched.append(args) or "proc")
    for exe in ("C:/x/chrome.exe", "C:/x/msedge.exe"):
        monkeypatch.setattr(desktop, "find_app_browser", lambda prefer, exe=exe: exe)
        assert desktop._open_app_window("http://127.0.0.1:1/", "chrome") == "proc"
    profiles = [next(a for a in args if a.startswith("--user-data-dir=")) for args in launched]
    assert profiles[0].endswith("window-profile-chrome") and profiles[1].endswith("window-profile")


def test_control_overlay_pieces():
    from app.overlay import (DEFAULT_LABEL, control_label, is_physical_escape, render_edge,
                             render_label, render_marker)
    assert control_label("简体中文").startswith("Claude 正在使用你的电脑")
    assert control_label("Deutsch") == DEFAULT_LABEL
    # only a real key press stops the agent; Esc the agent sends itself is injected
    assert is_physical_escape(0x1B, 0) and not is_physical_escape(0x1B, 0x10)
    assert not is_physical_escape(0x41, 0)
    assert render_edge(100, 14, "top").size == (100, 14) and render_edge(80, 14, "left").size == (14, 80)
    top = render_edge(10, 14, "top")
    assert top.getpixel((5, 0))[3] > 200 and top.getpixel((5, 13))[3] < 20    # solid at the edge, fades in
    assert render_label(DEFAULT_LABEL, 2.0).width > render_label(DEFAULT_LABEL, 1.0).width
    img, (hx, hy) = render_marker(1.0)
    assert img.getpixel((hx, hy))[3] > 200                                     # the hotspot dot


@pytest.mark.skipif(sys.platform != "win32", reason="Windows keyboard hook")
def test_control_overlay_hook_swallows_only_physical_escape():
    import ctypes
    from app import overlay as ov
    hits = []
    o = ov.ControlOverlay(on_escape=lambda: hits.append(1))
    injected = ov.KBDLLHOOKSTRUCT(vkCode=0x1B, flags=0x10)
    physical = ov.KBDLLHOOKSTRUCT(vkCode=0x1B, flags=0)
    assert o._on_key(0, 0x100, ctypes.addressof(injected)) != 1 and hits == []
    assert o._on_key(0, 0x100, ctypes.addressof(physical)) == 1 and hits == [1]
    assert o._on_key(0, 0x100, ctypes.addressof(physical)) == 1 and hits == [1]   # key repeat: once
    assert o._on_key(0, 0x101, ctypes.addressof(physical)) == 1                   # its key-up too


def test_data_folder_location(monkeypatch, tmp_path):
    import shutil

    import pytest

    from app import config
    monkeypatch.setattr(config, "_ENV_DIR", None)
    monkeypatch.setattr(config, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(config, "LOCATION_FILE", tmp_path / "state" / "data-location.json")
    monkeypatch.setattr(config, "DATA_DIR", config.DATA_DIR)          # restored after the test
    monkeypatch.setattr(config, "FROZEN", True)
    assert config._initial_data_dir() is None                        # standalone, first start: ask
    monkeypatch.setattr(config, "FROZEN", False)
    assert config._initial_data_dir() == tmp_path / "state"           # app folder: data\ as before
    config.set_data_dir(tmp_path / "mine")
    assert config.DATA_DIR == tmp_path / "mine" and config._initial_data_dir() == tmp_path / "mine"
    shutil.rmtree(tmp_path / "mine")                                  # e.g. an unplugged drive
    assert config._initial_data_dir() is None and config.saved_data_dir() == tmp_path / "mine"

    empty = tmp_path / "empty"
    empty.mkdir()
    assert config.data_target(str(empty)) == empty
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "x.txt").write_text("x")
    assert config.data_target(str(busy)) == busy / "Polyglot Agent"  # don't mix with other files
    (busy / "chats").mkdir()
    assert config.data_target(str(busy)) == busy                      # already holds the app's data
    (tmp_path / "state" / "app.log").write_text("log")
    assert config.data_target(str(tmp_path / "state")) == tmp_path / "state"
    assert config.data_target(str(tmp_path / "new" / "sub")) == tmp_path / "new" / "sub"
    for bad in ("", "relative/path", str(busy / "x.txt")):
        with pytest.raises(ValueError):
            config.data_target(bad)


def test_computer_control_is_off_by_default():
    s = Settings()
    assert s.computer_use is False
    assert apply_updates(s, {"computer_use": True}).computer_use is True    # the user turns it on


def test_display_list_for_settings():
    from app.computer import describe_displays, primary_index

    def mon(left, top, w, h):
        return {"left": left, "top": top, "width": w, "height": h}
    # Windows lists the right-hand screen first here; the main one sits at (0, 0)
    mons = [mon(-1920, -400, 6400, 1840), mon(2560, 0, 1920, 1080), mon(0, 0, 2560, 1440),
            mon(-1920, 0, 1920, 1080), mon(400, -1080, 1920, 1080)]
    assert primary_index(mons) == 2
    assert describe_displays(mons) == [
        {"index": 1, "width": 1920, "height": 1080, "position": "right"},
        {"index": 2, "width": 2560, "height": 1440, "position": "primary"},
        {"index": 3, "width": 1920, "height": 1080, "position": "left"},
        {"index": 4, "width": 1920, "height": 1080, "position": "above"},
    ]
    one = [mon(0, 0, 2560, 1440), mon(0, 0, 2560, 1440)]
    assert describe_displays(one) == [{"index": 1, "width": 2560, "height": 1440, "position": "primary"}]
    assert describe_displays([]) == [] and Settings().monitor == 0     # 0 = the main display


def test_monitor_setting_falls_back_to_the_main_display(monkeypatch):
    from app import computer
    from app.computer import LocalComputer
    mons = [{"left": 0, "top": 0, "width": 4480, "height": 1440},
            {"left": 2560, "top": 0, "width": 1920, "height": 1080},
            {"left": 0, "top": 0, "width": 2560, "height": 1440}]

    class FakeMss:
        monitors = mons
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(computer, "open_mss", FakeMss)
    c = LocalComputer.__new__(LocalComputer)
    for setting, rect in ((0, (0, 0, 2560, 1440)), (7, (0, 0, 2560, 1440)), (1, (2560, 0, 4480, 1080))):
        c.monitor = setting
        assert c._monitor_rect() == rect


# ------------------------------------------------------------ macOS / Linux
def test_folder_dialog_commands_on_mac_and_linux(monkeypatch):
    from app import folder_picker as fp
    monkeypatch.setattr(fp.sys, "platform", "darwin")
    argv = fp.posix_command('Pick "this"', '/Users/me/My "Docs"')
    assert argv == ["osascript", "-e", 'POSIX path of (choose folder with prompt "Pick \\"this\\"" '
                                       'default location (POSIX file "/Users/me/My \\"Docs\\""))']
    monkeypatch.setattr(fp.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    with pytest.raises(fp.PickerUnavailable):           # no graphical session: the UI asks for a path
        fp.posix_command("t", None)
    monkeypatch.setenv("DISPLAY", ":0")
    tools = {"zenity", "kdialog"}
    monkeypatch.setattr(fp.shutil, "which", lambda n: f"/usr/bin/{n}" if n in tools else None)
    assert fp.posix_command("t", "/home/me") == ["zenity", "--file-selection", "--directory", "--title=t",
                                                 "--filename=/home/me/"]
    tools.discard("zenity")
    assert fp.posix_command("t", "/home/me") == ["kdialog", "--getexistingdirectory", "/home/me", "--title", "t"]
    tools.clear()
    assert fp.posix_command("t", None) is None          # Tk is next


def test_mac_and_linux_get_a_shell_and_no_computer_control(monkeypatch):
    from app import config, local_tools
    from app.prompts import agent_system_prompt
    monkeypatch.setattr(local_tools.sys, "platform", "linux")
    assert local_tools.shell_tool()["name"] == "shell"
    assert local_tools._shell_argv("echo hi")[1:] == ["-c", "echo hi"]
    p = agent_system_prompt(app_title="X", system="Mac", shell_name="shell", computer_on=False, shell_on=True,
                            editor_on=True, web_on=False, memory_on=False, memory_snapshot="")
    assert "the user's Mac." in p and "`shell`" in p and "powershell" not in p.lower()
    monkeypatch.setattr(local_tools.sys, "platform", "win32")
    assert local_tools.shell_tool()["name"] == "powershell"
    w = agent_system_prompt(app_title="X", computer_on=False, shell_on=True, editor_on=False, web_on=False,
                            memory_on=False, memory_snapshot="")
    assert "the user's Windows PC." in w and "`powershell`" in w                # Windows text unchanged

    import app.agent as agent_mod
    monkeypatch.setattr(config, "COMPUTER_SUPPORTED", False)
    sess = agent_mod.Session(lambda: s, lambda ev: asyncio.sleep(0))
    s = Settings(language="English", computer_use=True, web_search=False, memory_enabled=False)
    sess._ensure_conversation_setup(s)
    names = [t.get("name") or t.get("type") for t in sess.chat["tools"]]
    assert "computer_toolset_20260801" not in names and "windows" not in names
    asyncio.run(sess.http.aclose())


def test_python_pack(tmp_path):
    import importlib.util
    import zipfile
    spec = importlib.util.spec_from_file_location("build_pack", "tools/build_pack.py")
    bp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bp)
    out = bp.build(tmp_path)
    ver = bp.version()
    assert out.name == f"PolyglotAgent-{ver}-python.zip"
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        top = f"PolyglotAgent-{ver}/"
        assert all(n.startswith(top) for n in names)
        for need in ("start.py", "start.sh", "start.command", "start.bat", "requirements.txt",
                     "tools/bootstrap.py", "app/server.py", "app/static/index.html", "app/folder_picker.py"):
            assert top + need in names, need
        assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)
        for script in ("start.py", "start.sh", "start.command"):
            info = z.getinfo(top + script)
            assert (info.external_attr >> 16) & 0o777 == 0o755             # stays executable when unzipped
            data = z.read(info)
            assert data.startswith(b"#!") and b"\r\n" not in data          # a CRLF shebang won't run
        req = z.read(top + "requirements.txt").decode()
        assert 'pywebview>=6.0; sys_platform == "win32"' in req           # Windows-only parts skipped elsewhere
