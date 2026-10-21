"""The agent session: translation in, Opus agent loop with tools, translation out,
background memory pass, projects/chats persistence and token accounting.

Prompt-cache rule: within a chat, the system prompt, tool list and thinking
display mode are frozen at the first turn and the message list is append-only.
The translation layer only ever reads text from the agent; it never edits the
transcript.
"""
from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import io
import json
import threading
import time
import uuid
from typing import Any, Awaitable, Callable, Optional

import httpx
from PIL import Image

from . import config
from .api import APIError, stream_message
from .computer import ComputerError, LocalComputer, UserAbort, compute_scale, thumbnail_b64
from .config import Settings
from .local_tools import (EDITOR_NAME, EDITOR_WRITE_COMMANDS, SHELL_TOOL_NAMES, WINDOWS_TOOL,
                          describe_editor_write, folder_for, is_within, run_editor, run_powershell,
                          shell_tool)
from .memory_store import MemoryError_, MemoryStore
from .overlay import ControlOverlay, control_label
from .prompts import COMPACT_INSTRUCTIONS, MEMORY_PASS_SYSTEM, MEMORY_TIDY_SYSTEM, agent_system_prompt, slug
from .store import Store, new_id
from .translator import StreamingTranslation, Translator, english_name

Emit = Callable[[dict], Awaitable[None]]
CONTEXT_BETA = "context-management-2025-06-27"
COMPACT_BETA = "compact-2026-01-12"
BINDING_BETA = "thinking-binding-controls-2026-08-01"
# Safety net for very long computer-use sessions: once the request carries this
# many screenshots (or this many bytes of them), old ones are replaced by text in
# one batch. Server-side clearing keeps the *context* small, but the client still
# uploads every image, so the request itself would keep growing.
PRUNE_MAX_IMAGES = 40
PRUNE_MAX_BYTES = 18_000_000
PRUNE_KEEP = 8
COMPUTER_READONLY = {"screenshot", "zoom", "cursor_position", "wait"}
# Actions after which the on-screen "Claude" pointer moves to the cursor (and pulses on clicks).
CLICK_ACTIONS = {"left_click", "right_click", "middle_click", "double_click", "triple_click",
                 "left_mouse_down", "left_click_drag"}
POINTER_ACTIONS = CLICK_ACTIONS | {"mouse_move", "left_mouse_up", "scroll"}
USAGE_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
KEEP_THUMBS = 20
MEMORY_TRANSLATION_CACHE = 200
TIDY_TIMEOUT = 300


class MemoryBusy(Exception):
    """A memory clean-up can't run right now (the agent or another pass is using memory)."""


def _id() -> str:
    return uuid.uuid4().hex[:10]


def blank_chat(project_id: Optional[str] = None) -> dict:
    now = time.time()
    return {"id": new_id(), "project_id": project_id, "title": "", "created": now, "updated": now,
            "messages": [], "display": [], "system_prompt": None, "tools": None,
            "show_thinking": None, "reply_language": "English", "folders": [],
            "usage": {}, "context": {}}


def usage_add(bucket: dict, category: str, model: str, usage: dict) -> None:
    u = bucket.setdefault(category, {**{k: 0 for k in USAGE_KEYS}, "cost": 0.0, "calls": 0})
    for k in USAGE_KEYS:
        u[k] += int(usage.get(k) or 0)
    u["cost"] += config.estimate_cost(model, usage)
    u["calls"] += 1


def usage_diff(after: dict, before: dict) -> dict:
    out = {}
    for cat, u in after.items():
        b = before.get(cat, {})
        d = {k: u.get(k, 0) - b.get(k, 0) for k in (*USAGE_KEYS, "cost", "calls")}
        if d["calls"]:
            d["cost"] = round(d["cost"], 5)
            out[cat] = d
    return out


class Session:
    def __init__(self, get_settings: Callable[[], Settings], emit: Emit, store: Optional[Store] = None):
        self.get_settings = get_settings
        self.emit_raw = emit
        self.store = store or Store(config.DATA_DIR)
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=600.0))
        self.translator = Translator(self.http, get_settings, on_usage=self._translator_usage)
        self.memory = MemoryStore(config.DATA_DIR / "memories")
        self.stop_event = threading.Event()
        self.turn_task: Optional[asyncio.Task] = None
        self._stream_task: Optional[asyncio.Task] = None
        self.memory_task: Optional[asyncio.Task] = None
        self.memory_maintenance = False        # a tidy-up is editing /memories
        self._memory_lock = asyncio.Lock()
        self.approvals: dict[str, asyncio.Future] = {}
        self.pending_approvals: dict[str, dict] = {}
        self._memory_translations: dict[tuple[str, str], str] = {}
        self.overlay: Optional[ControlOverlay] = None   # border, pointer, Esc-to-stop
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._live_translations: list[StreamingTranslation] = []
        self._translations: list[asyncio.Task] = []
        self._use_binding = True
        self._use_compact = True
        self.state = "idle"
        self.session_cost = 0.0
        self.computer: Optional[LocalComputer] = None
        self._set_chat(blank_chat())

    # ================================================================ state
    def _set_chat(self, chat: dict) -> None:
        self.chat = chat
        self._display_index = {it["id"]: it for it in chat["display"] if "id" in it}
        self.computer = None  # re-created lazily (capture mode resets per chat)

    @property
    def messages(self) -> list[dict]:
        return self.chat["messages"]

    @property
    def display(self) -> list[dict]:
        return self.chat["display"]

    @property
    def reply_language(self) -> str:
        return self.chat["reply_language"]

    @property
    def busy(self) -> bool:
        return self.turn_task is not None and not self.turn_task.done()

    def chat_meta(self) -> dict:
        c = self.chat
        return {"id": c["id"], "project_id": c["project_id"], "title": c["title"],
                "saved": bool(c["messages"])}

    def usage_payload(self) -> dict:
        return {"usage": self.chat["usage"], "context": self.chat["context"],
                "session_cost": round(self.session_cost, 4)}

    def sidebar(self) -> dict:
        return {"projects": self.store.list_projects(), "chats": self.store.list_chats()}

    async def emit(self, ev: dict) -> None:
        await self.emit_raw(ev)

    def _store(self, item: dict) -> dict:
        self.display.append(item)
        self._display_index[item["id"]] = item
        return item

    def _add_usage(self, category: str, model: str, usage: dict, chat: Optional[dict] = None) -> None:
        usage_add((chat or self.chat)["usage"], category, model, usage)
        self.session_cost += config.estimate_cost(model, usage)

    def _translator_usage(self, model: str, usage: dict) -> None:
        self._add_usage("translator", model, usage)

    async def status(self, state: str, detail: str = "") -> None:
        self.state = state
        await self.emit({"type": "status", "state": state, "detail": detail})

    async def notice(self, text: str, level: str = "info") -> None:
        item = self._store({"type": "notice", "id": _id(), "level": level, "text": text})
        await self.emit({**item, "type": "notice"})

    # ============================================================ commands
    async def submit(self, text: str, images: list[dict]) -> None:
        if self.busy:
            await self.notice("Still working on the previous request - press Stop first.", "warn")
            return
        if self.memory_maintenance:
            await self.emit({"type": "notice", "id": _id(), "level": "warn",
                             "text": "Memory is being tidied up - send again in a moment."})
            return
        if not self.get_settings().language:
            # The language is never guessed: the user picks it in the app first.
            await self.emit({"type": "notice", "id": _id(), "level": "warn",
                             "text": "Choose your language first (top bar)."})
            return
        self.stop_event.clear()
        self.turn_task = asyncio.create_task(self._turn(text, images))

    async def stop(self) -> None:
        self.stop_event.set()
        self._control_off()
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
        for fut in self.approvals.values():
            if not fut.done():
                fut.set_result({"approve": False})
        for tr in list(self._live_translations):
            tr.task.cancel()

    def resolve_approval(self, aid: str, decision: Any) -> None:
        fut = self.approvals.get(aid)
        if fut and not fut.done():
            fut.set_result(decision if isinstance(decision, dict) else {"approve": bool(decision)})

    async def _halt(self) -> None:
        if self.busy:
            await self.stop()
            try:
                await asyncio.wait_for(self.turn_task, 10)
            except (Exception, asyncio.CancelledError):  # noqa: BLE001
                pass

    async def new_chat(self, project_id: Optional[str] = None) -> None:
        await self._halt()
        if project_id and not self.store.get_project(project_id):
            project_id = None
        self._set_chat(blank_chat(project_id))
        await self._emit_opened()

    async def open_chat(self, chat_id: str) -> None:
        await self._halt()
        chat = self.store.load_chat(chat_id)
        if chat is None:
            await self.notice("That chat could not be loaded.", "error")
            return
        for k, v in blank_chat().items():
            chat.setdefault(k, v)
        self._set_chat(chat)
        await self._emit_opened()

    async def _emit_opened(self) -> None:
        await self.emit({"type": "chat_opened", "chat": self.chat_meta(), "history": self.display,
                         **self.usage_payload()})
        await self.status("idle")

    async def close(self) -> None:
        await self.stop()
        if self.overlay is not None:
            self.overlay.close()
        await self.http.aclose()

    # ===================================================== control signal
    def _control_on(self, computer: Any) -> None:
        """The agent starts acting on the desktop: show the border and arm Esc-to-stop."""
        if self.overlay is None:
            self.overlay = ControlOverlay(on_escape=self._escape_pressed)
        if self.overlay.active:
            return
        s = self.get_settings()
        self._loop = asyncio.get_running_loop()
        rect = getattr(computer, "_monitor_rect", lambda: None)()
        self.overlay.show(rect, control_label(s.language))

    def _control_off(self) -> None:
        if self.overlay is not None:
            self.overlay.hide()

    def _escape_pressed(self) -> None:
        """Esc on the physical keyboard while the agent controls the computer (called
        on the overlay thread): stop, just like the Stop button."""
        self.stop_event.set()                    # aborts the action in progress at once
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(lambda: loop.create_task(self._stop_by_escape()))

    async def _stop_by_escape(self) -> None:
        if self.busy:
            await self.notice("Stopped: you pressed Esc.", "warn")
        await self.stop()

    # ======================================================= folders
    def project(self) -> Optional[dict]:
        return self.store.get_project(self.chat["project_id"])

    def folders(self) -> list[str]:
        p = self.project()
        return list(p.get("folders", [])) if p else list(self.chat["folders"])

    async def grant_folder(self, folder: str) -> None:
        p = self.project()
        if p is not None:
            if folder not in p["folders"]:
                p["folders"].append(folder)
                self.store.save_project(p)
                await self.emit({"type": "sidebar", **self.sidebar()})
        elif folder not in self.chat["folders"]:
            self.chat["folders"].append(folder)

    # ======================================================= conversation
    def _get_computer(self) -> LocalComputer:
        if self.computer is None:
            s = self.get_settings()
            self.computer = LocalComputer(monitor=s.monitor, max_edge=s.max_screenshot_edge,
                                          stop_event=self.stop_event, capture_mode=s.capture_mode,
                                          own_title=config.APP_NAME)
        return self.computer

    def _ensure_conversation_setup(self, s: Settings) -> None:
        """First turn of a chat: build and freeze system prompt + tools."""
        chat = self.chat
        if chat["system_prompt"] is not None:
            return
        computer_on = s.computer_use and config.COMPUTER_SUPPORTED
        if computer_on:
            try:
                self._get_computer()
            except Exception as e:  # noqa: BLE001
                computer_on = False
                self._pending_notice = f"Computer control unavailable ({type(e).__name__}: {e}); continuing without it."
        tools: list[dict] = []
        if computer_on:
            tools.append({"type": "computer_toolset_20260801"})
            tools.append(copy.deepcopy(WINDOWS_TOOL))
        if s.shell_mode != "off":
            tools.append(copy.deepcopy(shell_tool()))
        if s.file_tools_mode != "off":
            tools.append({"type": "text_editor_20250728", "name": EDITOR_NAME, "max_characters": 30000})
        if s.web_search:
            tools.append({"type": "web_search_20250305", "name": "web_search", "max_uses": 8})
        if s.memory_enabled:
            tools.append({"type": "memory_20250818", "name": "memory"})
        chat["tools"] = tools
        chat["show_thinking"] = s.show_thinking
        chat["system_prompt"] = agent_system_prompt(
            app_title=config.APP_NAME, system=config.system_name(), shell_name=shell_tool()["name"],
            computer_on=computer_on,
            shell_on=s.shell_mode != "off", editor_on=s.file_tools_mode != "off",
            web_on=s.web_search, memory_on=s.memory_enabled,
            memory_snapshot=self.memory.snapshot() if s.memory_enabled else "",
            project=self.project(), folders=self.folders())

    def _request_body(self, s: Settings) -> tuple[dict, list[str]]:
        chat = self.chat
        body: dict[str, Any] = {
            "model": s.model,
            "max_tokens": s.max_tokens,
            "system": [{"type": "text", "text": chat["system_prompt"],
                        "cache_control": {"type": "ephemeral"}}],
            "messages": self.messages,
            "output_config": {"effort": s.effort},
            "cache_control": {"type": "ephemeral"},
        }
        tools = chat["tools"] or []
        if tools:
            body["tools"] = tools
        thinking: dict[str, Any] = {"type": "adaptive"}
        if chat.get("show_thinking"):
            thinking["display"] = "summarized"
        betas: list[str] = []
        if self._use_binding:
            # If any earlier block ever differs from what was signed (e.g. after a
            # screenshot prune), drop stale thinking instead of failing with a 400.
            thinking["block_binding"] = {"prefix_mismatch_behavior": "drop_block"}
            betas.append(BINDING_BETA)
        body["thinking"] = thinking
        edits: list[dict] = []
        if tools:
            edit: dict[str, Any] = {
                "type": "clear_tool_uses_20250919",
                "trigger": {"type": "input_tokens", "value": s.context_clear_trigger},
                "keep": {"type": "tool_uses", "value": s.context_keep_tool_uses},
                "clear_at_least": {"type": "input_tokens",
                                   "value": max(5000, min(30000, s.context_clear_trigger // 4))},
            }
            if any(t.get("name") == "memory" for t in tools):
                edit["exclude_tools"] = ["memory"]
            edits.append(edit)
            betas.append(CONTEXT_BETA)
        if s.auto_compact and self._use_compact:
            # Server-side compaction: once the prompt reaches the trigger, the API
            # summarizes the older turns into a compaction block at the start of the
            # reply. It is part of the reply, so the transcript stays append-only.
            edits.append({"type": "compact_20260112",
                          "trigger": {"type": "input_tokens", "value": s.compact_trigger},
                          "instructions": COMPACT_INSTRUCTIONS})
            betas.append(COMPACT_BETA)
        if edits:
            body["context_management"] = {"edits": edits}
        return body, betas

    def _repair_transcript(self) -> None:
        """Every tool_use needs a tool_result in the next message; add error
        results for any left unanswered (crash, shutdown) so the API accepts it."""
        if not self.messages or self.messages[-1]["role"] != "assistant":
            return
        uses = [b for b in self.messages[-1]["content"] if b.get("type") == "tool_use"]
        if uses:
            self.messages.append({"role": "user", "content": [
                _tool_result(u, "Not executed: the task was interrupted.", True) for u in uses]})

    def _drop_compacted(self) -> int:
        """The API ignores everything before the latest compaction block, so the
        messages before it are dropped at the start of a turn: the model sees the
        same conversation, but requests and the saved chat stay small. Only done
        when that block holds a summary (a failed compaction has none)."""
        msgs = self.messages
        for i in range(len(msgs) - 1, -1, -1):
            content = msgs[i]["content"] if isinstance(msgs[i]["content"], list) else []
            blocks = [b for b in content if b.get("type") == "compaction"]
            if blocks:
                if i == 0 or not blocks[-1].get("content"):
                    return 0
                del msgs[:i]
                return i
        return 0

    def _prune_images(self) -> int:
        """Batch-replace old screenshots once the request gets too heavy."""
        found = []  # (block_list, index, size)
        for m in self.messages:
            if m["role"] != "user" or not isinstance(m["content"], list):
                continue
            for b in m["content"]:
                if b.get("type") == "tool_result" and isinstance(b.get("content"), list):
                    for i, c in enumerate(b["content"]):
                        if c.get("type") == "image":
                            found.append((b["content"], i, len(c.get("source", {}).get("data", ""))))
        total = sum(f[2] for f in found)
        if len(found) <= PRUNE_MAX_IMAGES and total <= PRUNE_MAX_BYTES:
            return 0
        old = found[:-PRUNE_KEEP]
        for lst, i, _ in old:
            lst[i] = {"type": "text", "text": "[older screenshot removed to save space]"}
        return len(old)

    def _add_user_content(self, blocks: list[dict]) -> None:
        self._repair_transcript()
        if self.messages and self.messages[-1]["role"] == "user":
            # previous turn was interrupted; keep roles alternating
            self.messages[-1]["content"] = list(self.messages[-1]["content"]) + [
                {"type": "text", "text": "(The previous request was interrupted by the user.)"}] + blocks
        else:
            self.messages.append({"role": "user", "content": blocks})

    @staticmethod
    def _prepare_image(img_in: dict) -> Optional[tuple[dict, str]]:
        try:
            raw = base64.b64decode(img_in["data"])
            im = Image.open(io.BytesIO(raw))
            im.load()
        except Exception:  # noqa: BLE001
            return None
        s = compute_scale(im.width, im.height, 1568)
        if s < 1.0:
            im = im.resize((int(im.width * s), int(im.height * s)), Image.LANCZOS)
        buf = io.BytesIO()
        if im.mode in ("RGBA", "LA", "P"):
            im.save(buf, format="PNG")
            mt = "image/png"
        else:
            im.convert("RGB").save(buf, format="JPEG", quality=90)
            mt = "image/jpeg"
        block = {"type": "image", "source": {"type": "base64", "media_type": mt,
                                             "data": base64.b64encode(buf.getvalue()).decode()}}
        return block, thumbnail_b64(im, 320)

    def _save_chat(self) -> None:
        chat = self.chat
        if not chat["messages"]:
            return
        # keep screenshot thumbnails only for the most recent actions
        with_thumbs = [it for it in chat["display"] if it.get("thumb")]
        for it in with_thumbs[:-KEEP_THUMBS]:
            it["thumb"] = ""
        try:
            self.store.save_chat(chat)
        except OSError:
            pass

    # ================================================================ turn
    async def _turn(self, text: str, images: list[dict]) -> None:
        s = self.get_settings()
        chat = self.chat
        self._drop_compacted()
        turn_start = len(self.messages)
        saved_last = copy.deepcopy(self.messages[-1]) if self.messages else None
        usage_before = copy.deepcopy(chat["usage"])
        self._pending_notice = ""
        self._translations = []
        try:
            # ---- 1. translate the user's message into English (from the language they chose)
            language = english_name(s.language)
            await self.status("translating_in")
            try:
                english = await self.translator.to_english(text, s.language)
            except APIError as e:
                await self.notice(f"Translation failed ({e.message}); sending the original text.", "warn")
                english = text
            chat["reply_language"] = language
            if not chat["title"]:
                chat["title"] = " ".join(text.split())[:60] or "Image"

            img_blocks, thumbs = [], []
            for im in images[:6]:
                prepared = self._prepare_image(im)
                if prepared:
                    img_blocks.append(prepared[0])
                    thumbs.append(prepared[1])
            uitem = self._store({"type": "user", "id": _id(), "original": text, "english": english,
                                 "lang": language, "images": thumbs,
                                 "translated": english.strip() != text.strip()})
            await self.emit({**uitem, "type": "user_message"})

            # ---- 2. agent loop (English only)
            self._ensure_conversation_setup(s)
            if self._pending_notice:
                await self.notice(self._pending_notice, "warn")
            content = img_blocks + ([{"type": "text", "text": english}] if english.strip() else [])
            if not content:
                return
            self._add_user_content(content)
            await self._agent_loop(s)
        except asyncio.CancelledError:
            pass
        except (APIError, httpx.TransportError) as e:
            # Nothing from the assistant yet? Roll back so retrying is clean.
            if not any(m["role"] == "assistant" for m in self.messages[turn_start:]):
                del self.messages[turn_start:]
                if saved_last is not None and self.messages:
                    self.messages[-1] = saved_last
            msg = e.message if isinstance(e, APIError) else f"network error ({type(e).__name__}: {e})"
            await self.notice(f"API error: {msg}", "error")
        except Exception as e:  # noqa: BLE001
            await self.notice(f"Unexpected error: {type(e).__name__}: {e}", "error")
        finally:
            self._control_off()          # the agent is done acting on the desktop
            try:
                await self.status("translating_out")
                for t in list(self._translations):
                    try:
                        await t
                    except (Exception, asyncio.CancelledError):  # noqa: BLE001
                        pass
                release = getattr(self.computer, "release_inputs", None)
                if release:
                    await asyncio.to_thread(release)
            except (Exception, asyncio.CancelledError):  # noqa: BLE001
                pass
            finally:
                diff = usage_diff(chat["usage"], usage_before)
                if diff:
                    item = self._store({"type": "turn_usage", "id": _id(), "usage": diff,
                                        "context": dict(chat["context"])})
                    await self.emit({**item, "type": "turn_usage"})
                self._save_chat()
                await self.emit({"type": "turn_done", "chat": self.chat_meta(), **self.usage_payload()})
                await self.emit({"type": "sidebar", **self.sidebar()})
                await self.status("idle")
                if s.memory_enabled and s.memory_pass and len(self.messages) > turn_start \
                        and not self.stop_event.is_set():
                    self.memory_task = asyncio.create_task(self._memory_pass(chat, turn_start))

    async def _agent_loop(self, s: Settings) -> None:
        for step in range(s.max_steps):
            if self.stop_event.is_set():
                return
            await self.status("thinking")
            msg = await self._sample(s)
            if msg is None:
                return  # cancelled mid-stream; partial output discarded
            self.messages.append({"role": "assistant", "content": msg["content"]})
            await self._record_main_usage(s, msg)
            stop = msg.get("stop_reason")
            if stop in ("pause_turn", "compaction"):
                continue
            tool_uses = [b for b in msg["content"] if b.get("type") == "tool_use"]
            truncated = set()
            if stop == "max_tokens" and tool_uses and msg["content"][-1].get("type") == "tool_use":
                truncated.add(msg["content"][-1]["id"])  # its input JSON was cut off
            if stop == "refusal":
                await self.notice("The model declined to continue this request.", "warn")
            elif stop == "max_tokens" and not tool_uses:
                await self.notice("The reply hit the max_tokens limit and was cut off.", "warn")
            elif stop == "model_context_window_exceeded":
                await self.notice("The conversation is too long for the context window. Start a new chat.", "warn")
            if not tool_uses:
                return
            await self.status("acting")
            results = await self._run_tools(tool_uses, truncated)
            self.messages.append({"role": "user", "content": results})
        await self.notice(f"Stopped after {s.max_steps} steps (Settings → max steps).", "warn")

    async def _record_main_usage(self, s: Settings, msg: dict) -> None:
        u = msg.get("usage") or {}
        self._add_usage("agent", s.model, u)
        # The top-level counts leave out compaction; its summarizing step is billed too.
        for it in u.get("iterations") or []:
            if it.get("type") == "compaction":
                self._add_usage("compact", s.model, it)
        prompt =sum(int(u.get(k) or 0) for k in USAGE_KEYS[:3])
        self.chat["context"] = {
            "used": prompt + int(u.get("output_tokens") or 0),
            "window": config.CONTEXT_WINDOWS.get(s.model, config.DEFAULT_CONTEXT_WINDOW),
            "model": s.model,
            "cached": int(u.get("cache_read_input_tokens") or 0),
            "prompt": prompt,
        }
        await self.emit({"type": "usage", **self.usage_payload()})

    # ----------------------------------------------------------- sampling
    async def _sample(self, s: Settings) -> Optional[dict]:
        pruned = self._prune_images()
        if pruned:
            await self.notice(f"Removed {pruned} old screenshots from the request to keep it small.")
        body, betas = self._request_body(s)
        st = _StreamState(self, s)
        self._stream_task = asyncio.create_task(stream_message(
            self.http, api_key=s.effective_api_key(), base_url=s.base_url, body=body, betas=betas,
            on_event=st.on_event, on_retry=st.on_retry))
        if self.stop_event.is_set():  # Stop pressed while we were preparing
            self._stream_task.cancel()
        try:
            msg = await self._stream_task
        except asyncio.CancelledError:
            await st.close_segment(cancelled=True)
            if self.stop_event.is_set():
                return None
            raise
        except APIError as e:
            await st.discard_all()
            if e.status == 400 and self._use_binding and "binding" in e.message.lower():
                self._use_binding = False  # model/account without binding controls
                return await self._sample(s)
            if e.status == 400 and self._use_compact and "compact" in e.message.lower():
                self._use_compact = False  # model/account without server-side compaction
                await self.notice(f"Auto compact is not available here ({e.message}); continuing without it.",
                                  "warn")
                return await self._sample(s)
            raise
        except Exception:
            await st.discard_all()
            raise
        await st.close_segment()
        return msg

    # -------------------------------------------------------------- tools
    async def _run_tools(self, tool_uses: list[dict], truncated: set = frozenset()) -> list[dict]:
        results: list[dict] = []
        computer_failed = False
        for tu in tool_uses:
            name, inp = tu.get("name"), tu.get("input") or {}
            is_computer = tu.get("toolset_name") == "computer"
            try:
                if self.stop_event.is_set():
                    content, err = "Not executed: the user stopped the task.", True
                elif tu["id"] in truncated:
                    content, err = ("Not executed: this tool call was cut off by the max_tokens limit. "
                                    "Send it again."), True
                elif is_computer and computer_failed:
                    content, err = "Not executed: an earlier computer action in this turn failed.", True
                elif is_computer:
                    content, err = await self._computer(tu["id"], name, inp)
                    computer_failed = err
                elif name == "windows":
                    content, err = await self._windows(tu["id"], inp)
                elif name == "memory":
                    content, err = await self._memory_op(inp, source="agent")
                elif name in SHELL_TOOL_NAMES:
                    content, err = await self._powershell(tu["id"], inp)
                elif name == EDITOR_NAME:
                    content, err = await self._editor(tu["id"], inp)
                else:
                    content, err = f"Error: unknown tool {name}", True
            except Exception as e:  # noqa: BLE001 - never leave a tool_use unanswered
                content, err = f"Error: {type(e).__name__}: {e}", True
                computer_failed = computer_failed or is_computer
                item = self._display_index.get(tu["id"])
                if item is not None:
                    await self._action_done(item, False, str(e))
            results.append(_tool_result(tu, content, err))
        return results

    async def _action_event(self, aid: str, kind: str, name: str, inp: dict) -> dict:
        item = self._store({"type": "action", "id": aid, "kind": kind, "name": name,
                            "input": _ui_input(inp), "state": "running"})
        await self.emit({**item, "type": "action"})
        return item

    async def _action_done(self, item: dict, ok: bool, text: str = "", thumb: str = "") -> None:
        item.update(state="ok" if ok else "error", text=text[:2000], thumb=thumb)
        await self.emit({**item, "type": "action_result"})

    async def _computer(self, aid: str, name: str, inp: dict) -> tuple[Any, bool]:
        s = self.get_settings()
        item = await self._action_event(aid, "computer", name, inp)
        try:
            computer = self._get_computer()
        except Exception as e:  # noqa: BLE001
            await self._action_done(item, False, "computer unavailable")
            return f"Error: computer control is unavailable ({e}).", True
        computer.capture_mode = s.capture_mode  # the UI switch is the source of truth
        if s.confirm_computer_actions and name not in COMPUTER_READONLY:
            if not (await self._approve("computer", f"{name} {json.dumps(_ui_input(inp), ensure_ascii=False)}"))["approve"]:
                await self._action_done(item, False, "denied")
                return "The user denied this action. Ask what they want instead.", True
        self._control_on(computer)
        try:
            out = await asyncio.to_thread(computer.execute, name, inp)
        except UserAbort as e:
            self.stop_event.set()
            await self._action_done(item, False, str(e))
            return f"Not executed: {e}", True
        except ComputerError as e:
            await self._action_done(item, False, str(e))
            return f"Error: {e}", True
        if name in POINTER_ACTIONS:
            self.overlay.pointer(click=name in CLICK_ACTIONS)
        thumb = await asyncio.to_thread(thumbnail_b64, out["image"]) if out.get("image") is not None else ""
        text = " ".join(b.get("text", "") for b in out["content"] if b.get("type") == "text")
        await self._action_done(item, True, text, thumb)
        return out["content"], False

    async def set_capture_mode(self, mode: str) -> None:
        """Active app <-> full screen. Persisted, applied to the next screenshot."""
        s = self.get_settings()
        config.apply_updates(s, {"capture_mode": mode})
        try:
            config.save_settings(s)
        except OSError:
            pass
        if self.computer is not None:
            self.computer.capture_mode = s.capture_mode
        await self.emit({"type": "settings", "settings": s.public_dict()})

    async def _windows(self, aid: str, inp: dict) -> tuple[str, bool]:
        s = self.get_settings()
        item = await self._action_event(aid, "computer", "windows", inp)
        if inp.get("action") == "capture":
            mode = inp.get("mode")
            if mode not in ("window", "screen"):
                await self._action_done(item, False, "bad mode")
                return "Error: `mode` must be window or screen.", True
            if mode == "screen" and s.capture_mode != "screen":
                # The user chose to share only the active app: widening needs their OK.
                if not (await self._approve("screen", ""))["approve"]:
                    await self._action_done(item, False, "denied")
                    return ("The user wants to keep sharing only the active app. Continue with "
                            "window screenshots, or ask the user."), True
            await self.set_capture_mode(mode)
            out = ("Screenshots now show the full screen (taskbar hidden)." if mode == "screen"
                   else "Screenshots now show only the focused window.") + " Take a screenshot."
            await self._action_done(item, True, out)
            return out, False
        if s.confirm_computer_actions and inp.get("action") == "focus":
            if not (await self._approve("computer", f"focus window: {inp.get('title', '')}"))["approve"]:
                await self._action_done(item, False, "denied")
                return "The user denied this action.", True
        if inp.get("action") == "focus":
            self._control_on(self._get_computer())
        try:
            out = await asyncio.to_thread(self._get_computer().windows_tool, inp)
        except ComputerError as e:
            await self._action_done(item, False, str(e))
            return f"Error: {e}", True
        await self._action_done(item, True, out)
        return out, False

    async def _memory_op(self, inp: dict, source: str) -> tuple[str, bool]:
        async with self._memory_lock:
            try:
                text, err = self.memory.execute(inp), False
            except MemoryError_ as e:
                text, err = str(e), True
            except OSError as e:
                text, err = f"Error: {e}", True
        cmd = inp.get("command")
        if cmd != "view" and not err:
            await self.emit({"type": "memory_op", "source": source, "command": cmd,
                             "path": inp.get("path") or inp.get("new_path") or ""})
        return text, err

    async def _approve(self, kind: str, detail: str, extra: Optional[dict] = None) -> dict:
        """Ask the UI. Returns {"approve": bool, ...extra fields from the UI}."""
        aid = _id()
        fut = asyncio.get_running_loop().create_future()
        self.approvals[aid] = fut
        req = {"id": aid, "kind": kind, "detail": detail, **(extra or {})}
        self.pending_approvals[aid] = req
        await self.emit({**req, "type": "approval_request"})
        await self.status("waiting_approval")
        try:
            while not fut.done():
                if self.stop_event.is_set():
                    return {"approve": False}
                await asyncio.wait([fut], timeout=0.3)
            res = fut.result()
            return res if isinstance(res, dict) else {"approve": bool(res)}
        finally:
            self.approvals.pop(aid, None)
            self.pending_approvals.pop(aid, None)
            await self.emit({"type": "approval_resolved", "id": aid})
            await self.status("acting")

    async def _powershell(self, aid: str, inp: dict) -> tuple[str, bool]:
        s = self.get_settings()
        item = await self._action_event(aid, "shell", shell_tool()["name"], inp)
        if s.shell_mode == "off":
            await self._action_done(item, False, "disabled")
            return "Error: running commands is disabled in settings.", True
        if s.shell_mode == "ask":
            detail = str(inp.get("command", ""))
            if inp.get("cwd"):
                detail = f"# cwd: {inp['cwd']}\n{detail}"
            if not (await self._approve("shell", detail))["approve"]:
                await self._action_done(item, False, "denied")
                return "The user denied running this command. Ask what they want instead.", True
        out, err = await asyncio.to_thread(run_powershell, inp, self.stop_event)
        await self._action_done(item, not err, out)
        return out, err

    async def _editor(self, aid: str, inp: dict) -> tuple[str, bool]:
        s = self.get_settings()
        cmd = inp.get("command")
        item = await self._action_event(aid, "editor", f"file_{cmd}", inp)
        note = ""
        if cmd in EDITOR_WRITE_COMMANDS:
            if s.file_tools_mode == "off":
                await self._action_done(item, False, "disabled")
                return "Error: file editing is disabled in settings.", True
            if s.file_tools_mode == "ask" and not (await self._approve("file", describe_editor_write(inp)))["approve"]:
                await self._action_done(item, False, "denied")
                return "The user denied this file change. Ask what they want instead.", True
        elif cmd == "view":
            path = str(inp.get("path") or "")
            if path and not any(is_within(path, f) for f in self.folders()):
                # First read in this folder: ask once, then remember the folder.
                suggestion = folder_for(path)
                res = await self._approve("folder", path, {"folder": suggestion})
                if not res.get("approve"):
                    await self._action_done(item, False, "denied")
                    return "The user did not allow reading this location. Ask what they want instead.", True
                if res.get("scope") == "folder":
                    folder = str(res.get("folder") or suggestion)
                    if not is_within(path, folder):
                        folder = suggestion
                    await self.grant_folder(folder)
                    note = f"\n\n(The user granted read access to {folder}; reads there no longer need approval.)"
        out, err = await asyncio.to_thread(run_editor, inp)
        await self._action_done(item, not err, out if cmd != "view" else out[:400])
        return out + note, err

    # ================================================== memory translation
    async def translate_memory(self, text: str) -> str:
        """One-click translation of memory text into the user's language, for reading
        only. Memory stays English on disk (the agent reads it); nothing is written."""
        language = english_name(self.get_settings().language)
        if language in ("", "English") or not text.strip():
            return text
        key = (hashlib.sha256(text.encode("utf-8")).hexdigest(), language)
        if key not in self._memory_translations:
            # Counted in the session total, not in whichever chat happens to be open.
            tr = Translator(self.http, self.get_settings, on_usage=self._session_usage)
            out = await tr.from_english(text, language)
            if len(self._memory_translations) >= MEMORY_TRANSLATION_CACHE:
                self._memory_translations.clear()
            self._memory_translations[key] = out
        return self._memory_translations[key]

    def _session_usage(self, model: str, usage: dict) -> None:
        self.session_cost += config.estimate_cost(model, usage)

    # ========================================================= memory pass
    async def _memory_pass(self, chat: dict, turn_start: int) -> None:
        transcript = _transcript_text(chat["messages"][turn_start:])
        if not transcript.strip():
            return
        try:
            await asyncio.wait_for(self._memory_pass_inner(chat, transcript), timeout=240)
        except asyncio.TimeoutError:
            await self.emit({"type": "memory_pass", "state": "done", "summary": "timed out"})
        if chat["messages"]:
            try:
                self.store.save_chat(chat)  # persist the pass's token usage with its chat
            except OSError:
                pass

    async def _memory_pass_inner(self, chat: dict, transcript: str) -> None:
        s = self.get_settings()
        await self.emit({"type": "memory_pass", "state": "running"})
        proj = self.store.get_project(chat.get("project_id"))
        where = (f"This chat belongs to the project \"{proj['name']}\"; project facts go in "
                 f"/memories/projects/{slug(proj['name'])}.md.\n\n") if proj else ""
        prompt = (f"{where}<memory_snapshot>\n{self.memory.snapshot(8000)}\n</memory_snapshot>\n\n"
                  f"<transcript>\n{transcript}\n</transcript>\n\n"
                  "Review the transcript and update /memories as needed.")
        try:
            summary = await self._memory_agent(
                MEMORY_PASS_SYSTEM, prompt, lambda m, u: self._add_usage("memory", m, u, chat), "pass")
        except Exception as e:  # noqa: BLE001
            summary = f"failed: {e}"
        await self.emit({"type": "memory_pass", "state": "done", "summary": summary[:300]})
        if chat is self.chat:
            await self.emit({"type": "usage", **self.usage_payload()})

    async def _memory_agent(self, system: str, prompt: str, on_usage: Callable[[str, dict], None],
                            source: str, steps: int = 10) -> str:
        """Small tool loop in which a helper model edits /memories with the memory
        tool. Returns its one-line summary (the text after "DONE:")."""
        s = self.get_settings()
        msgs: list[dict] = [{"role": "user", "content": prompt}]
        summary = "no changes"
        for _ in range(steps):
            body = {"model": s.memory_pass_model, "max_tokens": 8000, "system": system,
                    "messages": msgs, "tools": [{"type": "memory_20250818", "name": "memory"}],
                    "output_config": {"effort": "medium"}}
            msg = await stream_message(self.http, api_key=s.effective_api_key(),
                                       base_url=s.base_url, body=body)
            on_usage(s.memory_pass_model, msg.get("usage") or {})
            msgs.append({"role": "assistant", "content": msg["content"]})
            uses = [b for b in msg["content"] if b.get("type") == "tool_use"]
            if not uses:
                text = "".join(b.get("text", "") for b in msg["content"] if b.get("type") == "text")
                return text.strip().removeprefix("DONE:").strip() or summary
            results = []
            for tu in uses:
                out, err = await self._memory_op(tu.get("input") or {}, source=source)
                r = {"type": "tool_result", "tool_use_id": tu["id"], "content": out}
                if err:
                    r["is_error"] = True
                results.append(r)
            msgs.append({"role": "user", "content": results})
        return summary

    # ======================================================= memory clean-up
    def _check_memory_idle(self) -> None:
        if self.busy:
            raise MemoryBusy("The agent is still working on a request.")
        if self.memory_task is not None and not self.memory_task.done():
            raise MemoryBusy("Memory is still being updated after the last request.")
        if self.memory_maintenance:
            raise MemoryBusy("A memory clean-up is already running.")

    async def tidy_memory(self) -> dict:
        """AI clean-up of /memories: merge duplicates, drop stale lines. Backed up
        first (undoable); if anything fails, the backup is put back."""
        self._check_memory_idle()
        if not self.memory.tree():
            return {"summary": "Memory is empty.", "backup": None}
        self.memory_maintenance = True
        try:
            async with self._memory_lock:
                backup = self.memory.backup("tidy")
            prompt = (f"<memory>\n{self.memory.dump()}\n</memory>\n\n"
                      "Tidy up /memories as described.")
            try:
                summary = await asyncio.wait_for(self._memory_agent(
                    MEMORY_TIDY_SYSTEM, prompt, self._session_usage, "tidy", steps=25), TIDY_TIMEOUT)
            except BaseException:
                async with self._memory_lock:
                    self.memory.restore_latest()
                raise
        finally:
            self.memory_maintenance = False
        return {"summary": summary, "backup": backup}

    async def clear_memory(self) -> dict:
        """Delete all memory files (backed up first, so it can be undone)."""
        self._check_memory_idle()
        if not self.memory.tree():
            return {"backup": None}
        async with self._memory_lock:
            backup = self.memory.backup("clear")
            self.memory.clear()
        return {"backup": backup}

    async def undo_memory_clean(self) -> Optional[dict]:
        """Put back the memory from before the last tidy-up or clear."""
        self._check_memory_idle()
        async with self._memory_lock:
            return self.memory.restore_latest()


class _StreamState:
    """Routes streaming events of one model call to the UI and translator."""

    def __init__(self, session: Session, s: Settings):
        self.sess = session
        self.translate = session.reply_language.lower() not in ("english", "")
        self.seg: Optional[dict] = None
        self.seg_tr: Optional[StreamingTranslation] = None
        self.thinking: dict[int, dict] = {}
        self.created: list[dict] = []   # display items made by this call

    async def discard_all(self) -> None:
        """Remove this call's partial output from the UI (failed or retried call)."""
        if self.seg_tr is not None:
            self.seg_tr.task.cancel()
        self.seg, self.seg_tr = None, None
        self.thinking.clear()
        for item in self.created:
            if item in self.sess.display:
                self.sess.display.remove(item)
            await self.sess.emit({"type": "discard", "id": item["id"]})
        self.created.clear()

    async def on_retry(self, attempt: int, why: str) -> None:
        """A failed attempt is re-sent from scratch: drop its partial output."""
        await self.discard_all()
        await self.sess.status("retrying", f"attempt {attempt}: {why[:120]}")

    async def _open_segment(self) -> None:
        sess = self.sess
        seg = sess._store({"type": "assistant", "id": _id(), "english": "", "translation": "",
                           "translated": self.translate, "sources": [], "lang": sess.reply_language})
        self.created.append(seg)
        self.seg = seg
        await sess.emit({"type": "assistant_start", "id": seg["id"], "translated": self.translate,
                         "lang": sess.reply_language})
        if self.translate:
            seg_id = seg["id"]

            async def on_delta(t: str) -> None:
                seg["translation"] += t
                await sess.emit({"type": "translation_delta", "id": seg_id, "text": t})
            self.seg_tr = StreamingTranslation(sess.translator, sess.reply_language, on_delta)

    async def close_segment(self, cancelled: bool = False) -> None:
        seg, tr = self.seg, self.seg_tr
        self.seg, self.seg_tr = None, None
        if seg is None:
            return
        await self.sess.emit({"type": "assistant_final", "id": seg["id"], "english": seg["english"],
                              "sources": seg["sources"]})
        if tr is not None and cancelled:
            # user pressed Stop: don't spend tokens translating a half sentence
            tr.task.cancel()
            await self.sess.emit({"type": "translation_final", "id": seg["id"],
                                  "text": seg["translation"] or seg["english"]})
        elif tr is not None:
            tr.finish()
            sess = self.sess
            sess._live_translations.append(tr)

            async def finalize() -> None:
                try:
                    text = await tr.result()
                except asyncio.CancelledError:  # Stop / New chat
                    text = seg["translation"] or seg["english"]
                finally:
                    if tr in sess._live_translations:
                        sess._live_translations.remove(tr)
                seg["translation"] = text
                await sess.emit({"type": "translation_final", "id": seg["id"], "text": text})
            sess._translations.append(asyncio.create_task(finalize()))

    async def on_event(self, kind: str, idx: int, payload: Any) -> None:
        sess = self.sess
        if kind == "block_start":
            btype = payload.get("type")
            if btype == "text":
                if self.seg is None:
                    await self._open_segment()
            else:
                await self.close_segment()
                if btype == "thinking":
                    item = sess._store({"type": "thinking", "id": _id(), "text": ""})
                    self.created.append(item)
                    self.thinking[idx] = item
                    await sess.emit({"type": "thinking_start", "id": item["id"]})
                elif btype == "compaction":
                    await sess.status("compacting")
        elif kind == "text_delta":
            if self.seg is None:
                await self._open_segment()
            self.seg["english"] += payload
            await sess.emit({"type": "assistant_delta", "id": self.seg["id"], "text": payload})
            if self.seg_tr is not None:
                self.seg_tr.feed(payload)
        elif kind == "thinking_delta":
            item = self.thinking.get(idx)
            if item is not None:
                item["text"] += payload
                await sess.emit({"type": "thinking_delta", "id": item["id"], "text": payload})
        elif kind == "block_stop":
            btype = payload.get("type")
            if btype == "text" and self.seg is not None:
                for c in payload.get("citations") or []:
                    url = c.get("url")
                    if url and all(src["url"] != url for src in self.seg["sources"]):
                        self.seg["sources"].append({"url": url, "title": c.get("title") or url})
            elif btype == "thinking" and idx in self.thinking:
                item = self.thinking.pop(idx)
                await sess.emit({"type": "thinking_final", "id": item["id"], "text": item["text"]})
                if not item["text"].strip() and item in sess.display:
                    sess.display.remove(item)
            elif btype == "compaction":
                # Older turns were summarized (English, kept for the agent). The UI shows
                # a marker; the summary can be expanded under it.
                summary = payload.get("content") or ""
                item = sess._store({"type": "compacted", "id": _id(), "ok": bool(summary),
                                    "summary": summary})
                self.created.append(item)
                await sess.emit(item)
                await sess.status("thinking")
            elif btype == "server_tool_use":
                inp = payload.get("input") or {}
                item = sess._store({"type": "action", "id": payload.get("id", _id()), "kind": "web",
                                    "name": payload.get("name", "web_search"), "input": inp,
                                    "state": "running"})
                self.created.append(item)
                await sess.emit({**item, "type": "action"})
            elif btype == "web_search_tool_result":
                item = sess._display_index.get(payload.get("tool_use_id", ""))
                content = payload.get("content")
                if item is not None:
                    if isinstance(content, list):
                        titles = [f"{r.get('title', '')} - {r.get('url', '')}" for r in content[:6]]
                        item.update(state="ok", text="\n".join(titles))
                    else:
                        item.update(state="error", text=str((content or {}).get("error_code", "error")))
                    await sess.emit({**item, "type": "action_result"})


def _tool_result(tu: dict, content: Any, is_error: bool) -> dict:
    res: dict = {"type": "tool_result", "tool_use_id": tu["id"], "content": content}
    if tu.get("toolset_name"):
        res["toolset_name"] = tu["toolset_name"]
    if is_error:
        res["is_error"] = True
    return res


def _ui_input(inp: dict) -> dict:
    """Input dict trimmed for display."""
    out = {}
    for k, v in (inp or {}).items():
        if isinstance(v, str) and len(v) > 600:
            v = v[:600] + "…"
        out[k] = v
    return out


def _transcript_text(messages: list[dict], limit: int = 30_000) -> str:
    lines: list[str] = []
    for m in messages:
        role = m["role"]
        content = m["content"] if isinstance(m["content"], list) else [{"type": "text", "text": m["content"]}]
        for b in content:
            t = b.get("type")
            if t == "text" and b.get("text", "").strip():
                lines.append(f"{'USER' if role == 'user' else 'ASSISTANT'}: {b['text'].strip()}")
            elif t == "image":
                lines.append("USER: [attached an image]")
            elif t == "tool_use":
                inp = json.dumps(b.get("input", {}), ensure_ascii=False)
                lines.append(f"ASSISTANT ACTION: {b.get('name')} {inp[:300]}")
            elif t == "server_tool_use":
                lines.append(f"ASSISTANT WEB SEARCH: {json.dumps(b.get('input', {}), ensure_ascii=False)[:200]}")
            elif t == "tool_result":
                c = b.get("content")
                if isinstance(c, list):
                    c = " ".join(x.get("text", "[image]") if x.get("type") == "text" else "[screenshot]"
                                 for x in c)
                lines.append(f"TOOL RESULT{' (error)' if b.get('is_error') else ''}: {str(c)[:300]}")
    text = "\n".join(lines)
    if len(text) > limit:
        text = text[: limit // 3] + "\n...[middle omitted]...\n" + text[-(2 * limit) // 3:]
    return text
