"""Markdown notes.

Lecture notes written in Markdown have no pages, so this has to invent them.
Two conventions cover almost everything people actually write: a thematic break
(`---`) between slides, which is what Marp and reveal.js use, and otherwise the
top-level headings, which is how a set of notes is already divided. A file with
neither is one long page, which is correct -- inventing breaks in continuous
prose would put page boundaries in the middle of an argument.

Inline markup is stripped rather than preserved. What goes to the translator
should be the sentence, not the sentence with asterisks in it, and what comes
back is displayed as text.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..model import Box, Deck, Page, TextBlock

# A line of three or more -, * or _ on its own. The slide separator.
_BREAK = re.compile(r"^ {0,3}([-*_])(?: *\1){2,} *$")
_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*(\S*)")
_BULLET = re.compile(r"^(\s*)([-*+]|\d{1,3}[.)])\s+(.*)$")
_QUOTE = re.compile(r"^ {0,3}> ?(.*)$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_RULE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")

# Heading sizes in points, so the reader's drawn view and the title heuristic
# have something to go on. A document has no real type size.
_HEADING_PT = {1: 30.0, 2: 24.0, 3: 19.0, 4: 16.0, 5: 14.0, 6: 13.0}
_BODY_PT = 14.0


def parse(path: str) -> Deck:
    deck = Deck(source_path=path, source_format="md")
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    pages = _split_pages(lines)
    for index, page_lines in enumerate(pages):
        page = Page(index=index)
        _emit(page_lines, page)
        _lay_out(page)
        deck.pages.append(page)

    if not deck.pages:
        deck.pages.append(Page(index=0))
    if not any(page.blocks for page in deck.pages):
        deck.warnings.append("这个 Markdown 文件里没有可读的文字。")
    return deck


# -- paging -----------------------------------------------------------------

def _split_pages(lines: list[str]) -> list[list[str]]:
    """Thematic breaks if the file uses them, otherwise top-level headings."""
    fenced = _fenced_lines(lines)

    breaks = [i for i, line in enumerate(lines)
              if i not in fenced and _BREAK.match(line)
              # A break directly under text is a setext underline, not a rule.
              and not (i and lines[i - 1].strip() and lines[i][:1] == "-")]
    if breaks:
        pages, start = [], 0
        for at in breaks + [len(lines)]:
            chunk = lines[start:at]
            if any(line.strip() for line in chunk):
                pages.append(chunk)
            start = at + 1
        return pages or [lines]

    # Otherwise split at whichever heading level actually divides the file.
    for level in (1, 2):
        starts = [i for i, line in enumerate(lines)
                  if i not in fenced and _heading_level(line) == level]
        if len(starts) >= 2:
            if starts[0] > 0 and any(l.strip() for l in lines[:starts[0]]):
                starts = [0] + starts
            return [lines[a:b] for a, b in zip(starts, starts[1:] + [len(lines)])]

    return [lines]


def _fenced_lines(lines: list[str]) -> set[int]:
    """Line numbers inside a fenced code block, where nothing means anything."""
    inside: set[int] = set()
    fence: str | None = None
    for i, line in enumerate(lines):
        match = _FENCE.match(line)
        if fence is None and match:
            fence = match.group(1)[0] * 3
            inside.add(i)
        elif fence is not None:
            inside.add(i)
            if match and match.group(1).startswith(fence):
                fence = None
    return inside


def _heading_level(line: str) -> int:
    match = _HEADING.match(line)
    return len(match.group(1)) if match else 0


# -- blocks -----------------------------------------------------------------

def _emit(lines: list[str], page: Page) -> None:
    i, n = 0, len(lines)
    paragraph: list[str] = []

    def flush() -> None:
        nonlocal paragraph
        if paragraph:
            _add(page, " ".join(paragraph), font_pt=_BODY_PT)
            paragraph = []

    while i < n:
        line = lines[i]

        fence = _FENCE.match(line)
        if fence:
            flush()
            marker, i = fence.group(1)[0] * 3, i + 1
            body: list[str] = []
            while i < n and not (_FENCE.match(lines[i])
                                 and _FENCE.match(lines[i]).group(1).startswith(marker)):
                body.append(lines[i])
                i += 1
            i += 1  # the closing fence
            if any(l.strip() for l in body):
                _add(page, "\n".join(body).strip("\n"), mono=True, font_pt=12.0)
            continue

        heading = _HEADING.match(line)
        if heading:
            flush()
            level = len(heading.group(1))
            _add(page, _inline(heading.group(2)), font_pt=_HEADING_PT[level],
                 heading=level)
            i += 1
            continue

        if _TABLE_ROW.match(line):
            flush()
            i = _emit_table(lines, i, page)
            continue

        bullet = _BULLET.match(line)
        if bullet:
            flush()
            _add(page, _inline(bullet.group(3)), font_pt=_BODY_PT,
                 bullet=True, depth=len(bullet.group(1)) // 2)
            i += 1
            continue

        quote = _QUOTE.match(line)
        if quote:
            flush()
            if quote.group(1).strip():
                _add(page, _inline(quote.group(1)), font_pt=_BODY_PT)
            i += 1
            continue

        if line.strip():
            paragraph.append(_inline(line.strip()))
        else:
            flush()
        i += 1

    flush()


def _emit_table(lines: list[str], start: int, page: Page) -> int:
    """A table is a grid of terms. One block per cell, as the pptx parser does."""
    i, row = start, 0
    while i < len(lines) and _TABLE_ROW.match(lines[i]):
        if _TABLE_RULE.match(lines[i]):
            i += 1
            continue
        cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
        for column, cell in enumerate(cells):
            if cell:
                _add(page, _inline(cell), font_pt=_BODY_PT,
                     table_cell=(row, column))
        row += 1
        i += 1
    return i


def _add(page: Page, text: str, *, font_pt: float, mono: bool = False,
         bullet: bool = False, depth: int = 0, heading: int = 0,
         table_cell: tuple[int, int] | None = None) -> None:
    text = text.strip() if not mono else text
    if not text:
        return
    meta = {
        "shape_id": f"p{page.index}b{len(page.blocks)}",
        "placeholder": "",
        # Headings are structure the file states outright, so the classifier
        # takes them as titles rather than guessing from size and position.
        "is_title_placeholder": heading in (1, 2),
        "is_chrome_placeholder": False,
        "font_pt": font_pt,
        "mono": mono,
        "bullet": bullet,
        "bullet_depth": depth,
        "group_depth": 0,
        "has_math": False,
        "math_only": False,
    }
    if table_cell is not None:
        meta["table_cell"] = table_cell
    page.blocks.append(TextBlock(text=text, box=Box(0.08, 0.0, 0.84, 0.0),
                                 meta=meta))


def _lay_out(page: Page) -> None:
    """Stack the blocks down a nominal page.

    Markdown has no geometry, but the classifier reads positions -- a page
    number rule and a title heuristic both look at `y`. Spreading the blocks
    top to bottom in order keeps those reading what they were written for.
    """
    count = max(1, len(page.blocks))
    for i, block in enumerate(page.blocks):
        block.box = Box(0.08, min(0.95, i / count * 0.9 + 0.02), 0.84,
                        0.9 / count)


# -- inline markup ----------------------------------------------------------

_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_EMPHASIS = re.compile(r"(\*\*|__|\*|_)(?=\S)(.+?)(?<=\S)\1")
_CODE = re.compile(r"`([^`]+)`")


def _inline(text: str) -> str:
    """Markup out, words in.

    An image becomes its alt text: it is what the author wrote about the
    picture, and dropping it silently would lose a caption.
    """
    text = _IMAGE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _CODE.sub(r"\1", text)
    for _ in range(2):  # bold inside italics, and the other way round
        text = _EMPHASIS.sub(r"\2", text)
    return " ".join(text.split())
