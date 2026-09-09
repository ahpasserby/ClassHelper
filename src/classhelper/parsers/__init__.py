"""Input formats.

Each parser turns one file format into a `Deck`. Adding a format means adding a
module with a `parse(path) -> Deck` function and one line in `PARSERS` -- the
classifier, translator and reader need no changes.
"""

from __future__ import annotations

import os

from ..model import Deck
from . import pdf_parser, pptx_parser

PARSERS = {
    ".pptx": pptx_parser.parse,
    ".pdf": pdf_parser.parse,
}

SUPPORTED = tuple(sorted(PARSERS))


class UnsupportedFormat(ValueError):
    pass


def parse(path: str) -> Deck:
    ext = os.path.splitext(path)[1].lower()
    if ext not in PARSERS:
        raise UnsupportedFormat(
            f"{ext or path!r} is not supported. Supported: {', '.join(SUPPORTED)}. "
            # .ppt and .key are the two people try most often, so say the fix.
            "Convert .ppt or .key to .pptx in PowerPoint or Keynote first."
        )
    return PARSERS[ext](path)
