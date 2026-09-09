"""Translation cache.

Two jobs. The obvious one is not paying twice for the same sentence. The one
that matters more day to day is that reopening a deck you read yesterday is
instant instead of a progress bar.

Keyed on the sentence text plus the target language and model, not on the file,
so a definition repeated across three lectures is translated once, and changing
one slide does not invalidate the rest of the deck.

SQLite because the reader translates pages on several threads at once, and
because a JSON file rewritten on every sentence would be both slow and easy to
corrupt by quitting at the wrong moment.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from .config import state_dir

_SCHEMA = """
CREATE TABLE IF NOT EXISTS translations (
    key         TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    translation TEXT NOT NULL,
    created_at  REAL NOT NULL DEFAULT (julianday('now'))
);
"""


class Cache:
    def __init__(self, path: Path | None = None):
        self._path = path or (state_dir() / "cache" / "translations.db")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        # One connection per thread: sqlite3 objects are not shareable across
        # threads, and the page translator runs a small pool of them.
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._path, timeout=10.0)
            conn.execute("PRAGMA journal_mode=WAL")  # concurrent readers
            self._local.conn = conn
        return conn

    @staticmethod
    def key(text: str, target_lang: str, model: str) -> str:
        from .model import _hash
        return _hash(f"{model}\x00{target_lang}\x00{text}")

    def get(self, text: str, target_lang: str, model: str) -> str | None:
        row = self._connect().execute(
            "SELECT translation FROM translations WHERE key = ?",
            (self.key(text, target_lang, model),),
        ).fetchone()
        return row[0] if row else None

    def put(self, text: str, target_lang: str, model: str, translation: str) -> None:
        conn = self._connect()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO translations (key, source, translation) "
                "VALUES (?, ?, ?)",
                (self.key(text, target_lang, model), text, translation),
            )

    def drop(self, text: str, target_lang: str, model: str) -> None:
        """Forget one entry, so "retranslate this sentence" really re-asks."""
        conn = self._connect()
        with conn:
            conn.execute("DELETE FROM translations WHERE key = ?",
                         (self.key(text, target_lang, model),))

    def stats(self) -> int:
        return self._connect().execute(
            "SELECT COUNT(*) FROM translations").fetchone()[0]
