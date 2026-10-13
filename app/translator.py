"""Translation layer (Haiku by default).

user text --(to_english)--> English --> main agent --> English --(StreamingTranslation)--> user language

The main agent only ever sees English, so its reasoning, tool use and memory
stay in one language. Replies are translated paragraph-by-paragraph while the
agent is still writing, which keeps the conversation feeling live.
"""
from __future__ import annotations

import asyncio
import re
from typing import Awaitable, Callable, Optional

import httpx

from .api import APIError, stream_message
from .config import LANGUAGE_ENGLISH_NAME, Settings
from .prompts import TO_ENGLISH_SYSTEM, from_english_system

UsageCallback = Callable[[str, dict], None]

# English function words (strong signal) vs. function words of other Latin-script
# languages. Loanwords like "file"/"folder" and ambiguous short words ("a", "no",
# "in", "me") count for neither side.
_EN_WORDS = set("""the is are was were be been being to of and or on at for with from by this that these
those it its i you he she we they my your our their him her us them does did not yes can could would should
will shall may might must have has had what which who whom whose when where why how please there here just
about into than then because if while after before again only very some any each every all""".split())

_FOREIGN_WORDS = set("""de la el los las que se es por para con una del al lo le les des et est pas je tu
nous vous ist der das und nicht ich sie wir ein eine het een van ik niet zijn dat yang saya dan ini itu
tidak ke dari akan untuk che non per sono della il não você eu em um uma och jag att är på ett
bitte kannst können mein meine wie warum favor ayuda archivo carpeta abre abrir fichier dossier ouvre
donde dónde como cómo quiero puedes hacer esto esta mi hola gracias bonjour merci hallo danke ciao
obrigado voglio puoi questo kan jij wil apa bisa tolong buka""".split())

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def english_name(language: str) -> str:
    return LANGUAGE_ENGLISH_NAME.get(language, language)


def looks_english(text: str) -> bool:
    """Cheap heuristic so obviously-English input skips a translation call."""
    t = text.strip()
    if not t:
        return True
    if any(ord(c) > 127 for c in t if c.isalpha()):
        return False
    words = re.findall(r"[A-Za-z']+", t.lower())
    if not words:
        return True  # code, numbers, paths ...
    foreign = sum(1 for w in words if w in _FOREIGN_WORDS)
    hits = sum(1 for w in words if w in _EN_WORDS)
    if foreign and foreign >= hits:
        return False
    if len(words) <= 2:
        return True
    return hits >= 1 and hits / len(words) >= 0.15


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
    async def to_english(self, text: str, language: str) -> tuple[str, str]:
        """Returns (english_text, detected_language_name)."""
        if not text.strip():
            return text, english_name(language) if language != "auto" else "English"
        if language == "English":
            return text, "English"
        # In auto mode, skip the call for obviously-English input. With an explicit
        # non-English language, always translate (the heuristic can't be trusted
        # with short Latin-script messages).
        if language == "auto" and looks_english(text):
            return text, "English"
        if not re.search(r"[^\W\d_]", text):
            return text, english_name(language) if language != "auto" else "English"
        raw = await self._call(TO_ENGLISH_SYSTEM, f"<message>\n{text}\n</message>", 8000)
        m_lang = re.search(r"<lang>(.*?)</lang>", raw, re.S)
        m_en = re.search(r"<english>(.*?)(?:</english>|$)", raw, re.S)
        english = (m_en.group(1) if m_en else raw).strip()
        detected = (m_lang.group(1).strip() if m_lang else "") or "English"
        if language != "auto":
            detected = english_name(language)
        return english or text, detected

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
