"""The pre-2007 binary Office formats.

`.ppt` is not a zip of XML, it is a compound document, and nothing in the
Python ecosystem reads its text reliably. Rather than half-read it, the file is
handed to LibreOffice to be converted once into the modern format, and the
normal parser takes it from there.

That makes support conditional on a program the user may not have, which is why
the failure says so in as many words instead of "unsupported format": the fix
is one install away, and the alternative -- opening it in PowerPoint and saving
as .pptx -- is one they already have.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

from ..model import Deck

_TIMEOUT = 180.0


class ConversionUnavailable(Exception):
    """LibreOffice is not installed, or would not convert the file."""


def parse_ppt(path: str) -> Deck:
    from .pptx_parser import parse as parse_pptx

    converted = convert(Path(path), "pptx")
    deck = parse_pptx(str(converted))
    # The deck must remember where it really came from: the board, the caches
    # and "reveal in Finder" all point at the file the user has.
    deck.source_path = path
    deck.source_format = "ppt"
    deck.notes.append("由 LibreOffice 转换成 .pptx 后读取。")
    return deck


def convert(source: Path, to: str) -> Path:
    """Convert with LibreOffice, and keep the result.

    Cached against the file's own mtime and size, so a deck converts once and
    every later open is instant.
    """
    from ..config import state_dir
    from ..render import find_soffice

    soffice = find_soffice()
    if soffice is None:
        raise ConversionUnavailable(
            f"{source.suffix} 是旧版 Office 格式，需要 LibreOffice 才能读取。"
            "可以装一个（brew install --cask libreoffice），"
            "或者用 PowerPoint 另存为 .pptx。"
        )

    stat = source.stat()
    key = hashlib.sha256(
        f"{source.resolve()}\x00{stat.st_mtime_ns}\x00{stat.st_size}".encode()
    ).hexdigest()[:16]
    folder = state_dir() / "converted"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{key}.{to}"
    if target.exists():
        return target

    workdir = folder / f"{key}.work"
    try:
        workdir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [soffice, f"-env:UserInstallation={(workdir / 'profile').as_uri()}",
             "--headless", "--norestore", "--convert-to", to,
             "--outdir", str(workdir), str(source)],
            capture_output=True, timeout=_TIMEOUT, check=False,
        )
        produced = next(iter(workdir.glob(f"*.{to}")), None)
        if produced is None:
            raise ConversionUnavailable(
                f"LibreOffice 没能把 {source.name} 转换成 .{to}。"
            )
        produced.replace(target)
    except subprocess.TimeoutExpired as exc:
        raise ConversionUnavailable(
            f"转换 {source.name} 超时。"
        ) from exc
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return target
