"""Split a block of text into translation units.

Sentence splitting is where a translator quietly goes wrong. Break
"E.g. David and Goliath are both students." after "E.g." and the model is
handed two fragments, translates them as two unrelated things, and the reader
sees a stray line that means nothing. So this splitter is conservative: it only
breaks where the evidence is clear, because an over-long unit still translates
fine while an over-short one produces nonsense.

Language-neutral by construction -- it keys off punctuation and casing rather
than any word list of English content, so the same code serves a deck in any
Latin-script language. `_ABBREV` is the one English-leaning part and is only
ever used to *suppress* a split, which is the safe direction.
"""

from __future__ import annotations

import re

from .model import Sentence, TextBlock

# Splitting after these would strand a fragment. Suppression only: an unknown
# abbreviation costs us a missed split (harmless), never a bad one.
_ABBREV = {
    "e.g", "i.e", "etc", "cf", "vs", "al", "approx", "resp",
    "fig", "eq", "sec", "ch", "ex", "no", "vol", "pp", "ref",
    "mr", "mrs", "ms", "dr", "prof", "st", "inc", "ltd", "jr", "sr",
}

# A boundary candidate: terminal punctuation, optional closing quote/bracket,
# whitespace, then something that can begin a sentence.
_CANDIDATE = re.compile(r'([.!?])(["\'’”)\]]*)\s+(?=[^\s])')

# The token immediately before the punctuation.
_LAST_TOKEN = re.compile(r"([\w.]+)$", re.UNICODE)


def segment(text: str) -> list[str]:
    """Split `text` into sentences. Always returns at least one unit."""
    text = " ".join(text.split())
    if not text:
        return []

    parts: list[str] = []
    start = 0
    for m in _CANDIDATE.finditer(text):
        end = m.end(2)
        if _is_real_boundary(text, m, start):
            chunk = text[start:end].strip()
            if chunk:
                parts.append(chunk)
            start = m.end()
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts or [text]


def _is_real_boundary(text: str, m: re.Match, start: int) -> bool:
    punct = m.group(1)
    # "!" and "?" are unambiguous; only "." is overloaded.
    if punct != ".":
        return True

    before = text[: m.start(1)]
    token_match = _LAST_TOKEN.search(before)
    token = token_match.group(1).lower() if token_match else ""

    if token.rstrip(".") in _ABBREV:
        return False
    # A single letter: an initial ("J. Smith") or a list marker ("a.").
    if len(token) == 1 and token.isalpha():
        return False
    # Dotted acronym or version: "U.S.", "v1.2".
    if "." in token:
        return False
    # A number is only a list marker when it *opens* the unit ("1. Choose ...").
    # A number that merely ends a clause ends a sentence: "then write 0. If ..."
    # must split, and so must "published in 2020. The second edition ...".
    # Decimals never reach here -- the boundary pattern requires whitespace
    # after the period, so the dot inside "3.14" is not a candidate at all.
    if token.isdigit() and text[start:m.start(1)].strip() == token:
        return False

    after = text[m.end():]
    # Real sentences start with something capital-ish. Lowercase after a period
    # usually means the period was not a full stop after all.
    if after[:1].isalpha() and after[:1].islower():
        return False
    return True


def segment_block(block: TextBlock) -> None:
    """Populate `block.sentences`, in place."""
    block.sentences = [Sentence(id=_sid(block, i, t), text=t)
                       for i, t in enumerate(segment(block.text))]


def _sid(block: TextBlock, index: int, text: str) -> str:
    """Stable per-sentence id.

    Derived from content, not from position, so that editing slide 3 does not
    invalidate the cached translation of every sentence after it.
    """
    from .model import _hash
    return _hash(f"{text}")
