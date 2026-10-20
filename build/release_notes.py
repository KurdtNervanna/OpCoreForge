#!/usr/bin/env python3
"""
Release notes for a GitHub release, taken from CHANGELOG.md.

A release covers every version since the last one that was published, not just
its own entry: 2.1.2 was the first release after 1.3.2, so its notes have to
carry 2.1.2, 2.1.1, 2.1.0, 2.0.0 and 1.3.3, or the companion app and the six-tab
rework go unannounced.

    python3 build/release_notes.py 2.1.2 --since 1.3.2   -> 2.1.2 ... 1.3.3
    python3 build/release_notes.py 2.1.2                 -> 2.1.2 only
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEADING = re.compile(r"^## (\S+)")


def sections(text: str):
    """``[(version, body)]`` in file order, newest first."""
    found = []
    version, lines = None, []
    for line in text.splitlines():
        match = HEADING.match(line)
        if match:
            if version:
                found.append((version, "\n".join(lines).strip()))
            version, lines = match.group(1), []
        elif version:
            lines.append(line)
    if version:
        found.append((version, "\n".join(lines).strip()))
    return found


def notes(text: str, version: str, since: str | None = None) -> str:
    """Every section from *version* down to, not including, *since*."""
    picked = []
    started = False
    for name, body in sections(text):
        if name == version:
            started = True
        if not started:
            continue
        if since and name == since:
            break
        picked.append((name, body))
        if not since:
            break
    if not picked:
        raise SystemExit("CHANGELOG.md has no entry for %s" % version)
    if len(picked) == 1:
        return picked[0][1] + "\n"
    return "\n\n".join("## %s\n\n%s" % pair for pair in picked) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("version")
    parser.add_argument("--since", help="the last published version")
    args = parser.parse_args(argv)
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    since = args.since.lstrip("v") if args.since else None
    sys.stdout.write(notes(text, args.version.lstrip("v"), since))
    return 0


if __name__ == "__main__":
    sys.exit(main())
