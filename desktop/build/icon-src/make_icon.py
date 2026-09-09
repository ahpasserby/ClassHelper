"""Draw the application icon.

Kept as code rather than a binary asset so the icon can be re-rendered at any
size, and so a change to it is a change anyone can read in the diff.

The mark is what the program does: one page, and under each line of it the
translation of that line. Drawn full-bleed, because macOS applies its own
rounded mask -- artwork with its own corners gets rounded twice.
"""

from __future__ import annotations

import math
import pathlib
import sys

import pymupdf

S = 1024.0

INK = (0.086, 0.125, 0.235)      # the source line
ACCENT = (0.231, 0.510, 0.965)   # the translation under it
TOP = (0.357, 0.549, 1.0)        # background, top left
MID = (0.208, 0.376, 0.910)
BOTTOM = (0.106, 0.184, 0.651)   # background, bottom right


def _mix(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def _diagonal_gradient(page, steps: int = 320) -> None:
    """A diagonal wash, drawn as bands.

    PDF shadings would be one object instead of three hundred, but the file is
    rasterised once at build time and never shipped -- clarity is worth more
    here than elegance.
    """
    span = S * 2
    width = span / steps
    # Each band overlaps the next. Butted edges leave a seam once antialiased,
    # which on a flat wash reads as diagonal stripes.
    overlap = width * 1.6
    for i in range(steps):
        t = i / (steps - 1)
        colour = _mix(TOP, MID, t / 0.5) if t < 0.5 else _mix(MID, BOTTOM, (t - 0.5) / 0.5)
        offset = i * width
        page.draw_polyline(
            [(offset, 0), (offset - span, span),
             (offset - span + width + overlap, span), (offset + width + overlap, 0)],
            color=None, fill=colour, closePath=True,
        )


def _sheen(page) -> None:
    """A little light from above, so the flat square has some form."""
    for i in range(90):
        t = i / 89
        page.draw_rect(
            pymupdf.Rect(0, S * 0.62 * t, S, S * 0.62 * (t + 1 / 89) + 1),
            color=None, fill=(1, 1, 1), fill_opacity=0.20 * (1 - t) ** 1.6,
        )


def _shadow(page, rect: pymupdf.Rect) -> None:
    """Just enough shadow to lift the page off the background.

    Stacked rectangles cannot make a real gaussian blur, and a heavy one shows its
    seams as a boxy halo. Kept faint and close, where the approximation holds.
    """
    layers = 40
    for i in range(layers):
        t = i / (layers - 1)
        grow = 2 + 26 * t
        page.draw_rect(
            pymupdf.Rect(rect.x0 - grow, rect.y0 - grow + 16,
                         rect.x1 + grow, rect.y1 + grow + 16),
            color=None, fill=(0.031, 0.063, 0.196),
            fill_opacity=0.012 * (1 - t) ** 2,
            radius=0.10,
        )


def draw(page) -> None:
    _diagonal_gradient(page)
    _sheen(page)

    # The page behind: the file itself, mostly hidden by the one you read.
    page.draw_rect(pymupdf.Rect(346, 128, 812, 720), color=None,
                   fill=(1, 1, 1), fill_opacity=0.32, radius=0.11)

    front = pymupdf.Rect(206, 196, 802, 836)
    _shadow(page, front)
    page.draw_rect(front, color=None, fill=(1, 1, 1), radius=0.10)

    # Each sentence, and its translation directly under it.
    rows = [(396, INK), (286, ACCENT), (446, INK), (332, ACCENT),
            (358, INK), (248, ACCENT)]
    y = 306.0
    for i, (width, colour) in enumerate(rows):
        page.draw_rect(pymupdf.Rect(278, y, 278 + width, y + 32),
                       color=None, fill=colour, radius=0.5)
        # A wider gap between pairs than inside one.
        y += 56 if i % 2 == 0 else 96


def render(path: pathlib.Path, size: int) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=S, height=S)
    draw(page)
    scale = size / S
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
    pix.save(str(path))
    doc.close()


if __name__ == "__main__":
    out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "icon.png")
    render(out, int(sys.argv[2]) if len(sys.argv) > 2 else 1024)
    print("画好", out, out.stat().st_size // 1024, "KB")
