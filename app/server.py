"""FastAPI app: serves the UI, a WebSocket for chat events, and small REST APIs."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import config
from .agent import MemoryBusy, Session
from .api import APIError
from .memory_store import MemoryError_

STATIC = Path(__file__).resolve().parent / "static"
TOKEN = secrets.token_urlsafe(24)   # per-launch; only the served page knows it


class Hub:
    def __init__(self) -> None:
        self.sockets: set[WebSocket] = set()

    async def broadcast(self, ev: dict) -> None:
        dead = []
        data = json.dumps(ev, ensure_ascii=False)
        for ws in list(self.sockets):
            try:
                await ws.send_text(data)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.sockets.discard(ws)


class State:
    settings: config.Settings
    session: Optional[Session] = None
    port: int = 8765
    shutdown = None          # set by the desktop launcher: callable that stops the app


hub = Hub()
state = State()
state.settings = config.load_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.session = Session(lambda: state.settings, hub.broadcast)
    yield
    await state.session.close()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
# Reject any Host but localhost: blocks DNS-rebinding pages from reading the
# token out of "/" and then calling the API as if they were same-origin.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def configure(port: int) -> None:
    state.port = port


def _check(request: Request) -> None:
    if not secrets.compare_digest(request.headers.get("x-token", ""), TOKEN) or \
            not _origin_ok(request.headers.get("origin")):
        raise HTTPException(403, "bad token")


def _origin_ok(origin: Optional[str]) -> bool:
    if not origin:
        return True  # non-browser client; the token still has to match
    return origin in (f"http://127.0.0.1:{state.port}", f"http://localhost:{state.port}")


def _meta() -> dict:
    return {"models": config.MODELS, "efforts": config.EFFORTS, "languages": config.LANGUAGES,
            "approval_modes": config.APPROVAL_MODES, "capture_modes": config.CAPTURE_MODES,
            "app_name": config.APP_NAME, "data_dir": str(config.DATA_DIR),
            "desktop": state.shutdown is not None}


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    html = html.replace("__TOKEN__", TOKEN).replace("__APP_NAME__", config.APP_NAME)
    inline = f'window.PA_TOKEN = "{TOKEN}";'
    digest = base64.b64encode(hashlib.sha256(inline.encode()).digest()).decode()
    port = state.port
    csp = ("default-src 'self'; "
           f"script-src 'self' 'sha256-{digest}'; "
           "style-src 'self'; img-src 'self' data:; "
           f"connect-src 'self' ws://127.0.0.1:{port} ws://localhost:{port}; "
           "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
    return HTMLResponse(html, headers={"Cache-Control": "no-store", "Content-Security-Policy": csp,
                                       "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"})


# --------------------------------------------------------------- settings
@app.get("/api/settings")
async def get_settings(request: Request):
    _check(request)
    return {"settings": state.settings.public_dict(), "meta": _meta()}


@app.post("/api/settings")
async def post_settings(request: Request):
    _check(request)
    updates = await request.json()
    config.apply_updates(state.settings, updates)
    config.save_settings(state.settings)
    pub = state.settings.public_dict()
    await hub.broadcast({"type": "settings", "settings": pub})
    return {"settings": pub}


# ----------------------------------------------------------------- memory
@app.get("/api/memory")
async def memory_tree(request: Request):
    _check(request)
    mem = state.session.memory
    return {"files": mem.tree(), "dir": str(mem.base), "undo": mem.latest_backup()}


@app.get("/api/memory/file")
async def memory_read(request: Request, path: str):
    _check(request)
    try:
        p = state.session.memory.resolve(path)
    except MemoryError_ as e:
        raise HTTPException(400, str(e)) from None
    if not p.is_file():
        raise HTTPException(404, "not found")
    return {"path": path, "content": p.read_text(encoding="utf-8", errors="replace")}


@app.put("/api/memory/file")
async def memory_write(request: Request):
    _check(request)
    body = await request.json()
    try:
        async with state.session._memory_lock:
            state.session.memory.cmd_create({"path": body.get("path"), "file_text": body.get("content", "")})
    except MemoryError_ as e:
        raise HTTPException(400, str(e)) from None
    await hub.broadcast({"type": "memory_op", "source": "user", "command": "create", "path": body.get("path")})
    return {"ok": True}


@app.post("/api/memory/translate")
async def memory_translate(request: Request):
    """Translate memory text into the user's language for reading. Nothing is saved."""
    _check(request)
    if not state.settings.language:
        raise HTTPException(400, "Choose your language first.")
    body = await request.json()
    try:
        text = await state.session.translate_memory(str(body.get("content") or "")[:100_000])
    except (APIError, httpx.HTTPError) as e:
        raise HTTPException(502, f"Translation failed: {getattr(e, 'message', None) or e}") from None
    await hub.broadcast({"type": "usage", **state.session.usage_payload()})
    return {"text": text, "language": state.settings.language}


@app.delete("/api/memory/file")
async def memory_delete(request: Request, path: str):
    _check(request)
    try:
        async with state.session._memory_lock:
            state.session.memory.cmd_delete({"path": path})
    except MemoryError_ as e:
        raise HTTPException(400, str(e)) from None
    await hub.broadcast({"type": "memory_op", "source": "user", "command": "delete", "path": path})
    return {"ok": True}


# Clean-up: every operation backs memory up first, and /undo puts the last backup back.
async def _memory_cleaned(command: str, result: dict) -> dict:
    await hub.broadcast({"type": "memory_op", "source": "clean", "command": command, "path": "/memories"})
    await hub.broadcast({"type": "usage", **state.session.usage_payload()})
    return {**result, "undo": state.session.memory.latest_backup()}


@app.post("/api/memory/tidy")
async def memory_tidy(request: Request):
    """AI tidy-up: merge duplicates, drop stale lines. Undoable."""
    _check(request)
    try:
        res = await state.session.tidy_memory()
    except MemoryBusy as e:
        raise HTTPException(409, str(e)) from None
    except (APIError, httpx.HTTPError, asyncio.TimeoutError) as e:
        why = getattr(e, "message", None) or str(e) or "timed out"
        raise HTTPException(502, f"Tidy-up failed; memory was restored. ({why})") from None
    try:  # the summary is English; show it in the user's language
        res["summary"] = await state.session.translate_memory(res["summary"])
    except (APIError, httpx.HTTPError):
        pass
    return await _memory_cleaned("tidy", res)


@app.post("/api/memory/clear")
async def memory_clear(request: Request):
    """Delete every memory file. Undoable."""
    _check(request)
    try:
        res = await state.session.clear_memory()
    except MemoryBusy as e:
        raise HTTPException(409, str(e)) from None
    return await _memory_cleaned("clear", res)


@app.post("/api/memory/undo")
async def memory_undo(request: Request):
    """Restore memory from before the last tidy-up or clear."""
    _check(request)
    try:
        restored = await state.session.undo_memory_clean()
    except MemoryBusy as e:
        raise HTTPException(409, str(e)) from None
    if restored is None:
        raise HTTPException(404, "Nothing to undo.")
    return await _memory_cleaned("undo", {"restored": restored})


@app.post("/api/quit")
async def quit_app(request: Request):
    _check(request)
    if state.shutdown is None:
        return {"ok": False, "reason": "not running as a desktop app"}
    await state.session.stop()
    asyncio.get_running_loop().call_later(0.3, state.shutdown)
    return {"ok": True}


# ------------------------------------------------------- projects & chats
def _clean_project_fields(body: dict, project: dict) -> dict:
    if "name" in body:
        project["name"] = str(body["name"]).strip()[:80] or "Untitled project"
    if "instructions" in body:
        project["instructions"] = str(body["instructions"])[:8000]
    if "folders" in body and isinstance(body["folders"], list):
        project["folders"] = [str(f).strip() for f in body["folders"] if str(f).strip()][:50]
    return project


async def _broadcast_sidebar() -> None:
    await hub.broadcast({"type": "sidebar", **state.session.sidebar()})


@app.get("/api/projects")
async def projects_list(request: Request):
    _check(request)
    return state.session.sidebar()


@app.post("/api/projects")
async def project_create(request: Request):
    _check(request)
    body = await request.json()
    proj = state.session.store.save_project(_clean_project_fields(body, {"name": "Untitled project"}))
    await _broadcast_sidebar()
    return proj


@app.put("/api/projects/{pid}")
async def project_update(request: Request, pid: str):
    _check(request)
    store = state.session.store
    try:
        proj = store.get_project(pid)
    except ValueError:
        proj = None
    if proj is None:
        raise HTTPException(404, "not found")
    proj = store.save_project(_clean_project_fields(await request.json(), proj))
    await _broadcast_sidebar()
    return proj


@app.delete("/api/projects/{pid}")
async def project_delete(request: Request, pid: str):
    _check(request)
    try:
        state.session.store.delete_project(pid)
    except ValueError:
        raise HTTPException(400, "bad id") from None
    sess = state.session
    if sess.chat.get("project_id") == pid:
        sess.chat["project_id"] = None
        await hub.broadcast({"type": "chat_meta", "chat": sess.chat_meta()})
    await _broadcast_sidebar()
    return {"ok": True}


@app.delete("/api/chats/{cid}")
async def chat_delete(request: Request, cid: str):
    _check(request)
    sess = state.session
    try:
        sess.store.delete_chat(cid)
    except ValueError:
        raise HTTPException(400, "bad id") from None
    if sess.chat["id"] == cid:
        await sess.new_chat(sess.chat.get("project_id"))
    await _broadcast_sidebar()
    return {"ok": True}


# -------------------------------------------------------------- websocket
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    if not secrets.compare_digest(ws.query_params.get("token", ""), TOKEN) or \
            not _origin_ok(ws.headers.get("origin")):
        await ws.close(code=4403)
        return
    await ws.accept()
    hub.sockets.add(ws)
    sess = state.session
    await ws.send_text(json.dumps({
        "type": "hello", "settings": state.settings.public_dict(), "meta": _meta(),
        "chat": sess.chat_meta(), "history": sess.display, "busy": sess.busy,
        "state": sess.state if sess.busy else "idle", **sess.usage_payload(), **sess.sidebar(),
        "pending_approvals": list(sess.pending_approvals.values()),
    }, ensure_ascii=False))
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            t = msg.get("type")
            if t == "send":
                text = str(msg.get("text", ""))[:100_000]
                images = [i for i in (msg.get("images") or []) if isinstance(i, dict) and i.get("data")]
                if text.strip() or images:
                    await sess.submit(text, images)
            elif t == "stop":
                await sess.stop()
            elif t == "approval":
                sess.resolve_approval(str(msg.get("id")), {
                    "approve": bool(msg.get("approve")), "scope": str(msg.get("scope") or "once"),
                    "folder": str(msg.get("folder") or "")})
            elif t == "new_chat":
                await sess.new_chat(msg.get("project_id") or None)
            elif t == "open_chat":
                try:
                    await sess.open_chat(str(msg.get("id")))
                except ValueError:
                    pass
            elif t == "ping":
                await ws.send_text('{"type":"pong"}')
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.sockets.discard(ws)


@app.exception_handler(HTTPException)
async def http_exc(request: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


__all__ = ["app", "configure", "TOKEN", "state", "asyncio"]
