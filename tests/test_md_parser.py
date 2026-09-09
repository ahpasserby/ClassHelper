"""Markdown notes.

Markdown has no pages, so the interesting decisions here are all about where a
page starts and about not letting the formatting reach the translator.
"""

from __future__ import annotations

import pytest

from classhelper.classify import classify
from classhelper.model import BlockKind
from classhelper.parsers import parse


@pytest.fixture
def note(tmp_path):
    def make(text: str, name: str = "notes.md"):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return parse(str(path))
    return make


def _texts(page):
    return [b.text for b in page.blocks]


def test_a_thematic_break_starts_a_new_page(note):
    """The convention Marp and reveal.js use, so a deck written for either
    opens as the deck it was written as."""
    deck = note("# One\n\nFirst.\n\n---\n\n# Two\n\nSecond.\n")
    assert len(deck.pages) == 2
    assert "First." in _texts(deck.pages[0])
    assert "Second." in _texts(deck.pages[1])


def test_headings_divide_a_file_that_has_no_breaks(note):
    deck = note("# A\n\nOne.\n\n# B\n\nTwo.\n\n# C\n\nThree.\n")
    assert len(deck.pages) == 3


def test_a_preamble_before_the_first_heading_is_kept(note):
    """It is the first thing in the file; losing it would be silent."""
    deck = note("Before any heading.\n\n# A\n\nOne.\n\n# B\n\nTwo.\n")
    assert "Before any heading." in _texts(deck.pages[0])


def test_continuous_prose_is_one_page(note):
    """No marked divisions, so inventing them would put a page boundary in the
    middle of an argument."""
    deck = note("Just some notes.\n\nAnd a second paragraph.\n")
    assert len(deck.pages) == 1
    assert len(deck.pages[0].blocks) == 2


def test_a_rule_inside_a_code_block_is_not_a_page_break(note):
    deck = note("# One\n\n```\n---\n```\n\nStill page one.\n")
    assert len(deck.pages) == 1


def test_a_fenced_block_is_code_and_keeps_its_line_breaks(note):
    deck = note("# T\n\n```c\nint main(void) {\n    return 0;\n}\n```\n")
    classify(deck)
    code = [b for b in deck.pages[0].blocks if b.kind is BlockKind.CODE]
    assert len(code) == 1
    assert code[0].text == "int main(void) {\n    return 0;\n}"
    assert not code[0].kind.worth_translating


def test_a_heading_is_a_title_without_guessing(note):
    deck = note("# Entity Sets\n\nAn entity is a thing.\n")
    classify(deck)
    assert deck.pages[0].blocks[0].kind is BlockKind.TITLE
    assert deck.pages[0].blocks[0].kind_reason == "placeholder:title"


def test_a_paragraph_is_joined_but_a_list_is_not(note):
    """Wrapped prose is one sentence in two lines; bullets are five things."""
    deck = note("Line one\nline two.\n\n- first\n- second\n")
    texts = _texts(deck.pages[0])
    assert "Line one line two." in texts
    assert "first" in texts and "second" in texts
    bullets = [b for b in deck.pages[0].blocks if b.meta["bullet"]]
    assert len(bullets) == 2


def test_inline_markup_never_reaches_the_translator(note):
    deck = note("A **bold** word, some `code`, and a [link](http://x).\n")
    assert _texts(deck.pages[0]) == ["A bold word, some code, and a link."]


def test_an_image_keeps_its_alt_text(note):
    """It is what the author wrote about the picture."""
    deck = note("![The E-R diagram](er.png)\n")
    assert _texts(deck.pages[0]) == ["The E-R diagram"]


def test_a_table_becomes_cells_not_prose(note):
    deck = note("| Term | Meaning |\n| --- | --- |\n| key | 键 |\n")
    classify(deck)
    kinds = {b.kind for b in deck.pages[0].blocks}
    assert kinds == {BlockKind.FRAGMENT}
    assert "Term" in _texts(deck.pages[0])


def test_an_empty_file_opens_rather_than_raising(note):
    deck = note("")
    assert len(deck.pages) == 1
    assert deck.warnings
