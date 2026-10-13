"""Projects and chats on disk (data/projects, data/chats).

A chat file holds the exact API transcript plus its frozen system prompt and
tool list, so reopening a chat sends byte-identical input and hits the prompt
cache. A small .meta.json beside it keeps the sidebar listing fast.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

_ID = re.compile(r"^[a-f0-9]{8,32}$")
_lock = threading.Lock()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


class Store:
    def __init__(self, root: Path):
        self.projects_dir = Path(root) / "projects"
        self.chats_dir = Path(root) / "chats"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.chats_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _check(id_: str) -> str:
        if not isinstance(id_, str) or not _ID.match(id_):
            raise ValueError("bad id")
        return id_

    # -------------------------------------------------------------- projects
    def list_projects(self) -> list[dict]:
        out = []
        for f in self.projects_dir.glob("*.json"):
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(out, key=lambda p: p.get("name", "").lower())

    def get_project(self, pid: Optional[str]) -> Optional[dict]:
        if not pid:
            return None
        f = self.projects_dir / f"{self._check(pid)}.json"
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def save_project(self, project: dict) -> dict:
        project.setdefault("id", new_id())
        project.setdefault("created", time.time())
        project.setdefault("folders", [])
        project.setdefault("instructions", "")
        project["updated"] = time.time()
        with _lock:
            _atomic_write(self.projects_dir / f"{self._check(project['id'])}.json", project)
        return project

    def delete_project(self, pid: str) -> None:
        """Deletes the project; its chats become random chats."""
        self._check(pid)
        for meta in self.list_chats():
            if meta.get("project_id") == pid:
                chat = self.load_chat(meta["id"])
                if chat:
                    chat["project_id"] = None
                    self.save_chat(chat)
        (self.projects_dir / f"{pid}.json").unlink(missing_ok=True)

    # ----------------------------------------------------------------- chats
    def list_chats(self) -> list[dict]:
        out = []
        for f in self.chats_dir.glob("*.meta.json"):
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(out, key=lambda c: c.get("updated", 0), reverse=True)

    def load_chat(self, cid: str) -> Optional[dict]:
        f = self.chats_dir / f"{self._check(cid)}.json"
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def save_chat(self, chat: dict) -> None:
        chat["updated"] = time.time()
        meta = {k: chat.get(k) for k in ("id", "project_id", "title", "created", "updated")}
        with _lock:
            _atomic_write(self.chats_dir / f"{self._check(chat['id'])}.json", chat)
            _atomic_write(self.chats_dir / f"{chat['id']}.meta.json", meta)

    def delete_chat(self, cid: str) -> None:
        self._check(cid)
        with _lock:
            (self.chats_dir / f"{cid}.json").unlink(missing_ok=True)
            (self.chats_dir / f"{cid}.meta.json").unlink(missing_ok=True)
