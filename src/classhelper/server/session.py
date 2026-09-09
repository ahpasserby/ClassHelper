"""One open deck, and the work happening to it.

The scheduling idea here is the whole reason the reader feels fast. Translating
a deck front-to-back means slide 30 is done last, which is fine, and slide 3 is
done third, which is not -- you are looking at it now. So pages are translated
in order of distance from the page you are actually on, and that ordering is
re-evaluated every time you turn a page. Skip to slide 25 and slide 25 is next,
regardless of what the queue looked like a moment ago.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue

from ..ask import Asker
from ..cache import Cache
from ..classify import classify
from ..config import Config
from ..glossary import for_deck, for_scopes
from ..model import Deck, Page, Sentence
from ..parsers import parse
from ..providers import build as build_provider
from ..render import Renderer
from ..segment import segment_block
from ..translate import Translator


@dataclass
class Event:
    """Something the reader should react to. Delivered over SSE."""

    type: str
    data: dict = field(default_factory=dict)


class DeckSession:
    def __init__(self, path: str, cfg: Config, scopes: list[str] | None = None,
                 cache_dir: Path | None = None):
        self.id = uuid.uuid4().hex[:12]
        self.path = str(Path(path).resolve())
        self.cfg = cfg
        # Where this deck sits on the board, broadest scope first. Decks that
        # are not on the board fall back to their folder on disk.
        self.scopes = scopes

        self.deck: Deck = parse(self.path)
        classify(self.deck)
        for page in self.deck.pages:
            for block in page.blocks:
                if block.kind.worth_translating:
                    segment_block(block)

        self.glossary = (
            for_scopes(scopes, cfg.target_lang) if scopes
            else for_deck(self.path, cfg.target_lang)
        )
        self.provider = build_provider(cfg)
        # Pictures of the source pages, for the side-by-side view. Built here
        # rather than on first request so a .pptx conversion is already running
        # by the time the reader asks for page one.
        self.renderer = Renderer(self.path)
        # Cached translations live beside the deck, so a course folder carries
        # its own work: copy the folder elsewhere and nothing is re-translated.
        self.cache = Cache(
            (cache_dir / "translations.db") if cache_dir else None
        )
        self.translator = Translator(self.provider, cfg, self.glossary, self.cache)
        self.asker = Asker(self.provider, cfg, self.glossary)

        self._lock = threading.Lock()
        self._pending: set[int] = set()
        self._done: set[int] = set()
        self._current = 0
        self._subscribers: list[Queue[Event]] = []
        self._workers: list[threading.Thread] = []
        self._stop = threading.Event()

    # -- events ------------------------------------------------------------

    def subscribe(self) -> Queue[Event]:
        queue: Queue[Event] = Queue()
        with self._lock:
            self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: Queue[Event]) -> None:
        with self._lock:
            if queue in self._subscribers:
                self._subscribers.remove(queue)

    def emit(self, event: Event) -> None:
        with self._lock:
            targets = list(self._subscribers)
        for queue in targets:
            queue.put(event)

    # -- translation -------------------------------------------------------

    def start(self, current_page: int = 0) -> None:
        with self._lock:
            if self._workers:
                return
            self._current = current_page
            self._pending = {
                p.index for p in self.deck.pages if self._untranslated(p)
            }
            # Pages already fully cached need no worker at all; report them as
            # done immediately so the progress bar tells the truth on reopen.
            self._done = {p.index for p in self.deck.pages} - self._pending

        for _ in range(max(1, self.cfg.max_workers)):
            worker = threading.Thread(target=self._run, daemon=True)
            worker.start()
            self._workers.append(worker)

    def stop(self) -> None:
        self._stop.set()

    def focus(self, page_index: int) -> None:
        """Tell the scheduler which page the reader is looking at."""
        with self._lock:
            self._current = page_index

    @property
    def progress(self) -> dict:
        with self._lock:
            done, pending = len(self._done), len(self._pending)
        usage = self.provider.usage
        return {
            "done": done,
            "total": done + pending,
            "calls": usage.calls,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
        }

    def _untranslated(self, page: Page) -> bool:
        return any(
            s.translation is None
            for b in page.blocks if b.kind.worth_translating
            for s in b.sentences
        )

    def _claim(self) -> Page | None:
        """Take the pending page nearest the one being read."""
        with self._lock:
            if not self._pending:
                return None
            index = min(self._pending, key=lambda i: (abs(i - self._current), i))
            self._pending.discard(index)
        return self.deck.pages[index]

    def _run(self) -> None:
        while not self._stop.is_set():
            page = self._claim()
            if page is None:
                self.emit(Event("idle", self.progress))
                return
            try:
                result = self.translator.translate_page(self.deck, page)
                errors = result.errors
            except Exception as exc:  # a worker must never die silently
                errors = [str(exc)]

            with self._lock:
                self._done.add(page.index)

            self.emit(Event("page", {
                "page": page.index,
                "blocks": page_payload(page),
                "errors": errors,
                "progress": self.progress,
            }))

    # -- lookups -----------------------------------------------------------

    def find_sentence(self, sentence_id: str) -> tuple[Page, Sentence] | None:
        for page, _, sentence in self.deck.sentences():
            if sentence.id == sentence_id:
                return page, sentence
        return None

    def image(self, image_id: str):
        for page in self.deck.pages:
            for img in page.images:
                if img.id == image_id:
                    return img
        return None

    # -- serialisation -----------------------------------------------------

    def payload(self) -> dict:
        return {
            "id": self.id,
            "name": Path(self.path).stem,
            "path": self.path,
            "format": self.deck.source_format,
            "warnings": self.deck.warnings,
            "notes": self.deck.notes,
            "target_lang": self.cfg.target_lang,
            "scopes": self.scopes or [],
            "pages": [
                {
                    "index": page.index,
                    "title": page.title,
                    # The source page's own proportions. The side-by-side view
                    # needs them to lay out a slide before its image arrives,
                    # and a 4:3 deck shown as 16:9 is not the source.
                    "aspect": round(page.aspect, 6),
                    # In points, so a type size taken from the file can be
                    # turned back into a fraction of the page.
                    "height_pt": round(page.height, 2),
                    "blocks": page_payload(page),
                    "images": [
                        {"id": im.id, "box": _box(im.box)} for im in page.images
                    ],
                }
                for page in self.deck.pages
            ],
            "progress": self.progress,
            "source": self.renderer.source.status(),
        }


def page_payload(page: Page) -> list[dict]:
    return [
        {
            "id": block.id,
            "kind": block.kind.value,
            "reason": block.kind_reason,
            "text": block.text,
            "box": _box(block.box),
            # Laid out as a list when the source said so; a bulleted slide read
            # as one run-on paragraph before this was carried through.
            "bullet": bool(block.meta.get("bullet")),
            "level": int(block.meta.get("bullet_depth") or 0),
            # Only the side-by-side view uses these: without a real render of
            # the page it draws one from the geometry, and type size is most of
            # what tells a title from a footnote at a glance.
            "font": float(block.meta.get("font_pt") or 0.0),
            "mono": bool(block.meta.get("mono")),
            # Which shape on the page this came from. Several blocks routinely
            # share one -- a bulleted list is one text box and five blocks --
            # and they all carry that box's position, so the drawn fallback has
            # to stack them inside it rather than pile them on the same spot.
            "shape": str(block.meta.get("shape_id") or block.id),
            "sentences": [
                {
                    "id": s.id,
                    "text": s.text,
                    "translation": s.translation,
                    "flagged": s.flagged,
                    "edited": s.edited,
                }
                for s in block.sentences
            ],
        }
        # Furniture is sent too, not filtered out here. The reader hides it by
        # default but can reveal it, which is the escape hatch for a deck whose
        # conventions the classifier misread.
        for block in page.blocks
    ]


def _box(box) -> dict:
    return {"x": box.x, "y": box.y, "w": box.w, "h": box.h}


class SessionStore:
    """Open decks, keyed by id. Single user, single process, so a dict is the
    right amount of machinery."""

    def __init__(self):
        self._sessions: dict[str, DeckSession] = {}
        self._lock = threading.Lock()

    def open(self, path: str, cfg: Config, scopes: list[str] | None = None,
             cache_dir: Path | None = None) -> DeckSession:
        with self._lock:
            for session in self._sessions.values():
                if session.path == str(Path(path).resolve()):
                    return session  # reopening a deck resumes it
        session = DeckSession(path, cfg, scopes, cache_dir)
        with self._lock:
            self._sessions[session.id] = session
        return session

    def get(self, deck_id: str) -> DeckSession | None:
        return self._sessions.get(deck_id)

    def all(self) -> list[DeckSession]:
        with self._lock:
            return list(self._sessions.values())

    def close(self, deck_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(deck_id, None)
        if session:
            session.stop()
