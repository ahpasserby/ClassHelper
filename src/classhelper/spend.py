"""What this has cost, in yuan.

A student paying per token deserves to know the number without doing arithmetic
on a token counter, and to be able to check it: every call is recorded with the
tokens it used *and* the unit prices it was billed at, so a total that looks
wrong can be traced to the call and the rate that produced it.

Two things this deliberately does not do. It does not estimate a price for a
model it has no price for -- those calls are counted and reported separately, so
the total is always a floor and never a fiction. And it does not try to be the
vendor's invoice: cache accounting and rounding are the vendor's, and the number
here is close enough to answer "can I afford to re-translate this deck", which
is the question actually being asked.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import state_dir
from .pricing import Price, Table

_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id          INTEGER PRIMARY KEY,
    ts          REAL    NOT NULL,
    provider    TEXT    NOT NULL,
    model       TEXT    NOT NULL,
    kind        TEXT    NOT NULL,
    prompt      INTEGER NOT NULL,
    cached      INTEGER NOT NULL,
    completion  INTEGER NOT NULL,
    cny         REAL    NOT NULL,
    priced      INTEGER NOT NULL,
    -- The rates actually used, so a total can be audited long after the
    -- vendor has changed its price list.
    rate_in     REAL    NOT NULL DEFAULT 0,
    rate_cached REAL    NOT NULL DEFAULT 0,
    rate_out    REAL    NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS calls_ts ON calls (ts);
"""


@dataclass
class Call:
    """One request's usage, as the provider reported it."""

    provider: str
    model: str
    kind: str  # "translate" | "ask" | "pricing"
    prompt: int
    cached: int
    completion: int
    ts: float = 0.0

    @property
    def uncached(self) -> int:
        """Input tokens billed at the full rate.

        `prompt` is the total the vendor reports, cache hits included, so the
        two must not simply be added -- doing so bills the cached half twice.
        """
        return max(0, self.prompt - self.cached)


class Ledger:
    def __init__(self, path: Path | None = None, table: Table | None = None):
        self._path = path or (state_dir() / "spend.db")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._table = table
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @property
    def table(self) -> Table:
        # Built lazily: the price table reads the state directory, which is not
        # known until the configuration has been loaded.
        if self._table is None:
            self._table = Table()
        return self._table

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._path, timeout=10.0)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    # -- writing -----------------------------------------------------------

    def record(self, call: Call) -> float:
        """Store one call and return what it cost in yuan (0 if unpriced)."""
        ts = call.ts or time.time()
        price = self.table.price(call.provider, call.model)
        cost, rates = 0.0, (0.0, 0.0, 0.0)
        priced = 0

        if price is not None:
            rate = self.table.rate_to_cny(price.currency)
            if rate is not None:
                now = price.at(ts)
                per_token = 1_000_000.0
                cost = rate * (
                    now.input * call.uncached
                    + now.cached_input * call.cached
                    + now.output * call.completion
                ) / per_token
                rates = (now.input * rate, now.cached_input * rate,
                         now.output * rate)
                priced = 1

        conn = self._connect()
        with conn:
            conn.execute(
                "INSERT INTO calls (ts, provider, model, kind, prompt, cached, "
                "completion, cny, priced, rate_in, rate_cached, rate_out) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (ts, call.provider, call.model, call.kind, call.prompt,
                 call.cached, call.completion, cost, priced, *rates),
            )
        return cost

    # -- reading -----------------------------------------------------------

    def reprice(self) -> int:
        """Cost the calls that had no price when they were made.

        A model's price arrives with the weekly check, which is usually after
        the first few hundred calls to it. Leaving those at zero forever would
        make the running total permanently short by exactly the amount the user
        most wants to know about.

        Only rows that were never priced are touched: a call already costed at
        the rate in force when it happened is history, and rewriting it at
        today's rate would be a different kind of wrong.
        """
        conn = self._connect()
        rows = conn.execute(
            "SELECT id, ts, provider, model, prompt, cached, completion "
            "FROM calls WHERE priced = 0"
        ).fetchall()
        fixed = 0
        for row_id, ts, provider, model, prompt, cached, completion in rows:
            price = self.table.price(provider, model)
            if price is None:
                continue
            rate = self.table.rate_to_cny(price.currency)
            if rate is None:
                continue
            now = price.at(ts)
            call = Call(provider, model, "", prompt, cached, completion, ts)
            cost = rate * (
                now.input * call.uncached
                + now.cached_input * cached
                + now.output * completion
            ) / 1_000_000.0
            with conn:
                conn.execute(
                    "UPDATE calls SET cny = ?, priced = 1, rate_in = ?, "
                    "rate_cached = ?, rate_out = ? WHERE id = ?",
                    (cost, now.input * rate, now.cached_input * rate,
                     now.output * rate, row_id),
                )
            fixed += 1
        return fixed

    def totals(self, provider: str | None = None) -> dict:
        conn = self._connect()
        now = time.localtime()
        month_start = time.mktime((now.tm_year, now.tm_mon, 1, 0, 0, 0, 0, 0, -1))
        day_start = time.mktime((now.tm_year, now.tm_mon, now.tm_mday,
                                 0, 0, 0, 0, 0, -1))

        def total(since: float | None = None) -> float:
            sql = "SELECT COALESCE(SUM(cny), 0) FROM calls"
            args: list = []
            if since is not None:
                sql += " WHERE ts >= ?"
                args.append(since)
            return float(conn.execute(sql, args).fetchone()[0])

        calls, tokens_in, tokens_out = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(prompt), 0), "
            "COALESCE(SUM(completion), 0) FROM calls"
        ).fetchone()
        unpriced = conn.execute(
            "SELECT COUNT(*) FROM calls WHERE priced = 0").fetchone()[0]
        unknown = [
            row[0] for row in conn.execute(
                "SELECT DISTINCT model FROM calls WHERE priced = 0 "
                "ORDER BY model LIMIT 8")
        ]
        first = conn.execute("SELECT MIN(ts) FROM calls").fetchone()[0]

        out = {
            "total": round(total(), 4),
            "month": round(total(month_start), 4),
            "today": round(total(day_start), 4),
            "calls": int(calls),
            "prompt_tokens": int(tokens_in),
            "completion_tokens": int(tokens_out),
            "unpriced_calls": int(unpriced),
            "unpriced_models": unknown,
            "since": first,
            "currency": "CNY",
        }
        if provider:
            out["pricing"] = self.table.status(provider)
        return out

    def by_model(self, limit: int = 12) -> list[dict]:
        """Where the money went. The answer is usually one model."""
        rows = self._connect().execute(
            "SELECT model, kind, COUNT(*), COALESCE(SUM(cny), 0), "
            "COALESCE(SUM(prompt), 0), COALESCE(SUM(completion), 0) "
            "FROM calls GROUP BY model, kind ORDER BY SUM(cny) DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {"model": r[0], "kind": r[1], "calls": r[2], "cny": round(r[3], 4),
             "prompt_tokens": r[4], "completion_tokens": r[5]}
            for r in rows
        ]

    def daily(self, days: int = 30) -> list[dict]:
        since = time.time() - days * 86400
        rows = self._connect().execute(
            "SELECT ts, cny FROM calls WHERE ts >= ? AND cny > 0", (since,)
        ).fetchall()
        buckets: dict[str, float] = {}
        for ts, cny in rows:
            day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            buckets[day] = buckets.get(day, 0.0) + cny
        return [{"day": d, "cny": round(v, 4)} for d, v in sorted(buckets.items())]


_ledger: Ledger | None = None
_ledger_lock = threading.Lock()


def ledger() -> Ledger:
    """The one ledger. Built on first use, after the config has been read."""
    global _ledger
    with _ledger_lock:
        if _ledger is None:
            _ledger = Ledger()
    return _ledger


def use_ledger(instance: Ledger | None) -> None:
    """Point the module at another ledger -- how tests keep out of real data."""
    global _ledger
    with _ledger_lock:
        _ledger = instance


def record(provider: str, model: str, kind: str, prompt: int, cached: int,
           completion: int) -> None:
    """Fire and forget. Accounting must never take down a translation."""
    try:
        ledger().record(Call(provider, model, kind, prompt, cached, completion))
    except Exception:  # noqa: BLE001
        pass
