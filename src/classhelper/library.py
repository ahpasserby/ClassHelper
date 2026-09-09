"""The library: the board as a real directory tree.

Earlier the board was a JSON file recording where each deck *belonged* while
the files stayed wherever they happened to be. That works until you want
anything to travel with the material — and terminology and translations both
should. A course folder that contains its decks, its glossary and its cached
translations can be copied to another machine, put in a sync folder, or backed
up, and it still works. A pointer-based board cannot do any of that.

So folders are directories, decks are files, and the tree on screen is the tree
on disk. Identity is the path relative to the library root, which means moving a
deck changes its id -- fine, because every mutation returns the whole tree and
the reader reloads from it.

Per-folder state lives in a `.classhelper` subdirectory: glossary and
translation cache, beside the material they describe.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath

from .parsers import SUPPORTED

# Everything imported lands here until filed. A real directory, so an unfiled
# deck is visible from the Finder too.
INBOX = "_inbox"
# Per-folder glossary and translation cache. Dot-prefixed so it stays out of
# the way when the folder is opened in a file manager.
META = ".classhelper"
# Deleted material goes here rather than being unlinked. The library owns these
# files now, so "remove from the board" must not mean "gone".
TRASH = ".trash"

_RESERVED = {META, TRASH}


class ImportMode(str, Enum):
    """What importing does to the original file.

    `MOVE` is the default: the point of a library is that the material lives in
    it, and a copy left behind in Downloads is the beginning of two diverging
    copies. `COPY` suits a shared drive you must not disturb; `LINK` suits a
    folder that is already organised elsewhere and should stay the original.
    """

    MOVE = "move"
    COPY = "copy"
    LINK = "link"


class LibraryError(Exception):
    """Raised with a message meant to be shown to the user unchanged."""


@dataclass
class Item:
    path: str  # relative to the root, POSIX separators
    name: str
    format: str
    missing: bool = False

    @property
    def id(self) -> str:
        return self.path


@dataclass
class Folder:
    path: str
    name: str
    depth: int
    items: list[Item] = field(default_factory=list)
    children: list["Folder"] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.path


class Library:
    def __init__(self, root: Path):
        self.root = Path(root).expanduser()

    # -- paths -------------------------------------------------------------

    def ensure(self) -> None:
        (self.root / INBOX).mkdir(parents=True, exist_ok=True)

    def _base(self) -> Path:
        return Path(os.path.realpath(self.root))

    def resolve(self, rel: str | None) -> Path:
        """Absolute path for a library-relative one, refusing to escape.

        Normalises the path *textually* rather than resolving it. Every path
        here arrives from the browser, so the traversal check is not paranoia --
        without it `../../.ssh` is a folder name -- but following symlinks would
        also reject the ones the LINK import mode deliberately puts inside the
        library.
        """
        base = self._base()
        if not rel:
            return base
        candidate = Path(os.path.normpath(base / PurePosixPath(rel)))
        if candidate != base and base not in candidate.parents:
            raise LibraryError("路径超出了课板目录。")
        return candidate

    def relative(self, path: Path) -> str:
        """The library-relative path, without following symlinks.

        `Path.resolve()` would follow a linked deck to wherever it really lives
        -- outside the library -- and a broken link would raise instead of being
        listed as missing.
        """
        absolute = Path(os.path.normpath(Path(os.path.abspath(path))))
        return PurePosixPath(absolute.relative_to(self._base())).as_posix()

    # -- reading -----------------------------------------------------------

    def tree(self) -> list[Folder]:
        self.ensure()
        return self._scan(self.root, depth=0)

    def _scan(self, directory: Path, depth: int) -> list[Folder]:
        folders: list[Folder] = []
        for child in sorted(directory.iterdir(), key=lambda p: p.name.casefold()):
            if not child.is_dir() or child.name in _RESERVED or child.name == INBOX:
                continue
            if depth == 0 and child.name.startswith("."):
                continue
            folders.append(
                Folder(
                    path=self.relative(child),
                    name=child.name,
                    depth=depth,
                    items=self._items(child),
                    children=self._scan(child, depth + 1),
                )
            )
        return folders

    def _items(self, directory: Path) -> list[Item]:
        items: list[Item] = []
        for child in sorted(directory.iterdir(), key=lambda p: p.name.casefold()):
            if child.is_dir() or child.name.startswith("."):
                continue
            if child.suffix.lower() not in SUPPORTED:
                continue
            items.append(
                Item(
                    path=self.relative(child),
                    name=child.stem,
                    format=child.suffix.lower().lstrip("."),
                    # A symlinked deck whose original has gone.
                    missing=not child.exists(),
                )
            )
        return items

    def inbox(self) -> list[Item]:
        self.ensure()
        return self._items(self.root / INBOX)

    def signature(self) -> str:
        """A short string that changes exactly when the board would look different.

        The board is a real directory tree, which means it can be rearranged in
        the Finder -- and it regularly is, because that is the point of keeping
        the material as files. So the reader cannot assume it is the only
        writer, and something has to notice.

        This is what it polls. It walks the same directories the board shows,
        and covers only what the board displays: names, structure, and whether
        a symlinked deck still resolves. Deliberately *not* mtime or size --
        translating a deck touches files constantly, and a signature that moved
        every time would send the reader refetching a tree that had not
        changed. Skipping the metadata directories keeps it cheap: on a term's
        material it is a scan of a few hundred names.
        """
        h = hashlib.sha256()
        self._sign(self.root, h, depth=0)
        self._sign(self.root / INBOX, h, depth=1)
        return h.hexdigest()[:16]

    def _sign(self, directory: Path, h, depth: int) -> None:
        try:
            children = sorted(directory.iterdir(), key=lambda p: p.name.casefold())
        except OSError:
            # A folder that vanished mid-walk is itself a change; the next scan
            # will see it gone.
            h.update(b"\x00missing")
            return

        for child in children:
            name = child.name
            if name in _RESERVED or (depth == 0 and name.startswith(".")):
                continue
            if child.is_dir():
                if name == INBOX and depth == 0:
                    continue  # walked separately, so the inbox is covered once
                # Names and nesting, with a marker for each end of a folder, so
                # moving a deck between two folders changes the string even
                # though the set of names did not. Built from names rather than
                # library-relative paths: this walk already knows where it is,
                # and the path arithmetic is the expensive part.
                h.update(f"d[{name}\n".encode())
                self._sign(child, h, depth + 1)
                h.update(b"]\n")
            elif not name.startswith(".") and child.suffix.lower() in SUPPORTED:
                # A broken symlink shows as missing on the board, so mending it
                # has to count as a change.
                h.update(f"f:{name}:{int(child.exists())}\n".encode())

    def chain(self, rel: str | None) -> list[str]:
        """Scope ids from the root down to `rel`, for glossary inheritance."""
        scopes = [""]
        if rel:
            parts = PurePosixPath(rel).parts
            scopes += [PurePosixPath(*parts[: i + 1]).as_posix()
                       for i in range(len(parts))]
        return scopes

    # -- structure ---------------------------------------------------------

    def create_folder(self, parent: str | None, name: str) -> str:
        name = _clean_name(name)
        target = self.resolve(parent) / name
        if target.exists():
            raise LibraryError(f"这里已经有一个叫「{name}」的文件夹了。")
        target.mkdir(parents=True)
        return self.relative(target)

    def rename(self, rel: str, name: str) -> str:
        source = self.resolve(rel)
        if not source.exists():
            raise LibraryError("这个项目已经不存在了。")
        # Files keep their extension; only the visible name is editable.
        suffix = source.suffix if source.is_file() else ""
        target = source.parent / (_clean_name(name) + suffix)
        if target != source and target.exists():
            raise LibraryError(f"这里已经有一个叫「{target.name}」的项目了。")
        source.rename(target)
        return self.relative(target)

    def move(self, rels: list[str], dest: str | None) -> list[str]:
        """Move items or folders into `dest` (None means the inbox)."""
        target_dir = self.resolve(INBOX if dest is None else dest)
        if not target_dir.is_dir():
            raise LibraryError("目标文件夹不存在。")

        moved: list[str] = []
        for rel in rels:
            source = self.resolve(rel)
            if not source.exists():
                continue
            if source.is_dir() and source in target_dir.parents:
                raise LibraryError("不能把一个文件夹移进它自己里面。")
            if source.parent == target_dir:
                moved.append(rel)
                continue
            destination = _unique(target_dir / source.name)
            shutil.move(str(source), str(destination))
            moved.append(self.relative(destination))
        return moved

    def delete_folder(self, rel: str) -> int:
        """Send a folder to the trash, after rescuing the decks inside it.

        Deleting a folder must never take material with it silently, so its
        decks go back to the inbox where they are visible and re-filable, and
        the folder itself goes to the trash rather than being erased.
        """
        source = self.resolve(rel)
        if not source.is_dir():
            raise LibraryError("这个文件夹不存在。")

        rescued = [
            self.relative(deck)
            for deck in sorted(source.rglob("*"))
            if deck.is_file()
            and deck.suffix.lower() in SUPPORTED
            and META not in deck.parts
        ]
        self.move(rescued, None)
        self._to_trash(source)
        return len(rescued)

    def delete_item(self, rel: str) -> None:
        source = self.resolve(rel)
        if source.exists():
            self._to_trash(source)

    def _to_trash(self, source: Path) -> None:
        trash = self.root / TRASH
        trash.mkdir(exist_ok=True)
        shutil.move(str(source), str(_unique(trash / source.name)))

    # -- importing ---------------------------------------------------------

    def import_path(self, source: Path, mode: ImportMode,
                    dest: str | None = None) -> Item:
        """Bring an existing file into the library."""
        source = Path(source).expanduser()
        if not source.exists():
            raise LibraryError(f"{source.name} 不存在。")
        if source.suffix.lower() not in SUPPORTED:
            raise LibraryError(f"{source.name} 不是支持的格式。")

        target_dir = self.resolve(INBOX if dest is None else dest)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = _unique(target_dir / source.name)

        try:
            if mode is ImportMode.MOVE:
                shutil.move(str(source), str(target))
            elif mode is ImportMode.COPY:
                shutil.copy2(source, target)
            else:
                target.symlink_to(source.resolve())
        except OSError as exc:
            raise LibraryError(f"导入 {source.name} 失败：{exc}") from exc

        return Item(
            path=self.relative(target),
            name=target.stem,
            format=target.suffix.lower().lstrip("."),
        )

    def import_bytes(self, name: str, data: bytes, dest: str | None = None) -> Item:
        """Bring in a dropped file, which arrives as content rather than a path.

        Import mode does not apply: there is no original on disk to move, copy
        or link -- the browser handed over the bytes and nothing else.
        """
        safe = _clean_name(Path(name).stem) + Path(name).suffix.lower()
        if Path(safe).suffix not in SUPPORTED:
            raise LibraryError(f"{name} 不是支持的格式。")

        target_dir = self.resolve(INBOX if dest is None else dest)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = _unique(target_dir / safe)
        target.write_bytes(data)
        return Item(
            path=self.relative(target),
            name=target.stem,
            format=target.suffix.lower().lstrip("."),
        )

    # -- per-folder state --------------------------------------------------

    def meta_dir(self, rel: str | None) -> Path:
        """Where a folder keeps its glossary and translation cache."""
        directory = self.resolve(rel) / META
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def folder_of(self, rel: str) -> str:
        parent = PurePosixPath(rel).parent.as_posix()
        return "" if parent == "." else parent


def _clean_name(name: str) -> str:
    """A name safe to use as a directory entry.

    Separators and the parent reference are stripped rather than escaped: a
    folder called `../etc` is never what someone meant to type.
    """
    cleaned = name.strip().replace("/", "／").replace("\\", "＼").strip(". ")
    if not cleaned:
        raise LibraryError("名字不能为空。")
    if len(cleaned) > 120:
        raise LibraryError("名字太长了。")
    return cleaned


def _unique(target: Path) -> Path:
    """`target`, or `target (2)`, `target (3)`... if it is taken."""
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for n in range(2, 1000):
        candidate = target.with_name(f"{stem} ({n}){suffix}")
        if not candidate.exists():
            return candidate
    raise LibraryError("同名文件太多了。")
