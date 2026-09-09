"""PDF -> Deck.

PDFs carry no notion of a paragraph: a page is a bag of positioned glyphs, and
PyMuPDF groups them into "blocks" that are close to paragraphs but not always.
The work here is turning lines back into prose:

* rejoining a paragraph that PyMuPDF handed back one line at a time -- common
  in LaTeX-produced PDFs, and left alone it guillotines sentences mid-clause,
* and *not* rejoining a bulleted list or a code listing, which arrive the same
  way: five bullets joined with spaces become one run-on sentence, and a seven
  line C program becomes one unreadable line with its indentation gone,
* repairing words split by an end-of-line hyphen,
* noticing a page that has no text layer at all, so the user is told their file
  is a scan rather than being shown a mysteriously empty reader.

Running headers, footers and page numbers are *not* handled here -- they are
repeated text at a fixed position, which is exactly what the shared classifier
already detects, and doing it in one place means the pptx and pdf paths behave
identically.
"""

from __future__ import annotations

import re

import pymupdf

from ..fonts import is_monospace
from ..model import Box, Deck, Image, Page, TextBlock

# "informa-\ntion" -> "information". Only when a lowercase letter precedes and
# follows, so hyphenated compounds ("well-known") and ranges survive intact.
_SOFT_HYPHEN = re.compile(r"([a-z])-\n([a-z])")

# A line that opens a list item. The marker is captured so the reader can lay
# the list out; the depth map is a convention rather than a measurement, since
# a PDF records the glyph and not the outline level it came from.
_BULLET = re.compile(
    r"^[\s\u00a0]*("
    r"[\u2022\u25cf\u25aa\u25e6\u2023\u2043\u00b7]"   # • ● ▪ ◦ ‣ ⁃ ·
    r"|>"                                                  # a sub-level in decks exported to PDF
    r"|[-\u2013\u2014](?=\s)"                             # - – —
    r"|\(?\d{1,2}[.)]"                                     # 1. 1)
    r"|\(?[a-zA-Z][.)](?=\s)"                              # a. a)
    r")[\s\u00a0]+"
)
_DEPTH = {"\u2022": 0, "\u25cf": 0, "\u00b7": 0}


def parse(path: str) -> Deck:
    deck = Deck(source_path=path, source_format="pdf")
    doc = pymupdf.open(path)
    pages_with_text = 0

    try:
        for i, pdf_page in enumerate(doc):
            pw = pdf_page.rect.width or 1.0
            phh = pdf_page.rect.height or 1.0
            page = Page(index=i, width=pw, height=phh)

            raw = pdf_page.get_text("dict")
            for blk in raw.get("blocks", []):
                if blk.get("type") != 0:  # 0 = text, 1 = image (handled below)
                    continue
                for text, bullet, level, size, mono in _block_segments(blk):
                    page.blocks.append(
                        TextBlock(
                            text=text,
                            box=_box(blk["bbox"], pw, phh),
                            meta={
                                "shape_id": f"p{i}b{blk.get('number', 0)}",
                                "font_pt": size,
                                "mono": mono,
                                "group_depth": 0,
                                "placeholder": "",
                                "bullet": bullet,
                                "bullet_depth": level,
                            },
                        )
                    )

            if page.blocks:
                pages_with_text += 1
            _extract_images(doc, pdf_page, page, pw, phh)
            page.blocks.sort(key=_reading_order)
            page.blocks = _merge_paragraphs(page.blocks)
            deck.pages.append(page)
    finally:
        doc.close()

    if deck.pages and pages_with_text == 0:
        deck.warnings.append(
            "这份 PDF 没有文字层，多半是扫描件。无法提取文字，也就无法翻译。"
        )
    elif deck.pages and pages_with_text < len(deck.pages) * 0.5:
        deck.warnings.append(
            f"{len(deck.pages)} 页中只有 {pages_with_text} 页能提取到文字，"
            "其余可能是扫描图片。"
        )
    return deck


def _block_segments(blk: dict) -> list[tuple[str, bool, int, float, bool]]:
    """Split one PyMuPDF block into paragraphs and list items.

    A block can hold a paragraph wrapped over several lines *or* a list with one
    item per line, and they are indistinguishable until you look at how the
    lines begin. Joining a list produces a single run-on sentence -- which is
    what the reader showed before this existed -- so a line opening with a
    bullet starts a new segment and lines that do not continue the current one.
    """
    text, size, mono = _block_text(blk)
    if not text:
        return []

    # A listing is one block whose lines matter; never split it on a leading
    # "-" or "*", which in code is an operator rather than a bullet.
    lines = _lines(blk)
    if mono or not any(_BULLET.match(line) for line in lines):
        return [(text, False, 0, size, mono)]

    segments: list[tuple[str, bool, int, float, bool]] = []
    current: list[str] = []
    marker: str | None = None

    def flush() -> None:
        if not current:
            return
        joined = " ".join(" ".join(current).split())
        if joined:
            segments.append(
                (joined, marker is not None, _DEPTH.get(marker or "", 1), size, mono)
            )

    for line in lines:
        match = _BULLET.match(line)
        if match:
            flush()
            marker = match.group(1)
            current = [line[match.end():]]
        else:
            current.append(line)
    flush()
    return segments


def _lines(blk: dict) -> list[str]:
    return [
        "".join(span.get("text", "") for span in line.get("spans", []))
        for line in blk.get("lines", [])
        if line.get("spans")
    ]


def _block_text(blk: dict) -> tuple[str, float, bool]:
    """Flatten a PyMuPDF block into one paragraph, plus its largest font size.

    Lines are joined with a space rather than a newline: within a block the line
    breaks are typographic wrapping, not meaning, and keeping them would make
    the sentence splitter break mid-clause.
    """
    lines: list[str] = []
    max_size = 0.0
    fonts: list[str] = []
    for line in blk.get("lines", []):
        spans = line.get("spans", [])
        if not spans:
            continue
        lines.append("".join(s.get("text", "") for s in spans))
        max_size = max(max_size, *(s.get("size", 0.0) for s in spans))
        fonts.extend(s.get("font", "") for s in spans if s.get("text", "").strip())

    mono = bool(fonts) and all(is_monospace(f) for f in fonts)

    if mono:
        # Code is shown as written. Collapsing it the way prose is collapsed
        # turns a seven line program into one line with its indentation gone,
        # which is the one thing the listing was on the slide to convey.
        return "\n".join(line.rstrip() for line in lines).strip("\n"), round(max_size, 1), True

    joined = _SOFT_HYPHEN.sub(r"\1\2", "\n".join(lines))
    return " ".join(joined.split()), round(max_size, 1), False


# A line that opens a list item is a new paragraph, never a continuation.
_LIST_MARKER = re.compile(r"^\s*(?:[\u2022\u25aa\u25e6\u2023\u2043*]|[-\u2013\u2014]\s|"
                          r"\(?[0-9]{1,2}[.)]|\(?[a-zA-Z][.)])\s")


def _merge_paragraphs(blocks: list[TextBlock]) -> list[TextBlock]:
    """Rejoin consecutive blocks that are really lines of one paragraph.

    PyMuPDF sometimes returns a whole paragraph as one block and sometimes one
    block per line, depending on how the PDF was produced. Left unmerged, a
    sentence is cut at the line break and each half is translated as if it were
    a complete thought, which produces confident nonsense.

    Merging is the safe direction here: the sentence splitter runs afterwards
    and will re-divide the joined text, so an over-eager merge costs nothing,
    while a missed merge corrupts the output.
    """
    out: list[TextBlock] = []
    for block in blocks:
        if out and _continues(out[-1], block):
            _absorb(out[-1], block)
        else:
            block.meta.setdefault("line_h", block.box.h)
            out.append(block)
    return out


def _continues(prev: TextBlock, cur: TextBlock) -> bool:
    """Is `cur` the next line of the paragraph `prev` started?"""
    if _LIST_MARKER.match(cur.text):
        return False

    # A list item is its own line by definition. The marker has already been
    # stripped by the time this runs, so the flag is the only evidence left --
    # without checking it, the bullets split apart a moment ago are glued back
    # together here.
    if prev.meta.get("bullet") or cur.meta.get("bullet"):
        return False

    # Verbatim text and prose never belong to the same block: merging a code
    # listing into the sentence above it would send the code to the translator.
    if prev.meta.get("mono") != cur.meta.get("mono"):
        return False

    # A size change means a heading met body text, or a footnote began.
    if abs(prev.meta.get("font_pt", 0.0) - cur.meta.get("font_pt", 0.0)) > 0.6:
        return False

    # Vertically adjacent: the gap must look like line spacing, not a paragraph
    # break or a jump to another part of the page.
    line_h = prev.meta.get("line_h") or prev.box.h or cur.box.h
    gap = cur.box.y - (prev.box.y + prev.box.h)
    if not (-0.5 * line_h <= gap <= 0.8 * line_h):
        return False

    # Same column: aligned to the same margin, or indented under it. A block
    # starting to the *left* of the previous one is a new column or a new list.
    if cur.box.x < prev.box.x - 0.02:
        return False

    return True


def _absorb(prev: TextBlock, cur: TextBlock) -> None:
    """Append `cur`'s text to `prev` and grow its box to cover both."""
    if prev.meta.get("mono") and cur.meta.get("mono"):
        # Two halves of one listing: they are separate lines, not a sentence
        # that happened to wrap.
        prev.text = f"{prev.text}\n{cur.text}"
    elif prev.text.endswith("-") and cur.text[:1].islower():
        prev.text = prev.text[:-1] + cur.text  # word broken across lines
    else:
        prev.text = f"{prev.text} {cur.text}"

    right = max(prev.box.x + prev.box.w, cur.box.x + cur.box.w)
    bottom = max(prev.box.y + prev.box.h, cur.box.y + cur.box.h)
    prev.box.x = min(prev.box.x, cur.box.x)
    prev.box.w = right - prev.box.x
    prev.box.h = bottom - prev.box.y
    prev.meta["merged_lines"] = prev.meta.get("merged_lines", 1) + 1


def _extract_images(doc, pdf_page, page: Page, pw: float, ph: float) -> None:
    """Pull embedded images with their on-page position.

    An xref can be placed more than once, and PyMuPDF may report no rect for an
    image referenced through a form XObject; both are skipped rather than
    guessed at, since a figure in the wrong place is worse than one absent.
    """
    seen: set[int] = set()
    for info in pdf_page.get_images(full=True):
        xref = info[0]
        if xref in seen:
            continue
        seen.add(xref)
        rects = pdf_page.get_image_rects(xref)
        if not rects:
            continue
        try:
            blob = doc.extract_image(xref)
        except (RuntimeError, ValueError):
            continue
        page.images.append(
            Image(
                box=_box(tuple(rects[0]), pw, ph),
                data=blob["image"],
                ext=blob["ext"],
            )
        )


def _box(bbox, pw: float, ph: float) -> Box:
    x0, y0, x1, y1 = bbox
    return Box(x=x0 / pw, y=y0 / ph, w=(x1 - x0) / pw, h=(y1 - y0) / ph)


def _reading_order(block: TextBlock):
    band = round(block.box.y * 40)
    return (band, block.box.x)
