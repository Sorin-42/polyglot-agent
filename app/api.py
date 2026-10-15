"""Minimal async streaming client for the Anthropic Messages API.

We talk to the HTTP API directly (instead of the SDK's typed models) so that
every content block - thinking signatures, toolset_name on computer-use calls,
web-search encrypted content - is kept byte-for-byte as the API sent it and can
be passed back unchanged on the next request.
"""
from __future__ import annotations

import asyncio
import json
import random
from typing import Any, Awaitable, Callable, Optional

import httpx

API_VERSION = "2023-06-01"
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504, 529}

EventCallback = Callable[[str, int, Any], Awaitable[None]]


class APIError(Exception):
    def __init__(self, status: int, err_type: str, message: str):
        super().__init__(f"{status} {err_type}: {message}")
        self.status = status
        self.err_type = err_type
        self.message = message

    @property
    def retryable(self) -> bool:
        return self.status in RETRYABLE_STATUS or self.err_type in ("overloaded_error", "api_error")


class StreamAccumulator:
    """Rebuilds the final message from SSE events, keeping raw dict blocks."""

    def __init__(self) -> None:
        self.message: dict = {}
        self.content: list[dict] = []
        self._partial_json: dict[int, str] = {}
        self.done = False

    def handle(self, ev: dict) -> list[tuple[str, int, Any]]:
        """Apply one event. Returns simplified notifications for the UI."""
        t = ev.get("type")
        out: list[tuple[str, int, Any]] = []
        if t == "message_start":
            self.message = dict(ev["message"])
            self.content = list(self.message.get("content") or [])
        elif t == "content_block_start":
            idx = ev["index"]
            block = dict(ev["content_block"])
            while len(self.content) <= idx:
                self.content.append({})
            self.content[idx] = block
            if block.get("type") in ("tool_use", "server_tool_use"):
                self._partial_json[idx] = ""
            out.append(("block_start", idx, block))
        elif t == "content_block_delta":
            idx = ev["index"]
            d = ev["delta"]
            block = self.content[idx]
            dt = d.get("type")
            if dt == "text_delta":
                block["text"] = block.get("text", "") + d["text"]
                out.append(("text_delta", idx, d["text"]))
            elif dt == "thinking_delta":
                block["thinking"] = block.get("thinking", "") + d.get("thinking", "")
                if d.get("thinking"):
                    out.append(("thinking_delta", idx, d["thinking"]))
            elif dt == "signature_delta":
                block["signature"] = d["signature"]
            elif dt == "input_json_delta":
                self._partial_json[idx] = self._partial_json.get(idx, "") + d.get("partial_json", "")
            elif dt == "citations_delta":
                cits = block.get("citations") or []
                cits.append(d["citation"])
                block["citations"] = cits
            elif dt == "compaction_delta":
                # the whole summary arrives in one delta; keep its fields exactly as sent
                block.update({k: v for k, v in d.items() if k != "type"})
        elif t == "content_block_stop":
            idx = ev["index"]
            if idx in self._partial_json:
                raw = self._partial_json.pop(idx)
                block = self.content[idx]
                if raw.strip():
                    try:
                        block["input"] = json.loads(raw)
                    except json.JSONDecodeError:
                        block["input"] = {}
                        block["_invalid_input"] = raw  # stripped before resend
                elif "input" not in block:
                    block["input"] = {}
            out.append(("block_stop", idx, self.content[idx]))
        elif t == "message_delta":
            d = ev.get("delta", {})
            for k in ("stop_reason", "stop_sequence", "stop_details"):
                if k in d:
                    self.message[k] = d[k]
            if ev.get("usage"):
                usage = dict(self.message.get("usage") or {})
                usage.update({k: v for k, v in ev["usage"].items() if v is not None})
                self.message["usage"] = usage
        elif t == "message_stop":
            self.done = True
        elif t == "error":
            err = ev.get("error", {})
            raise APIError(529 if err.get("type") == "overloaded_error" else 500,
                           err.get("type", "error"), err.get("message", "stream error"))
        return out

    def final(self) -> dict:
        msg = dict(self.message)
        msg["content"] = [clean_block(b) for b in self.content if b]
        return msg


def clean_block(block: dict) -> dict:
    """Drop only our private markers; everything the API sent is kept exactly as
    received (including nulls), so re-sent history matches what was signed and
    cached byte for byte."""
    return {k: v for k, v in block.items() if not k.startswith("_")}


async def stream_message(
    http: httpx.AsyncClient,
    *,
    api_key: str,
    base_url: str,
    body: dict,
    betas: Optional[list[str]] = None,
    on_event: Optional[EventCallback] = None,
    on_retry: Optional[Callable[[int, str], Awaitable[None]]] = None,
    max_retries: int = 4,
) -> dict:
    """POST /v1/messages with stream=true; returns the assembled message dict."""
    if not api_key:
        raise APIError(401, "authentication_error", "No API key set. Open Settings and add your Anthropic API key.")
    headers = {
        "x-api-key": api_key,
        "anthropic-version": API_VERSION,
        "content-type": "application/json",
        "accept": "text/event-stream",
    }
    if betas:
        headers["anthropic-beta"] = ",".join(betas)
    url = base_url.rstrip("/") + "/v1/messages"
    payload = dict(body, stream=True)

    attempt = 0
    while True:
        acc = StreamAccumulator()
        try:
            async with http.stream("POST", url, headers=headers, json=payload,
                                   timeout=httpx.Timeout(30.0, read=600.0)) as resp:
                if resp.status_code != 200:
                    text = (await resp.aread()).decode("utf-8", "replace")
                    try:
                        err = json.loads(text).get("error", {})
                    except json.JSONDecodeError:
                        err = {"type": "http_error", "message": text[:500]}
                    e = APIError(resp.status_code, err.get("type", "error"), err.get("message", text[:500]))
                    e.retry_after = resp.headers.get("retry-after")  # type: ignore[attr-defined]
                    raise e
                data_lines: list[str] = []
                async for line in resp.aiter_lines():
                    if line == "":
                        if data_lines:
                            ev = json.loads("\n".join(data_lines))
                            data_lines = []
                            for note in acc.handle(ev):
                                if on_event:
                                    await on_event(*note)
                        continue
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                if data_lines:
                    for note in acc.handle(json.loads("\n".join(data_lines))):
                        if on_event:
                            await on_event(*note)
            if not acc.done and not acc.message.get("stop_reason"):
                raise APIError(502, "incomplete_stream", "Stream ended before message_stop")
            return acc.final()
        except (APIError, httpx.TransportError) as e:
            retryable = e.retryable if isinstance(e, APIError) else True
            attempt += 1
            if not retryable or attempt > max_retries:
                raise
            delay = min(30.0, (2 ** attempt) + random.random())
            ra = getattr(e, "retry_after", None)
            if ra:
                try:
                    delay = max(delay, float(ra))
                except ValueError:
                    pass
            if on_retry:
                await on_retry(attempt, str(e))
            await asyncio.sleep(delay)
