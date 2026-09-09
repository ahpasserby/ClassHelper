"""Word documents.

Read straight out of the OOXML rather than through python-docx, for the same
reason `pptx_parser` walks the XML itself: `python-docx` returns paragraph text
by concatenating `w:t` runs, which silently drops OMML equations. A handout
where every formula has quietly vanished is worse than one that fails to open.

A document has no slides, so pages come from what the author actually marked:
explicit page breaks if there are any, otherwise the top-level headings. A file
with neither is one page, which is what it is.
"""

from __future__ import annotations

import unicodedata
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from ..model import Box, Deck, Image, Page, TextBlock

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_R = "http://schemas.openxmlformats.org/package/2006/relationships"

_MONO_FONTS = ("mono", "consol", "courier", "menlo", "monaco", "fixed")
# Word's own names for the styles that mean something here. Matched against the
# style's `w:name`, which is the canonical English one whatever the UI shows.
_BODY_PT = 11.0

# A4 in points, the fallback when the file does not say.
_DEFAULT_SIZE = (595.0, 842.0)


def parse(path: str) -> Deck:
    deck = Deck(source_path=path, source_format="docx")
    with zipfile.ZipFile(path) as zf:
        document = ET.fromstring(zf.read("word/document.xml"))
        styles = _style_names(zf)
        images = _images(zf)

    body = document.find(f"{{{_W}}}body")
    if body is None:
        deck.pages.append(Page(index=0))
        deck.warnings.append("这个 .docx 里没有正文。")
        return deck

    width, height = _page_size(body)
    blocks: list[tuple[TextBlock, bool]] = []  # block, starts a new page
    figures: list[Image] = []

    for child in body:
        tag = child.tag.split("}")[-1]
        if tag == "p":
            made = _paragraph(child, styles, len(blocks))
            if made is not None:
                blocks.append(made)
            figures.extend(_paragraph_images(child, images))
        elif tag == "tbl":
            blocks.extend(_table(child, styles, len(blocks)))

    deck.pages = _paginate(blocks, figures, width, height)
    if not deck.pages:
        deck.pages.append(Page(index=0, width=width, height=height))
        deck.warnings.append("这个 .docx 里没有可读的文字。")
    if images and not figures:
        deck.notes.append(f"{len(images)} 张图片在文档里，但没有找到它们的位置。")
    return deck


# -- the package ------------------------------------------------------------

def _style_names(zf: zipfile.ZipFile) -> dict[str, str]:
    """styleId -> the style's canonical name, lower-cased.

    Read rather than assumed: a document written in a localised Word names its
    heading styles in that language, and only `w:name` is stable.
    """
    try:
        root = ET.fromstring(zf.read("word/styles.xml"))
    except (KeyError, ET.ParseError):
        return {}
    out = {}
    for style in root.findall(f"{{{_W}}}style"):
        style_id = style.get(f"{{{_W}}}styleId")
        name = style.find(f"{{{_W}}}name")
        if style_id and name is not None:
            out[style_id] = (name.get(f"{{{_W}}}val") or "").strip().lower()
    return out


def _images(zf: zipfile.ZipFile) -> dict[str, Image]:
    """Relationship id -> the picture it points at."""
    try:
        rels = ET.fromstring(zf.read("word/_rels/document.xml.rels"))
    except (KeyError, ET.ParseError):
        return {}

    out: dict[str, Image] = {}
    for rel in rels.findall(f"{{{_PKG_R}}}Relationship"):
        target = rel.get("Target") or ""
        if "image" not in (rel.get("Type") or ""):
            continue
        name = "word/" + target.lstrip("/").replace("../", "")
        try:
            data = zf.read(name)
        except KeyError:
            continue
        ext = Path(name).suffix.lstrip(".").lower() or "png"
        # Word does not record where a picture sits on the page, so it is
        # placed centred and the reader shows it under the text as a figure.
        out[rel.get("Id") or ""] = Image(box=Box(0.15, 0.3, 0.7, 0.4),
                                         data=data, ext=ext)
    return out


def _page_size(body) -> tuple[float, float]:
    section = body.find(f"{{{_W}}}sectPr")
    size = section.find(f"{{{_W}}}pgSz") if section is not None else None
    if size is None:
        return _DEFAULT_SIZE
    try:  # twips, 1440 to the inch
        return (int(size.get(f"{{{_W}}}w")) / 20.0,
                int(size.get(f"{{{_W}}}h")) / 20.0)
    except (TypeError, ValueError):
        return _DEFAULT_SIZE


# -- paragraphs -------------------------------------------------------------

def _paragraph(para, styles: dict[str, str], index: int):
    text, has_math, math_only = _text_of(para)
    if not text.strip():
        return None

    properties = para.find(f"{{{_W}}}pPr")
    style = _style_of(properties, styles)
    heading = _heading_level(style)
    numbering = properties is not None and properties.find(f"{{{_W}}}numPr") is not None
    mono = _is_mono(para, style)

    block = TextBlock(
        text=text,
        box=Box(0.08, 0.0, 0.84, 0.0),
        meta={
            "shape_id": f"p{index}",
            "placeholder": "",
            "is_title_placeholder": heading in (1, 2) or style == "title",
            "is_chrome_placeholder": False,
            "font_pt": _font_pt(para, heading),
            "mono": mono,
            "bullet": numbering,
            "bullet_depth": _indent_level(properties),
            "group_depth": 0,
            "has_math": has_math,
            "math_only": math_only,
            "style": style,
        },
    )
    return block, _breaks_page(para, properties) or heading == 1


def _style_of(properties, styles: dict[str, str]) -> str:
    if properties is None:
        return ""
    style = properties.find(f"{{{_W}}}pStyle")
    if style is None:
        return ""
    style_id = style.get(f"{{{_W}}}val") or ""
    return styles.get(style_id, style_id.lower())


def _heading_level(style: str) -> int:
    """1-9 for a heading style, 0 otherwise."""
    if style.startswith("heading"):
        tail = style[len("heading"):].strip()
        if tail.isdigit():
            return int(tail)
    return 0


def _breaks_page(para, properties) -> bool:
    if properties is not None and properties.find(f"{{{_W}}}pageBreakBefore") is not None:
        return True
    for br in para.iter(f"{{{_W}}}br"):
        if br.get(f"{{{_W}}}type") == "page":
            return True
    return False


def _indent_level(properties) -> int:
    if properties is None:
        return 0
    numbering = properties.find(f"{{{_W}}}numPr")
    level = numbering.find(f"{{{_W}}}ilvl") if numbering is not None else None
    try:
        return int(level.get(f"{{{_W}}}val"))
    except (AttributeError, TypeError, ValueError):
        return 0


def _text_of(para) -> tuple[str, bool, bool]:
    """The paragraph's text, in document order, equations included.

    Walked rather than joined from `w:t`, so an `m:oMath` between two runs lands
    between them instead of disappearing -- the same hole this parser exists to
    avoid.
    """
    parts: list[str] = []
    math_chars = 0

    def walk(node) -> None:
        nonlocal math_chars
        for child in node:
            tag = child.tag
            if tag == f"{{{_M}}}oMath" or tag == f"{{{_M}}}oMathPara":
                rendered = _math_text(child)
                math_chars += len(rendered)
                parts.append(rendered)
                continue
            if tag == f"{{{_W}}}t":
                parts.append(child.text or "")
            elif tag == f"{{{_W}}}tab":
                parts.append(" ")
            elif tag == f"{{{_W}}}br":
                parts.append("\n")
            walk(child)

    walk(para)
    text = "".join(parts)
    collapsed = " ".join(text.split())
    letters = sum(1 for c in collapsed if c.isalpha())
    return collapsed, math_chars > 0, math_chars > 0 and math_chars >= letters


def _math_text(omath) -> str:
    """The readable text of an equation.

    Word stores maths in Cambria Math's Unicode alphanumerics, so a plain "x"
    arrives as MATHEMATICAL ITALIC SMALL X. NFKC folds those back to letters,
    which is what makes the result legible and translatable. Applied only here,
    never to the whole document.
    """
    raw = "".join(t.text or "" for t in omath.iter(f"{{{_M}}}t"))
    return unicodedata.normalize("NFKC", raw)


def _font_pt(para, heading: int) -> float:
    """Largest explicit run size, in points. Word stores half-points."""
    sizes = [
        int(sz.get(f"{{{_W}}}val")) / 2.0
        for sz in para.iter(f"{{{_W}}}sz")
        if (sz.get(f"{{{_W}}}val") or "").isdigit()
    ]
    if sizes:
        return max(sizes)
    # Nothing stated. A heading level is still a real fact about the document.
    return {1: 20.0, 2: 16.0, 3: 14.0}.get(heading, 0.0)


def _is_mono(para, style: str) -> bool:
    """Every run that names a font names a monospace one.

    Requiring all of them keeps an inline code span inside a sentence from
    turning the whole paragraph into an untranslated block.
    """
    if style in ("html preformatted", "code", "source code", "plain text"):
        return True
    names = [
        fonts.get(f"{{{_W}}}ascii") or ""
        for fonts in para.iter(f"{{{_W}}}rFonts")
        if fonts.get(f"{{{_W}}}ascii")
    ]
    if not names:
        return False
    return all(any(m in name.lower() for m in _MONO_FONTS) for name in names)


def _paragraph_images(para, images: dict[str, Image]) -> list[Image]:
    found = []
    for blip in para.iter(f"{{{_A}}}blip"):
        image = images.get(blip.get(f"{{{_R}}}embed") or "")
        if image is not None:
            found.append(image)
    return found


def _table(table, styles: dict[str, str], index: int):
    """One block per cell, so a grid of terms is not read as running prose."""
    made = []
    for r, row in enumerate(table.findall(f"{{{_W}}}tr")):
        for c, cell in enumerate(row.findall(f"{{{_W}}}tc")):
            text = " ".join(
                part for para in cell.findall(f"{{{_W}}}p")
                if (part := _text_of(para)[0])
            )
            if not text:
                continue
            made.append((
                TextBlock(
                    text=text,
                    box=Box(0.08, 0.0, 0.84, 0.0),
                    meta={
                        "shape_id": f"t{index}r{r}",
                        "placeholder": "",
                        "is_title_placeholder": False,
                        "is_chrome_placeholder": False,
                        "font_pt": _BODY_PT,
                        "mono": False,
                        "bullet": False,
                        "bullet_depth": 0,
                        "group_depth": 0,
                        "has_math": False,
                        "math_only": False,
                        "table_cell": (r, c),
                    },
                ),
                False,
            ))
    return made


# -- paging -----------------------------------------------------------------

def _paginate(blocks, figures: list[Image], width: float, height: float) -> list[Page]:
    if not blocks:
        return []

    pages: list[Page] = []
    current = Page(index=0, width=width, height=height)
    for block, starts_page in blocks:
        if starts_page and current.blocks:
            pages.append(current)
            current = Page(index=len(pages), width=width, height=height)
        current.blocks.append(block)
    if current.blocks:
        pages.append(current)

    # Word does not say which page a picture is on. They go with the first, as
    # figures under the text, rather than being dropped.
    if figures:
        pages[0].images.extend(figures)

    for page in pages:
        count = max(1, len(page.blocks))
        for i, block in enumerate(page.blocks):
            block.box = Box(0.08, min(0.95, i / count * 0.9 + 0.02), 0.84,
                            0.9 / count)
    return pages
