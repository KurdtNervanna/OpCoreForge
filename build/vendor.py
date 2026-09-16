#!/usr/bin/env python3
"""
Vendoring script for OpCoreForge.

Clones the three upstream projects and rewrites them into three *distinct*
Python packages so they can coexist inside a single process / single .exe.

All three upstream repos ship a top-level package literally named ``Scripts``:

    OpCore-Simplify/Scripts/...
    USBToolBox-tool/Scripts/...
    ProperTree/Scripts/...

Importing more than one of those in the same interpreter is impossible -- the
first one to land in ``sys.modules["Scripts"]`` wins and the other two silently
import the wrong files.  So we rename them:

    OpCore-Simplify  Scripts -> ocs_scripts      OpCore-Simplify.py -> ocs_main.py
                                                 updater.py         -> ocs_updater.py
    USBToolBox/tool  Scripts -> utb_scripts      base.py            -> utb_base.py
                                                 Windows.py         -> utb_windows.py
                                                 macOS.py           -> utb_macos.py
    ProperTree       Scripts -> pt_scripts       ProperTree.py      -> pt_app.py

The rewrite is *purely mechanical* and is the only place upstream source is
edited.  Everything else OpCoreForge does to these projects is applied at
runtime by opcoreforge.patches, so re-vendoring a newer upstream release is a
single command and does not lose any of our behaviour.

Every rewrite rule below is asserted: if upstream changes such that a rule no
longer matches, vendoring FAILS LOUDLY instead of silently producing a broken
build.

Usage:
    python3 build/vendor.py                 # clone fresh + rewrite
    python3 build/vendor.py --from-cache DIR  # reuse already-cloned repos
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "src" / "vendor"

REPOS = {
    "ocs": {
        "url": "https://github.com/lzhoang2801/OpCore-Simplify.git",
        "clone_name": "OpCore-Simplify",
        "package": "ocs_scripts",
        "renames": {
            "Scripts": "ocs_scripts",
            "OpCore-Simplify.py": "ocs_main.py",
            "updater.py": "ocs_updater.py",
        },
        # Files we do not need in the bundle.
        "drop": [
            "OpCore-Simplify.bat",
            "OpCore-Simplify.command",
            ".github",
            ".gitattributes",
            ".gitignore",
        ],
    },
    "utb": {
        "url": "https://github.com/USBToolBox/tool.git",
        "clone_name": "USBToolBox-tool",
        "package": "utb_scripts",
        "renames": {
            "Scripts": "utb_scripts",
            "base.py": "utb_base.py",
            "Windows.py": "utb_windows.py",
            "macOS.py": "utb_macos.py",
        },
        "drop": [
            "spec",
            "debug_dump.py",
            ".github",
            ".pylintrc",
            ".flake8",
            ".gitignore",
        ],
    },
    "ptree": {
        "url": "https://github.com/corpnewt/ProperTree.git",
        "clone_name": "ProperTree",
        "package": "pt_scripts",
        "renames": {
            "Scripts": "pt_scripts",
            "ProperTree.py": "pt_app.py",
        },
        "drop": [
            "ProperTree.bat",
            "ProperTreeQuiet.bat",
            "ProperTree.command",
            "Scripts/buildapp-select.command",
            "Scripts/buildapp-select.py",
            "Scripts/AssociatePlistFiles.bat",
            "Scripts/Remove_AssociatePlistFiles.bat",
            ".gitattributes",
            ".gitignore",
        ],
    },
}


class RewriteError(RuntimeError):
    pass


class Rule:
    """A single search/replace rule with a minimum expected hit count."""

    def __init__(self, pattern: str, replacement: str, minimum: int = 1,
                 regex: bool = True, files: str | None = None, label: str = ""):
        self.pattern = pattern
        self.replacement = replacement
        self.minimum = minimum
        self.regex = regex
        self.files = files  # optional filename filter (exact basename)
        self.label = label or pattern
        self.hits = 0

    def apply(self, text: str, filename: str) -> str:
        if self.files and filename != self.files:
            return text
        if self.regex:
            text, n = re.subn(self.pattern, self.replacement, text)
        else:
            n = text.count(self.pattern)
            text = text.replace(self.pattern, self.replacement)
        self.hits += n
        return text


def import_rules(package: str) -> list[Rule]:
    """Mechanical rewrites turning ``Scripts`` imports into ``<package>``."""
    return [
        Rule(r"(?m)^(\s*)from Scripts import ",
             r"\1from %s import " % package, minimum=1,
             label="from Scripts import"),
        Rule(r"(?m)^(\s*)from Scripts\.",
             r"\1from %s." % package, minimum=0,
             label="from Scripts.X import"),
        Rule(r"(?m)^(\s*)import Scripts\.",
             r"\1import %s." % package, minimum=0,
             label="import Scripts.X"),
    ]


def extra_rules(key: str, package: str) -> list[Rule]:
    """Repo-specific rewrites that exist *only* to undo the rename above."""
    if key == "ocs":
        return [
            # OpCore-Simplify.py does `import updater`
            Rule(r"(?m)^import updater$", "import ocs_updater as updater",
                 minimum=1, files="ocs_main.py", label="import updater"),
        ]
    if key == "utb":
        return [
            # Windows.py / macOS.py do `from base import BaseUSBMap`
            Rule(r"(?m)^from base import ", "from utb_base import ",
                 minimum=1, label="from base import"),
            # Scripts/_build.py is generated by the upstream spec; provide a stub
            # so `from utb_scripts._build import BUILD` keeps working.
        ]
    if key == "ptree":
        return [
            # ProperTree.py reads these relative to its own directory after an
            # os.chdir(); the leading quote anchors us so the version.json URL
            # (".../ProperTree/master/Scripts/version.json") is left alone.
            Rule('"Scripts/settings.json"', '"%s/settings.json"' % package,
                 minimum=2, regex=False, files="pt_app.py",
                 label='"Scripts/settings.json"'),
            Rule('"Scripts/snapshot.plist"', '"%s/snapshot.plist"' % package,
                 minimum=2, regex=False, files="pt_app.py",
                 label='"Scripts/snapshot.plist"'),
            Rule('"Scripts/version.json"', '"%s/version.json"' % package,
                 minimum=2, regex=False, files="pt_app.py",
                 label='"Scripts/version.json"'),
            Rule('"Scripts","update_check.py"', '"%s","update_check.py"' % package,
                 minimum=2, regex=False, files="pt_app.py",
                 label='"Scripts","update_check.py"'),
        ]
    return []


def clone(url: str, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    print("  cloning %s" % url)
    subprocess.run(
        ["git", "clone", "--depth", "1", url, str(dest)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )


def record_revision(src: Path, out: Path, url: str) -> str:
    try:
        rev = subprocess.run(
            ["git", "-C", str(src), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except Exception:
        rev = "unknown"
    (out / "UPSTREAM.txt").write_text(
        "source: %s\ncommit: %s\n\n"
        "This directory is a mechanically rewritten copy of the upstream project.\n"
        "The ONLY edits are package/module renames (see build/vendor.py) needed so\n"
        "all three upstream projects -- which each ship a top-level package called\n"
        "'Scripts' -- can be imported into one process. All behavioural changes are\n"
        "applied at runtime by opcoreforge/patches.py; nothing here is hand-edited.\n"
        % (url, rev)
    )
    return rev


def vendor_one(key: str, cfg: dict, cache: Path | None) -> str:
    print("[%s]" % key)
    out = VENDOR / key
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    if cache is not None:
        src = cache / cfg["clone_name"]
        if not src.exists():
            raise RewriteError("cached clone not found: %s" % src)
        print("  using cached clone %s" % src)
    else:
        src = VENDOR / ("_clone_" + key)
        clone(cfg["url"], src)

    # Copy everything except .git and the explicit drop list.
    drops = {d.replace("/", os.sep) for d in cfg["drop"]}
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        parts = rel.parts
        if ".git" in parts:
            continue
        if str(rel) in drops or (parts and parts[0] in drops):
            continue
        target = out / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)

    rev = record_revision(src, out, cfg["url"])

    # Apply directory / file renames (longest first so Scripts/ moves before
    # files inside it are considered).
    for old, new in sorted(cfg["renames"].items(), key=lambda kv: -len(kv[0])):
        o, n = out / old, out / new
        if o.exists():
            o.rename(n)
            print("  renamed %s -> %s" % (old, new))
        else:
            raise RewriteError("[%s] expected to rename %r but it does not exist"
                               % (key, old))

    # Rewrite imports across every .py file.
    rules = import_rules(cfg["package"]) + extra_rules(key, cfg["package"])
    changed = 0
    for py in sorted(out.rglob("*.py")):
        original = py.read_text(encoding="utf-8", errors="surrogateescape")
        text = original
        for rule in rules:
            text = rule.apply(text, py.name)
        if text != original:
            py.write_text(text, encoding="utf-8", errors="surrogateescape")
            changed += 1

    for rule in rules:
        if rule.hits < rule.minimum:
            raise RewriteError(
                "[%s] rewrite rule %r matched %d time(s), expected at least %d. "
                "Upstream layout has changed -- update build/vendor.py."
                % (key, rule.label, rule.hits, rule.minimum)
            )
        if rule.hits:
            print("  rewrote %-32s x%d" % (rule.label, rule.hits))
    print("  %d file(s) rewritten, commit %s" % (changed, rev[:12]))

    # Guarantee nothing still refers to the old package name in an import.
    leftovers = []
    for py in out.rglob("*.py"):
        for i, line in enumerate(py.read_text(encoding="utf-8",
                                              errors="surrogateescape").splitlines(), 1):
            if re.match(r"\s*(from|import)\s+Scripts\b", line):
                leftovers.append("%s:%d: %s" % (py.relative_to(out), i, line.strip()))
    if leftovers:
        raise RewriteError("[%s] leftover 'Scripts' imports:\n  %s"
                           % (key, "\n  ".join(leftovers)))

    if cache is None:
        shutil.rmtree(src, ignore_errors=True)
    return rev


def post_fixups() -> None:
    """Small structural additions the rewritten trees need."""
    # USBToolBox's Scripts/_build.py is normally generated at build time by the
    # upstream PyInstaller spec, which shells out to git. Pin it instead.
    build_py = VENDOR / "utb" / "utb_scripts" / "_build.py"
    build_py.write_text('BUILD = "OpCoreForge"\n')

    # Namespace marker so `import ocs_scripts` etc. resolve as packages even if
    # upstream ever drops its __init__.py.
    for key, cfg in REPOS.items():
        init = VENDOR / key / cfg["package"] / "__init__.py"
        if not init.exists():
            init.write_text("")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-cache", type=Path, default=None,
                    help="directory containing already-cloned upstream repos")
    args = ap.parse_args()

    VENDOR.mkdir(parents=True, exist_ok=True)
    try:
        for key, cfg in REPOS.items():
            vendor_one(key, cfg, args.from_cache)
        post_fixups()
    except (RewriteError, subprocess.CalledProcessError) as exc:
        print("\nVENDORING FAILED: %s" % exc, file=sys.stderr)
        return 1

    print("\nVendored into %s" % VENDOR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
