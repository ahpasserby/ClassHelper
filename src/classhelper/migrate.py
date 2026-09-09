"""One-off move from the pointer-based board to the on-disk library.

The old board recorded where each deck belonged while the file stayed wherever
it was. Rebuilding that as real directories is a migration rather than a
conversion: folders become directories, and each deck is moved into the one it
was filed under.

Runs once, leaves the old `board.json` renamed rather than deleted, and never
overwrites an existing library.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .config import CONFIG_DIR
from .library import Library


def pending(library: Library) -> bool:
    old = CONFIG_DIR / "board.json"
    return old.exists() and not any(
        p.is_dir() and not p.name.startswith((".", "_"))
        for p in library.root.iterdir()
    ) if library.root.exists() else old.exists()


def run(library: Library) -> dict:
    """Rebuild the old board as directories. Returns what it did."""
    old = CONFIG_DIR / "board.json"
    if not old.exists():
        return {"folders": 0, "decks": 0, "skipped": []}

    try:
        data = json.loads(old.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"folders": 0, "decks": 0, "skipped": ["board.json 读不出来"]}

    library.ensure()
    folders = data.get("folders") or {}

    def path_of(folder_id: str | None) -> str | None:
        """The old ids were opaque; rebuild the name chain they described."""
        parts: list[str] = []
        seen: set[str] = set()
        current = folder_id
        while current and current in folders and current not in seen:
            seen.add(current)
            parts.append(folders[current]["name"])
            current = folders[current].get("parent")
        return "/".join(reversed(parts)) or None

    made = 0
    for folder_id in folders:
        rel = path_of(folder_id)
        if rel and not (library.root / rel).exists():
            (library.root / rel).mkdir(parents=True, exist_ok=True)
            made += 1

    moved, skipped = 0, []
    for item in (data.get("items") or {}).values():
        source = Path(item.get("path", ""))
        if not source.exists():
            skipped.append(f"{item.get('name', '?')}（文件不在了）")
            continue
        rel = path_of(item.get("folder"))
        target_dir = library.root / rel if rel else library.root / "_inbox"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / source.name
        if target.exists():
            continue
        shutil.move(str(source), str(target))
        moved += 1

    old.rename(old.with_suffix(".json.migrated"))
    return {"folders": made, "decks": moved, "skipped": skipped}
