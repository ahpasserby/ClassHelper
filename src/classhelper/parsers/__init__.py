"""Input formats.

Each parser turns one file format into a `Deck`. Adding a format means adding a
module with a `parse(path) -> Deck` function and one line in `PARSERS` -- the
classifier, translator and reader need no changes.
"""

from __future__ import annotations

import os

from ..model import Deck
from . import docx_parser, legacy, md_parser, pdf_parser, pptx_parser

PARSERS = {
    ".pptx": pptx_parser.parse,
    ".pdf": pdf_parser.parse,
    ".docx": docx_parser.parse,
    ".md": md_parser.parse,
    ".markdown": md_parser.parse,
    # Read by converting first; see legacy.py.
    ".ppt": legacy.parse_ppt,
}

SUPPORTED = tuple(sorted(PARSERS))


class UnsupportedFormat(ValueError):
    pass


def parse(path: str) -> Deck:
    ext = os.path.splitext(path)[1].lower()
    if ext not in PARSERS:
        raise UnsupportedFormat(
            f"不支持 {ext or path!r}。支持的格式：{', '.join(SUPPORTED)}。"
            # .key and .doc are the two people try most often, so say the fix.
            "Keynote 请先导出 .pptx，旧版 .doc 请先另存为 .docx。"
        )
    try:
        return PARSERS[ext](path)
    except legacy.ConversionUnavailable as exc:
        # Not a broken file: a format we can read only with help that is not
        # installed. Reported as unsupported so the reader shows the message.
        raise UnsupportedFormat(str(exc)) from exc
