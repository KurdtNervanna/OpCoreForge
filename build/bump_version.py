#!/usr/bin/env python3
"""
Bumps the version and records the change in CHANGELOG.md.

Every delivered build gets a new version so it can be told apart from the last
one at a glance -- in the title bar, from ``OpCoreForge.exe --version``, in the
self-test output and in the release zip's filename.

    python3 build/bump_version.py patch -m "Fix the USB port list not refreshing"
    python3 build/bump_version.py minor -m "Add EFI backup before rebuild"
    python3 build/bump_version.py major -m "Rework the workflow into five stages"
    python3 build/bump_version.py --show

patch = a fix or small change      minor = new behaviour      major = rework
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "src" / "opcoreforge" / "version.py"
CHANGELOG = ROOT / "CHANGELOG.md"

VERSION_RE = re.compile(r'^__version__ = "(\d+)\.(\d+)\.(\d+)"$', re.M)
RELEASED_RE = re.compile(r'^__released__ = "\d{4}-\d{2}-\d{2}"$', re.M)


def read_version() -> tuple[int, int, int]:
    match = VERSION_RE.search(VERSION_FILE.read_text(encoding="utf-8"))
    if not match:
        raise SystemExit("Could not find __version__ in %s" % VERSION_FILE)
    return tuple(int(part) for part in match.groups())


def format_version(parts) -> str:
    return "%d.%d.%d" % parts


def write_version(parts) -> None:
    text = VERSION_FILE.read_text(encoding="utf-8")
    text = VERSION_RE.sub('__version__ = "%s"' % format_version(parts), text)
    # The release date doubles as a floor for the system clock, so it has to
    # move with the version or a new build silently accepts an older date.
    today = datetime.date.today().isoformat()
    text, count = RELEASED_RE.subn('__released__ = "%s"' % today, text)
    if count != 1:
        raise SystemExit("Could not find __released__ in %s" % VERSION_FILE)
    VERSION_FILE.write_text(text, encoding="utf-8")


def bump(parts, level: str):
    major, minor, patch = parts
    if level == "major":
        return (major + 1, 0, 0)
    if level == "minor":
        return (major, minor + 1, 0)
    return (major, minor, patch + 1)


def add_entry(version: str, notes: list[str]) -> None:
    today = datetime.date.today().isoformat()
    entry = ["## %s - %s" % (version, today), ""]
    entry += ["- %s" % note for note in notes]
    entry.append("")

    if CHANGELOG.exists():
        existing = CHANGELOG.read_text(encoding="utf-8")
    else:
        existing = "# Changelog\n\nEvery delivered build gets its own version.\n\n"

    marker = "\n"
    head, _, tail = existing.partition("\n\n")
    if head.startswith("# "):
        # Insert the new entry directly beneath the file header.
        rest = tail.split("\n\n", 1)
        intro = rest[0] if rest and not rest[0].startswith("## ") else ""
        remainder = rest[1] if len(rest) > 1 else (tail if intro == "" else "")
        pieces = [head, ""]
        if intro:
            pieces += [intro, ""]
        pieces += ["\n".join(entry)]
        if remainder.strip():
            pieces.append(remainder.rstrip("\n"))
        CHANGELOG.write_text("\n".join(pieces).rstrip("\n") + "\n",
                             encoding="utf-8")
    else:
        CHANGELOG.write_text("\n".join(entry) + marker + existing,
                             encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("level", nargs="?", choices=("major", "minor", "patch"),
                        help="which part of the version to increase")
    parser.add_argument("-m", "--message", action="append", default=[],
                        help="changelog line (repeat for several)")
    parser.add_argument("--show", action="store_true",
                        help="print the current version and exit")
    args = parser.parse_args()

    current = read_version()
    if args.show or not args.level:
        print(format_version(current))
        return 0

    if not args.message:
        parser.error("give at least one -m/--message describing the change")

    new = bump(current, args.level)
    write_version(new)
    add_entry(format_version(new), args.message)

    print("%s -> %s" % (format_version(current), format_version(new)))
    print("recorded in %s" % CHANGELOG.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
