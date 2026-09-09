"""Board and glossary endpoints, over the on-disk library.

The board is a directory tree, so these are thin: create a folder, rename,
move, import. Identity is the path relative to the library root, which travels
in request bodies rather than URLs -- a relative path contains slashes, and
encoding them into a path parameter only invites the two sides to disagree
about escaping.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import Config, ConfigError, DEFAULT_USER_DATA, LIBRARY_DIRNAME
from ..config import load as load_config, use_state_dir
from ..glossary import for_scopes, use_library as glossary_use_library
from ..library import INBOX, ImportMode, Library, LibraryError
from ..parsers import SUPPORTED

router = APIRouter(prefix="/api/board", tags=["board"])
glossary_router = APIRouter(prefix="/api/glossary", tags=["glossary"])

_library: Library | None = None


def use_library(library: Library) -> None:
    """Point the board, and the glossaries, at one library root."""
    global _library
    _library = library
    library.ensure()
    glossary_use_library(library.root)


def library() -> Library:
    """The library, created from configuration on first use.

    Lazy rather than wired up at startup so the board still works before the
    translation service has been configured -- organising material is useful on
    its own, and refusing to show the tree until an API key exists would be
    strange.
    """
    if _library is None:
        try:
            cfg = load_config()
            root, state = cfg.library_path, cfg.state_path
        except ConfigError:
            root = str(DEFAULT_USER_DATA / LIBRARY_DIRNAME)
            state = str(DEFAULT_USER_DATA / "用户配置")
        use_state_dir(Path(state))
        use_library(Library(Path(root)))
    assert _library is not None
    return _library


def _guard(action):
    """Turn a library rule into a 400 with the message the user should see."""
    try:
        return action()
    except LibraryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"文件系统错误：{exc}") from exc


# -- serialisation ---------------------------------------------------------

def item_payload(item) -> dict:
    return {
        "id": item.path,
        "name": item.name,
        "path": item.path,
        "folder": str(Path(item.path).parent) if "/" in item.path else None,
        "missing": item.missing,
        "format": item.format,
    }


def folder_payload(folder) -> dict:
    return {
        "id": folder.path,
        "name": folder.name,
        "parent": folder.path.rsplit("/", 1)[0] if "/" in folder.path else None,
        "depth": folder.depth,
        "items": [item_payload(i) for i in folder.items],
        "children": [folder_payload(c) for c in folder.children],
    }


def board_payload() -> dict:
    lib = library()
    return {
        "folders": [folder_payload(f) for f in lib.tree()],
        "inbox": [item_payload(i) for i in lib.inbox()],
        "formats": list(SUPPORTED),
        "root": str(lib.root),
        # Sent with the board so a client that just changed something knows the
        # version it is already holding, and does not fetch it straight back.
        "version": lib.signature(),
    }


# -- structure -------------------------------------------------------------

class NewFolder(BaseModel):
    parent: str | None = None
    name: str


class Rename(BaseModel):
    path: str
    name: str


class Move(BaseModel):
    paths: list[str]
    dest: str | None = None  # None means the inbox


class Target(BaseModel):
    path: str


class ImportPaths(BaseModel):
    paths: list[str]
    dest: str | None = None


@router.get("")
def get_board():
    return board_payload()


@router.get("/version")
def board_version():
    """Has anything on the board changed?

    Separate from the board itself because the reader asks this every couple of
    seconds and the answer is sixteen bytes. Fetching the whole tree that often
    to find out it is unchanged would be silly.
    """
    return {"version": library().signature()}


@router.post("/folders")
def create_folder(body: NewFolder):
    path = _guard(lambda: library().create_folder(body.parent, body.name))
    return {"id": path, "board": board_payload()}


@router.post("/rename")
def rename(body: Rename):
    path = _guard(lambda: library().rename(body.path, body.name))
    return {"id": path, "board": board_payload()}


@router.post("/move")
def move(body: Move):
    """Move a whole selection at once, so the board is never half-moved."""
    moved = _guard(lambda: library().move(body.paths, body.dest))
    return {"moved": moved, "board": board_payload()}


@router.post("/delete-folder")
def delete_folder(body: Target):
    freed = _guard(lambda: library().delete_folder(body.path))
    return {"returned_to_inbox": freed, "board": board_payload()}


@router.post("/delete-item")
def delete_item(body: Target):
    """Send a deck to the library's trash.

    Not unlinked: the library owns these files now, so a mis-click has to be
    recoverable from the Finder.
    """
    _guard(lambda: library().delete_item(body.path))
    return {"board": board_payload()}


@router.post("/import")
def import_paths(body: ImportPaths, mode: str = "move"):
    """Bring files chosen from the system dialog into the library."""
    lib = library()
    try:
        how = ImportMode(mode)
    except ValueError:
        how = ImportMode.MOVE

    added, failed = [], []
    for raw in body.paths:
        try:
            added.append(item_payload(lib.import_path(Path(raw), how, body.dest)))
        except LibraryError as exc:
            failed.append({"name": Path(raw).name, "error": str(exc)})
        except OSError as exc:
            failed.append({"name": Path(raw).name, "error": str(exc)})
    return {"added": added, "failed": failed, "board": board_payload()}


# -- glossary --------------------------------------------------------------

def scopes_for(scope: str | None) -> list[str]:
    """The inheritance chain ending at `scope` ("" or None is the whole library)."""
    lib = library()
    if scope:
        try:
            resolved = lib.resolve(scope)
        except LibraryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not resolved.is_dir():
            raise HTTPException(status_code=404, detail="这个作用域不存在。")
    return lib.chain(scope or None)


def make_glossary(cfg: Config, scope: str | None):
    return for_scopes(scopes_for(scope), cfg.target_lang)


def scope_labels(scopes: list[str]) -> list[dict]:
    return [
        {"id": s, "name": "全局" if s == "" else s.rsplit("/", 1)[-1]}
        for s in scopes
    ]


__all__ = [
    "INBOX",
    "board_payload",
    "glossary_router",
    "item_payload",
    "library",
    "make_glossary",
    "router",
    "scope_labels",
    "scopes_for",
    "use_library",
]
