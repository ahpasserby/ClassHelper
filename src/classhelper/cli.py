"""Command line entry point.

Right now this exposes `inspect`, which shows exactly what was pulled out of a
file and why each piece was classified the way it was. That is deliberately the
first command: translation quality is downstream of extraction quality, so when
a deck reads badly the first question is always whether the text came out right,
and this answers it without spending a cent on the API.
"""

from __future__ import annotations

import argparse
import json
import sys

from .classify import classify
from .model import BlockKind, Deck
from .parsers import SUPPORTED, UnsupportedFormat, parse
from .segment import segment_block

# ANSI colour, dropped automatically when output is piped to a file.
_COLOUR = {
    BlockKind.TITLE: "\033[1;36m",
    BlockKind.BODY: "\033[0m",
    BlockKind.CODE: "\033[0;33m",
    BlockKind.CAPTION: "\033[0;35m",
    BlockKind.FRAGMENT: "\033[0;34m",
    BlockKind.CHROME: "\033[0;90m",
}
_RESET = "\033[0m"
_DIM = "\033[0;90m"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="classhelper",
        description="Read lecture slides with translations inline.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    insp = sub.add_parser(
        "inspect",
        help="show what was extracted from a file and how it was classified",
    )
    insp.add_argument("file", help=f"a {' or '.join(SUPPORTED)} file")
    insp.add_argument("-p", "--page", type=int, metavar="N",
                      help="show only page N (1-based)")
    insp.add_argument("--hidden", action="store_true",
                      help="include blocks classified as page furniture")
    insp.add_argument("--sentences", action="store_true",
                      help="show the sentence split, i.e. the translation units")
    insp.add_argument("--json", action="store_true",
                      help="machine-readable output")

    server = sub.add_parser(
        "serve", help="run the reader's server (the desktop app does this for you)"
    )
    server.add_argument("--port", type=int,
                        help="fixed port (default: any free one)")

    args = parser.parse_args(argv)

    if args.command == "serve":
        from .launcher import serve
        return serve(args.port)

    try:
        deck = parse(args.file)
    except UnsupportedFormat as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"error: could not read {args.file}: {exc}", file=sys.stderr)
        return 1

    classify(deck)
    for _, block, _ in _iter_blocks(deck):
        segment_block(block)

    if args.json:
        json.dump(_as_dict(deck), sys.stdout, indent=2, ensure_ascii=False)
        print()
        return 0

    _report(deck, args)
    return 0


def _iter_blocks(deck: Deck):
    for page in deck.pages:
        for block in page.blocks:
            yield page, block, block.kind


def _report(deck: Deck, args) -> None:
    colour = sys.stdout.isatty()
    c = (lambda k: _COLOUR[k]) if colour else (lambda k: "")
    reset = _RESET if colour else ""
    dim = _DIM if colour else ""

    counts = {k: 0 for k in BlockKind}
    for _, block, kind in _iter_blocks(deck):
        counts[kind] += 1
    units = sum(len(b.sentences) for _, b, k in _iter_blocks(deck)
                if k.worth_translating)

    print(f"{deck.source_path}")
    print(f"  {deck.source_format}, {len(deck.pages)} pages, "
          f"{units} translation units")
    print("  " + "  ".join(f"{k.value}={counts[k]}" for k in BlockKind
                           if counts[k]))
    for warning in deck.warnings:
        print(f"  {warning}")
    if args.hidden:
        for note in deck.notes:
            print(f"  {dim}diagnostic: {note}{reset}")
    print()

    pages = deck.pages
    if args.page:
        if not 1 <= args.page <= len(pages):
            print(f"error: no page {args.page}", file=sys.stderr)
            return
        pages = [pages[args.page - 1]]

    for page in pages:
        head = f"── page {page.index + 1}"
        if page.title:
            head += f"  {page.title}"
        print(f"{dim}{head}{reset}")
        for block in page.blocks:
            if block.kind is BlockKind.CHROME and not args.hidden:
                continue
            print(f"  {c(block.kind)}{block.kind.value:<8}{reset} "
                  f"{block.text[:96]}")
            if args.sentences and len(block.sentences) > 1:
                for s in block.sentences:
                    print(f"           {dim}·{reset} {s.text[:88]}")
            if args.hidden:
                print(f"           {dim}{block.kind_reason}{reset}")
        print()


def _as_dict(deck: Deck) -> dict:
    return {
        "source": deck.source_path,
        "format": deck.source_format,
        "warnings": deck.warnings,
        "notes": deck.notes,
        "pages": [
            {
                "index": p.index,
                "title": p.title,
                "images": len(p.images),
                "blocks": [
                    {
                        "kind": b.kind.value,
                        "reason": b.kind_reason,
                        "text": b.text,
                        "sentences": [s.text for s in b.sentences],
                        "box": [b.box.x, b.box.y, b.box.w, b.box.h],
                    }
                    for b in p.blocks
                ],
            }
            for p in deck.pages
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
