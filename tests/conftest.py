import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# isolate data dir for every test session
os.environ.setdefault("POLYGLOT_DATA_DIR", tempfile.mkdtemp(prefix="polyglot-test-"))


def sse(blocks, stop_reason="end_turn", model="claude-opus-5-5", chunk=7):
    """Build an SSE byte stream equivalent to a Messages API streaming response."""
    out = []

    def ev(name, data):
        out.append(f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n")

    ev("message_start", {"type": "message_start", "message": {
        "id": "msg_test", "type": "message", "role": "assistant", "model": model, "content": [],
        "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 1, "cache_read_input_tokens": 0}}})
    ev("ping", {"type": "ping"})
    for i, b in enumerate(blocks):
        t = b["type"]
        if t == "text":
            ev("content_block_start", {"type": "content_block_start", "index": i,
                                       "content_block": {"type": "text", "text": "", "citations": None}})
            txt = b["text"]
            for j in range(0, len(txt), chunk):
                ev("content_block_delta", {"type": "content_block_delta", "index": i,
                                           "delta": {"type": "text_delta", "text": txt[j:j + chunk]}})
            for c in b.get("citations", []):
                ev("content_block_delta", {"type": "content_block_delta", "index": i,
                                           "delta": {"type": "citations_delta", "citation": c}})
        elif t == "thinking":
            ev("content_block_start", {"type": "content_block_start", "index": i,
                                       "content_block": {"type": "thinking", "thinking": "", "signature": ""}})
            ev("content_block_delta", {"type": "content_block_delta", "index": i,
                                       "delta": {"type": "thinking_delta", "thinking": b["thinking"]}})
            ev("content_block_delta", {"type": "content_block_delta", "index": i,
                                       "delta": {"type": "signature_delta", "signature": b["signature"]}})
        elif t == "tool_use":
            start = {"type": "tool_use", "id": b["id"], "name": b["name"], "input": {}}
            if "toolset_name" in b:
                start["toolset_name"] = b["toolset_name"]
            ev("content_block_start", {"type": "content_block_start", "index": i, "content_block": start})
            raw = json.dumps(b["input"])
            for j in range(0, len(raw), 5):
                ev("content_block_delta", {"type": "content_block_delta", "index": i,
                                           "delta": {"type": "input_json_delta", "partial_json": raw[j:j + 5]}})
        ev("content_block_stop", {"type": "content_block_stop", "index": i})
    ev("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                         "usage": {"output_tokens": 50}})
    ev("message_stop", {"type": "message_stop"})
    return "".join(out).encode()
