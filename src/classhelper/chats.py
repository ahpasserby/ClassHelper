"""Saved conversations, stored beside the deck they are about.

A question asked while reading slide 12 is part of studying that deck, not a
throwaway. Keeping the transcript in the deck's folder means it is still there
next week, it travels with the course folder, and it is a plain JSON file you
can read without this program.

One file per deck, holding all of its conversations. A deck accumulates a
handful over a term, not thousands, so a single file is simpler than a
directory of them and makes "list the conversations" one read.

The transcript format is assistant-ui's own exported repository shape --
{parentId, message} -- stored verbatim, so loading a conversation is handing it
straight back with no lossy translation in between.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Conversation:
    id: str
    title: str
    created: float
    updated: float
    messages: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "created": self.created,
            "updated": self.updated,
            "count": len(self.messages),
        }


class ChatStore:
    """The conversations for one deck."""

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()

    # -- reading -----------------------------------------------------------

    def _read(self) -> list[Conversation]:
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # A damaged transcript is an annoyance, not a reason to refuse to
            # open the deck it belongs to.
            return []
        return [
            Conversation(
                id=str(c.get("id", "")),
                title=str(c.get("title", "")),
                created=float(c.get("created", 0)),
                updated=float(c.get("updated", 0)),
                messages=list(c.get("messages") or []),
            )
            for c in raw.get("conversations", [])
            if isinstance(c, dict) and c.get("id")
        ]

    def list(self) -> list[dict]:
        return [
            c.summary()
            for c in sorted(self._read(), key=lambda c: c.updated, reverse=True)
        ]

    def get(self, conversation_id: str) -> Conversation | None:
        return next((c for c in self._read() if c.id == conversation_id), None)

    # -- writing -----------------------------------------------------------

    def create(self, title: str = "") -> Conversation:
        now = time.time()
        conversation = Conversation(
            id=uuid.uuid4().hex[:12],
            title=title or "新对话",
            created=now,
            updated=now,
        )
        self._mutate(lambda cs: cs.append(conversation))
        return conversation

    def append(self, conversation_id: str, item: dict) -> Conversation | None:
        """Add one message, creating the conversation if it is not there yet.

        An upsert rather than a failure: the reader appends as the exchange
        happens, and a transcript deleted in another window should not lose the
        answer being written right now.
        """
        result: list[Conversation] = []

        def change(conversations: list[Conversation]) -> None:
            conversation = next(
                (c for c in conversations if c.id == conversation_id), None
            )
            if conversation is None:
                now = time.time()
                conversation = Conversation(
                    id=conversation_id, title="新对话", created=now, updated=now
                )
                conversations.append(conversation)
            conversation.messages.append(item)
            conversation.updated = time.time()
            # Name it after the question that started it, so the list reads as
            # a list of questions rather than of timestamps.
            if conversation.title in ("", "新对话"):
                text = _text_of(item)
                if text and (item.get("message") or {}).get("role") == "user":
                    conversation.title = text[:40]
            result.append(conversation)

        self._mutate(change)
        return result[0] if result else None

    def rename(self, conversation_id: str, title: str) -> None:
        def change(conversations: list[Conversation]) -> None:
            for conversation in conversations:
                if conversation.id == conversation_id:
                    conversation.title = title.strip()[:60] or "新对话"

        self._mutate(change)

    def delete(self, conversation_id: str) -> None:
        def change(conversations: list[Conversation]) -> None:
            conversations[:] = [c for c in conversations if c.id != conversation_id]

        self._mutate(change)

    def _mutate(self, change) -> None:
        """Read, change, write -- under one lock, so two exchanges landing at
        the same moment cannot each overwrite the other's message."""
        with self._lock:
            conversations = self._read()
            change(conversations)
            payload = {"conversations": [asdict(c) for c in conversations]}
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(f"{self._path.name}.{os.getpid()}.tmp")
            try:
                tmp.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                tmp.replace(self._path)
            except OSError:
                tmp.unlink(missing_ok=True)
                raise


def _text_of(item: dict) -> str:
    message = item.get("message") or {}
    return " ".join(
        str(part.get("text", ""))
        for part in (message.get("content") or [])
        if isinstance(part, dict) and part.get("type") == "text"
    ).strip()


def for_deck(deck_path: str, meta_dir: Path | None) -> ChatStore:
    """Where a deck's conversations live: beside it when it is in the library.

    A deck opened from outside the library still gets a transcript, kept with
    the program's own files -- losing the conversation would be worse than
    storing it somewhere less tidy.
    """
    stem = Path(deck_path).stem
    if meta_dir is not None:
        return ChatStore(meta_dir / "chats" / f"{stem}.json")

    import hashlib

    from .config import state_dir

    digest = hashlib.sha256(str(Path(deck_path).resolve()).encode()).hexdigest()[:12]
    return ChatStore(state_dir() / "chats" / f"{stem}-{digest}.json")
