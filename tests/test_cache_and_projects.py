"""Prompt-cache stability, folder permissions, projects and token accounting."""
import asyncio
import json
import re

import httpx

import app.agent as agent_mod
from app.config import Settings
from app.prompts import MEMORY_PASS_SYSTEM
from app.store import Store
from conftest import sse
from test_agent import FakeComputer, _stream, _system_text, make_session

NON_LATIN = re.compile(r"[぀-ヿ㐀-鿿가-힯]")


def _dump(x):
    return json.dumps(x, ensure_ascii=False, sort_keys=False)


def test_translation_never_touches_cached_prefix(monkeypatch, tmp_path):
    """Across turns, a language switch and an app restart + chat resume, every
    main-model request must be the previous one plus appended messages."""
    monkeypatch.setattr(agent_mod, "LocalComputer", FakeComputer)
    store = Store(tmp_path)
    main, translator_bodies = [], []
    THINK = {"type": "thinking", "thinking": "look first", "signature": "c2ln"}

    def handler(request):
        body = json.loads(request.content)
        system = _system_text(body)
        if system.startswith("You are a translation layer between"):
            translator_bodies.append(body)
            n = len(translator_bodies)
            return _stream([{"type": "text", "text": f"<english>Request number {n}.</english>"}])
        if system.startswith("You are a translation layer. Translate"):
            translator_bodies.append(body)
            return _stream([{"type": "text", "text": "翻訳されたテキスト"}])
        if system == MEMORY_PASS_SYSTEM:
            return _stream([{"type": "text", "text": "DONE: no changes"}])
        main.append(body)
        if len(main) == 1:
            return _stream([THINK, {"type": "text", "text": "Checking the screen."},
                             {"type": "tool_use", "id": "toolu_1", "name": "screenshot",
                              "toolset_name": "computer", "input": {}}], "tool_use")
        return _stream([THINK, {"type": "text", "text": f"Answer {len(main)}."}])

    settings = Settings(api_key="k", language="简体中文", shell_mode="off", memory_pass=True)
    settings._auto_answer = True
    events = []

    async def run():
        sess = make_session(settings, handler, events)
        sess.store = store
        await sess.submit("截个图", [])
        await sess.turn_task
        if sess.memory_task:
            await sess.memory_task
        settings.language = "日本語"           # switch language mid-chat
        settings.show_thinking = False          # a settings change must not alter this chat's prefix
        await sess.submit("もう一度確認して", [])
        await sess.turn_task
        chat_id = sess.chat["id"]
        await sess.close()
        # restart the app and resume the chat
        settings.language = "English"
        sess2 = make_session(settings, handler, events)
        sess2.store = store
        await sess2.open_chat(chat_id)
        await sess2.submit("and one more thing please", [])
        await sess2.turn_task
        await sess2.close()

    asyncio.run(run())
    assert len(main) == 4
    for a, b in zip(main, main[1:]):
        assert _dump(a["system"]) == _dump(b["system"])
        assert _dump(a["tools"]) == _dump(b["tools"])
        assert a["thinking"] == b["thinking"]
        n = len(a["messages"])
        assert _dump(b["messages"][:n]) == _dump(a["messages"]), "history was modified"
        assert len(b["messages"]) > n
    for b in main:
        assert not NON_LATIN.search(_dump(b["messages"])) and not NON_LATIN.search(_dump(b["system"]))
        assert b["cache_control"] == {"type": "ephemeral"}
    # the translator only ever sees the one message it translates, never the history
    assert len(translator_bodies) == 5   # 2 in + 3 out; the English turn needs none
    assert all(len(tb["messages"]) == 1 for tb in translator_bodies)


def test_folder_read_permission_is_asked_once(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_mod, "LocalComputer", FakeComputer)
    work = tmp_path / "work"
    (work / "src").mkdir(parents=True)
    (work / "src" / "a.py").write_text("print('a')\n")
    (work / "src" / "b.py").write_text("print('b')\n")
    other = tmp_path / "other.txt"
    other.write_text("secret\n")
    store = Store(tmp_path / "data")
    proj = store.save_project({"name": "Demo board", "instructions": "Use metric units."})
    bodies, asked = [], []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        k = len(bodies)
        if k == 1:
            return _stream([{"type": "tool_use", "id": "v1", "name": "str_replace_based_edit_tool",
                             "input": {"command": "view", "path": str(work / "src" / "a.py")}}], "tool_use")
        if k == 2:
            return _stream([{"type": "tool_use", "id": "v2", "name": "str_replace_based_edit_tool",
                             "input": {"command": "view", "path": str(work / "src" / "b.py")}},
                            {"type": "tool_use", "id": "v3", "name": "str_replace_based_edit_tool",
                             "input": {"command": "view", "path": str(other)}}], "tool_use")
        return _stream([{"type": "text", "text": "done"}])

    def answer(ev):
        asked.append(ev)
        if ev["kind"] == "folder" and ev["detail"].endswith("a.py"):
            return {"approve": True, "scope": "folder", "folder": str(work)}  # user widened it
        return {"approve": False}

    settings = Settings(api_key="k", language="English", memory_pass=False, computer_use=False,
                        shell_mode="off", web_search=False, memory_enabled=False)
    settings._auto_answer = answer

    async def run():
        sess = make_session(settings, handler, [])
        sess.store = store
        await sess.new_chat(proj["id"])
        await sess.submit("read my code", [])
        await sess.turn_task
        await sess.new_chat(proj["id"])   # a later chat in the same project
        await sess.close()
        return sess

    asyncio.run(run())
    assert [a["detail"] for a in asked] == [str(work / "src" / "a.py"), str(other)]
    r1 = bodies[1]["messages"][-1]["content"][0]
    assert "print('a')" in r1["content"] and "granted read access" in r1["content"]
    r2, r3 = bodies[2]["messages"][-1]["content"]
    assert "print('b')" in r2["content"] and "is_error" not in r2          # no second prompt
    assert r3["is_error"] is True                                           # outside: denied
    assert store.get_project(proj["id"])["folders"] == [str(work)]
    sysmsg = _system_text(bodies[0])
    assert '<project name="Demo board">' in sysmsg and "Use metric units." in sysmsg


def test_chats_projects_and_usage(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_mod, "LocalComputer", FakeComputer)
    store = Store(tmp_path)

    def handler(request):
        body = json.loads(request.content)
        if _system_text(body).startswith("You are a translation layer"):
            return _stream([{"type": "text", "text": "<english>Hi</english>"}]) \
                if "between" in _system_text(body) else _stream([{"type": "text", "text": "你好"}])
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse(
            [{"type": "text", "text": "Hello."}]))

    events = []
    settings = Settings(api_key="k", language="简体中文", memory_pass=False, computer_use=False)
    settings._auto_answer = True

    async def run():
        sess = make_session(settings, handler, events)
        sess.store = store
        p = store.save_project({"name": "Thesis"})
        await sess.new_chat(p["id"])
        await sess.submit("你好", [])
        await sess.turn_task
        cid = sess.chat["id"]
        await sess.new_chat(None)                  # random chat, never used -> not saved
        await sess.close()
        return p, cid

    p, cid = asyncio.run(run())
    chats = store.list_chats()
    assert [c["id"] for c in chats] == [cid] and chats[0]["project_id"] == p["id"]
    assert chats[0]["title"] == "你好"
    tu = next(e for e in events if e["type"] == "turn_usage")
    assert set(tu["usage"]) == {"agent", "translator"}
    assert tu["usage"]["agent"]["input_tokens"] == 100 and tu["usage"]["agent"]["output_tokens"] == 50
    assert tu["usage"]["translator"]["calls"] == 2
    assert tu["context"]["window"] == 1_000_000 and tu["context"]["used"] == 150
    store.delete_project(p["id"])
    assert store.list_chats()[0]["project_id"] is None   # chats survive as random chats


def _capture_flow(monkeypatch, answer, start_mode="window"):
    monkeypatch.setattr(agent_mod, "LocalComputer", FakeComputer)
    bodies, events = [], []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            return _stream([{"type": "tool_use", "id": "w1", "name": "windows",
                             "input": {"action": "capture", "mode": "screen"}},
                            {"type": "tool_use", "id": "s1", "name": "screenshot",
                             "toolset_name": "computer", "input": {}}], "tool_use")
        return _stream([{"type": "text", "text": "ok"}])

    settings = Settings(api_key="k", language="English", memory_pass=False, capture_mode=start_mode)
    settings._auto_answer = answer

    async def run():
        sess = make_session(settings, handler, events)
        await sess.submit("drag the file into the other window", [])
        await sess.turn_task
        mode_seen = sess.computer.capture_mode
        await sess.close()
        return mode_seen

    mode_seen = asyncio.run(run())
    return settings, bodies, events, mode_seen


def test_agent_needs_permission_for_full_screen(monkeypatch):
    settings, bodies, events, mode_seen = _capture_flow(monkeypatch, {"approve": False})
    results = bodies[1]["messages"][-1]["content"]
    assert results[0]["is_error"] is True and "only the active app" in results[0]["content"]
    assert settings.capture_mode == "window" and mode_seen == "window"   # screenshot stayed app-only
    assert any(e["type"] == "approval_request" and e["kind"] == "screen" for e in events)


def test_full_screen_after_user_allows_and_switch_is_honoured(monkeypatch):
    settings, bodies, events, mode_seen = _capture_flow(monkeypatch, {"approve": True})
    assert settings.capture_mode == "screen" and mode_seen == "screen"
    assert any(e["type"] == "settings" and e["settings"]["capture_mode"] == "screen" for e in events)
    # already in full-screen mode: no prompt at all
    settings2, _, events2, mode2 = _capture_flow(monkeypatch, {"approve": False}, start_mode="screen")
    assert not any(e["type"] == "approval_request" for e in events2) and mode2 == "screen"
