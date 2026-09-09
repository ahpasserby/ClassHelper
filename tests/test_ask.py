"""The context a question carries.

The whole point of the ask panel is that the student does not have to explain
what they are reading before they can ask about it. So what matters here is
what ends up in the prompt: the sentences they picked, the slide they are on,
and -- when they ask for it -- the slides either side, because a definition
given on one page and used on the next is the ordinary case.
"""

from __future__ import annotations

import pytest

from classhelper.ask import Asker
from classhelper.config import Config
from classhelper.glossary import Glossary
from classhelper.model import BlockKind, Box, Deck, Page, Sentence, TextBlock


class CapturingProvider:
    """Answers nothing; keeps what it was asked."""

    name = "fake"

    def __init__(self):
        self.messages: list[dict] = []

    def complete(self, system, user, **kwargs):  # pragma: no cover
        raise AssertionError("questions are streamed")

    def stream(self, messages, **kwargs):
        self.messages = messages
        yield "ok"


def _sentence(text: str, translation: str | None = None) -> Sentence:
    return Sentence(id=text[:8], text=text, translation=translation)


def _deck(pages: int = 5) -> Deck:
    deck = Deck(source_path="/tmp/Lec02.pptx", source_format="pptx")
    for i in range(pages):
        title = TextBlock(text=f"Topic {i}", box=Box(0, 0, 1, 0.1),
                          kind=BlockKind.TITLE,
                          sentences=[_sentence(f"Topic {i}", f"主题 {i}")])
        body = TextBlock(text=f"Body of slide {i}.", box=Box(0, 0.2, 1, 0.2),
                         kind=BlockKind.BODY,
                         sentences=[_sentence(f"Body of slide {i}.", f"第 {i} 页正文。")])
        deck.pages.append(Page(index=i, blocks=[title, body]))
    return deck


@pytest.fixture
def asker(tmp_path):
    provider = CapturingProvider()
    cfg = Config(provider="fake", api_key="x", model="m", ask_model="m",
                 target_lang="zh-CN")
    made = Asker(provider, cfg, Glossary(tmp_path / "g.json"))
    return made, provider


def _ask(asker, deck, page, **kwargs) -> str:
    made, provider = asker
    list(made.stream(deck, deck.pages[page], "为什么？", **kwargs))
    return provider.messages[1]["content"]


def test_a_question_carries_the_slide_in_both_languages(asker):
    deck = _deck()
    context = _ask(asker, deck, 2)
    assert "Body of slide 2." in context
    assert "第 2 页正文。" in context
    assert "Body of slide 1." not in context, "one page unless asked otherwise"


def test_several_selected_sentences_are_numbered(asker):
    """"How do these two differ" is only answerable if they arrive separately."""
    deck = _deck()
    page = deck.pages[2]
    chosen = [page.blocks[0].sentences[0], page.blocks[1].sentences[0]]
    context = _ask(asker, deck, 2, sentences=chosen)

    assert "these 2 sentences" in context
    assert "1. Topic 2" in context
    assert "2. Body of slide 2." in context


def test_one_selected_sentence_is_not_numbered(asker):
    deck = _deck()
    context = _ask(asker, deck, 2, sentences=[deck.pages[2].blocks[1].sentences[0]])
    assert "this sentence" in context
    assert "1. Body of slide 2." not in context


def test_the_slides_the_student_picked_are_the_ones_sent(asker):
    """Not a window around the current page: a definition given on 1 and used
    on 4 is a pairing only the student knows about."""
    deck = _deck()
    context = _ask(asker, deck, 3, pages=[0, 3])

    assert "Body of slide 0." in context
    assert "Body of slide 3." in context
    assert "Body of slide 1." not in context
    assert "Body of slide 2." not in context
    # And it has to be clear which one they are actually reading.
    assert "(the slide being read)" in context
    assert context.count("--- Slide") == 2


def test_the_slide_being_read_is_always_included(asker):
    """The selected sentences are on it, so leaving it out would ask about
    something the model cannot see."""
    deck = _deck()
    context = _ask(asker, deck, 3, pages=[0])
    assert "Body of slide 3." in context
    assert "slides 1, 4" in context


def test_a_page_that_is_not_in_the_deck_is_ignored(asker):
    deck = _deck()
    context = _ask(asker, deck, 2, pages=[99, -1])
    assert "--- Slide" not in context, "one page, so no need to label them"
    assert "The full slide" in context


def test_no_choice_reads_as_it_always_did(asker):
    deck = _deck()
    context = _ask(asker, deck, 2, pages=[])
    assert "--- Slide" not in context
    assert "The full slide" in context


def test_furniture_is_not_sent(asker):
    deck = _deck(1)
    deck.pages[0].blocks.append(
        TextBlock(text="CS101 · Autumn", box=Box(0, 0.95, 1, 0.04),
                  kind=BlockKind.CHROME,
                  sentences=[_sentence("CS101 · Autumn")])
    )
    assert "CS101" not in _ask(asker, deck, 0)
