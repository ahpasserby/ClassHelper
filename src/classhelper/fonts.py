"""Monospace font detection.

Slides and handouts set code in a monospace face, and that face is the one
reliable, format-independent, language-independent signal that a block is code
rather than prose. It matters because translating code is actively destructive:
rename an identifier in a SQL example and the slide no longer teaches what it
was written to teach.

Matched on substrings of the font name so that weight and style suffixes
("Consolas-Bold", "CMTT10", "JetBrainsMono-Regular") all resolve correctly.
"""

from __future__ import annotations

_MONO_MARKERS = (
    "mono",         # DejaVu Sans Mono, Roboto Mono, IBM Plex Mono, PT Mono...
    "courier",
    "consol",       # Consolas
    "menlo",
    "inconsolata",
    "cmtt",         # LaTeX Computer Modern Typewriter
    "typewriter",
    "source code",
    "fira code",
    "jetbrains",
    "cascadia",
    "andale",
    "lucida console",
    "terminal",
    "hack-",
    "ubuntu mono",
)


def is_monospace(font_name: str | None) -> bool:
    if not font_name:
        return False
    name = font_name.casefold()
    return any(marker in name for marker in _MONO_MARKERS)
