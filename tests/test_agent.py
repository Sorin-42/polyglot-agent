import asyncio
import json
import re

import httpx
from PIL import Image

import app.agent as agent_mod
from app.computer import LocalComputer
from app.config import Settings
from app.prompts import MEMORY_PASS_SYSTEM
from conftest import sse

CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")


class FakeComputer:
    instances = []

    def __init__(self, monitor=1, max_edge=1568, stop_event=None, **_):
        self.calls = []
        self.stop_event = stop_event
        FakeComputer.instances.append(self)

    def describe(self):
        return "monitor 1: 1920x1080 physical pixels; screenshots are 1568x882"

    def execute(self, name, inp):
        self.calls.append((name, inp))
        if name == "screenshot":
            return LocalComputer._image_result(Image.new("RGB", (1568, 882), "white"))
        return {"content": [{"type": "text", "text": "OK"}], "image": None}


def _system_text(body):
    s = body.get("system", "")
    return s if isinstance(s, str) else "".join(b.get("text", "") for b in s)


def _stream(blocks, stop="end_turn"):
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse(blocks, stop))


def make_session(settings, handler, events):
    async def emit(ev):
        events.append(ev)
        if ev["type"] == "approval_request":
            ans = settings._auto_answer
            ans = ans(ev) if callable(ans) else ans
            asyncio.get_running_loop().call_soon(sess.resolve_approval, ev["id"], ans)

    sess = agent_mod.Session(lambda: settings, emit)
    sess.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sess.translator.http = sess.http
    return sess


def test_full_turn_translates_and_keeps_agent_english(monkeypatch):
    monkeypatch.setattr(agent_mod, "LocalComputer", FakeComputer)
    main_bodies, main_headers, kinds = [], [], []
    THINK = {"type": "thinking", "thinking": "I should look at the screen first.", "signature": "c2lnbmF0dXJl"}

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        system = _system_text(body)
        if system.startswith("You are a translation layer between"):
            kinds.append("to_en")
            assert body["model"] == "claude-haiku-5-5"
            assert body["thinking"] == {"type": "disabled"}
            assert "The user writes in Simplified Chinese." in system   # chosen, not detected
            return _stream([{"type": "text", "text": "<english>Take a "
                                                     "screenshot and tell me what is on the screen.</english>"}])
        if system.startswith("You are a translation layer. Translate"):
            kinds.append("from_en")
            assert "Simplified Chinese" in system
            return _stream([{"type": "text", "text": "我截了图：屏幕上是一个白色的空桌面。"}])
        if system == MEMORY_PASS_SYSTEM:
            kinds.append("memory")
            if body["messages"][-1]["role"] == "user" and isinstance(body["messages"][-1]["content"], list):
                return _stream([{"type": "text", "text": "DONE: noted the user's screen setup"}])
            return _stream([{"type": "tool_use", "id": "toolu_m1", "name": "memory", "input": {
                "command": "create", "path": "/memories/environment.md",
                "file_text": "- Primary screen 1920x1080\n"}}], "tool_use")
        # ---- main agent
        kinds.append("main")
        main_bodies.append(body)
        main_headers.append(request.headers.get("anthropic-beta", ""))
        if len(main_bodies) == 1:
            return _stream([THINK, {"type": "text", "text": "I will take a screenshot."},
                            {"type": "tool_use", "id": "toolu_s1", "name": "screenshot",
                             "toolset_name": "computer", "input": {}}], "tool_use")
        return _stream([{"type": "text", "text": "The screen shows an empty white desktop.\n\n"
                                                 "Nothing else is open."}])

    events = []
    settings = Settings(api_key="sk-test", language="简体中文", shell_mode="off", file_tools_mode="off")
    settings._auto_answer = True

    async def run():
        sess = make_session(settings, handler, events)
        await sess.submit("截个图，告诉我屏幕上有什么", [])
        await sess.turn_task
        if sess.memory_task:
            await sess.memory_task
        mem = (sess.memory.base / "environment.md").read_text()
        await sess.close()
        return mem

    mem = asyncio.run(run())

    # request shape for the main model
    b1, b2 = main_bodies
    assert b1["model"] == "claude-opus-5-5"
    assert b1["output_config"] == {"effort": "high"}
    assert b1["thinking"] == {"type": "adaptive", "display": "summarized",
                              "block_binding": {"prefix_mismatch_behavior": "drop_block"}}
    types = [t.get("type") for t in b1["tools"]]
    assert "computer_toolset_20260801" in types and "memory_20250818" in types and "web_search_20250305" in types
    assert not any("display_width_px" in t for t in b1["tools"])
    assert b1["context_management"]["edits"][0]["type"] == "clear_tool_uses_20250919"
    assert "context-management-2025-06-27" in main_headers[0]
    assert "thinking-binding-controls-2026-08-01" in main_headers[0]
    # second call: thinking block passed back untouched, tool_result carries toolset_name + image
    assert b2["messages"][1]["content"][0] == THINK
    tr = b2["messages"][2]["content"][0]
    assert tr["type"] == "tool_result" and tr["tool_use_id"] == "toolu_s1" and tr["toolset_name"] == "computer"
    assert tr["content"][0]["type"] == "image" and tr["content"][0]["source"]["media_type"] == "image/png"
    # the agent never sees the user's language
    for b in main_bodies:
        assert not CJK.search(json.dumps(b["messages"], ensure_ascii=False))
        assert not CJK.search(_system_text(b))
        # ...and is never told that a translation layer or another language exists
        told = (_system_text(b) + json.dumps(b["tools"])).lower()
        assert "translat" not in told and "english" not in told and "<language>" not in told
    assert b1["messages"][0]["content"][-1]["text"] == "Take a screenshot and tell me what is on the screen."

    # UI got the original, the English, the translation, the screenshot and the memory update
    um = next(e for e in events if e["type"] == "user_message")
    assert um["original"].startswith("截个图") and um["english"].startswith("Take a screenshot")
    finals = [e["text"] for e in events if e["type"] == "translation_final"]
    assert any("白色的空桌面" in f for f in finals)
    assert any(e["type"] == "action_result" and e["name"] == "screenshot" and e["thumb"] for e in events)
    assert any(e["type"] == "memory_op" and e["source"] == "pass" for e in events)
    assert any(e["type"] == "memory_pass" and e.get("summary", "").startswith("noted") for e in events)
    assert "1920x1080" in mem
    assert kinds[0] == "to_en" and kinds.count("main") == 2 and "from_en" in kinds
    assert FakeComputer.instances[-1].calls == [("screenshot", {})]


def test_shell_denied_and_english_needs_no_translation(monkeypatch):
    monkeypatch.setattr(agent_mod, "LocalComputer", FakeComputer)
    main_bodies, translator_calls = [], []

    def handler(request):
        body = json.loads(request.content)
        system = _system_text(body)
        if system.startswith("You are a translation layer"):
            translator_calls.append(body)
            return _stream([{"type": "text", "text": "x"}])
        main_bodies.append(body)
        if len(main_bodies) == 1:
            return _stream([{"type": "tool_use", "id": "toolu_p1", "name": "powershell",
                             "input": {"command": "Remove-Item C:\\temp\\x -Recurse"}}], "tool_use")
        return _stream([{"type": "text", "text": "Okay, I did not delete anything."}])

    events = []
    settings = Settings(api_key="sk-test", language="English", shell_mode="ask", file_tools_mode="off",
                        memory_pass=False, computer_use=False, web_search=False)
    settings._auto_answer = False

    async def run():
        sess = make_session(settings, handler, events)
        await sess.submit("Please delete the temp folder", [])
        await sess.turn_task
        await sess.close()

    asyncio.run(run())
    assert translator_calls == []
    tr = main_bodies[1]["messages"][2]["content"][0]
    assert tr["tool_use_id"] == "toolu_p1" and tr["is_error"] is True and "denied" in tr["content"]
    assert "toolset_name" not in tr
    assert [t["name"] for t in main_bodies[0]["tools"]] == ["powershell", "memory"]
    ap = next(e for e in events if e["type"] == "approval_request")
    assert ap["kind"] == "shell" and "Remove-Item" in ap["detail"]
    assert any(e["type"] == "assistant_final" and "did not delete" in e["english"] for e in events)


def test_no_request_until_language_is_chosen(monkeypatch):
    monkeypatch.setattr(agent_mod, "LocalComputer", FakeComputer)
    calls, events = [], []

    def handler(request):
        calls.append(request)
        return _stream([{"type": "text", "text": "x"}])

    settings = Settings(api_key="sk-test")          # language not chosen yet
    settings._auto_answer = True

    async def run():
        sess = make_session(settings, handler, events)
        await sess.submit("你好", [])
        busy = sess.busy
        await sess.close()
        return busy, sess.messages

    busy, messages = asyncio.run(run())
    assert not busy and messages == [] and calls == []
    assert any(e["type"] == "notice" and "language" in e["text"] for e in events)


def test_api_error_rolls_back_user_message(monkeypatch):
    monkeypatch.setattr(agent_mod, "LocalComputer", FakeComputer)

    def handler(request):
        return httpx.Response(400, json={"type": "error", "error": {"type": "invalid_request_error",
                                                                      "message": "bad request"}})

    events = []
    settings = Settings(api_key="sk-test", language="English", memory_pass=False)
    settings._auto_answer = True

    async def run():
        sess = make_session(settings, handler, events)
        await sess.submit("hello there", [])
        await sess.turn_task
        n = len(sess.messages)
        await sess.close()
        return n

    assert asyncio.run(run()) == 0
    assert any(e["type"] == "notice" and "bad request" in e["text"] for e in events)


def test_stop_mid_stream_keeps_transcript_valid(monkeypatch):
    monkeypatch.setattr(agent_mod, "LocalComputer", FakeComputer)
    started = asyncio.Event()
    calls = []

    async def slow_body():
        raw = sse([{"type": "text", "text": "This is a long answer " * 50}], chunk=5)
        for i, part in enumerate(raw.split(b"\n\n")):
            if i == 4:
                started.set()
            yield part + b"\n\n"
            await asyncio.sleep(0.01)

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=slow_body())

    events = []
    settings = Settings(api_key="sk-test", language="English", memory_pass=False)
    settings._auto_answer = True

    async def run():
        sess = make_session(settings, handler, events)
        await sess.submit("write a long answer", [])
        await asyncio.wait_for(started.wait(), 5)
        await sess.stop()
        await asyncio.wait_for(sess.turn_task, 5)
        roles = [m["role"] for m in sess.messages]
        # next message merges into the dangling user turn instead of breaking alternation
        sess.stop_event.clear()
        sess._add_user_content([{"type": "text", "text": "second"}])
        merged = sess.messages[-1]["content"]
        await sess.close()
        return roles, merged

    roles, merged = asyncio.run(run())
    assert roles == ["user"]
    assert [b["text"] for b in merged][-2:] == ["(The previous request was interrupted by the user.)", "second"]
    assert any(e["type"] == "status" and e["state"] == "idle" for e in events)


def test_crashing_tool_still_answers_every_tool_use(monkeypatch):
    """A tool that raises must not leave a tool_use without a tool_result."""
    monkeypatch.setattr(agent_mod, "LocalComputer", FakeComputer)
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            return _stream([{"type": "tool_use", "id": "toolu_e1", "name": "str_replace_based_edit_tool",
                             "input": {"command": "view", "path": "/tmp", "view_range": [5]}},
                            {"type": "tool_use", "id": "toolu_e2", "name": "memory",
                             "input": {"command": "view", "path": "/memories"}}], "tool_use")
        return _stream([{"type": "text", "text": "done"}])

    events = []
    settings = Settings(api_key="sk-test", language="English", memory_pass=False, file_tools_mode="auto",
                        shell_mode="off", computer_use=False, web_search=False)
    settings._auto_answer = True

    def boom(inp):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(agent_mod, "run_editor", boom)

    async def run():
        sess = make_session(settings, handler, events)
        await sess.submit("look around", [])
        await sess.turn_task
        await sess.close()

    asyncio.run(run())
    results = bodies[1]["messages"][2]["content"]
    assert [r["tool_use_id"] for r in results] == ["toolu_e1", "toolu_e2"]
    assert results[0]["is_error"] is True and "kaboom" in results[0]["content"]
    assert "is_error" not in results[1]


def test_repair_and_prune_helpers(monkeypatch):
    async def emit(ev):
        pass
    sess = agent_mod.Session(lambda: Settings(), emit)
    sess.chat["messages"] = [{"role": "user", "content": [{"type": "text", "text": "hi"}]},
                     {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "screenshot",
                                                        "toolset_name": "computer", "input": {}}]}]
    sess._add_user_content([{"type": "text", "text": "again"}])
    last = sess.messages[-1]["content"]
    assert last[0]["tool_use_id"] == "t1" and last[0]["toolset_name"] == "computer" and last[0]["is_error"]
    assert last[-1]["text"] == "again"
    # prune: 45 screenshots -> keep the last PRUNE_KEEP
    img = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "x" * 10}}
    sess.chat["messages"] = []
    for i in range(45):
        sess.messages.append({"role": "assistant", "content": [{"type": "tool_use", "id": f"s{i}",
                                                                 "name": "screenshot", "input": {}}]})
        sess.messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"s{i}",
                                                            "toolset_name": "computer", "content": [dict(img)]}]})
    assert sess._prune_images() == 45 - agent_mod.PRUNE_KEEP
    kinds = [m["content"][0]["content"][0]["type"] for m in sess.messages if m["role"] == "user"]
    assert kinds.count("image") == agent_mod.PRUNE_KEEP and kinds[-1] == "image"
    assert sess._prune_images() == 0
    asyncio.run(sess.http.aclose())


def _click_then_done(main_bodies):
    def handler(request):
        body = json.loads(request.content)
        main_bodies.append(body)
        if len(main_bodies) == 1:
            return _stream([{"type": "tool_use", "id": "toolu_c1", "name": "left_click",
                             "toolset_name": "computer", "input": {"coordinate": [10, 10]}}], "tool_use")
        return _stream([{"type": "text", "text": "Clicked."}])
    return handler


def test_control_signal_shows_while_acting(monkeypatch, fake_overlay):
    monkeypatch.setattr(agent_mod, "LocalComputer", FakeComputer)
    main_bodies, events = [], []
    settings = Settings(api_key="sk-test", language="English", memory_pass=False, shell_mode="off",
                        file_tools_mode="off", web_search=False)
    settings._auto_answer = True

    async def run():
        sess = make_session(settings, _click_then_done(main_bodies), events)
        await sess.submit("Click the corner", [])
        await sess.turn_task
        await sess.close()

    asyncio.run(run())
    from app.overlay import DEFAULT_LABEL
    assert fake_overlay[0].calls == [("show", DEFAULT_LABEL, True), ("pointer", True), ("hide",)]
    # the agent is never told about the signal
    told = _system_text(main_bodies[0]).lower()
    assert all(w not in told for w in ("esc to stop", "border", "overlay", "pointer marker"))


def test_escape_stops_the_agent(monkeypatch, fake_overlay):
    import threading as th
    from app.computer import UserAbort
    clicking = th.Event()

    class SlowComputer(FakeComputer):
        def execute(self, name, inp):
            self.calls.append((name, inp))
            clicking.set()
            if self.stop_event.wait(5):        # like LocalComputer: abort when stopped
                raise UserAbort("Stopped by user")
            return {"content": [{"type": "text", "text": "OK"}], "image": None}

    monkeypatch.setattr(agent_mod, "LocalComputer", SlowComputer)
    main_bodies, events = [], []
    settings = Settings(api_key="sk-test", language="English", memory_pass=False, shell_mode="off",
                        file_tools_mode="off", web_search=False, control_indicator=False)
    settings._auto_answer = True

    async def run():
        sess = make_session(settings, _click_then_done(main_bodies), events)
        await sess.submit("Click the corner", [])
        await asyncio.to_thread(clicking.wait, 5)
        # the overlay's keyboard hook calls this from its own thread on a physical Esc
        th.Thread(target=fake_overlay[0].on_escape).start()
        await asyncio.wait_for(sess.turn_task, 5)
        await asyncio.sleep(0.05)
        await sess.close()

    asyncio.run(run())
    assert len(main_bodies) == 1                                   # nothing after the Esc
    assert any(e["type"] == "notice" and "Esc" in e["text"] for e in events)
    calls = fake_overlay[0].calls
    assert calls[0][0] == "show" and calls[0][2] is False          # Esc works with the border off
    assert calls[-1] == ("hide",)
