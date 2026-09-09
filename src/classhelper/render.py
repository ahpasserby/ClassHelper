"""Pictures of the source pages.

The reading view is deliberately not a reproduction of the slide -- it is laid
out to be read. But "which of these bullets is the lecturer pointing at" is a
question only the slide itself can answer, so the source has to be visible
somewhere, at its own proportions, next to the translation.

For a PDF that is free: the file is already a picture of itself and PyMuPDF
rasterises a page in a few dozen milliseconds.

An Office file is not. Rendering one properly means implementing PowerPoint, so
the only honest options are to hand it to something that already has
(LibreOffice, if it happens to be installed) or to draw an approximation from
what the parser extracted -- every text box and picture at its real position
and size, which is enough to point at a bullet even though it is not the slide.
The reader does the second one itself, from data it already has; this module
reports which is on offer so the UI can say so plainly rather than showing a
crude drawing as though it were the file.

Markdown has no third option: there is no page to be a picture of, so the
answer is "none" and the reader shows the text alone.

Nothing here ever blocks a request for long: a LibreOffice conversion takes
seconds, so it runs on a thread and the reader shows the approximation until it
finishes.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .config import state_dir

# Where LibreOffice hides on the platforms people run this on. `which` first,
# because a user who installed it deliberately usually put it on the path.
_SOFFICE_CANDIDATES = (
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/bin/soffice",
    "/usr/bin/libreoffice",
    "/usr/local/bin/soffice",
    "/opt/homebrew/bin/soffice",
    r"C:\Program Files\LibreOffice\program\soffice.exe",
)

# Long enough for a hundred-slide deck on a cold start, short enough that a
# wedged converter does not hold a thread forever.
_CONVERT_TIMEOUT = 180.0

# Rasterising above this is paying for pixels no screen will show.
_MAX_WIDTH = 2400
_MIN_WIDTH = 200

# Formats with no page of their own to show.
_NO_SOURCE_VIEW = {".md", ".markdown"}
# Formats LibreOffice can render exactly but whose parser records no positions,
# so there is no approximation to fall back to.
_NO_GEOMETRY = {".docx", ".doc"}


def find_soffice() -> str | None:
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    for candidate in _SOFFICE_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def _key(path: Path) -> str:
    """Identifies a file *version*: edit the deck and the old render is not it."""
    stat = path.stat()
    raw = f"{path.resolve()}\x00{stat.st_mtime_ns}\x00{stat.st_size}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass
class Source:
    """One deck's rendered form, and how far along it is."""

    path: Path
    # "exact"        -- pages come from the file itself
    # "building"     -- a conversion is running; approximate until it lands
    # "approximate"  -- the reader draws it from the extracted geometry
    # "none"         -- the format has no pages to show
    mode: str = "approximate"
    detail: str = ""
    pdf: Path | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def status(self) -> dict:
        return {"mode": self.mode, "detail": self.detail}


class Renderer:
    """Renders pages for one deck, on demand."""

    def __init__(self, path: str):
        self.source = Source(Path(path))
        self._prepare()

    # -- setting up --------------------------------------------------------

    def _prepare(self) -> None:
        path = self.source.path
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            self.source.mode = "exact"
            self.source.pdf = path
            return

        if suffix in _NO_SOURCE_VIEW:
            self.source.mode = "none"
            self.source.detail = "这个格式没有版面，只有正文。"
            return

        cached = self._converted_path()
        if cached.exists():
            self.source.mode = "exact"
            self.source.pdf = cached
            return

        if find_soffice() is None:
            if suffix in _NO_GEOMETRY:
                # A Word document has no recorded positions, so there is nothing
                # to draw an approximation from -- a drawing made up here would
                # be a picture of nothing.
                self.source.mode = "none"
                self.source.detail = (
                    "装上 LibreOffice 就能在这里显示原文档的排版。"
                )
                return
            self.source.mode = "approximate"
            self.source.detail = (
                "没装 LibreOffice，右边显示的是按原文位置还原的版式，不是幻灯片本身。"
                "装上 LibreOffice 就会自动换成真正的原图。"
            )
            return

        self.source.mode = "building"
        self.source.detail = "正在生成幻灯片原图…"
        threading.Thread(target=self._convert, daemon=True,
                         name="render-convert").start()

    def _converted_path(self) -> Path:
        folder = state_dir() / "renders"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{_key(self.source.path)}.pdf"

    def _convert(self) -> None:
        """Hand the deck to LibreOffice and keep the PDF it produces."""
        soffice = find_soffice()
        target = self._converted_path()
        workdir = target.parent / f"{target.stem}.work"
        try:
            workdir.mkdir(parents=True, exist_ok=True)
            # Its own profile directory, or this fights with a LibreOffice the
            # user happens to have open and silently does nothing.
            profile = (workdir / "profile").as_uri()
            subprocess.run(
                [soffice, f"-env:UserInstallation={profile}", "--headless",
                 "--norestore", "--convert-to", "pdf", "--outdir", str(workdir),
                 str(self.source.path)],
                capture_output=True, timeout=_CONVERT_TIMEOUT, check=False,
            )
            produced = next(iter(workdir.glob("*.pdf")), None)
            if produced is None:
                raise RuntimeError("LibreOffice 没有产出 PDF")
            produced.replace(target)
        except Exception as exc:  # noqa: BLE001 - falling back is the fix
            with self.source._lock:
                if self.source.path.suffix.lower() in _NO_GEOMETRY:
                    self.source.mode = "none"
                    self.source.detail = f"没能转换成原图（{exc}）。"
                else:
                    self.source.mode = "approximate"
                    self.source.detail = f"没能转换成原图（{exc}），显示的是还原的版式。"
            return
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        with self.source._lock:
            self.source.pdf = target
            self.source.mode = "exact"
            self.source.detail = ""

    # -- rendering ---------------------------------------------------------

    def page_count(self) -> int:
        pdf = self.source.pdf
        if pdf is None:
            return 0
        import pymupdf

        with pymupdf.open(pdf) as doc:
            return doc.page_count

    def render(self, index: int, width: int = 1200) -> bytes | None:
        """One page as PNG, or None when there is nothing exact to show."""
        pdf = self.source.pdf
        if pdf is None or not pdf.exists():
            return None
        width = max(_MIN_WIDTH, min(_MAX_WIDTH, int(width)))

        import pymupdf

        with pymupdf.open(pdf) as doc:
            if not 0 <= index < doc.page_count:
                return None
            page = doc[index]
            scale = width / (page.rect.width or width)
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale),
                                     alpha=False)
            return pixmap.tobytes("png")

    @property
    def etag(self) -> str:
        """Changes when the file does, so a stale image is never served."""
        try:
            return _key(self.source.path)
        except OSError:
            return "0"
