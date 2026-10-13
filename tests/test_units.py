import asyncio
import json

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
