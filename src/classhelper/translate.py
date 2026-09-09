"""Page-at-a-time translation with alignment guarantees.

Three things here are load-bearing.

**Whole-page context.** A bullet means what it means because of the slide it is
on. Sent alone, `Key` in a database course becomes the thing that opens a door.
Every request therefore carries the full page, and asks for translations of only
the numbered units within it.

**Numbered units, checked on return.** Given a list of sentences, a model will
cheerfully merge two of them, drop one, or return nine for ten. The reader puts
each translation directly under its own line of English, so a single dropped
unit shifts everything after it and silently corrupts the rest of the page. So
units go out with ids, come back keyed by id, and every id is verified. Missing
ones are re-requested on their own; anything still missing is marked failed and
shown as failed, never guessed at.

**A glossary that accumulates.** Terms established on slide 3 are sent with the
request for slide 20, so a course reads consistently instead of renaming its
concepts every few pages.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .cache import Cache
from .config import Config
from .glossary import Glossary
from .model import BlockKind, Deck, Page, Sentence, TextBlock
from .providers import Provider, ProviderError
from .segment import segment_block

_MAX_ALIGNMENT_RETRIES = 2

# Rendering a code point of a language tag is not worth a dependency; these are
# the tags people actually put in the config, and anything else falls through as
# the tag itself, which models handle fine.
_LANG_NAMES = {
    "zh-cn": "Simplified Chinese", "zh-hans": "Simplified Chinese",
    "zh-tw": "Traditional Chinese", "zh-hant": "Traditional Chinese",
    "en": "English", "ja": "Japanese", "ko": "Korean", "fr": "French",
    "de": "German", "es": "Spanish", "pt": "Portuguese", "ru": "Russian",
    "it": "Italian", "ar": "Arabic", "hi": "Hindi", "vi": "Vietnamese",
    "th": "Thai", "id": "Indonesian", "tr": "Turkish", "pl": "Polish",
}

_SYSTEM = """\
You translate lecture slides for a student who attends class in {source} but \
reads {target} far more comfortably. They see your translation directly beneath \
each line of the original, and use it to keep up with the lecture.

Translate the meaning. Produce {target} that someone in this field would \
actually write, not a word-by-word transposition of the source.

Rules:

1. You are given the whole slide for context, then a numbered list of units. \
Translate ONLY the numbered units.
2. Return exactly one translation for every id you were given. Never merge, \
split, reorder, add, or omit a unit. Each translation is displayed under its own \
line of source text, so a missing unit corrupts every line after it.
3. Leave in the original language anything that {target} speakers in this field \
do not translate: product and tool names, standard acronyms and initialisms, \
identifiers, code, file names, mathematical notation, and units of measurement.
4. Technical terms have settled translations in their field. Use them, not a \
literal rendering. When a term appears in the glossary, use exactly the given \
form.
5. Match the register of the source. A heading stays a short heading. A bullet \
stays a bullet. Do not expand a fragment into a full sentence.
6. A unit marked as a label or a table cell is a term, not a sentence. Translate \
it as a term.
7. Add nothing. No explanations, no notes, no restored ellipses, no commentary.

Also report technical terms you translated that are likely to appear again later \
in this course, so that later slides stay consistent with this one.

Respond with JSON only:
{{"units": {{"<id>": "<translation>"}}, "glossary": {{"<source term>": "<translation>"}}}}\
"""


@dataclass
class Unit:
    """One sentence on its way to the model."""

    id: int
    sentence: Sentence
    block: TextBlock

    @property
    def hint(self) -> str:
        if self.block.meta.get("has_math"):
            return " (contains an equation; reproduce the notation exactly)"
        if self.block.kind is BlockKind.TITLE:
            return " (slide heading)"
        if self.block.kind is BlockKind.FRAGMENT:
            return " (label in a diagram or table cell)"
        if self.block.kind is BlockKind.CAPTION:
            return " (caption on a figure)"
        return ""


@dataclass
class PageResult:
    page: int
    translated: int = 0
    from_cache: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed and not self.errors


class Translator:
    def __init__(
        self,
        provider: Provider,
        cfg: Config,
        glossary: Glossary,
        cache: Cache | None = None,
    ):
        self._provider = provider
        self._cfg = cfg
        self._glossary = glossary
        self._cache = cache if cache is not None else Cache()

    # -- public ------------------------------------------------------------

    def translate_page(self, deck: Deck, page: Page) -> PageResult:
        result = PageResult(page=page.index)
        units = self._collect(page, result)
        if not units:
            return result

        pending = {u.id: u for u in units}
        for attempt in range(_MAX_ALIGNMENT_RETRIES + 1):
            try:
                answers, terms = self._ask(deck, page, list(pending.values()))
            except ProviderError as exc:
                result.errors.append(str(exc))
                break

            self._glossary.observe(terms)
            for uid, text in answers.items():
                unit = pending.pop(uid, None)
                if unit is None:
                    continue  # a unit we did not ask for; ignore rather than trust
                self._accept(unit, text, result)

            if not pending:
                break
            if attempt < _MAX_ALIGNMENT_RETRIES:
                result.errors.append(
                    f"{len(pending)} unit(s) missing from the reply; re-requesting"
                )

        # Whatever never came back is reported as failed. The reader shows these
        # as "translation failed, retry" -- an honest gap the user can act on,
        # rather than a plausible sentence nobody asked for.
        for unit in pending.values():
            unit.sentence.flagged = True
            result.failed += 1

        self._glossary.save()
        return result

    def translate_deck(self, deck: Deck, on_page=None) -> list[PageResult]:
        """Translate every page, current-page-first ordering left to the caller.

        Pages run on a small thread pool because each is one network round trip
        and they are independent. The glossary is shared and locked, so pages
        translated early still inform the prompt of pages translated later.
        """
        results: list[PageResult] = []
        with ThreadPoolExecutor(max_workers=self._cfg.max_workers) as pool:
            for result in pool.map(lambda p: self.translate_page(deck, p), deck.pages):
                results.append(result)
                if on_page:
                    on_page(result)
        return results

    def lock_term(self, deck: Deck, term: str, translation: str) -> list[PageResult]:
        """Pin a term, then rebuild every sentence that used it.

        The cache is the subtle part. Correcting a term and seeing nothing
        change is exactly what would happen without this: the sentences are
        already cached, so the next pass serves the old wording straight back
        and the correction appears to do nothing. Locking a term therefore
        *invalidates* the sentences containing it.

        The sentences are re-asked rather than string-substituted. Swapping a
        word inside finished prose leaves the grammar built around the old
        term; asking again rebuilds the sentence around the right one.
        """
        self._glossary.set(term, translation)

        # Word boundaries so that locking "key" does not drag in "monkey".
        pattern = re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
        affected: list[Page] = []

        for page in deck.pages:
            touched = False
            for block in page.blocks:
                if not block.kind.worth_translating:
                    continue
                if not block.sentences:
                    segment_block(block)
                for sentence in block.sentences:
                    if not pattern.search(sentence.text):
                        continue
                    self._cache.drop(sentence.text, self._cfg.target_lang,
                                     self._cfg.model)
                    sentence.translation = None
                    sentence.flagged = False
                    touched = True
            if touched:
                affected.append(page)

        return [self.translate_page(deck, page) for page in affected]

    def retranslate(self, deck: Deck, page: Page, sentence: Sentence) -> PageResult:
        """Re-ask for one sentence, ignoring what is cached.

        The escape hatch behind "this reads wrong" and behind a glossary edit:
        after a term is locked, the sentences that used it are re-asked rather
        than string-substituted, so the whole sentence is rebuilt around the
        correct term instead of having a word swapped inside it.
        """
        self._cache.drop(sentence.text, self._cfg.target_lang, self._cfg.model)
        sentence.translation = None
        sentence.flagged = False

        block = next((b for b in page.blocks if sentence in b.sentences), None)
        if block is None:
            return PageResult(page=page.index, failed=1)

        result = PageResult(page=page.index)
        unit = Unit(id=1, sentence=sentence, block=block)
        try:
            answers, terms = self._ask(deck, page, [unit])
        except ProviderError as exc:
            result.errors.append(str(exc))
            sentence.flagged = True
            result.failed += 1
            return result

        self._glossary.observe(terms)
        if 1 in answers:
            self._accept(unit, answers[1], result)
        else:
            sentence.flagged = True
            result.failed += 1
        return result

    # -- internals ---------------------------------------------------------

    def _collect(self, page: Page, result: PageResult) -> list[Unit]:
        """Split the page into units, serving whatever the cache already holds."""
        units: list[Unit] = []
        for block in page.blocks:
            if not block.kind.worth_translating:
                continue
            if not block.sentences:
                segment_block(block)
            for sentence in block.sentences:
                if sentence.translation and not sentence.flagged:
                    continue
                hit = self._cache.get(sentence.text, self._cfg.target_lang,
                                      self._cfg.model)
                if hit:
                    sentence.translation = hit
                    result.from_cache += 1
                    continue
                units.append(Unit(id=len(units) + 1, sentence=sentence, block=block))
        return units

    def _accept(self, unit: Unit, text, result: PageResult) -> None:
        text = str(text).strip()
        if not text:
            unit.sentence.flagged = True
            result.failed += 1
            return

        source = unit.sentence.text
        # Two cheap sanity checks. Neither rejects the translation -- the model
        # is usually right and the user can see both languages -- but a flagged
        # sentence is marked in the reader so suspicion is visible rather than
        # buried.
        if len(text) > 4 * len(source) + 60:
            unit.sentence.flagged = True
        elif text == source and len(source.split()) > 3:
            unit.sentence.flagged = True

        unit.sentence.translation = text
        self._cache.put(source, self._cfg.target_lang, self._cfg.model, text)
        result.translated += 1

    def _ask(self, deck: Deck, page: Page, units: list[Unit]):
        system = _SYSTEM.format(
            source=_lang(self._cfg.source_lang, "the source language"),
            target=_lang(self._cfg.target_lang, self._cfg.target_lang),
        )
        raw = self._provider.complete(
            system,
            self._user_message(deck, page, units),
            model=self._cfg.model,
            json_mode=True,
            temperature=0.2,  # translation wants consistency, not invention
        )
        return _parse(raw)

    def _user_message(self, deck: Deck, page: Page, units: list[Unit]) -> str:
        parts = [
            f"Deck: {_deck_name(deck)}",
            f"Slide {page.index + 1} of {len(deck.pages)}"
            + (f": {page.title}" if page.title else ""),
            "",
            "Full slide text, for context only:",
            _page_context(page) or "(no text)",
        ]

        terms = self._glossary.prompt_block()
        if terms:
            parts += ["", "Glossary established for this course. Use exactly:", terms]

        if self._cfg.keep_verbatim:
            parts += ["", "Always leave these unchanged: "
                      + ", ".join(self._cfg.keep_verbatim)]

        parts += ["", f"Translate these {len(units)} units:"]
        parts += [f"[{u.id}]{u.hint} {u.sentence.text}" for u in units]
        return "\n".join(parts)


def _page_context(page: Page) -> str:
    lines = []
    for block in page.blocks:
        if block.kind is BlockKind.CHROME:
            continue
        prefix = "# " if block.kind is BlockKind.TITLE else ""
        lines.append(f"{prefix}{block.text}")
    return "\n".join(lines)


def _deck_name(deck: Deck) -> str:
    from pathlib import Path
    return Path(deck.source_path).stem


def _lang(tag: str, fallback: str) -> str:
    if not tag or tag == "auto":
        return fallback
    return _LANG_NAMES.get(tag.casefold(), tag)


def _parse(raw: str) -> tuple[dict[int, str], dict[str, str]]:
    """Read the model's JSON, tolerating the usual deviations.

    Even in JSON mode a reply can arrive fenced in a code block, and unit ids
    come back as strings as often as numbers. Both are trivially recoverable and
    not worth failing a page over.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"Reply was not valid JSON: {exc}") from exc

    units: dict[int, str] = {}
    for key, value in (data.get("units") or {}).items():
        try:
            units[int(str(key).strip().strip("[]"))] = value
        except (TypeError, ValueError):
            continue

    glossary = {
        str(k): str(v)
        for k, v in (data.get("glossary") or {}).items()
        if isinstance(k, str) and v
    }
    return units, glossary
