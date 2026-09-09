"""Cost accounting.

The tests worth having here are the ones about *not* inventing a number: a
price the model made up, a cache hit billed twice, a model with no price
quietly counted as free. A total that is merely approximate is fine; one that
is confidently wrong is the failure mode.
"""

import time
from datetime import datetime, timedelta, timezone

import pytest

from classhelper.pricing import Price, Table, parse_extraction
from classhelper.providers.openai_compatible import _cached_tokens
from classhelper.spend import Call, Ledger

BEIJING = timezone(timedelta(hours=8))


def _table(tmp_path, **models):
    table = Table(tmp_path / "pricing.json")
    table.put("deepseek", models, source="test://prices")
    return table


def _deepseek(tmp_path):
    return _table(tmp_path, **{
        "deepseek-chat": {"input": 1.5, "cached_input": 0.05, "output": 4.5,
                          "peak_multiplier": 2.0, "currency": "CNY"},
    })


# -- what a call costs ------------------------------------------------------

def test_cache_hits_are_not_billed_at_the_full_rate(tmp_path):
    """`prompt_tokens` already includes the cached ones. Adding the two would
    bill the cached half twice, at thirty times its real price."""
    call = Call("deepseek", "deepseek-chat", "translate",
                prompt=1000, cached=800, completion=0)
    assert call.uncached == 200

    ledger = Ledger(tmp_path / "spend.db", table=_deepseek(tmp_path))
    off_peak = _at_hour(3)
    cost = ledger.record(Call("deepseek", "deepseek-chat", "translate",
                              1000, 800, 0, ts=off_peak))
    # 200 uncached at 1.5 + 800 cached at 0.05, per million.
    assert cost == pytest.approx((200 * 1.5 + 800 * 0.05) / 1e6)


def test_the_peak_rate_applies_during_the_vendors_busy_window(tmp_path):
    ledger = Ledger(tmp_path / "spend.db", table=_deepseek(tmp_path))
    quiet = ledger.record(Call("deepseek", "deepseek-chat", "translate",
                               0, 0, 1000, ts=_at_hour(3)))
    busy = ledger.record(Call("deepseek", "deepseek-chat", "translate",
                              0, 0, 1000, ts=_at_hour(10)))
    assert busy == pytest.approx(quiet * 2)


def test_the_weekend_is_off_peak(tmp_path):
    ledger = Ledger(tmp_path / "spend.db", table=_deepseek(tmp_path))
    weekday = ledger.record(Call("deepseek", "deepseek-chat", "t", 0, 0, 1000,
                                 ts=_at_hour(10)))
    saturday = ledger.record(Call("deepseek", "deepseek-chat", "t", 0, 0, 1000,
                                  ts=_at_hour(10, weekday=5)))
    assert saturday == pytest.approx(weekday / 2)


def test_a_model_with_no_price_is_reported_rather_than_counted_as_free(tmp_path):
    """The total has to be a floor, never a fiction. A call we cannot price is
    still recorded -- it happened, and the user should be told it is missing."""
    ledger = Ledger(tmp_path / "spend.db", table=_deepseek(tmp_path))
    assert ledger.record(Call("deepseek", "some-new-model", "ask",
                              5000, 0, 5000)) == 0.0
    totals = ledger.totals()
    assert totals["total"] == 0
    assert totals["unpriced_calls"] == 1
    assert totals["unpriced_models"] == ["some-new-model"]
    assert totals["prompt_tokens"] == 5000, "the tokens are still counted"


def test_a_price_in_dollars_needs_a_rate_before_it_becomes_yuan(tmp_path):
    table = Table(tmp_path / "pricing.json")
    table.put("openai", {"gpt-x": {"input": 1.0, "cached_input": 1.0,
                                   "output": 2.0, "currency": "USD"}},
              source="test://prices")
    ledger = Ledger(tmp_path / "spend.db", table=table)

    unconverted = ledger.record(Call("openai", "gpt-x", "ask", 1_000_000, 0, 0))
    assert unconverted == 0.0, "no rate yet, so no invented conversion"
    assert ledger.totals()["unpriced_calls"] == 1

    table.put_fx("USD_CNY", 7.0)
    assert ledger.record(
        Call("openai", "gpt-x", "ask", 1_000_000, 0, 0)) == pytest.approx(7.0)


def test_totals_split_the_month_and_the_day_out_of_the_running_total(tmp_path):
    ledger = Ledger(tmp_path / "spend.db", table=_deepseek(tmp_path))
    ledger.record(Call("deepseek", "deepseek-chat", "t", 0, 0, 1_000_000,
                       ts=time.time() - 400 * 86400))
    ledger.record(Call("deepseek", "deepseek-chat", "t", 0, 0, 1_000_000))
    totals = ledger.totals()
    assert totals["total"] > totals["month"] >= totals["today"] > 0


# -- reading a vendor's page ------------------------------------------------

def test_an_extracted_price_that_is_not_a_number_is_dropped():
    models, _ = parse_extraction(
        '{"models": {"a": {"input": "很便宜", "output": 1}, '
        '"b": {"input": 1, "output": 2}}}', "CNY")
    assert list(models) == ["b"]


def test_an_absurd_price_is_dropped_rather_than_believed():
    """A model that misreads a table returns a number, not an error. Nothing
    costs fifty thousand yuan per million tokens."""
    models, _ = parse_extraction(
        '{"models": {"a": {"input": 50000, "output": 1}}}', "CNY")
    assert models == {}


def test_a_reply_that_is_not_json_yields_nothing():
    assert parse_extraction("I could not find a price on that page.", "CNY") == ({}, "")


def test_a_missing_cached_price_falls_back_to_the_full_input_price():
    models, note = parse_extraction(
        '{"models": {"a": {"input": 2, "output": 8}}, "note": "别名"}', "CNY")
    assert models["a"]["cached_input"] == 2
    assert note == "别名"


def test_a_price_list_older_than_a_week_is_stale(tmp_path):
    table = _deepseek(tmp_path)
    assert not table.is_stale("deepseek", ["deepseek-chat"])
    # A model the user just switched to has no price, whatever the file's age.
    assert table.is_stale("deepseek", ["deepseek-chat", "deepseek-v4-pro"])


def test_refreshing_merges_rather_than_replaces(tmp_path):
    """A refresh that resolved only the two models in use must not delete the
    prices of the others."""
    table = _deepseek(tmp_path)
    table.put("deepseek", {"deepseek-v4-pro": {"input": 4.5, "output": 13.5}},
              source="test://prices")
    assert table.price("deepseek", "deepseek-chat") is not None
    assert table.price("deepseek", "deepseek-v4-pro") is not None


def test_a_local_provider_is_free_without_asking_anyone(tmp_path):
    table = Table(tmp_path / "pricing.json")
    assert table.price("ollama", "llama3") == Price(0.0, 0.0, 0.0, "CNY")
    assert not table.is_stale("ollama", ["llama3"])


# -- reading the meter ------------------------------------------------------

def test_cached_tokens_are_read_in_both_of_the_shapes_vendors_use():
    assert _cached_tokens({"prompt_cache_hit_tokens": 128}) == 128
    assert _cached_tokens({"prompt_tokens_details": {"cached_tokens": 64}}) == 64
    assert _cached_tokens({}) == 0
    assert _cached_tokens({"prompt_cache_hit_tokens": None}) == 0


def test_a_streamed_request_asks_for_its_usage_to_be_reported():
    """Without stream_options every answer the user asked for was billed to
    them and counted as free."""
    import inspect

    from classhelper.providers import openai_compatible

    source = inspect.getsource(openai_compatible.OpenAICompatibleProvider.stream)
    assert '"stream_options": {"include_usage": True}' in source


def _at_hour(hour: int, weekday: int = 0) -> float:
    """A timestamp at that Beijing hour on a chosen weekday (0 = Monday)."""
    base = datetime(2026, 9, 7, hour, 30, tzinfo=BEIJING)  # a Monday
    return (base + timedelta(days=weekday)).timestamp()


def test_calls_made_before_the_price_was_known_are_costed_afterwards(tmp_path):
    """The weekly check usually lands after the first few hundred calls. Leaving
    those at zero would make the total permanently short by the amount the user
    most wants to know about."""
    table = Table(tmp_path / "pricing.json")
    ledger = Ledger(tmp_path / "spend.db", table=table)

    ledger.record(Call("deepseek", "deepseek-chat", "translate", 0, 0, 1_000_000))
    assert ledger.totals()["unpriced_calls"] == 1

    table.put("deepseek", {"deepseek-chat": {"input": 1.5, "cached_input": 0.05,
                                             "output": 4.5, "currency": "CNY"}},
              source="test://prices")
    assert ledger.reprice() == 1
    totals = ledger.totals()
    assert totals["unpriced_calls"] == 0
    assert totals["total"] == pytest.approx(4.5)


def test_a_call_already_costed_is_not_rewritten_at_todays_rate(tmp_path):
    """History is history. Repricing is for what was never priced."""
    table = _deepseek(tmp_path)
    ledger = Ledger(tmp_path / "spend.db", table=table)
    before = ledger.record(Call("deepseek", "deepseek-chat", "t", 0, 0, 1_000_000,
                                ts=_at_hour(3)))

    table.put("deepseek", {"deepseek-chat": {"input": 99.0, "cached_input": 99.0,
                                             "output": 99.0, "currency": "CNY"}},
              source="test://prices")
    assert ledger.reprice() == 0
    assert ledger.totals()["total"] == pytest.approx(before)


def test_a_price_corrected_by_hand_takes_effect_without_a_restart(tmp_path):
    """pricing.json is a small file a user is invited to fix. The server can run
    for days, so it has to notice."""
    path = tmp_path / "pricing.json"
    table = _table(tmp_path, **{"deepseek-chat": {"input": 1.0, "output": 1.0}})
    assert table.price("deepseek", "deepseek-chat").input == 1.0

    edited = Table(path)  # stands in for a text editor
    edited.put("deepseek", {"deepseek-chat": {"input": 2.0, "output": 2.0}},
               source="hand")
    assert table.price("deepseek", "deepseek-chat").input == 2.0


def test_the_whole_refresh_runs_without_the_network(tmp_path, monkeypatch):
    """End to end with the fetch and the model faked out.

    The prompt is full of literal JSON braces, so the one placeholder in it is
    substituted rather than formatted -- `.format()` reads `{"models"` as a
    field name and raises, which killed the refresh thread in silence.
    """
    from classhelper import pricing

    page = "模型 deepseek-chat 价格 百万tokens输入 1.5元 输出 4.5元 " * 20
    monkeypatch.setattr(pricing, "fetch_page", lambda url, ca=None: page)

    seen: dict[str, str] = {}

    def ask(system: str, user: str) -> str:
        seen["system"], seen["user"] = system, user
        return ('{"models": {"deepseek-chat": {"input": 1.5, '
                '"cached_input": 0.05, "output": 4.5}}, "note": "按 flash 计价"}')

    table = Table(tmp_path / "pricing.json")
    assert pricing.refresh(table, "deepseek", ["deepseek-chat"], ask,
                           language="简体中文")

    assert "{language}" not in seen["system"], "the placeholder was left in"
    assert '{"models"' in seen["system"], "the JSON shape was mangled"
    assert "简体中文" in seen["system"]
    assert "deepseek-chat" in seen["user"]

    price = table.price("deepseek", "deepseek-chat")
    assert price is not None and price.input == 1.5 and price.cached_input == 0.05
    assert table.status("deepseek")["note"] == "按 flash 计价"


def test_a_provider_with_no_published_page_is_not_pretended_at(tmp_path):
    from classhelper import pricing

    table = Table(tmp_path / "pricing.json")

    def ask(system: str, user: str) -> str:  # pragma: no cover - must not run
        raise AssertionError("nothing to read, so nothing to ask")

    assert pricing.refresh(table, "some-vendor", ["m"], ask) is False
