import json

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import server
from conftest import sse


def test_token_and_origin_checks():
    with TestClient(server.app, base_url="http://127.0.0.1:8765") as c:
        page = c.get("/").text
        assert server.TOKEN in page and "__TOKEN__" not in page
        assert c.get("/api/settings").status_code == 403
        r = c.get("/api/settings", headers={"X-Token": server.TOKEN})
        assert r.status_code == 200 and r.json()["settings"]["api_key"] == ""

        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("ws://127.0.0.1:8765/ws?token=wrong") as ws:
                ws.receive_text()
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect(f"ws://127.0.0.1:8765/ws?token={server.TOKEN}",
                                     headers={"origin": "https://evil.example"}) as ws:
                ws.receive_text()
        with c.websocket_connect(f"ws://127.0.0.1:8765/ws?token={server.TOKEN}",
                                 headers={"origin": "http://127.0.0.1:8765"}) as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello" and "languages" in hello["meta"]


def test_settings_and_memory_api():
    h = {"X-Token": server.TOKEN}
    with TestClient(server.app, base_url="http://127.0.0.1:8765") as c:
        r = c.post("/api/settings", headers=h, json={"language": "日本語", "effort": "bogus", "max_steps": 5})
        s = r.json()["settings"]
        assert s["language"] == "日本語" and s["effort"] == "high" and s["max_steps"] == 5
        assert c.put("/api/memory/file", headers=h,
                     json={"path": "/memories/notes.md", "content": "- likes tea"}).json() == {"ok": True}
        assert c.get("/api/memory/file", headers=h, params={"path": "/memories/notes.md"}).json()["content"] == "- likes tea"
        assert c.get("/api/memory/file", headers=h, params={"path": "/memories/../x"}).status_code == 400
        files = [f["path"] for f in c.get("/api/memory", headers=h).json()["files"]]
        assert "/memories/notes.md" in files
        assert c.delete("/api/memory/file", headers=h, params={"path": "/memories/notes.md"}).json() == {"ok": True}


def test_dns_rebinding_host_rejected():
    with TestClient(server.app, base_url="http://rebind.attacker.example:8765") as c:
        r = c.get("/")
        assert r.status_code == 400 and server.TOKEN not in r.text
        r = c.post("/api/settings", headers={"X-Token": server.TOKEN}, json={"shell_mode": "auto"})
        assert r.status_code == 400
    with TestClient(server.app, base_url="http://127.0.0.1:8765") as c:
        r = c.post("/api/settings", headers={"X-Token": server.TOKEN, "Origin": "http://evil.example"},
                   json={"shell_mode": "auto"})
        assert r.status_code == 403
        csp = c.get("/").headers["content-security-policy"]
        assert "img-src 'self' data:" in csp and "'sha256-" in csp


def test_websocket_rejects_foreign_host():
    with TestClient(server.app, base_url="http://127.0.0.1:8765") as c:
        with pytest.raises(Exception):
            with c.websocket_connect(f"ws://rebind.attacker.example:8765/ws?token={server.TOKEN}") as ws:
                ws.receive_text()


def test_memory_translation_is_read_only_and_cached():
    h = {"X-Token": server.TOKEN}
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=sse([{"type": "text", "text": "- 喜欢喝茶"}], model="claude-haiku-5-5"))

    with TestClient(server.app, base_url="http://127.0.0.1:8765") as c:
        server.state.session.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        c.put("/api/memory/file", headers=h, json={"path": "/memories/likes.md", "content": "- likes tea"})
        server.state.settings.language = ""
        assert c.post("/api/memory/translate", headers=h, json={"content": "- likes tea"}).status_code == 400
        c.post("/api/settings", headers=h, json={"language": "English"})
        r = c.post("/api/memory/translate", headers=h, json={"content": "- likes tea"})
        assert r.json()["text"] == "- likes tea" and calls == []          # English: no call
        c.post("/api/settings", headers=h, json={"language": "简体中文", "api_key": "sk-test"})
        for _ in range(2):
            r = c.post("/api/memory/translate", headers=h, json={"content": "- likes tea"})
            assert r.status_code == 200 and r.json() == {"text": "- 喜欢喝茶", "language": "简体中文"}
        assert len(calls) == 1                                             # second click: cached
        assert "into Simplified Chinese" in calls[0]["system"]
        # the memory file itself stays English
        got = c.get("/api/memory/file", headers=h, params={"path": "/memories/likes.md"}).json()
        assert got["content"] == "- likes tea"
        assert c.post("/api/memory/translate", json={"content": "x"}).status_code == 403
