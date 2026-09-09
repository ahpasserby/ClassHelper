"""Word documents.

Built by hand rather than with python-docx, because the point of the parser is
that it reads the XML itself -- a fixture made by the same library it avoids
would not exercise the reason it exists.
"""

from __future__ import annotations

import zipfile

import pytest

from classhelper.classify import classify
from classhelper.model import BlockKind
from classhelper.parsers import parse

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"

_STYLES = f"""<?xml version="1.0"?>
<w:styles xmlns:w="{W}">
  <w:style w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>
  <w:style w:styleId="Heading2"><w:name w:val="heading 2"/></w:style>
  <w:style w:styleId="Code"><w:name w:val="HTML Preformatted"/></w:style>
</w:styles>"""


def _p(text: str, *, style: str = "", size: int = 0, font: str = "",
       bullet: bool = False, page_break: bool = False, math: str = "") -> str:
    props = ""
    if style or bullet or page_break:
        props = "<w:pPr>"
        if style:
            props += f'<w:pStyle w:val="{style}"/>'
        if bullet:
            props += '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'
        if page_break:
            props += "<w:pageBreakBefore/>"
        props += "</w:pPr>"
    run_props = ""
    if size or font:
        run_props = "<w:rPr>"
        if font:
            run_props += f'<w:rFonts w:ascii="{font}"/>'
        if size:
            run_props += f'<w:sz w:val="{size * 2}"/>'
        run_props += "</w:rPr>"
    body = f"<w:r>{run_props}<w:t xml:space='preserve'>{text}</w:t></w:r>"
    if math:
        # An equation between two runs: the thing python-docx drops.
        body += f"<m:oMath><m:r><m:t>{math}</m:t></m:r></m:oMath>"
    return f"<w:p>{props}{body}</w:p>"


@pytest.fixture
def document(tmp_path):
    def make(body: str, name: str = "handout.docx", section: str = ""):
        path = tmp_path / name
        section = section or '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr>'
        xml = (
            f'<?xml version="1.0"?>'
            f'<w:document xmlns:w="{W}" xmlns:m="{M}">'
            f"<w:body>{body}{section}</w:body></w:document>"
        )
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("word/document.xml", xml)
            zf.writestr("word/styles.xml", _STYLES)
        return parse(str(path))
    return make


def _texts(page):
    return [b.text for b in page.blocks]


def test_paragraphs_come_out_in_order(document):
    deck = document(_p("First.") + _p("Second."))
    assert _texts(deck.pages[0]) == ["First.", "Second."]


def test_an_equation_between_two_runs_is_not_dropped(document):
    """python-docx joins w:t runs and loses OMML, which is why this parser
    walks the XML itself."""
    deck = document(_p("The bound is ", math="O(n log n)"))
    assert _texts(deck.pages[0]) == ["The bound is O(n log n)"]


def test_maths_alphanumerics_are_folded_back_to_letters(document):
    """Word writes a plain x in the Cambria Math block, which is unreadable
    everywhere else."""
    deck = document(_p("Let ", math="\U0001d465"))  # MATHEMATICAL ITALIC SMALL X
    assert _texts(deck.pages[0]) == ["Let x"]


def test_a_heading_is_a_title_without_guessing(document):
    deck = document(_p("Entity Sets", style="Heading1") + _p("An entity is a thing."))
    classify(deck)
    assert deck.pages[0].blocks[0].kind is BlockKind.TITLE
    assert deck.pages[0].blocks[0].kind_reason == "placeholder:title"


def test_a_localised_heading_style_is_still_a_heading(document):
    """Word names its styles in the UI language; only w:name is stable, so the
    style table is read rather than the id being pattern-matched."""
    path_styles = f"""<?xml version="1.0"?>
<w:styles xmlns:w="{W}">
  <w:style w:styleId="berschrift1"><w:name w:val="heading 1"/></w:style>
</w:styles>"""
    import zipfile as zf_mod
    from pathlib import Path
    import tempfile
    folder = Path(tempfile.mkdtemp())
    path = folder / "de.docx"
    xml = (f'<?xml version="1.0"?><w:document xmlns:w="{W}" xmlns:m="{M}"><w:body>'
           f'{_p("Kapitel", style="berschrift1")}{_p("Text.")}</w:body></w:document>')
    with zf_mod.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", xml)
        zf.writestr("word/styles.xml", path_styles)
    deck = parse(str(path))
    classify(deck)
    assert deck.pages[0].blocks[0].kind is BlockKind.TITLE


def test_an_explicit_page_break_starts_a_page(document):
    deck = document(_p("One.") + _p("Two.", page_break=True))
    assert len(deck.pages) == 2
    assert _texts(deck.pages[1]) == ["Two."]


def test_headings_divide_a_document_with_no_breaks(document):
    deck = document(
        _p("A", style="Heading1") + _p("One.")
        + _p("B", style="Heading1") + _p("Two.")
    )
    assert len(deck.pages) == 2


def test_a_monospace_paragraph_is_code(document):
    deck = document(_p("printf(\"hi\");", font="Consolas"))
    classify(deck)
    assert deck.pages[0].blocks[0].kind is BlockKind.CODE
    assert not deck.pages[0].blocks[0].kind.worth_translating


def test_a_numbered_paragraph_keeps_its_shape(document):
    deck = document(_p("First point.", bullet=True))
    assert deck.pages[0].blocks[0].meta["bullet"]


def test_a_table_becomes_cells_not_prose(document):
    table = (f'<w:tbl><w:tr><w:tc>{_p("Term")}</w:tc>'
             f'<w:tc>{_p("Meaning")}</w:tc></w:tr></w:tbl>')
    deck = document(table)
    classify(deck)
    assert {b.kind for b in deck.pages[0].blocks} == {BlockKind.FRAGMENT}
    assert _texts(deck.pages[0]) == ["Term", "Meaning"]


def test_the_page_size_is_read_from_the_file(document):
    """A4 in twips. The reader shows the source at its own proportions."""
    deck = document(_p("Text."))
    page = deck.pages[0]
    assert round(page.width) == 595 and round(page.height) == 842
    assert page.aspect < 1, "A4 is portrait"


def test_font_size_comes_through(document):
    deck = document(_p("Big.", size=20))
    assert deck.pages[0].blocks[0].meta["font_pt"] == 20.0


def test_an_empty_document_opens_rather_than_raising(document):
    deck = document("")
    assert len(deck.pages) == 1
    assert deck.warnings
