#!/usr/bin/env python3
"""Freeze the Python server into a single binary for the desktop build.

The desktop app ships this in its resources and spawns it, so nothing is asked
of the machine it runs on -- no Python, no pip, no virtualenv. In development
the app uses the project's own .venv instead, so a change to the Python is one
restart away rather than a rebuild.

PyInstaller does not cross-compile: run this on the platform you are building
for.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "desktop" / "resources" / "sidecar"

# Imported dynamically or pulled in by data files, so PyInstaller's static
# analysis does not see them on its own.
HIDDEN = [
    "classhelper.parsers.pptx_parser",
    "classhelper.parsers.pdf_parser",
    "classhelper.providers.openai_compatible",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]


def main() -> int:
    python = ROOT / ".venv" / "bin" / "python"
    if not python.exists():
        print("需要先建好 .venv 并 pip install -e .", file=sys.stderr)
        return 1

    try:
        subprocess.run([str(python), "-c", "import PyInstaller"], check=True,
                       capture_output=True)
    except subprocess.CalledProcessError:
        print("正在安装 PyInstaller…")
        subprocess.run([str(python), "-m", "pip", "install", "-q", "pyinstaller"],
                       check=True)

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.parent.mkdir(parents=True, exist_ok=True)

    entry = ROOT / "scripts" / "_sidecar_entry.py"
    entry.write_text(
        '"""Entry point for the frozen server: the CLI, nothing else."""\n'
        "from classhelper.cli import main\n\n"
        'if __name__ == "__main__":\n'
        "    raise SystemExit(main())\n",
        encoding="utf-8",
    )

    # PyInstaller writes <distpath>/<name>/, so it builds into a scratch
    # directory and the finished tree is moved to the one both
    # electron-builder.yml and sidecar.ts name.
    staging = ROOT / "build" / "dist"
    command = [
        str(python), "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onedir",
        "--name", "classhelper-server",
        "--distpath", str(staging),
        "--workpath", str(ROOT / "build" / "pyinstaller"),
        "--specpath", str(ROOT / "build"),
        # The reader itself, served by the frozen binary.
        "--add-data", f"{ROOT / 'src' / 'classhelper' / 'web'}:classhelper/web",
        *sum(([["--hidden-import", name][0], name] for name in HIDDEN), []),
        str(entry),
    ]
    print(" ".join(command))
    subprocess.run(command, check=True, cwd=ROOT)

    shutil.move(str(staging / "classhelper-server"), str(OUT))
    binary = OUT / "classhelper-server"
    if not binary.exists():
        print(f"没有生成可执行文件：{binary}", file=sys.stderr)
        return 1
    print(f"打包完成：{binary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
