"""
The companion app's place on the install media.

The install media stage adds an ``OpCoreForge`` folder beside ``EFI`` and the recovery:

    OpCoreForge/
        OpCoreForge-Companion.dmg   the macOS app, if it could be had
        EFI/                        the EFI exactly as built, for the internal disk
        media.json                  which OpCoreForge made this, and when
        READ ME.txt

The second EFI is the point. The one at the root of the stick has the install
tweaks from ``media.tune_config_for_install`` (recovery shown, a long timeout),
which are wrong for an installed system; the companion installs this copy
instead.

The disk image cannot be built on Windows -- it is made on a Mac by GitHub
Actions and attached to each release -- so it is downloaded from the release
matching this version, or the latest one, and cached in the data folder. A
``.dmg`` placed in ``OpCoreForge_Data/Companion`` by hand is used as it is.
None of this is allowed to fail the media: without the image the stick still
installs macOS, and the note says where to get the app.
"""

from __future__ import annotations

import datetime
import json
import shutil
from pathlib import Path

from . import project
from .version import __version__

FOLDER = "OpCoreForge"
DMG_NAME = "OpCoreForge-Companion.dmg"

#: set once every download URL has failed, so preparing the media again in the
#: same session does not sit through the same retries
_download_failed = False

README = """\
OpCoreForge Companion
=====================

Once macOS is installed on this machine, open %(dmg)s from this
drive and run OpCoreForge Companion. It copies OpenCore onto the machine's own
disk, so this USB stick is no longer needed to start it, and it can keep a copy
of itself in Applications.

The EFI folder next to this file is the EFI exactly as OpCoreForge built it.
That is the one the companion installs. The EFI folder at the top of this drive
is set up for installing (macOS Recovery shown in the picker, a longer
timeout) and is what this stick boots with.

%(missing)s
Made by OpCoreForge %(version)s - %(url)s
"""

MISSING = """\
The companion app could not be downloaded when this stick was made. Get
%s from the Releases page:
%s/releases
""" % (DMG_NAME, project.REPO_URL)


def cache_dir(paths) -> Path:
    return paths.data / "Companion"


def download_urls(version: str = __version__) -> list:
    return [
        "%s/releases/download/v%s/%s" % (project.REPO_URL, version, DMG_NAME),
        "%s/releases/latest/download/%s" % (project.REPO_URL, DMG_NAME),
    ]


def _looks_like_dmg(path: Path) -> bool:
    # A UDIF image ends in a 512-byte trailer starting "koly". Checked so an
    # HTML error page saved under the right name is not shipped as the app.
    try:
        size = path.stat().st_size
        if size < 1024:
            return False
        with path.open("rb") as handle:
            handle.seek(size - 512)
            return handle.read(4) == b"koly"
    except OSError:
        return False


def find_dmg(paths, fetcher=None, report=None):
    """The companion disk image, downloading it if needed. None if not to be had."""
    def say(message):
        if report:
            report(message)

    folder = cache_dir(paths)
    folder.mkdir(parents=True, exist_ok=True)
    versioned = folder / ("OpCoreForge-Companion-%s.dmg" % __version__)
    if _looks_like_dmg(versioned):
        return versioned
    # Anything dropped in by hand, newest first.
    for candidate in sorted(folder.glob("*.dmg"),
                            key=lambda p: p.stat().st_mtime, reverse=True):
        if _looks_like_dmg(candidate):
            return candidate

    global _download_failed
    if _download_failed and fetcher is None:
        return None
    if fetcher is None:
        try:
            from ocs_scripts import resource_fetcher
            fetcher = resource_fetcher.ResourceFetcher()
        except Exception:
            return None
    partial = folder / (versioned.name + ".part")
    for url in download_urls():
        say("Downloading the companion app...")
        try:
            ok = fetcher.download_and_save_file(url, str(partial))
        except Exception:
            ok = False
        if ok and _looks_like_dmg(partial):
            partial.replace(versioned)
            return versioned
        if partial.exists():
            partial.unlink()
    _download_failed = True
    return None


def add_to_media(paths, media: Path, efi: Path, fetcher=None,
                 report=None) -> dict:
    """Write the ``OpCoreForge`` folder into *media*. Returns what it holds.

    *efi* must be the as-built EFI, copied before the media copy is tuned.
    """
    def say(message):
        if report:
            report(message)

    target = Path(media) / FOLDER
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    shutil.copytree(efi, target / "EFI")

    dmg = find_dmg(paths, fetcher, report)
    if dmg:
        shutil.copy2(dmg, target / DMG_NAME)
        say("Added the companion app for after macOS is installed")
    else:
        say("Note: the companion app could not be downloaded, so it is not "
            "on the media. %s" % MISSING.replace("\n", " ").strip())

    info = {
        "opcoreforge": __version__,
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "companion": DMG_NAME if dmg else None,
    }
    (target / "media.json").write_text(json.dumps(info, indent=2),
                                       encoding="utf-8")
    (target / "READ ME.txt").write_text(README % {
        "dmg": DMG_NAME, "version": __version__, "url": project.REPO_URL,
        "missing": "" if dmg else MISSING + "\n",
    }, encoding="utf-8")
    return info
