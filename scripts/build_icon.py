"""Render the app icon into an .icns.

Run from the repo root:  .venv/bin/python scripts/build_icon.py

The artwork is drawn by desktop/build/icon-src/make_icon.py, at every size
macOS asks for rather than by scaling one bitmap down -- the strokes are laid
out in points, so a 16px icon drawn at 16px keeps its edges.
"""

from __future__ import annotations

import importlib.util
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "desktop" / "build" / "icon-src" / "make_icon.py"
OUT = ROOT / "desktop" / "build" / "icon.icns"

# What iconutil expects in an .iconset, name for name.
SIZES = [
    ("icon_16x16", 16), ("icon_16x16@2x", 32),
    ("icon_32x32", 32), ("icon_32x32@2x", 64),
    ("icon_128x128", 128), ("icon_128x128@2x", 256),
    ("icon_256x256", 256), ("icon_256x256@2x", 512),
    ("icon_512x512", 512), ("icon_512x512@2x", 1024),
]


def main() -> int:
    spec = importlib.util.spec_from_file_location("make_icon", SRC)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    iconset = OUT.with_suffix(".iconset")
    shutil.rmtree(iconset, ignore_errors=True)
    iconset.mkdir(parents=True)

    for name, size in SIZES:
        module.render(iconset / f"{name}.png", size)

    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(OUT)],
                   check=True)
    shutil.rmtree(iconset, ignore_errors=True)

    module.render(ROOT / "docs" / "icon.png", 512)
    print(f"{OUT.relative_to(ROOT)}  {OUT.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
