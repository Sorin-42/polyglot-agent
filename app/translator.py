"""Translation layer (Haiku by default).

user text --(to_english)--> English --> main agent --> English --(StreamingTranslation)--> user language

The main agent only ever sees English, so its reasoning, tool use and memory
stay in one language. Replies are translated paragraph-by-paragraph while the
agent is still writing, which keeps the conversation feeling live.

The user's language is always the one they chose in the app; it is never
detected from their text.
"""
from __future__ import annotations

import asyncio
import re
from typing import Awaitable, Callable, Optional

import httpx

from .api import APIError, stream_message
from .config import LANGUAGE_ENGLISH_NAME, Settings
from .prompts import from_english_system, to_english_system

UsageCallback = Callable[[str, dict], None]

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def english_name(language: str) -> str:
    return LANGUAGE_ENGLISH_NAME.get(language, language)


def is_pure_code(chunk: str) -> bool:
    """True if the chunk is exactly one fenced code block."""
    lines = chunk.strip("\n").split("\n")
    if len(lines) < 2:
        return False
    m = _FENCE.match(lines[0])
    if not m:
        return False
    fence = m.group(1)
    closing = lines[-1].strip()
    if not (closing and set(closing) == {fence[0]} and len(closing) >= len(fence)):
        return False
    return not any(_closes(fence, ln) for ln in lines[1:-1])


def _closes(fence: str, line: str) -> bool:
    s = line.strip()
    return bool(s) and set(s) == {fence[0]} and len(s) >= len(fence) and line.startswith(" " * 0) \
        and len(line) - len(line.lstrip(" ")) <= 3


class Translator:
    def __init__(self, http: httpx.AsyncClient, settings: Callable[[], Settings],
                 on_usage: Optional[UsageCallback] = None):
        self.http = http
        self.settings = settings
        self.on_usage = on_usage
        self._no_disable_thinking: set[str] = set()

    async def _call(self, system: str, user: str, max_tokens: int,
                    on_text: Optional[Callable[[str], Awaitable[None]]] = None) -> str:
        s = self.settings()
        model = s.translator_model
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {"effort": s.translator_effort},
        }
        if model not in self._no_disable_thinking:
            body["thinking"] = {"type": "disabled"}

        async def on_event(kind, idx, payload):
            if kind == "text_delta" and on_text:
                await on_text(payload)

        try:
            msg = await stream_message(self.http, api_key=s.effective_api_key(), base_url=s.base_url,
                                       body=body, on_event=on_event)
        except APIError as e:
            if e.status == 400 and "thinking" in e.message.lower() and "thinking" in body:
                self._no_disable_thinking.add(model)
                body.pop("thinking")
                msg = await stream_message(self.http, api_key=s.effective_api_key(),
                                           base_url=s.base_url, body=body, on_event=on_event)
            else:
                raise
        if self.on_usage:
            self.on_usage(model, msg.get("usage") or {})
        return "".join(b.get("text", "") for b in msg["content"] if b.get("type") == "text")

    # ------------------------------------------------------------ inbound
    async def to_english(self, text: str, language: str) -> str:
        """Translate from the user's chosen language (a UI label such as "简体中文")."""
        source = english_name(language)
        if source in ("", "English") or not re.search(r"[^\W\d_]", text):
            return text  # English, or nothing to translate (code, numbers, paths ...)
        raw = await self._call(to_english_system(source), f"<message>\n{text}\n</message>", 8000)
        m_en = re.search(r"<english>(.*?)(?:</english>|$)", raw, re.S)
        english = (m_en.group(1) if m_en else raw).strip()
        return english or text

    # ----------------------------------------------------------- outbound
    async def from_english(self, text: str, target: str,
                           on_delta: Optional[Callable[[str], Awaitable[None]]] = None,
                           context: str = "") -> str:
        ctx = f"<context>\n{context}\n</context>\n\n" if context else ""
        out = await self._call(from_english_system(target), f"{ctx}<message>\n{text}\n</message>",
                               16000, on_text=on_delta)
        return out.strip()


class StreamingTranslation:
    """Receives the agent's English text as it streams and translates it in
    paragraph-sized chunks, in order, emitting translated deltas as they come."""

    def __init__(self, translator: Translator, target: str,
                 on_delta: Callable[[str], Awaitable[None]], min_chunk: int = 300):
        self.translator = translator
        self.target = target
        self.on_delta = on_delta
        self.min_chunk = min_chunk
        self.pending = ""
        self.queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
        self.parts: list[str] = []
        self.error: Optional[Exception] = None
        self.task = asyncio.create_task(self._worker())

    # -- producer side
    def feed(self, text: str) -> None:
        self.pending += text
        cut = self._safe_cut()
        if cut:
            self.queue.put_nowait(self.pending[:cut])
            self.pending = self.pending[cut:]

    def finish(self) -> None:
        if self.pending.strip():
            self.queue.put_nowait(self.pending)
        self.pending = ""
        self.queue.put_nowait(None)

    def _safe_cut(self) -> int:
        """Index just after the last blank line outside a code fence, if the
        text before it is long enough to be worth a translation call."""
        if len(self.pending) < self.min_chunk:
            return 0
        pos, fence, best = 0, "", 0
        for line in self.pending.splitlines(keepends=True):
            if not line.endswith("\n"):
                break  # incomplete last line
            pos += len(line)
            body = line.rstrip("\r\n")
            if fence:
                if _closes(fence, body):
                    fence = ""
                    if pos >= self.min_chunk:
                        best = pos  # right after a closing fence
                continue
            m = _FENCE.match(body)
            if m:
                fence = m.group(1)
                continue
            if body.strip() == "" and pos >= self.min_chunk:
                best = pos
        return best

    # -- consumer side
    async def _worker(self) -> None:
        prev_en = ""
        while True:
            chunk = await self.queue.get()
            if chunk is None:
                return
            sep = "\n\n" if self.parts else ""
            if sep:
                await self.on_delta(sep)
            if is_pure_code(chunk) or self.error:
                out = chunk.strip("\n")
                await self.on_delta(out)
            else:
                try:
                    out = await self.translator.from_english(chunk, self.target, self.on_delta,
                                                             context=prev_en[-500:])
                except Exception as e:  # noqa: BLE001 - fall back to English
                    self.error = e
                    out = chunk.strip("\n")
                    await self.on_delta("\n" + out)
            self.parts.append(out)
            prev_en = chunk

    async def result(self) -> str:
        await self.task
        return "\n\n".join(self.parts)
