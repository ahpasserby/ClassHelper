"""Translation orchestration, against a fake provider.

No network and no API key: these test the parts that go wrong in practice --
alignment, cache invalidation, failure handling -- none of which need a real
model to exercise.
"""

from __future__ import annotations

import json

import pytest

from classhelper.cache import Cache
from classhelper.classify import classify
from classhelper.config import Config
from classhelper.glossary import Glossary
from classhelper.model import BlockKind, Box, Deck, Page, TextBlock
from classhelper.providers.base import ProviderError, Usage
from classhelper.translate import Translator


class FakeProvider:
    """Returns a scripted reply per call, and records what it was asked."""

    name = "fake"

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.usage = Usage()

    def complete(self, system, user, **kwargs):
        self.prompts.append(user)
        if not self.replies:
            raise ProviderError("no more scripted replies")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return json.dumps(reply)


@pytest.fixture
def setup(tmp_path):
    def build(replies, texts=("The key is unique.", "A relationship set links entities.")):
        page = Page(index=0, blocks=[
            TextBlock(text=t, box=Box(0.1, 0.2 + i * 0.1, 0.8, 0.08))
            for i, t in enumerate(texts)
        ])
        deck = Deck(source_path=str(tmp_path / "d.pptx"), source_format="pptx",
                    pages=[page])
        classify(deck)
        provider = FakeProvider(replies)
        translator = Translator(
            provider,
            Config(provider="fake", api_key="x", model="m", target_lang="zh-CN"),
            Glossary(tmp_path / "g.json"),
            Cache(tmp_path / "c.db"),
        )
        return deck, page, provider, translator

    return build


def test_translates_every_unit(setup):
    deck, page, _, tr = setup([{"units": {"1": "甲", "2": "乙"}}])
    result = tr.translate_page(deck, page)
    assert result.translated == 2 and result.failed == 0
    assert [s.translation for b in page.blocks for s in b.sentences] == ["甲", "乙"]


def test_a_missing_unit_is_re_requested_not_guessed(setup):
    """The failure that silently shifts every later line on the page."""
    deck, page, provider, tr = setup([
        {"units": {"1": "甲"}},          # unit 2 dropped
        {"units": {"2": "乙"}},          # re-request keeps the original id
    ])
    result = tr.translate_page(deck, page)
    assert result.translated == 2 and result.failed == 0
    assert len(provider.prompts) == 2
    assert "A relationship set links entities." in provider.prompts[1]


def test_ids_are_stable_across_a_retry(setup):
    """Renumbering on retry would make a reply ambiguous if it arrived late."""
    deck, page, provider, tr = setup([
        {"units": {"1": "甲"}},
        {"units": {"2": "乙"}},
    ])
    tr.translate_page(deck, page)
    assert "[2]" in provider.prompts[1]


def test_a_unit_that_never_returns_is_reported_as_failed(setup):
    deck, page, _, tr = setup([{"units": {"1": "甲"}}] * 3)
    result = tr.translate_page(deck, page)
    assert result.failed == 1
    missing = [s for b in page.blocks for s in b.sentences if s.translation is None]
    assert len(missing) == 1 and missing[0].flagged


def test_provider_failure_does_not_invent_translations(setup):
    deck, page, _, tr = setup([ProviderError("upstream is down")])
    result = tr.translate_page(deck, page)
    assert result.failed == 2
    assert all(s.translation is None for b in page.blocks for s in b.sentences)
    assert result.errors


def test_second_pass_is_served_from_cache(setup):
    deck, page, provider, tr = setup([{"units": {"1": "甲", "2": "乙"}}])
    tr.translate_page(deck, page)
    for block in page.blocks:
        for sentence in block.sentences:
            sentence.translation = None
    result = tr.translate_page(deck, page)
    assert result.from_cache == 2 and len(provider.prompts) == 1


def test_locking_a_term_invalidates_the_sentences_that_used_it(setup):
    """Regression: the cache served the old wording straight back, so correcting
    a term appeared to do nothing at all."""
    deck, page, provider, tr = setup([
        {"units": {"1": "甲", "2": "关系集连接实体。"}},
        {"units": {"1": "联系集连接实体。"}},
    ])
    tr.translate_page(deck, page)
    tr.lock_term(deck, "relationship set", "联系集")

    texts = [s.translation for b in page.blocks for s in b.sentences]
    assert texts == ["甲", "联系集连接实体。"]
    assert len(provider.prompts) == 2


def test_locking_a_term_leaves_unrelated_sentences_alone(setup):
    deck, page, provider, tr = setup([
        {"units": {"1": "甲", "2": "乙"}},
        {"units": {"1": "丙"}},
    ])
    tr.translate_page(deck, page)
    tr.lock_term(deck, "relationship set", "联系集")
    # Only the sentence containing the term was re-asked.
    assert "The key is unique." not in provider.prompts[1].split("Translate these")[-1]


def test_word_boundaries_stop_a_term_matching_inside_another_word(setup):
    deck, page, provider, tr = setup(
        [{"units": {"1": "甲", "2": "乙"}}],
        texts=("The monkey escaped.", "Turkeys are birds."),
    )
    tr.translate_page(deck, page)
    results = tr.lock_term(deck, "key", "码")
    assert results == [], "no sentence contains the standalone word 'key'"


def test_code_blocks_are_never_sent_for_translation(setup):
    deck, page, provider, tr = setup([{"units": {"1": "甲"}}])
    page.blocks[1].kind = BlockKind.CODE
    result = tr.translate_page(deck, page)
    assert result.translated == 1
    assert "A relationship set links entities." not in provider.prompts[0].split(
        "Translate these")[-1]


def test_the_glossary_is_sent_with_later_requests(setup):
    deck, page, provider, tr = setup([
        {"units": {"1": "甲", "2": "乙"}, "glossary": {"entity": "实体"}},
        {"units": {"1": "丙"}},
    ])
    tr.translate_page(deck, page)
    tr.lock_term(deck, "key", "码")
    assert "key -> 码" in provider.prompts[1]
    assert "entity -> 实体" in provider.prompts[1]
