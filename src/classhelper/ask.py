"""Answering a question about one sentence of a slide.

The point of this feature is that the student should not have to explain what
they are reading before they can ask about it. Selecting a sentence and typing
"why?" has to work. So every question carries, automatically:

* the sentence, and how it was translated,
* the whole slide it sits on, in both languages,
* which slide it is, out of how many, in which deck,
* the terminology already established for this course.

That context is the entire difference between a generic dictionary answer and
one that explains what the word means *in this lecture*.
"""

from __future__ import annotations

from typing import Iterator

from .config import Config
from .glossary import Glossary
from .model import BlockKind, Deck, Page, Sentence
from .providers import Provider

_SYSTEM = """\
You are helping a student who is reading a lecture slide written in {source}. \
They are studying the material for the first time and have selected one \
sentence they did not follow.

Answer in {target}.

* Answer the question they asked. Do not summarise the slide, and do not \
re-translate it -- they already have the translation.
* Explain the term as it is used *in this course*, on this slide. A term can \
mean something different in another field; the slide's context decides.
* Be brief and concrete. A short paragraph is usually right. Use an example \
from the slide's own material when one helps.
* Keep technical terms in {source} alongside your explanation, so the student \
can still follow the lecture and the textbook.
* If the slide genuinely does not contain enough to answer, say so and explain \
what is missing, rather than inventing detail.\
"""


class Asker:
    def __init__(self, provider: Provider, cfg: Config, glossary: Glossary):
        self._provider = provider
        self._cfg = cfg
        self._glossary = glossary

    def stream(
        self,
        deck: Deck,
        page: Page,
        question: str,
        sentences: list[Sentence] | None = None,
        history: list[dict] | None = None,
        pages: list[int] | None = None,
    ) -> Iterator[str]:
        from .translate import _lang

        system = _SYSTEM.format(
            source=_lang(self._cfg.source_lang, "the source language"),
            target=_lang(self._cfg.target_lang, self._cfg.target_lang),
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user",
             "content": self._context(deck, page, sentences or [], pages or [])},
            {"role": "assistant", "content": "Understood. What is your question?"},
        ]
        # Follow-ups keep the thread coherent without resending the slide.
        messages += list(history or [])
        messages.append({"role": "user", "content": question})

        return self._provider.stream(messages, model=self._cfg.ask_model)

    def _context(self, deck: Deck, page: Page, sentences: list[Sentence],
                 pages: list[int]) -> str:
        """Everything the model needs that the student should not have to type.

        `pages` is the set of slides they picked out in the chapter list. A
        slide often only makes sense with another one -- a definition given on
        12 and used on 20 -- and which one that is is something they know and
        the program does not. Empty means the slide they are reading, which is
        the common case and the cheap one.

        The slide the question is anchored to is always included, whatever they
        picked: the sentences they selected are on it.
        """
        from pathlib import Path

        wanted = sorted({page.index} | {
            i for i in pages if 0 <= i < len(deck.pages)
        })

        parts = [
            f"Deck: {Path(deck.source_path).stem}",
            f"Slide {page.index + 1} of {len(deck.pages)}"
            + (f" — {page.title}" if page.title else ""),
        ]
        if len(wanted) > 1:
            listed = ", ".join(str(i + 1) for i in wanted)
            parts.append(
                f"The student chose slides {listed} as the context for this "
                f"question; they are reading slide {page.index + 1}."
            )
        parts.append("")

        for index in wanted:
            here = deck.pages[index]
            if len(wanted) > 1:
                mark = " (the slide being read)" if index == page.index else ""
                title = f" — {here.title}" if here.title else ""
                parts.append(f"--- Slide {index + 1}{title}{mark} ---")
            else:
                parts.append("The full slide, with the translation shown to the student:")
            parts.extend(self._page_lines(here))
            parts.append("")

        if sentences:
            # Numbered when there are several: a question like "how do these two
            # differ" needs the model to be able to refer to them separately.
            label = ("The student selected this sentence:" if len(sentences) == 1
                     else f"The student selected these {len(sentences)} sentences:")
            parts.append(label)
            for n, unit in enumerate(sentences, 1):
                prefix = "  " if len(sentences) == 1 else f"  {n}. "
                parts.append(f"{prefix}{unit.text}")
                if unit.translation:
                    parts.append(f"     (shown to them as: {unit.translation})")

        terms = self._glossary.prompt_block(limit=30)
        if terms:
            parts += ["", "Terminology established in this course:", terms]

        return "\n".join(parts)

    @staticmethod
    def _page_lines(page: Page) -> list[str]:
        lines: list[str] = []
        for block in page.blocks:
            if block.kind is BlockKind.CHROME:
                continue
            marker = "#" if block.kind is BlockKind.TITLE else "-"
            for unit in block.sentences or []:
                lines.append(f"{marker} {unit.text}")
                if unit.translation:
                    lines.append(f"    {unit.translation}")
            if not block.sentences:
                lines.append(f"{marker} {block.text}")
        return lines
