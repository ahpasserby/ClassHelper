"""PowerPoint (.pptx) -> Deck.

Two things here are less obvious than they look:

1. Shapes must be flattened out of group shapes, and a group defines its own
   child coordinate space, so child positions need a transform to become page
   coordinates. Without it, every label inside an ER diagram lands at the wrong
   place and reading order falls apart.

2. XML order is authoring order, not reading order. A slide edited over several
   years has its shapes in whatever sequence they were added. We sort by
   position instead.

3. python-pptx does not descend into <mc:AlternateContent>, so any shape
   PowerPoint wrapped for compatibility is silently absent from `slide.shapes`
   -- no error, just missing text. Real decks hit this constantly: it is where
   the main body placeholder ends up on any slide using a post-2010 drawing
   feature. We unwrap those nodes before reading the tree.

4. Equations are OMML (<m:oMath>), not text runs, so `paragraph.text` drops them
   silently too. The damage is worse than a missing shape: the sentence still
   arrives, with a hole where the formula was, and a translator will write
   fluent nonsense around it -- "the course code is ``." So paragraphs are read
   by walking the XML in document order rather than through `.text`.
"""

from __future__ import annotations

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Emu

from ..fonts import is_monospace
from ..model import Box, Deck, Image, Page, TextBlock

# Markup Compatibility namespace. PowerPoint wraps a shape in
# <mc:AlternateContent> whenever it uses a feature that older readers cannot
# render, offering a modern <mc:Choice> and a degraded <mc:Fallback>.
_MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_M = "http://schemas.openxmlformats.org/officeDocument/2006/math"

# Placeholder types that PowerPoint itself tells us are page furniture.
_CHROME_PLACEHOLDERS = {"FOOTER", "SLIDE_NUMBER", "DATE"}
_TITLE_PLACEHOLDERS = {"TITLE", "CENTER_TITLE"}


def parse(path: str) -> Deck:
    prs = Presentation(path)
    pw, ph = prs.slide_width, prs.slide_height
    deck = Deck(source_path=path, source_format="pptx")

    if not pw or not ph:  # malformed deck; fall back to 16:9 at 10in wide
        pw, ph = Emu(9144000), Emu(5143500)
        deck.warnings.append("Slide size missing; assumed 16:9.")

    recovered = 0
    for i, slide in enumerate(prs.slides):
        # EMU are 914400 to the inch, points 72 -- the reader wants points.
        page = Page(index=i, width=pw / 12700, height=ph / 12700)
        recovered += _unwrap_alternate_content(slide.shapes)
        _walk(slide.shapes, page, pw, ph, offset=(0, 0), scale=(1.0, 1.0), depth=0)
        page.blocks.sort(key=_reading_order)
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            page.notes = (slide.notes_slide.notes_text_frame.text or "").strip()
        deck.pages.append(page)

    if recovered:
        deck.notes.append(
            f"Recovered {recovered} shape(s) from mc:AlternateContent wrappers."
        )
    return deck


def _unwrap_alternate_content(shapes) -> int:
    """Replace every <mc:AlternateContent> in the shape tree with its preferred
    branch, in place, so ordinary shape iteration can see the shapes inside.

    Prefers <mc:Choice> (what PowerPoint actually renders) and falls back to
    <mc:Fallback>. Runs over the whole tree, so wrappers nested inside groups
    are handled in the same pass. Returns how many were unwrapped.
    """
    tree = getattr(shapes, "_spTree", None)
    if tree is None:
        tree = getattr(shapes, "_element", None)
    if tree is None:
        return 0

    count = 0
    # Re-query each round: unwrapping can expose wrappers nested in a Choice.
    for _ in range(10):  # depth guard; real decks never nest more than 1-2 deep
        nodes = tree.findall(f".//{{{_MC}}}AlternateContent")
        if not nodes:
            break
        for ac in nodes:
            parent = ac.getparent()
            if parent is None:
                continue
            branch = ac.find(f"{{{_MC}}}Choice")
            if branch is None:
                branch = ac.find(f"{{{_MC}}}Fallback")
            at = parent.index(ac)
            if branch is not None:
                for child in reversed(list(branch)):
                    parent.insert(at, child)
            parent.remove(ac)
            count += 1
    return count


def _walk(shapes, page: Page, pw: int, ph: int, offset, scale, depth: int) -> None:
    """Recursively flatten shapes into the page, mapping into page coordinates."""
    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            child_off, child_scale = _group_transform(shape, offset, scale)
            _walk(shape.shapes, page, pw, ph, child_off, child_scale, depth + 1)
            continue

        box = _box(shape, pw, ph, offset, scale)
        if box is None:
            # Geometry is genuinely unknown (no xfrm, nothing inherited from the
            # layout). Never drop the shape over this: losing a line of the
            # lecture is far worse than placing it imprecisely. Park it at the
            # page centre; position-based rules will simply abstain on it.
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                continue
            box = Box(x=0.0, y=0.5, w=1.0, h=0.0)

        if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
            try:
                page.images.append(
                    Image(box=box, data=shape.image.blob, ext=shape.image.ext)
                )
            except (AttributeError, ValueError):
                pass  # linked-but-not-embedded picture; nothing to show
            continue

        if getattr(shape, "has_table", False) and shape.has_table:
            _emit_table(shape, page, box, depth)
            continue

        if not getattr(shape, "has_text_frame", False):
            continue

        _emit_text(shape, page, box, depth)


def _emit_text(shape, page: Page, box: Box, depth: int) -> None:
    """One TextBlock per paragraph, tagged with its parent shape.

    Paragraph granularity is what the reader wants (each bullet is its own
    English/Chinese pair). Shape identity is kept in meta so the classifier can
    reason about a shape as a whole -- "every paragraph in this box is one or
    two words" is the signal that says "diagram labels", and you cannot see it
    from a single paragraph.
    """
    ph_type = _placeholder_type(shape)
    shape_id = f"p{page.index}s{shape.shape_id}"
    # Filtered on the walked text, not on `.text`: a paragraph that is nothing
    # but an equation looks empty to python-pptx and would be dropped here.
    paragraphs = [p for p in shape.text_frame.paragraphs
                  if _paragraph_text(p)[0]]  # non-empty once maths is included

    for pi, para in enumerate(paragraphs):
        text, has_math, math_only = _paragraph_text(para)
        page.blocks.append(
            TextBlock(
                text=text,
                box=box,
                meta={
                    "shape_id": shape_id,
                    "shape_para_count": len(paragraphs),
                    "para_index": pi,
                    "placeholder": ph_type,
                    "is_title_placeholder": ph_type in _TITLE_PLACEHOLDERS,
                    "is_chrome_placeholder": ph_type in _CHROME_PLACEHOLDERS,
                    "font_pt": _font_pt(para),
                    "mono": _is_mono(para),
                    "bullet_depth": para.level,
                    "bullet": _is_bullet(para, ph_type),
                    "group_depth": depth,
                    "has_math": has_math,
                    "math_only": math_only,
                },
            )
        )


def _emit_table(shape, page: Page, box: Box, depth: int) -> None:
    """Tables become one block per cell, marked so the reader can keep the grid.

    Cell text is usually a term or a short value, not prose, so treating a table
    as a paragraph of running text would produce nonsense translations.
    """
    shape_id = f"p{page.index}s{shape.shape_id}"
    for r, row in enumerate(shape.table.rows):
        for c, cell in enumerate(row.cells):
            text = " ".join(cell.text.split())
            if not text:
                continue
            page.blocks.append(
                TextBlock(
                    text=text,
                    box=box,
                    meta={
                        "shape_id": shape_id,
                        "table_cell": (r, c),
                        "is_header_row": r == 0,
                        "group_depth": depth,
                    },
                )
            )


def _group_transform(group, offset, scale):
    """Compose this group's child-space transform with its parent's.

    A group maps its child coordinate box (chOff/chExt) onto its own frame
    (off/ext). Nested groups compose, hence carrying offset+scale down.
    """
    try:
        c_off, c_ext = group.child_offset, group.child_extents
        sx = (group.width / c_ext.cx) if c_ext.cx else 1.0
        sy = (group.height / c_ext.cy) if c_ext.cy else 1.0
    except (AttributeError, TypeError, ZeroDivisionError):
        return offset, scale

    # page_x = offset + (child_x - c_off) * scale, folded into the parent's own
    new_sx, new_sy = scale[0] * sx, scale[1] * sy
    new_ox = offset[0] + (group.left - c_off.x * sx) * scale[0]
    new_oy = offset[1] + (group.top - c_off.y * sy) * scale[1]
    return (new_ox, new_oy), (new_sx, new_sy)


def _box(shape, pw: int, ph: int, offset, scale) -> Box | None:
    """Shape geometry as a page-relative 0..1 box, or None if unpositioned."""
    if shape.left is None or shape.top is None:
        return None
    w = shape.width or 0
    h = shape.height or 0
    x = offset[0] + shape.left * scale[0]
    y = offset[1] + shape.top * scale[1]
    return Box(x=x / pw, y=y / ph, w=(w * scale[0]) / pw, h=(h * scale[1]) / ph)


def _placeholder_type(shape) -> str:
    if not getattr(shape, "is_placeholder", False):
        return ""
    try:
        return str(shape.placeholder_format.type).split()[0].strip("()")
    except (AttributeError, ValueError):
        return ""


def _font_pt(para) -> float:
    """Largest explicit run size in the paragraph, in points.

    Often absent -- pptx inherits from the layout and master when a run sets no
    size. The classifier treats 0.0 as "unknown" and leans on other signals
    rather than guessing, since a wrong font size would mislabel titles.
    """
    sizes = [r.font.size.pt for r in para.runs if r.font.size is not None]
    if para.font.size is not None:
        sizes.append(para.font.size.pt)
    return max(sizes) if sizes else 0.0


def _is_bullet(para, placeholder: str) -> bool:
    """Whether this paragraph is a list item.

    PowerPoint records the decision as `buNone` / `buChar` / `buAutoNum` on the
    paragraph, but usually records nothing and inherits from the master -- where
    body placeholders are bulleted and titles are not. So: an explicit marker
    wins, and absent one the placeholder decides.
    """
    properties = para._p.find(f"{{{_A}}}pPr")
    if properties is not None:
        for tag in ("buNone", "buChar", "buAutoNum"):
            if properties.find(f"{{{_A}}}{tag}") is not None:
                return tag != "buNone"
    if placeholder in _TITLE_PLACEHOLDERS:
        return False
    return placeholder in {"BODY", "OBJECT", "SUBTITLE"} or para.level > 0


def _paragraph_text(para) -> tuple[str, bool, bool]:
    """Read a paragraph in document order, equations included.

    Returns the text, whether any of it came from an equation, and whether it is
    *nothing but* an equation. The last one matters: a line that is pure notation
    -- a set definition, a relational-algebra expression -- has no prose to
    translate, and sending it anyway costs money and invites the model to
    "helpfully" rewrite the symbols.
    """
    parts: list[tuple[str, bool]] = []
    has_math = _collect(para._p, parts)
    raw = "".join(t for t, _ in parts)
    text = (
        "\n".join(line.rstrip() for line in raw.split("\n")).strip("\n")
        if _is_mono(para)
        else " ".join(raw.split())
    )
    prose = "".join(t for t, is_math in parts if not is_math)
    math_only = has_math and not any(ch.isalnum() for ch in prose)
    return text, has_math, math_only


def _collect(node, parts: list[tuple[str, bool]]) -> bool:
    """Depth-first walk gathering visible text. Returns True if math was found."""
    found = False
    for child in node:
        if not isinstance(child.tag, str):
            continue  # comment or processing instruction
        ns, _, tag = child.tag.rpartition("}")
        ns = ns.lstrip("{")

        if ns == _M and tag == "oMath":
            parts.append((_math_text(child), True))
            found = True
            continue  # its <m:t> leaves are consumed here
        if ns == _A and tag == "t":
            parts.append((child.text or "", False))
            continue
        if ns == _A and tag == "br":
            # A real line break. Prose collapses it back to a space below;
            # a code listing keeps it, because the lines are the point.
            parts.append(("\n", False))
            continue
        found |= _collect(child, parts)
    return found


def _math_text(omath) -> str:
    """The readable text of an equation.

    PowerPoint stores maths in Cambria Math's Unicode alphanumeric block, so
    "COMP" is written with the codepoints for MATHEMATICAL ITALIC CAPITAL C and
    friends. NFKC folds those back to plain letters, which is what makes the
    result searchable, translatable, and legible in a browser that has no maths
    font installed. Applied only to equation text -- running it over the whole
    slide would rewrite full-width punctuation the author chose deliberately.
    """
    import unicodedata

    raw = "".join(t.text or "" for t in omath.iter(f"{{{_M}}}t"))
    return unicodedata.normalize("NFKC", raw)


def _is_mono(para) -> bool:
    """True when every run that names a font names a monospace one.

    Requiring *all* runs to agree keeps an inline `code` span inside a normal
    sentence from turning the whole paragraph into an untranslated code block.
    """
    names = [r.font.name for r in para.runs if r.font.name]
    if not names and para.font.name:
        names = [para.font.name]
    return bool(names) and all(is_monospace(n) for n in names)


def _reading_order(block: TextBlock):
    """Sort top-to-bottom, then left-to-right, banding rows so that items on
    roughly the same line are not reordered by a few stray pixels."""
    band = round(block.box.y * 40)  # 40 bands down the page
    return (band, block.box.x, block.meta.get("para_index", 0))
