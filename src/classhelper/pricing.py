"""What the models cost, in the user's own currency.

The program cannot know its own price list: a vendor changes prices whenever it
likes, and a table hardcoded here would quietly bill the user at last year's
rate. So the table is *fetched* -- the vendor's own pricing page, once a week --
and read by the model itself, which is the one thing on hand that can make sense
of an HTML page laid out for humans.

Three things keep that from being a licence to invent numbers:

* the page text comes from the vendor, over HTTPS, and is passed to the model
  verbatim. The model extracts; it is never asked to recall a price.
* every extracted number is bounds-checked, and anything that fails is dropped
  rather than rounded into something plausible.
* a model whose price is not known is *shown* as not known. A cost display that
  silently omits half the calls is worse than one that says it is incomplete.

Time-of-day pricing is real and worth honouring -- DeepSeek's peak rate is
double its off-peak one -- so a price carries both and the ledger picks by the
timestamp of the call.
"""

from __future__ import annotations

import json
import re
import ssl
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import state_dir
from .tls import create_context

# A week. Prices move on the scale of months; asking more often is spending the
# user's money to learn nothing.
MAX_AGE = 7 * 24 * 3600

# Where each vendor publishes its rates. The Chinese page is used for DeepSeek
# because the English one omits the table entirely.
PRICING_PAGES = {
    "deepseek": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing",
    "openai": "https://platform.openai.com/docs/pricing",
    "moonshot": "https://platform.moonshot.cn/docs/pricing/chat",
    "siliconflow": "https://cloud.siliconflow.cn/models",
}

# Native billing currency, so a price read off the page is not silently treated
# as yuan when it is dollars.
CURRENCIES = {
    "deepseek": "CNY",
    "openai": "USD",
    "moonshot": "CNY",
    "siliconflow": "CNY",
    "ollama": "CNY",  # local: free, and the table below says so
}

# Beijing time, since that is the timezone DeepSeek's peak window is defined in.
_BEIJING = timezone(timedelta(hours=8))

# No sane per-million-token price falls outside this. A model that returns 50000
# has misread a table, and a model that returns 0 would make the display lie.
_MIN_PRICE = 0.0
_MAX_PRICE = 10_000.0


@dataclass(frozen=True)
class Price:
    """Per million tokens, in `currency`, at the off-peak rate.

    `peak_multiplier` is what the vendor charges during its busy window; 1.0
    for the vendors that charge one rate around the clock.
    """

    input: float
    cached_input: float
    output: float
    currency: str = "CNY"
    peak_multiplier: float = 1.0

    def at(self, when: float) -> "Price":
        if self.peak_multiplier == 1.0 or not _is_peak(when):
            return self
        m = self.peak_multiplier
        return Price(self.input * m, self.cached_input * m, self.output * m,
                     self.currency, 1.0)


def _is_peak(when: float) -> bool:
    """DeepSeek's peak window: weekdays 09:00-12:00 and 14:00-18:00 Beijing."""
    t = datetime.fromtimestamp(when, _BEIJING)
    if t.weekday() >= 5:
        return False
    return 9 <= t.hour < 12 or 14 <= t.hour < 18


# Free by definition -- a local model costs electricity, not money, and putting
# a guessed number on the status bar would be worse than putting zero.
_BUILTIN: dict[str, dict[str, Price]] = {
    "ollama": {},
}


class Table:
    """The price list on disk, plus whatever is built in.

    Kept as one JSON file rather than rows in the ledger database: it is small,
    it is worth reading by eye when a number looks wrong, and it is exactly the
    thing a user might want to correct by hand.
    """

    def __init__(self, path: Path | None = None):
        self._path = path or (state_dir() / "pricing.json")
        self._lock = threading.Lock()
        self._mtime = -1.0
        self._data = self._read()

    def _read(self) -> dict:
        try:
            self._mtime = self._path.stat().st_mtime
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._mtime = -1.0
            return {"providers": {}, "fx": {}}

    def _fresh(self) -> dict:
        """Reload if the file changed underneath us.

        It is a small JSON file a user is invited to correct by hand, and the
        server can run for days -- read once at startup and a hand-fixed price
        would not take effect until the next launch.
        """
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            return self._data
        if mtime != self._mtime:
            with self._lock:
                self._data = self._read()
        return self._data

    def _write(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(f"{self._path.name}.{threading.get_ident():x}.tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(self._path)

    # -- reading -----------------------------------------------------------

    def price(self, provider: str, model: str) -> Price | None:
        builtin = _BUILTIN.get(provider, {})
        if model in builtin:
            return builtin[model]
        if provider in _BUILTIN and not builtin:
            # A provider explicitly listed as free, e.g. a local runtime.
            return Price(0.0, 0.0, 0.0, "CNY")

        entry = (self._fresh().get("providers", {}).get(provider, {})
                 .get("models", {}).get(model))
        if not entry:
            return None
        try:
            return Price(
                float(entry["input"]),
                float(entry.get("cached_input", entry["input"])),
                float(entry["output"]),
                str(entry.get("currency") or CURRENCIES.get(provider, "CNY")),
                float(entry.get("peak_multiplier", 1.0)),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def rate_to_cny(self, currency: str) -> float | None:
        if currency == "CNY":
            return 1.0
        rate = self._fresh().get("fx", {}).get(f"{currency}_CNY")
        try:
            rate = float(rate)
        except (TypeError, ValueError):
            return None
        return rate if rate > 0 else None

    def age(self, provider: str) -> float | None:
        """Seconds since this provider's prices were last fetched."""
        at = self._fresh().get("providers", {}).get(provider, {}).get("updated_at")
        return None if at is None else max(0.0, time.time() - float(at))

    def is_stale(self, provider: str, models: list[str]) -> bool:
        if provider in _BUILTIN:
            return False
        age = self.age(provider)
        if age is None or age > MAX_AGE:
            return True
        # A model the user has just switched to has no price yet, and waiting a
        # week to find that out would show them a wrong total all week.
        return any(self.price(provider, m) is None for m in models if m)

    def status(self, provider: str) -> dict:
        entry = self._fresh().get("providers", {}).get(provider, {})
        return {
            "updated_at": entry.get("updated_at"),
            "source": entry.get("source") or PRICING_PAGES.get(provider, ""),
            "note": entry.get("note", ""),
            "models": sorted((entry.get("models") or {}).keys()),
            "fx": self._fresh().get("fx", {}),
        }

    # -- writing -----------------------------------------------------------

    def put(self, provider: str, models: dict[str, dict], *,
            source: str, note: str = "") -> None:
        with self._lock:
            providers = self._data.setdefault("providers", {})
            entry = providers.setdefault(provider, {})
            # Merged, not replaced: one refresh that only resolved the two
            # models in use must not delete the prices of the others.
            entry.setdefault("models", {}).update(models)
            entry["updated_at"] = time.time()
            entry["source"] = source
            if note:
                entry["note"] = note
            self._write()
            self._mtime = self._path.stat().st_mtime

    def put_fx(self, pair: str, rate: float) -> None:
        with self._lock:
            self._data.setdefault("fx", {})[pair] = rate
            self._data["fx"]["updated_at"] = time.time()
            self._write()


# -- fetching --------------------------------------------------------------

_EXTRACT_SYSTEM = """\
You read a vendor's published pricing page and report what it says. You never \
supply a price from memory: if the page does not state it, you leave it out.

Reply with JSON only, in exactly this shape:

{"models": {"<model name>": {"input": <number>, "cached_input": <number>, \
"output": <number>, "peak_multiplier": <number>}}, "note": "<short note>"}

* Prices are per one million tokens, in the page's own currency.
* "input" is the price for input tokens that were NOT served from cache.
  "cached_input" is the price when the input was a cache hit. If the page does
  not distinguish them, use the same number for both.
* If the page gives an off-peak and a peak price, report the OFF-PEAK price and
  set "peak_multiplier" to peak divided by off-peak. Otherwise use 1.
* If a model name I ask about does not appear on the page but the page makes
  clear which listed model it is an alias of, use that model's prices and say
  so in "note".
* Omit any model you cannot price from this page. Omitting is correct; guessing
  is not.
* Write "note" in {language}. It is shown to the user as-is.\
"""


def fetch_page(url: str, ca_bundle: str | None = None, timeout: float = 20.0) -> str:
    """The pricing page as plain text.

    Tags are stripped rather than parsed. A vendor's page is a moving target and
    every selector written against one is a thing that breaks silently; the
    numbers survive in the text either way, and the model reads text.
    """
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(
        request, timeout=timeout, context=create_context(ca_bundle)
    ) as response:
        html = response.read().decode("utf-8", "replace")

    html = re.sub(r"(?is)<(script|style|nav|footer)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;?", " ", text)
    text = re.sub(r"&amp;", "&", text)
    return re.sub(r"\s+", " ", text).strip()


def _around_prices(text: str, budget: int = 8000) -> str:
    """The part of the page that talks about money.

    A docs page is mostly navigation. Sending all of it would cost more than
    the answer is worth and buries the table the model is meant to read.
    """
    if len(text) <= budget:
        return text
    hits = [m.start() for m in re.finditer(
        r"(元|USD|\$|price|Price|价格|百万|1M tokens|per million)", text)]
    if not hits:
        return text[:budget]
    start = max(0, hits[0] - 500)
    return text[start:start + budget]


def _sane(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not (_MIN_PRICE <= number <= _MAX_PRICE):
        return None
    return number


def parse_extraction(reply: str, currency: str) -> tuple[dict[str, dict], str]:
    """Validate what the model returned. Anything doubtful is dropped."""
    match = re.search(r"\{.*\}", reply, re.S)
    if not match:
        return {}, ""
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return {}, ""

    out: dict[str, dict] = {}
    for name, entry in (data.get("models") or {}).items():
        if not isinstance(entry, dict):
            continue
        inp = _sane(entry.get("input"))
        outp = _sane(entry.get("output"))
        if inp is None or outp is None:
            continue
        cached = _sane(entry.get("cached_input"))
        multiplier = _sane(entry.get("peak_multiplier")) or 1.0
        out[str(name)] = {
            "input": inp,
            "cached_input": inp if cached is None else cached,
            "output": outp,
            "peak_multiplier": multiplier if 1.0 <= multiplier <= 10.0 else 1.0,
            "currency": currency,
        }
    return out, str(data.get("note") or "")[:200]


def fetch_fx(ca_bundle: str | None = None) -> float | None:
    """USD to CNY. Two sources, because a free endpoint is a free endpoint."""
    context = create_context(ca_bundle)
    for url, pick in (
        ("https://api.frankfurter.app/latest?base=USD&symbols=CNY",
         lambda d: (d.get("rates") or {}).get("CNY")),
        ("https://open.er-api.com/v6/latest/USD",
         lambda d: (d.get("rates") or {}).get("CNY")),
    ):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "classhelper"})
            with urllib.request.urlopen(
                request, timeout=15, context=context
            ) as response:
                rate = pick(json.loads(response.read().decode("utf-8")))
            rate = float(rate)
            if 1.0 < rate < 100.0:
                return rate
        except (OSError, ValueError, TypeError, urllib.error.URLError):
            continue
    return None


# -- the weekly refresh ------------------------------------------------------

_refreshing: set[str] = set()
_refresh_lock = threading.Lock()


def refresh(table: Table, provider: str, models: list[str],
            ask, ca_bundle: str | None = None, language: str = "") -> bool:
    """Fetch the vendor's page and have `ask` read the prices out of it.

    `ask(system, user) -> str` is the model call, passed in rather than built
    here so this module stays independent of which provider is configured --
    and so tests can drive it without a network.
    """
    url = PRICING_PAGES.get(provider)
    if not url:
        return False
    try:
        text = fetch_page(url, ca_bundle)
    except (OSError, urllib.error.URLError, ValueError):
        return False
    if len(text) < 200:
        return False

    wanted = ", ".join(sorted({m for m in models if m}))
    question = (
        f"Vendor: {provider}\n"
        f"Models I am billed for: {wanted}\n\n"
        f"The pricing page, as text:\n\n{_around_prices(text)}\n\n"
        # Repeated here rather than only in the system prompt: an extraction
        # task in JSON mode reliably ignores a style instruction given once,
        # and this string is shown to the user in a Chinese interface.
        f"Reply with the JSON only. Write the \"note\" field in "
        f"{language or 'the language of the page'}."
    )
    # The note is shown in the reader, so it is written in the reader's
    # language -- which is configuration, not a constant. Substituted rather
    # than formatted: the prompt is full of literal JSON braces.
    system = _EXTRACT_SYSTEM.replace(
        "{language}", language or "the page's language")
    try:
        reply = ask(system, question)
    except Exception:  # noqa: BLE001 - a failed refresh is never fatal
        return False

    priced, note = parse_extraction(reply, CURRENCIES.get(provider, "CNY"))
    if not priced:
        return False
    table.put(provider, priced, source=url, note=note)

    if CURRENCIES.get(provider, "CNY") != "CNY":
        rate = fetch_fx(ca_bundle)
        if rate:
            table.put_fx("USD_CNY", rate)
    return True


def ensure_fresh(table: Table, provider: str, models: list[str],
                 ask, ca_bundle: str | None = None, after=None,
                 language: str = "") -> None:
    """Refresh in the background if the table is old, and never block on it.

    Called when a question is asked, which is the one moment the user is already
    waiting on the model and one more small request costs them nothing they can
    perceive. It must not delay the answer, and it must not fail loudly: a
    pricing display that cannot update is a stale number, not an error.
    """
    if not table.is_stale(provider, models):
        return
    with _refresh_lock:
        if provider in _refreshing:
            return
        _refreshing.add(provider)

    def run() -> None:
        try:
            if refresh(table, provider, models, ask, ca_bundle, language) and after:
                after()
        finally:
            with _refresh_lock:
                _refreshing.discard(provider)

    threading.Thread(target=run, daemon=True, name="pricing-refresh").start()
