"""Format-agnostic document model.

Everything downstream — classification, translation, the reading UI — only ever
sees these types. Adding a new input format means writing a parser that produces
a `Deck`; nothing else changes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class BlockKind(str, Enum):
    """What a text block is *for*, which decides how the reader displays it.

    The classifier assigns these. When it is unsure it must fall back to BODY:
    showing a line of navigation noise is a nuisance, dropping a line of the
    lecture is a failure.
    """

    TITLE = "title"      # Page heading. Translated, rendered as a heading.
    BODY = "body"        # Actual prose. The reading flow.
    CODE = "code"        # Code, or a bare equation. Shown as-is, never translated.
    CAPTION = "caption"  # Labels/annotations belonging to a figure.
    FRAGMENT = "fragment"  # Isolated words inside a diagram (e.g. "ID", "name").
    CHROME = "chrome"    # Repeated navigation, footers, page numbers, logos.

    @property
    def in_reading_flow(self) -> bool:
        """Whether this block belongs in the main bilingual reading column."""
        return self in (BlockKind.TITLE, BlockKind.BODY, BlockKind.CODE)

    @property
    def worth_translating(self) -> bool:
        """CHROME is furniture and CODE must survive byte-for-byte -- translating
        an identifier silently breaks the thing the slide is teaching. Fragments
        and captions are translated, but as labelled terms rather than prose."""
        return self not in (BlockKind.CHROME, BlockKind.CODE)


@dataclass
class Box:
    """Position on the page, normalised to 0..1 of page width/height.

    Normalising here (rather than keeping EMU for pptx and points for pdf) is
    what lets the classifier and the reading-order sort be format-agnostic.
    """

    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def area(self) -> float:
        return self.w * self.h


@dataclass
class Sentence:
    """One translation unit.

    `id` is stable across runs for the same text, which is what makes the
    translation cache and the "retranslate just this sentence" action work.
    """

    id: str
    text: str
    translation: str | None = None
    # Set when the model's output failed alignment checks and had to be retried,
    # or when the user edited it. Surfaced in the UI so trust is visible.
    flagged: bool = False
    edited: bool = False


@dataclass
class TextBlock:
    text: str
    box: Box
    kind: BlockKind = BlockKind.BODY
    sentences: list[Sentence] = field(default_factory=list)
    # Why the classifier chose `kind`. Shown in a debug view and, more
    # importantly, makes misclassification diagnosable on other people's decks.
    kind_reason: str = ""
    # Populated by the parser: font size in points (max run), bullet depth, and
    # whether the source marked it as a title placeholder.
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return _hash(self.text)


@dataclass
class Image:
    """A picture on the page. `data` is the raw bytes; the server hands these to
    the browser so the reader can show the figure a caption refers to."""

    box: Box
    data: bytes
    ext: str

    @property
    def id(self) -> str:
        return _hash_bytes(self.data)


@dataclass
class Page:
    index: int  # 0-based
    blocks: list[TextBlock] = field(default_factory=list)
    images: list[Image] = field(default_factory=list)
    notes: str = ""
    # The page's real size in points, kept only so the reader can show the
    # source at its own proportions. Everything else works in the normalised
    # coordinates of `Box`; a 4:3 deck rendered as 16:9 is not the source.
    width: float = 0.0
    height: float = 0.0

    @property
    def aspect(self) -> float:
        """Width over height, falling back to 16:9 when the file did not say."""
        if self.width > 0 and self.height > 0:
            return self.width / self.height
        return 16 / 9

    @property
    def title(self) -> str:
        for b in self.blocks:
            if b.kind is BlockKind.TITLE:
                return b.text
        return ""

    def reading_blocks(self) -> list[TextBlock]:
        return [b for b in self.blocks if b.kind.in_reading_flow]

    def figure_blocks(self) -> list[TextBlock]:
        return [b for b in self.blocks
                if b.kind in (BlockKind.CAPTION, BlockKind.FRAGMENT)]

    def hidden_blocks(self) -> list[TextBlock]:
        """Shown only when the user toggles "show hidden content" — the escape
        hatch for when the classifier gets it wrong on an unfamiliar deck."""
        return [b for b in self.blocks if b.kind is BlockKind.CHROME]


@dataclass
class Deck:
    source_path: str
    source_format: str  # "pptx" | "pdf"
    pages: list[Page] = field(default_factory=list)
    # Things the reader must be told, in their own terms: "this PDF is a scan
    # and has no text to extract". Shown in the reading view.
    warnings: list[str] = field(default_factory=list)
    # Diagnostics for whoever is debugging an extraction problem: which
    # workarounds fired, what was recovered. Never shown in the reader -- a
    # sentence about mc:AlternateContent means nothing to a student, and
    # putting it on screen only teaches them to ignore the notice area.
    notes: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        """Identifies the deck for caching. Content-derived, so moving or
        renaming the file does not throw away its translations."""
        h = hashlib.sha256()
        for p in self.pages:
            for b in p.blocks:
                h.update(b.text.encode())
        return h.hexdigest()[:16]

    def sentences(self):
        for page in self.pages:
            for block in page.blocks:
                for s in block.sentences:
                    yield page, block, s


def _hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode()).hexdigest()[:16]


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]
