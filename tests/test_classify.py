"""Block classification.

The tests that matter most are the ones asserting we do *not* hide content:
a false CHROME is invisible to the user, so only a test can catch it.
"""

from classhelper.classify import classify
from classhelper.model import BlockKind, Box, Deck, Page, TextBlock


def _block(text, x=0.1, y=0.3, **meta):
    return TextBlock(text=text, box=Box(x, y, 0.8, 0.1), meta=meta)


def _deck(pages):
    return Deck(source_path="t.pptx", source_format="pptx", pages=pages)


def test_repeated_text_at_a_fixed_position_is_chrome():
    pages = [Page(index=i, blocks=[_block("Entity Sets", y=0.0),
                                   _block(f"Body sentence {i}.")])
             for i in range(8)]
    deck = _deck(pages)
    classify(deck)
    assert pages[0].blocks[0].kind is BlockKind.CHROME
    assert pages[0].blocks[1].kind is BlockKind.BODY


def test_title_placeholder_survives_a_navigation_strip_of_the_same_words():
    """The regression that motivated keying repetition on position as well as
    text: a nav item and a real slide title can be the same words."""
    pages = []
    for i in range(8):
        blocks = [_block("Design Process", y=0.0)]  # nav strip, every page
        if i == 4:
            blocks.append(_block("Design Process", y=0.06,
                                 is_title_placeholder=True))
        blocks.append(_block(f"Body sentence {i}."))
        pages.append(Page(index=i, blocks=blocks))
    deck = _deck(pages)
    classify(deck)
    assert pages[4].blocks[0].kind is BlockKind.CHROME
    assert pages[4].blocks[1].kind is BlockKind.TITLE


def test_repetition_across_too_few_pages_is_left_alone():
    """Two pages cannot establish a pattern. Better to show a header twice than
    to hide real content in a short document."""
    pages = [Page(index=i, blocks=[_block("Handout header", y=0.0)])
             for i in range(2)]
    deck = _deck(pages)
    classify(deck)
    assert all(p.blocks[0].kind is not BlockKind.CHROME for p in pages)


def test_long_repeated_text_stays_in_the_reading_flow():
    """A lecturer restating a definition on every slide is content, not furniture."""
    long_text = ("A relational schema is a description of the data that is "
                 "modelled in an application and its constraints.")
    pages = [Page(index=i, blocks=[_block(long_text, y=0.0)]) for i in range(8)]
    deck = _deck(pages)
    classify(deck)
    assert pages[0].blocks[0].kind is BlockKind.BODY


def test_short_label_inside_a_drawing_is_a_fragment():
    page = Page(index=0, blocks=[_block("Student", group_depth=1),
                                 _block("A full sentence of prose here.")])
    classify(_deck([page]))
    assert page.blocks[0].kind is BlockKind.FRAGMENT
    assert page.blocks[1].kind is BlockKind.BODY


def test_sentence_inside_a_drawing_is_a_caption_not_a_fragment():
    page = Page(index=0, blocks=[
        _block("This arrow shows the relationship.", group_depth=1)])
    classify(_deck([page]))
    assert page.blocks[0].kind is BlockKind.CAPTION


def test_monospace_text_is_code_and_is_not_translated():
    page = Page(index=0, blocks=[_block("SELECT * FROM student;", mono=True)])
    classify(_deck([page]))
    assert page.blocks[0].kind is BlockKind.CODE
    assert not page.blocks[0].kind.worth_translating
    assert page.blocks[0].kind.in_reading_flow  # shown, just never translated


def test_code_at_the_top_of_a_page_is_not_mistaken_for_a_title():
    page = Page(index=0, blocks=[_block("db.commit()", y=0.02, mono=True,
                                        font_pt=40.0)])
    classify(_deck([page]))
    assert page.blocks[0].kind is BlockKind.CODE


def test_footer_placeholder_is_chrome_on_sight():
    page = Page(index=0, blocks=[_block("CS101", is_chrome_placeholder=True,
                                        placeholder="FOOTER"),
                                 _block("Real content on the slide.")])
    classify(_deck([page]))
    assert page.blocks[0].kind is BlockKind.CHROME


def test_the_self_check_does_not_overturn_the_file_own_structure():
    """A slide whose only text is its footer is a divider slide. Printing
    "CS101" as though it were the lecture would be worse than showing nothing."""
    page = Page(index=0, blocks=[_block("CS101", is_chrome_placeholder=True,
                                        placeholder="FOOTER")])
    deck = _deck([page])
    classify(deck)
    assert page.blocks[0].kind is BlockKind.CHROME
    assert not deck.warnings


def test_page_number_at_the_bottom_is_chrome():
    page = Page(index=0, blocks=[_block("12", y=0.95),
                                 _block("Real content on the slide.")])
    classify(_deck([page]))
    assert page.blocks[0].kind is BlockKind.CHROME


def test_a_page_is_never_left_with_nothing_to_read():
    """The self-check. If every block on a page looks like furniture, the rules
    were wrong -- an empty page is not a possible correct answer."""
    pages = [Page(index=i, blocks=[_block("Repeated thing", y=0.0)])
             for i in range(8)]
    deck = _deck(pages)
    classify(deck)
    assert pages[0].reading_blocks(), "page was left blank"
    assert any("restored" in b.kind_reason for b in pages[0].blocks)
    assert deck.warnings


def test_the_glossary_survives_concurrent_saves(tmp_path):
    """Regression: pages translate on several threads and each saves when it
    finishes. A fixed temporary filename meant the first rename pulled the file
    out from under every other thread mid-write."""
    import threading

    from classhelper.glossary import Glossary

    glossary = Glossary(tmp_path / "g.json")
    errors: list[Exception] = []

    def worker(n: int) -> None:
        try:
            for i in range(25):
                glossary.observe({f"term{n}-{i}": f"译{n}-{i}"})
                glossary.save()
        except Exception as exc:  # noqa: BLE001 - the point is to catch any
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert len(Glossary(tmp_path / "g.json")) == 150, "the file was left corrupt"
