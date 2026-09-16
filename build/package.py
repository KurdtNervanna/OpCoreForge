#!/usr/bin/env python3
"""
Builds the release zip: the sources, the vendored upstreams, and BUILD_EXE.bat.

Not the .exe. PyInstaller cannot cross-compile, so the executable has to be
produced on the machine that will run it -- which is what BUILD_EXE.bat is for.
The zip therefore has to carry everything that script needs and nothing that
would go stale in it.

    python3 build/package.py                 -> OpCoreForge-<version>.zip here
    python3 build/package.py --out DIR       -> put it somewhere else
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Everything the build needs, in the order a person would read it.
INCLUDE = [
    "README.md",
    "CHANGELOG.md",
    "BUILD_EXE.bat",
    "OpCoreForge.spec",
    "src",
    "build",
    "tools",
    "_fixture",
]

# Working state, caches and outputs: regenerated on demand, and shipping them
# means shipping paths from this machine.
SKIP_DIRS = {"__pycache__", ".git", ".venv", "seed"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".log"}
SKIP_NAMES = {".DS_Store"}

# PyInstaller's scratch directory sits inside build/ and is tens of megabytes
# of this machine's intermediate state. Excluded by path rather than by name,
# because "OpCoreForge" is not a name to blanket-exclude.
SKIP_PREFIXES = ("build/OpCoreForge", "build/file_version_info.txt")


def version() -> str:
    namespace = {}
    exec((ROOT / "src" / "opcoreforge" / "version.py").read_text(
        encoding="utf-8"), namespace)
    return namespace["__version__"]


def wanted(path: Path) -> bool:
    # Relative to the project, so a directory name higher up the machine's
    # own path cannot accidentally exclude the whole tree.
    relative = path.relative_to(ROOT).as_posix()
    if any(part in SKIP_DIRS for part in relative.split("/")):
        return False
    if relative.startswith(SKIP_PREFIXES):
        return False
    if path.suffix in SKIP_SUFFIXES or path.name in SKIP_NAMES:
        return False
    return True


def collect() -> list[Path]:
    files = []
    for entry in INCLUDE:
        target = ROOT / entry
        if target.is_dir():
            files += [p for p in sorted(target.rglob("*"))
                      if p.is_file() and wanted(p)]
        elif target.is_file():
            files.append(target)
        else:
            raise SystemExit("missing from the tree: %s" % entry)
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(ROOT),
                        help="directory to write the zip into")
    args = parser.parse_args()

    release = version()
    out = Path(args.out) / ("OpCoreForge-%s.zip" % release)
    out.parent.mkdir(parents=True, exist_ok=True)

    files = collect()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, "OpCoreForge/%s" % path.relative_to(ROOT))

    size = out.stat().st_size / (1024 * 1024)
    print("%s  (%d files, %.1f MB)" % (out, len(files), size))
    return 0


if __name__ == "__main__":
    sys.exit(main())
