"""
First-run payload seeding.

OpCore-Simplify downloads OpenCorePkg, the kexts it selected, macserial and
iasl before every build, and keeps them in ``OCK_Files``. That is the right
design -- it is how the tool stays current with Dortania's builds -- but it
means a brand new copy cannot do anything at all without a working internet
connection, and the very first build is a long wait.

So a snapshot of that payload is baked into the executable and unpacked beside
it on first run. From then on, OpCore-Simplify's own update logic takes over
untouched: it compares release ids against ``history.json``, verifies SHA-256
hashes, and re-downloads anything that has moved on. The seed only removes the
cold start; it never pins a version.

The archive is stored as a single compressed file rather than thousands of
loose members, because a one-file PyInstaller build re-extracts every bundled
data file on *every* launch -- one blob costs a fraction of the startup time
that the unpacked tree would.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

SEED_ARCHIVE = ("seed", "payload.zip")
SEED_VERSION = ("seed", "VERSION")


def _bundled(paths, parts) -> Path:
    return paths.bundle.joinpath(*parts)


def seed_version(paths) -> str | None:
    marker = _bundled(paths, SEED_VERSION)
    if marker.exists():
        try:
            return marker.read_text(encoding="utf-8").strip()
        except Exception:
            return None
    return None


def installed_version(paths) -> str | None:
    if paths.seed_stamp.exists():
        try:
            return paths.seed_stamp.read_text(encoding="utf-8").strip()
        except Exception:
            return None
    return None


def ensure_seed(paths, report=None) -> bool:
    """Unpack the bundled payload if this data folder has not had it yet.

    Returns True when something was extracted. Existing files are never
    overwritten, so a payload OpCore-Simplify has already updated in place
    stays as it is.
    """
    archive = _bundled(paths, SEED_ARCHIVE)
    if not archive.exists():
        return False

    version = seed_version(paths) or "unknown"
    if installed_version(paths) == version:
        return False

    if report:
        report("Unpacking bundled OpenCore payload (first run only)...")

    try:
        with zipfile.ZipFile(archive) as bundle:
            members = bundle.namelist()
            for index, name in enumerate(members):
                target = paths.data / name
                if name.endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if target.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(name) as source, target.open("wb") as sink:
                    shutil.copyfileobj(source, sink)
                if report and index % 200 == 0:
                    report("Unpacking bundled payload... %d/%d"
                           % (index, len(members)))
        paths.seed_stamp.write_text(version, encoding="utf-8")
    except Exception as exc:
        if report:
            report("Bundled payload could not be unpacked (%s); "
                   "files will be downloaded instead." % exc)
        return False

    _make_executable(paths)
    if report:
        report("Bundled payload ready.")
    return True


def _make_executable(paths):
    """Restore the executable bit that zip archives do not carry."""
    import os
    import stat
    if os.name == "nt":
        return
    for name in ("iasl", "iasl-stable", "iasl-dev", "macserial",
                 "macserial.linux"):
        candidate = paths.bin / name
        if candidate.exists():
            try:
                candidate.chmod(candidate.stat().st_mode | stat.S_IXUSR
                                | stat.S_IXGRP | stat.S_IXOTH)
            except Exception:
                pass
