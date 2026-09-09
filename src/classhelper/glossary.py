"""Per-course terminology.

The single highest-leverage feature in the project. A model will render
`relation` three different ways across three slides, and the reader has to
re-learn the term each time. Worse, it will confidently translate `key` in a
database course as the thing that opens a door.

No model fixes this reliably. A person fixing it once does. So a term the user
sets is *locked*: it is fed to every later request and never overwritten by a
model's suggestion. Terms the model proposes are kept too, but only to hold the
deck self-consistent, and they yield to the user without argument.

Scoped to the folder the slides live in, because that is how course material is
actually organised -- Lec01, Lec02 and Lec03 sit together, and terminology
should carry between them.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import state_dir

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class Entry:
    translation: str
    # Set by the user. Locked entries are authoritative and survive every
    # subsequent run; unlocked ones are the model's own working consistency.
    locked: bool = False
    count: int = 0


class Glossary:
    def __init__(self, path: Path):
        self._path = path
        self._terms: dict[str, Entry] = {}
        self._lock = threading.Lock()
        # Separate from the state lock. Pages are translated on several threads
        # and each saves when it finishes, so the write itself has to be
        # serialised -- see save().
        self._io_lock = threading.Lock()
        self._load()

    # -- reading -----------------------------------------------------------

    def __len__(self) -> int:
        return len(self._terms)

    def get(self, term: str) -> Entry | None:
        return self._terms.get(term.casefold())

    def items(self) -> list[tuple[str, Entry]]:
        with self._lock:
            return sorted(
                self._terms.items(),
                # Locked first, then by how often the term has come up: the
                # prompt has finite room and these are the ones worth spending
                # it on.
                key=lambda kv: (not kv[1].locked, -kv[1].count, kv[0]),
            )

    def prompt_block(self, limit: int = 60) -> str:
        """The glossary as the model should see it. Empty string when unset, so
        the caller can leave the section out entirely rather than send a header
        with nothing under it."""
        rows = [f"{term} -> {entry.translation}"
                for term, entry in self.items()[:limit]]
        return "\n".join(rows)

    # -- writing -----------------------------------------------------------

    def observe(self, proposed: dict[str, str]) -> None:
        """Record terms a model proposed while translating a page.

        Never overrides a locked entry, and never overrides an existing
        unlocked one either: the first rendering used in the deck becomes the
        deck's rendering, which is the whole point.
        """
        with self._lock:
            for term, translation in proposed.items():
                if not term.strip() or not str(translation).strip():
                    continue
                key = term.strip().casefold()
                existing = self._terms.get(key)
                if existing is None:
                    self._terms[key] = Entry(str(translation).strip(), count=1)
                else:
                    existing.count += 1

    def set(self, term: str, translation: str) -> None:
        """A user correction. Authoritative from here on."""
        with self._lock:
            key = term.strip().casefold()
            existing = self._terms.get(key)
            count = existing.count if existing else 0
            self._terms[key] = Entry(translation.strip(), locked=True, count=count)
        self.save()

    def unset(self, term: str) -> None:
        with self._lock:
            self._terms.pop(term.strip().casefold(), None)
        self.save()

    # -- persistence -------------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return  # a corrupt glossary is a rebuildable annoyance, not a crash
        self._terms = {
            term: Entry(**entry)
            for term, entry in raw.get("terms", {}).items()
            if isinstance(entry, dict) and "translation" in entry
        }

    def save(self) -> None:
        """Write the glossary out atomically, and safely from several threads.

        Three things matter, and each one was a real failure first.

        Writing to a temporary file and renaming means a crash mid-write cannot
        leave a truncated glossary behind.

        The temporary file needs a *unique* name: with a fixed one, two worker
        threads finishing their pages together race, and the first rename
        deletes the second thread's file out from under it.

        And the snapshot has to be taken inside the same lock as the write.
        Snapshotting outside it lets a slow writer rename an older view over a
        newer one, quietly losing whatever terms were learned in between.
        """
        with self._io_lock:
            with self._lock:
                payload = {"terms": {t: asdict(e) for t, e in self._terms.items()}}

            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(f"{self._path.name}.{os.getpid()}.tmp")
            try:
                tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                               encoding="utf-8")
                tmp.replace(self._path)
            except OSError:
                tmp.unlink(missing_ok=True)
                raise


@dataclass
class Resolved:
    """One term as the reader should see it, and where it came from."""

    term: str
    translation: str
    locked: bool
    count: int
    scope: str      # the scope that supplied this rendering
    inherited: bool  # true when it came from a broader scope than the current one


class ScopedGlossary:
    """A stack of glossaries, from the broadest scope to the narrowest.

    Terminology is not flat. `set` means one thing in a database course and
    another in a topology course, while `algorithm` means the same in both. So
    a term can be pinned globally, for a semester, or for one course, and the
    narrower scope simply wins.

    Inheritance is the point: opening a course's glossary shows everything that
    already applies to it, without anything having been copied. Editing there
    writes to that course alone -- the broader entry is untouched and keeps
    applying everywhere else.

    Presents the same surface as a single `Glossary`, so the translator neither
    knows nor cares how many layers are underneath.
    """

    def __init__(self, scopes: list[str], target_lang: str):
        if not scopes:
            scopes = [GLOBAL_SCOPE]
        self.scopes = scopes
        self._layers = [_glossary_for(scope, target_lang) for scope in scopes]

    @property
    def current(self) -> Glossary:
        """The narrowest layer -- where edits and new terms land."""
        return self._layers[-1]

    @property
    def current_scope(self) -> str:
        return self.scopes[-1]

    # -- reading -----------------------------------------------------------

    def resolved(self) -> list[Resolved]:
        """Every term that applies here, narrower definitions winning."""
        merged: dict[str, Resolved] = {}
        for scope, layer in zip(self.scopes, self._layers):
            for term, entry in layer.items():
                merged[term] = Resolved(
                    term=term,
                    translation=entry.translation,
                    locked=entry.locked,
                    count=entry.count,
                    scope=scope,
                    inherited=scope != self.current_scope,
                )
        return sorted(
            merged.values(),
            key=lambda r: (not r.locked, r.inherited, -r.count, r.term),
        )

    def __len__(self) -> int:
        return len(self.resolved())

    def items(self) -> list[tuple[str, Entry]]:
        return [(r.term, Entry(r.translation, r.locked, r.count))
                for r in self.resolved()]

    def prompt_block(self, limit: int = 60) -> str:
        rows = [f"{r.term} -> {r.translation}" for r in self.resolved()[:limit]]
        return "\n".join(rows)

    # -- writing -----------------------------------------------------------

    def observe(self, proposed: dict[str, str]) -> None:
        """Record terms a model proposed, without shadowing what is inherited.

        A rendering that already applies from a broader scope needs no copy in
        the narrower one; duplicating it would freeze the course's wording
        against a later correction made at semester level.
        """
        known = {r.term for r in self.resolved()}
        fresh = {t: v for t, v in proposed.items()
                 if t.strip().casefold() not in known}
        self.current.observe(fresh)

    def set(self, term: str, translation: str, scope: str | None = None) -> None:
        """Pin a term, by default at the narrowest scope in view."""
        self._layer(scope).set(term, translation)

    def unset(self, term: str, scope: str | None = None) -> None:
        """Drop a term from one scope, revealing whatever it was overriding."""
        self._layer(scope).unset(term)

    def save(self) -> None:
        for layer in self._layers:
            layer.save()

    def _layer(self, scope: str | None) -> Glossary:
        if scope is None:
            return self.current
        try:
            return self._layers[self.scopes.index(scope)]
        except ValueError:
            raise KeyError(f"{scope!r} is not in this glossary's scope chain") from None


GLOBAL_SCOPE = "global"

# Set once at startup to the library root, so a glossary is stored inside the
# folder it applies to: a course directory carries its own terminology, and
# copying that directory to another machine takes the terminology with it.
_library_root: Path | None = None


def use_library(root: Path | None) -> None:
    global _library_root
    _library_root = Path(root) if root else None


def _glossary_for(scope: str, target_lang: str) -> Glossary:
    suffix = f"glossary.{_SAFE.sub('-', target_lang).casefold()}.json"
    if _library_root is not None:
        folder = _library_root if scope in ("", GLOBAL_SCOPE) else _library_root / scope
        return Glossary(folder / ".classhelper" / suffix)
    # No library configured (tests, and decks opened by path): fall back to the
    # program's own directory.
    name = _SAFE.sub("-", f"{scope or GLOBAL_SCOPE}-{target_lang}").strip("-").casefold()
    return Glossary(state_dir() / "glossary" / f"{name}.json")


def for_scopes(scopes: list[str], target_lang: str) -> ScopedGlossary:
    return ScopedGlossary(scopes, target_lang)


def for_deck(source_path: str, target_lang: str) -> ScopedGlossary:
    """Fallback for a deck opened from outside the library.

    Scoped to the folder it sits in, which is how course material is usually
    organised on disk anyway, and still layered under the global glossary.
    """
    folder = Path(source_path).resolve().parent.name or "default"
    return ScopedGlossary([GLOBAL_SCOPE, f"path-{folder}"], target_lang)
