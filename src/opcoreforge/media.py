"""
Install media: the macOS recovery, laid out next to the EFI that was built.

**What this makes, and what it does not.** It makes a *recovery* installer: the
machine boots into macOS Recovery over OpenCore, connects to Apple, and
downloads the release from there. It does not make a full offline installer.
That is not a shortcut -- Apple's ``createinstallmedia`` is a macOS binary and
the layout it produces is APFS, so a full installer simply cannot be built from
Windows. OpenCore Legacy Patcher can do it because it runs on macOS. Recovery
is the documented Windows path, and it installs the same macOS.

The download itself is done by **OpenCore's own ``macrecovery.py``**, extracted
from the OpenCorePkg release. Apple's recovery protocol is undocumented and
changes; reimplementing it from memory would be a good way to quietly fetch the
wrong thing. The board id that selects which macOS Apple serves is likewise
never guessed -- it is read from what OpenCorePkg ships, and if that cannot be
parsed the user is asked rather than given a plausible-looking default.
"""

from __future__ import annotations

import json
import os
import re
import runpy
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

RECOVERY_DIR = "com.apple.recovery.boot"
MEDIA_DIR_NAME = "InstallMedia"
#: what macrecovery produces, and what a booting Mac looks for
RECOVERY_FILES = ("BaseSystem.dmg", "BaseSystem.chunklist")
BOARD_PATTERN = re.compile(r"Mac-[0-9A-F]{8,20}")
#: the page the board IDs in boards.py were transcribed from
DORTANIA_URL = ("https://dortania.github.io/OpenCore-Install-Guide/extras/"
                "smbios-support.html")


class MediaError(Exception):
    """Something in the media pipeline could not be done, with a reason."""

    title = "Install media"

    def __init__(self, message, hint=""):
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self):
        return "%s\n\n%s" % (self.message, self.hint) if self.hint else self.message


def media_dir(paths) -> Path:
    return paths.data / MEDIA_DIR_NAME


def recovery_dir(paths) -> Path:
    return paths.data / "Recovery" / RECOVERY_DIR


# ---------------------------------------------------------------------------
# macrecovery, from OpenCorePkg
# ---------------------------------------------------------------------------

def macrecovery_dir(paths) -> Path:
    return paths.bin / "macrecovery"


def have_macrecovery(paths) -> bool:
    return (macrecovery_dir(paths) / "macrecovery.py").exists()


#: where OpCore-Simplify itself gets OpenCorePkg, and the more reliable of the
#: two: a JSON index rather than a scraped release page.
DORTANIA_BUILDS = ("https://raw.githubusercontent.com/dortania/build-repo/"
                   "builds/latest.json")


def _opencorepkg_url(github, fetcher):
    """The OpenCorePkg RELEASE zip, from the build index first.

    Stage 6 already downloads OpenCorePkg through Dortania's build index, so
    that path is known to work on a machine that has got this far. The GitHub
    release page is the fallback -- and it is scraped rather than served as
    JSON, so its entries carry ``product_name`` and a download ``url`` rather
    than the ``name`` a REST API would give. Reading the wrong key is what made
    this report "the OpenCorePkg release does not list a RELEASE zip" on a
    release that lists one perfectly well.
    """
    try:
        index = fetcher.fetch_and_parse_content(DORTANIA_BUILDS, "json") or {}
        url = index["OpenCorePkg"]["versions"][0]["links"]["release"]
        if url:
            return url
    except Exception:
        pass
    try:
        release = github.get_latest_release("acidanthera", "OpenCorePkg") or {}
    except Exception:
        return None
    for asset in release.get("assets") or []:
        url = asset.get("url") or asset.get("browser_download_url") or ""
        name = (asset.get("product_name") or "") + " " + url
        if url.lower().endswith(".zip") and "release" in name.lower():
            return url
    return None


def ensure_macrecovery(paths, fetcher=None, github=None, report=None) -> Path:
    """Extract ``Utilities/macrecovery`` from the OpenCorePkg release.

    Everything in that folder is kept, not just the script: whatever data it
    ships alongside is where the board ids come from, and guessing those is
    exactly what this avoids.
    """
    def say(message):
        if report:
            report(message)

    target = macrecovery_dir(paths)
    if (target / "macrecovery.py").exists():
        return target

    if fetcher is None or github is None:
        from ocs_scripts import github as github_module
        from ocs_scripts import resource_fetcher
        github = github or github_module.Github()
        fetcher = fetcher or resource_fetcher.ResourceFetcher()

    say("Looking up the OpenCorePkg release...")
    url = _opencorepkg_url(github, fetcher)
    if not url:
        raise MediaError(
            "The OpenCorePkg release does not list a RELEASE zip.",
            "macrecovery lives inside that archive, so the recovery cannot be "
            "downloaded without it. Try again later, or fetch macrecovery.py "
            "yourself and put it in %s." % target)

    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        archive = Path(scratch) / "OpenCorePkg.zip"
        say("Downloading OpenCorePkg for macrecovery...")
        fetcher.download_and_save_file(url, str(archive))
        if not archive.exists():
            raise MediaError(
                "OpenCorePkg could not be downloaded.",
                "The recovery download needs macrecovery.py from that "
                "archive. Check the connection and try again.")
        extracted = 0
        with zipfile.ZipFile(archive) as zipped:
            for member in zipped.namelist():
                lowered = member.lower().replace("\\", "/")
                if "utilities/macrecovery/" in lowered and not member.endswith("/"):
                    name = Path(member).name
                    with zipped.open(member) as source, \
                            open(target / name, "wb") as out:
                        shutil.copyfileobj(source, out)
                    extracted += 1
    if not (target / "macrecovery.py").exists():
        raise MediaError(
            "macrecovery.py was not in the OpenCorePkg archive.",
            "OpenCore may have moved it. You can download it from the "
            "OpenCorePkg repository and put it in:\n\n%s" % target)
    say("macrecovery ready (%d file(s))" % extracted)
    return target


# ---------------------------------------------------------------------------
# board ids
# ---------------------------------------------------------------------------

def board_options(paths, darwin_version="", smbios_model="") -> list:
    """Boards that can be asked for *darwin_version*, as ``(board_id, label)``.

    Comes from the table transcribed off Dortania's SMBIOS page, joined with
    OpCore-Simplify's own support ranges -- so which boards exist and which
    macOS each reaches are each answered by the project that maintains that
    fact. Falls back to whatever ``Utilities/macrecovery`` ships, and then to
    nothing at all rather than to a guess.
    """
    if darwin_version:
        from . import boards as board_table
        options = board_table.options_for(darwin_version)
        if options:
            return [(board, label) for board, _model, label in options]
    folder = macrecovery_dir(paths)
    if not folder.exists():
        return []

    for candidate in sorted(folder.glob("*.json")):
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
        found = _boards_from_json(data)
        if found:
            return found

    # Fall back to any documentation shipped beside it: the guide's table is
    # "<board id>  <macOS name>" often enough to be worth reading.
    for candidate in sorted(folder.glob("*.md")) + sorted(folder.glob("*.txt")):
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        found = _boards_from_text(text)
        if found:
            return found
    return []


def _boards_from_json(data) -> list:
    out = []
    if isinstance(data, dict):
        for key, value in data.items():
            if BOARD_PATTERN.fullmatch(str(key)):
                out.append((str(key), _label_of(value) or str(key)))
            elif isinstance(value, (dict, str)):
                board = _board_in(value)
                if board:
                    out.append((board, str(key)))
    elif isinstance(data, list):
        for item in data:
            board = _board_in(item)
            if board:
                out.append((board, _label_of(item) or board))
    return out


def _board_in(value):
    if isinstance(value, str):
        match = BOARD_PATTERN.search(value)
        return match.group(0) if match else None
    if isinstance(value, dict):
        for key in ("board", "board_id", "boardid", "BoardID", "id"):
            if key in value and BOARD_PATTERN.search(str(value[key])):
                return BOARD_PATTERN.search(str(value[key])).group(0)
    return None


def _label_of(value):
    if isinstance(value, dict):
        for key in ("os", "name", "version", "macos", "title", "product"):
            if value.get(key):
                return str(value[key])
    elif isinstance(value, str) and not BOARD_PATTERN.fullmatch(value):
        return value
    return None


def _boards_from_text(text: str) -> list:
    out = []
    for line in text.splitlines():
        match = BOARD_PATTERN.search(line)
        if not match:
            continue
        label = line.replace(match.group(0), " ").strip(" |#-\t")
        label = re.sub(r"\s{2,}", " ", label).strip()
        out.append((match.group(0), label or match.group(0)))
    return out


def match_board(options, macos_name: str):
    """The board whose label names *macos_name*, if there is one."""
    if not macos_name:
        return None
    wanted = macos_name.lower().replace("macos", "").strip()
    words = [w for w in re.split(r"[^a-z0-9.]+", wanted) if w]
    best = None
    for board, label in options:
        lowered = label.lower()
        if words and all(word in lowered for word in words):
            return board
        if words and words[0] and words[0] in lowered:
            best = best or board
    return best


# ---------------------------------------------------------------------------
# downloading
# ---------------------------------------------------------------------------

def download_recovery(paths, board_id: str, report=None,
                      os_type: str = "latest") -> Path:
    """Run OpenCore's macrecovery for *board_id*. Returns the recovery folder.

    Executed in-process: a frozen build has no python.exe to hand the script
    to, and macrecovery is pure standard library, so ``runpy`` is both simpler
    and faster than shipping an interpreter to run it with.
    """
    def say(message):
        if report:
            report(message)

    if not BOARD_PATTERN.fullmatch(board_id or ""):
        raise MediaError(
            "\"%s\" is not a board id." % (board_id or ""),
            "It looks like Mac-E43C1C25D4880AD6: the letters \"Mac-\" and then "
            "hexadecimal. The board id decides which macOS Apple sends, which "
            "is why OpCoreForge will not invent one.")

    folder = macrecovery_dir(paths)
    script = folder / "macrecovery.py"
    if not script.exists():
        raise MediaError(
            "macrecovery.py is not available.",
            "It is extracted from the OpenCorePkg release; that step has not "
            "run or did not finish.")

    destination = recovery_dir(paths)
    if destination.exists():
        shutil.rmtree(destination, ignore_errors=True)
    destination.parent.mkdir(parents=True, exist_ok=True)

    say("Asking Apple for the recovery image (this takes a few minutes)...")
    argv = list(sys.argv)
    cwd = os.getcwd()
    try:
        os.chdir(str(destination.parent))
        sys.argv = [str(script), "-b", board_id, "-m", "00000000000000000",
                    "-os", os_type, "download"]
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as stop:
        if stop.code not in (0, None):
            raise MediaError(
                "macrecovery could not download the recovery image.",
                "It exited with code %s. The tool output pane has what it "
                "printed; a wrong board id and a blocked connection both look "
                "like this." % stop.code)
    except MediaError:
        raise
    except Exception as exc:
        raise MediaError(
            "The recovery download failed.",
            "%s: %s\n\nThe tool output pane has the details."
            % (type(exc).__name__, exc)) from exc
    finally:
        sys.argv = argv
        os.chdir(cwd)

    missing = [name for name in RECOVERY_FILES
               if not (destination / name).exists()]
    if missing:
        raise MediaError(
            "The recovery download did not produce %s." % ", ".join(missing),
            "Without those files there is nothing for the Mac to boot. The "
            "tool output pane has what macrecovery reported.")
    size = (destination / "BaseSystem.dmg").stat().st_size
    say("Recovery downloaded (%.1f GB)" % (size / 1024 ** 3))
    return destination


# ---------------------------------------------------------------------------
# laying out the media
# ---------------------------------------------------------------------------

def copy_tree_with_progress(source: Path, destination: Path, total: int,
                            done: list, progress=None) -> None:
    """``copytree`` that says how far it has got.

    The recovery is one 2-3 GB file, so per-file reporting would jump from 0
    to 100 with a silent gap in between; the copy is done in chunks for that
    reason alone.
    """
    def copy(src, dst):
        with open(src, "rb") as reader, open(dst, "wb") as writer:
            while True:
                chunk = reader.read(1024 * 1024)
                if not chunk:
                    break
                writer.write(chunk)
                done[0] += len(chunk)
                if progress:
                    progress(min(done[0], total), total)
        shutil.copystat(src, dst)
        return dst

    shutil.copytree(source, destination, copy_function=copy)


def stage_media(paths, report=None, progress=None) -> Path:
    """Assemble EFI + recovery into one folder ready to be copied to a USB."""
    def say(message):
        if report:
            report(message)

    efi = paths.efi_dir
    if not efi.exists():
        raise MediaError(
            "There is no EFI folder to put on the media.",
            "Build it in stage 6 first; the installer needs OpenCore as well "
            "as the recovery.")
    recovery = recovery_dir(paths)
    if not (recovery / "BaseSystem.dmg").exists():
        raise MediaError(
            "The macOS recovery has not been downloaded yet.",
            "Use \"Download recovery\" first - it is what the installer "
            "actually boots.")

    target = media_dir(paths)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)

    # Last chance to catch a config that names a file the EFI does not have.
    # OpenCore halts on one of those before the picker is any use, and by then
    # the stick is written and the user is standing at a dead machine, so it is
    # worth the half second here. Repairing the source as well as the copy is
    # deliberate: the same EFI is what goes on the internal disk afterwards.
    from . import eficheck
    for note in eficheck.sync_usb_map(efi):
        say("Fixed: %s" % note)
    for note in eficheck.repair(efi):
        say("Fixed: %s" % note)
    for problem in eficheck.audit(efi):
        if not problem.fatal:
            say("Note: %s" % problem)

    total = folder_size(efi) + folder_size(recovery)
    done = [0]
    if progress:
        progress(0, total)
    say("Copying the EFI folder...")
    copy_tree_with_progress(efi, target / "EFI", total, done, progress)
    say("Copying the recovery (%.1f GB)..." % (folder_size(recovery) / 1024 ** 3))
    copy_tree_with_progress(recovery, target / RECOVERY_DIR, total, done,
                            progress)
    if progress:
        progress(total, total)
    changed = tune_config_for_install(target / "EFI" / "OC" / "config.plist")
    for note in changed:
        say(note)
    say("Install media ready")
    return target


#: Settings the installer media needs that an installed system does not.
INSTALL_CONFIG = {
    # macOS Recovery is an *auxiliary* entry in OpenCore's picker, and the
    # OpenCore sample config hides auxiliary entries. On an installed system
    # that is the right default -- it keeps recovery and tools out of the way.
    # On the installer it hides the only thing you booted the stick for, and
    # the picker comes up looking empty. Reported exactly that way from the
    # field: "OpenCore booted, I just had no option for macOS recovery."
    "HideAuxiliary": False,
    # Five seconds to notice an unfamiliar picker and choose is not enough.
    "Timeout": 15,
    # So the boot picker responds to the Apple keyboard shortcuts people
    # expect while installing.
    "PollAppleHotKeys": True,
}


def tune_config_for_install(config_path: Path) -> list:
    """Adjust the *media copy* of config.plist for installing.

    Only the copy on the media: the EFI in Results is for the machine once
    macOS is on it, where hiding auxiliary entries and a short timeout are
    the better defaults. Returns a list of what was changed, for the log.
    """
    import plistlib

    config_path = Path(config_path)
    if not config_path.exists():
        return []
    try:
        with config_path.open("rb") as handle:
            config = plistlib.load(handle)
    except Exception:
        return []

    boot = config.setdefault("Misc", {}).setdefault("Boot", {})
    security = config["Misc"].setdefault("Security", {})
    changed = []
    for key, value in INSTALL_CONFIG.items():
        if boot.get(key) != value:
            changed.append("Install media: %s %r -> %r"
                           % (key, boot.get(key), value))
            boot[key] = value
    # ScanPolicy 0 scans everything; anything else can hide the recovery on an
    # external FAT volume. OpCore-Simplify already sets this, so it is a
    # backstop rather than a change.
    if security.get("ScanPolicy") != 0:
        changed.append("Install media: ScanPolicy %r -> 0"
                       % security.get("ScanPolicy"))
        security["ScanPolicy"] = 0

    if changed:
        try:
            with config_path.open("wb") as handle:
                plistlib.dump(config, handle, sort_keys=False)
        except Exception:
            return []
    return changed


def media_summary(paths) -> dict:
    """What exists so far, for the stage to show without doing any work."""
    recovery = recovery_dir(paths)
    staged = media_dir(paths)
    base = recovery / "BaseSystem.dmg"
    return {
        "efi": paths.efi_dir.exists(),
        "macrecovery": have_macrecovery(paths),
        "recovery": base.exists(),
        "recovery_size": base.stat().st_size if base.exists() else 0,
        "staged": (staged / "EFI").exists()
                  and (staged / RECOVERY_DIR / "BaseSystem.dmg").exists(),
        "staged_path": staged,
    }


def folder_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total
