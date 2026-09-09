"""Decide what each text block *is*, so the reader can lay the page out.

Design rule, and it is not negotiable: **when the evidence is weak, return
BODY.** Showing one extra line of navigation noise is a small annoyance;
hiding a line of the lecture is a silent failure the user cannot even detect.
Every rule below therefore demands positive evidence to move a block *out* of
the reading flow, never the other way round.

These heuristics were derived from real decks, but they run on strangers'
decks made in Keynote, Google Slides and LaTeX Beamer. So each rule leans on
structural facts (placeholder type, repetition, geometry) rather than on
anything specific to one course or one language, and `kind_reason` records
which rule fired so a bad call on an unfamiliar deck can be diagnosed instead
of just suffered.
"""

from __future__ import annotations

import re
from collections import defaultdict

from .model import BlockKind, Deck, Page, TextBlock

# A block must recur on at least this fraction of pages *at the same spot*
# before we will treat it as page furniture.
_CHROME_PAGE_RATIO = 0.5
_CHROME_MIN_PAGES = 3
# Navigation items and footers are short. A long paragraph that repeats is more
# likely a definition the lecturer restates on purpose, so leave it in.
_CHROME_MAX_CHARS = 80

# Reasons that come from the file's own structure rather than from a guess of
# ours. These are facts, and the self-check below must not overturn them.
_STRUCTURAL = "placeholder:"

_PAGE_NUMBER = re.compile(r"^[\d\s/–—-]{1,12}$")
_SENTENCE_END = re.compile(r"[.!?:;]\s*$")


def classify(deck: Deck) -> None:
    """Assign `kind` and `kind_reason` to every block in the deck, in place."""
    repeats = _repetition_index(deck)
    n_pages = max(len(deck.pages), 1)

    for page in deck.pages:
        for block in page.blocks:
            block.kind, block.kind_reason = _classify(block, page, repeats, n_pages)
        _rescue_page(page, deck)


def _repetition_index(deck: Deck) -> dict[tuple, set[int]]:
    """Map (normalised text, coarse position) -> the pages it appears on.

    Keying on position as well as text is what stops a genuine slide title from
    being mistaken for a navigation item that happens to use the same words.
    In the deck this was built against, "Design Process" is both a nav item
    pinned to the top strip *and* the real title of one slide; only position
    tells them apart.
    """
    index: dict[tuple, set[int]] = defaultdict(set)
    for page in deck.pages:
        for block in page.blocks:
            index[_key(block)].add(page.index)
    return index


def _key(block: TextBlock) -> tuple:
    # ~5% of page width/height per bucket: tolerant of nudged shapes, tight
    # enough that a top-strip nav item never collides with body text.
    return (
        block.text.strip().casefold(),
        round(block.box.x * 20),
        round(block.box.y * 20),
    )


def _classify(
    block: TextBlock, page: Page, repeats: dict[tuple, set[int]], n_pages: int
) -> tuple[BlockKind, str]:
    text = block.text.strip()
    meta = block.meta
    words = text.split()

    # -- Structural facts the file states outright. Trust these first. --------
    if meta.get("is_chrome_placeholder"):
        return BlockKind.CHROME, f"placeholder:{meta.get('placeholder')}"

    # A title placeholder outranks the repetition rule below, so a real title is
    # never swallowed by a nav strip that reuses its wording.
    if meta.get("is_title_placeholder"):
        return BlockKind.TITLE, "placeholder:title"

    if _PAGE_NUMBER.match(text) and (block.box.y > 0.85 or block.box.y < 0.08):
        return BlockKind.CHROME, "page-number"

    # -- Repetition: the strongest evidence for furniture. --------------------
    seen_on = repeats.get(_key(block), set())
    threshold = max(_CHROME_MIN_PAGES, int(n_pages * _CHROME_PAGE_RATIO))
    if len(seen_on) >= threshold and len(text) <= _CHROME_MAX_CHARS:
        return BlockKind.CHROME, f"repeats on {len(seen_on)}/{n_pages} pages, same position"

    # -- Verbatim text. Checked before every prose rule: code that happens to
    # sit at the top of a page is still code, not a title. -------------------
    if meta.get("mono"):
        return BlockKind.CODE, "monospace font"

    # A line that is pure notation has no prose in it. Translating it can only
    # damage it, and paying to be told the symbols back is pointless.
    if meta.get("math_only"):
        return BlockKind.CODE, "equation only, no prose"

    # -- Table cells are a grid of terms, not prose. --------------------------
    if "table_cell" in meta:
        return BlockKind.FRAGMENT, "table-cell"

    # -- Title fallback, for decks that never mark placeholders (Beamer, PDF). -
    if _looks_like_title(block, page, words):
        return BlockKind.TITLE, "top of page, short, largest text"

    # -- Diagram innards. Only inside a group, only if genuinely word-like. ---
    # Being inside a group means the author bound this text to a drawing; a bare
    # noun there is a node label ("Student", "ID"), not a sentence.
    if meta.get("group_depth", 0) > 0:
        if len(words) <= 4 and not _SENTENCE_END.search(text):
            return BlockKind.FRAGMENT, "short label inside a grouped drawing"
        return BlockKind.CAPTION, "text inside a grouped drawing"

    # -- Small standalone box sitting on a figure: an annotation. -------------
    if (
        not meta.get("placeholder")
        and len(words) <= 12
        and not _SENTENCE_END.search(text)
        and _overlaps_image(block, page)
    ):
        return BlockKind.CAPTION, "short unpunctuated box overlapping a figure"

    # Everything else is the lecture.
    return BlockKind.BODY, "default"


def _looks_like_title(block: TextBlock, page: Page, words: list[str]) -> bool:
    """Title heuristic for decks with no placeholder metadata.

    Deliberately strict, and it abstains entirely when font sizes are unknown
    (pptx runs usually inherit their size from the layout, so `font_pt` is 0
    more often than not). A block wrongly called a title still shows up in the
    reading flow, so the cost of abstaining is nothing.
    """
    if block.box.y > 0.22 or len(words) > 12 or _SENTENCE_END.search(block.text):
        return False
    if any(b.kind is BlockKind.TITLE for b in page.blocks):
        return False
    size = block.meta.get("font_pt", 0.0)
    if not size:
        return False
    return size >= max(
        (b.meta.get("font_pt", 0.0) for b in page.blocks if b is not block),
        default=0.0,
    )


def _overlaps_image(block: TextBlock, page: Page) -> bool:
    for img in page.images:
        a, b = block.box, img.box
        if (
            a.x < b.x + b.w
            and b.x < a.x + a.w
            and a.y < b.y + b.h
            and b.y < a.y + a.h
        ):
            return True
    return False


def _rescue_page(page: Page, deck: Deck) -> None:
    """Self-check: a page that has text but nothing to read means we blundered.

    This is the guard that keeps a bad heuristic from showing the user a blank
    page on a deck whose conventions we have never seen. Rather than trusting
    the rules, we notice the impossible outcome and undo it.
    """
    if not page.blocks or page.reading_blocks():
        return

    # Only undo our own inferences. When PowerPoint states outright that a shape
    # is the footer or the slide number, that is not a guess to second-guess --
    # a slide holding nothing but a footer really is a blank or divider slide,
    # and "restoring" it would print page furniture as if it were the lecture.
    restored = 0
    for block in page.blocks:
        if block.kind is BlockKind.CHROME and not block.kind_reason.startswith(_STRUCTURAL):
            block.kind = BlockKind.BODY
            block.kind_reason = "restored: page would otherwise have no content"
            restored += 1

    if restored:
        deck.warnings.append(
            f"第 {page.index + 1} 页的内容无法可靠区分正文与版面装饰，已全部显示。"
        )
