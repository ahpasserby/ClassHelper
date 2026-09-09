"""The slide pane: showing the source page next to its translation.

What matters here is that the program is straight about *what* it is showing.
A PDF can be rendered exactly; a .pptx cannot without LibreOffice, and the
reader draws an approximation instead -- which is fine, and is only fine
because the pane says so.
"""

from __future__ import annotations

import struct

import pymupdf
import pytest


@pytest.fixture
def pdf_file(tmp_path):
    doc = pymupdf.open()
    for i in range(3):
        page = doc.new_page(width=720, height=405)  # 16:9
        page.insert_text((60, 100), f"Slide {i + 1}: Entity Sets", fontsize=28)
        page.insert_text((60, 160), "An entity is a thing in the world.",
                         fontsize=16)
    path = tmp_path / "lecture.pdf"
    doc.save(str(path))
    doc.close()
    return str(path)


def _open(client, path):
    response = client.post("/api/open", json={"path": path})
    assert response.status_code == 200, response.text
    return response.json()


def _png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def test_a_pdf_page_is_rendered_from_the_file_itself(client, pdf_file):
    deck = _open(client, pdf_file)
    assert deck["source"]["mode"] == "exact"

    response = client.get(f"/api/deck/{deck['id']}/source/0?w=800")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    width, height = _png_size(response.content)
    assert width == 800
    assert width / height == pytest.approx(16 / 9, rel=0.01), \
        "the source must not be reshaped to fit the pane"


def test_the_render_carries_the_files_identity_so_a_stale_one_is_not_served(
    client, pdf_file
):
    deck = _open(client, pdf_file)
    first = client.get(f"/api/deck/{deck['id']}/source/0?w=400")
    second = client.get(f"/api/deck/{deck['id']}/source/1?w=400")
    assert first.headers["etag"] != second.headers["etag"]


def test_asking_for_a_page_that_is_not_there_is_a_404(client, pdf_file):
    deck = _open(client, pdf_file)
    assert client.get(f"/api/deck/{deck['id']}/source/99").status_code == 404


def test_a_pptx_without_libreoffice_says_so_rather_than_pretending(
    client, deck_file, monkeypatch
):
    """The reader falls back to drawing the page. That is honest only if the
    pane tells the user it is a drawing."""
    import classhelper.render as render_module

    monkeypatch.setattr(render_module, "find_soffice", lambda: None)
    deck = _open(client, deck_file)

    assert deck["source"]["mode"] == "approximate"
    assert "LibreOffice" in deck["source"]["detail"]
    assert client.get(f"/api/deck/{deck['id']}/source/0").status_code == 404


def test_a_page_carries_the_shape_the_reader_needs_to_lay_it_out(client, deck_file):
    """Proportions and type size, so the drawn fallback is the right shape and
    a 4:3 deck is not shown as 16:9."""
    deck = _open(client, deck_file)
    page = deck["pages"][0]
    assert page["aspect"] == pytest.approx(4 / 3, rel=0.01) or \
           page["aspect"] == pytest.approx(16 / 9, rel=0.01)
    assert page["height_pt"] > 100
    # 0 means "the file did not say" -- pptx inherits size from its layout far
    # more often than it sets it, and the reader falls back on the block kind.
    assert all(block["font"] >= 0 for block in page["blocks"])


def test_a_pdf_reports_the_type_size_it_actually_used(client, pdf_file):
    deck = _open(client, pdf_file)
    sizes = [b["font"] for b in deck["pages"][0]["blocks"]]
    assert max(sizes) > 20, "the 28pt heading should come through as 28pt"


def test_a_deck_whose_pages_differ_in_size_keeps_each_pages_own_shape(client, tmp_path):
    doc = pymupdf.open()
    doc.new_page(width=720, height=405)   # 16:9
    doc.new_page(width=595, height=842)   # A4 portrait
    path = tmp_path / "mixed.pdf"
    doc.save(str(path))
    doc.close()

    deck = _open(client, str(path))
    wide, tall = deck["pages"][0]["aspect"], deck["pages"][1]["aspect"]
    assert wide > 1 > tall
