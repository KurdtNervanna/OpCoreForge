"""
Putting OpenCore on the internal disk, so the USB stick is no longer needed.

The work is split the same way the Windows USB writer splits it:

- ``find_sources`` / ``check_source`` / ``prepare`` run as the user and touch
  nothing but a staging folder. That is where the EFI is checked and where
  config.plist is adjusted, so the privileged part has no decisions left in it.
- ``install_script`` builds the shell script that does the privileged part. It
  is a pure function that runs nothing, so what will be executed as root can
  be read, logged and tested -- and the tests do run it, against folders.
- ``install`` asks for the administrator password once (through
  ``osascript``, the standard macOS prompt) and runs that script.

The script backs up the whole ``EFI`` folder first and replaces only
``EFI/OC`` and ``EFI/BOOT``. Everything else on the partition -- above all
``EFI/Microsoft`` on a disk shared with Windows -- is left exactly as it was.
If any copy fails, the previous ``OC`` and ``BOOT`` are put back before the
script exits.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .. import eficheck

#: the folder OpCoreForge puts on the install media for the companion
MEDIA_FOLDER = "OpCoreForge"
MEDIA_INFO = "media.json"

#: absolute paths, because the script runs as root with a minimal PATH; the
#: tests point these at stand-ins
TOOLS = {
    "DISKUTIL": "/usr/sbin/diskutil",
    "DITTO": "/usr/bin/ditto --norsrc --noextattr --noacl",
}

#: macOS litter that must not be copied onto a FAT partition
JUNK = ("._*", ".DS_Store", ".Trashes", ".fseventsd", ".Spotlight-V100")


class InstallError(Exception):
    """Installing could not be done, with a reason the user can act on."""

    def __init__(self, message, hint=""):
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self):
        return "%s\n\n%s" % (self.message, self.hint) if self.hint else self.message


class Cancelled(InstallError):
    """The password prompt was dismissed. Nothing was changed."""


@dataclass
class Source:
    """An EFI folder that can be installed: it holds ``OC`` and ``BOOT``."""

    path: Path
    label: str
    #: "built" - the EFI exactly as OpCoreForge built it, for the internal disk
    #: "media" - the copy the install stick boots from (install settings)
    #: "chosen" - a folder the user picked
    kind: str


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------

def _media_note(folder: Path) -> str:
    try:
        info = json.loads((folder / MEDIA_INFO).read_text(encoding="utf-8"))
    except Exception:
        return ""
    version = info.get("opcoreforge")
    created = info.get("created", "")[:10]
    if version and created:
        return " - OpCoreForge %s, %s" % (version, created)
    return " - OpCoreForge %s" % version if version else ""


def find_sources(volumes=Path("/Volumes")) -> list:
    """EFI folders on mounted drives, the as-built one first.

    An install stick made by OpCoreForge 2.0 or later carries the EFI twice:
    at its root, tuned for installing (recovery shown in the picker, a long
    timeout), and under ``OpCoreForge/EFI`` exactly as it was built. The second
    is the one meant for the internal disk. Older sticks only have the first,
    which still works -- it just keeps the installer's picker settings.
    """
    built, media = [], []
    try:
        children = sorted(Path(volumes).iterdir())
    except OSError:
        return []
    for volume in children:
        try:
            if volume.resolve() == Path("/").resolve():
                continue  # the boot volume's link in /Volumes
        except OSError:
            continue
        kept = volume / MEDIA_FOLDER
        if (kept / "EFI" / "OC" / "OpenCore.efi").is_file():
            built.append(Source(
                kept / "EFI",
                "%s: the EFI as OpCoreForge built it%s"
                % (volume.name, _media_note(kept)), "built"))
        if (volume / "EFI" / "OC" / "OpenCore.efi").is_file():
            media.append(Source(
                volume / "EFI",
                "%s: the EFI this drive boots with" % volume.name, "media"))
    return built + media


def chosen_source(folder: Path) -> Source:
    """A folder the user picked: the EFI folder itself, or one containing it."""
    folder = Path(folder)
    if folder.name.upper() != "EFI" and (folder / "EFI").is_dir():
        folder = folder / "EFI"
    return Source(folder, str(folder), "chosen")


def check_source(efi: Path) -> list:
    """Reasons *efi* cannot be installed. Empty when it can."""
    efi = Path(efi)
    problems = []
    for relative in ("OC/OpenCore.efi", "OC/config.plist", "BOOT/BOOTx64.efi"):
        if not (efi / relative).is_file():
            problems.append("%s is missing" % relative)
    if problems:
        return problems
    for problem in eficheck.fatal(eficheck.audit(efi)):
        problems.append(str(problem))
    return problems


# ---------------------------------------------------------------------------
# staging
# ---------------------------------------------------------------------------

def prepare(source: Path, staging: Path, launcher_full: bool = True) -> list:
    """Copy *source* into *staging* and adjust it. Returns what was changed.

    ``LauncherOption`` is what makes this worth doing: with it set to ``Full``
    OpenCore adds its own entry to the firmware's boot menu the first time it
    starts from the internal disk, and keeps it at the top -- which is what
    replaces "boot from the USB stick". The sample config ships it
    ``Disabled``. A value someone set deliberately is left alone.
    """
    problems = check_source(source)
    if problems:
        raise InstallError(
            "That EFI folder cannot be installed: %s." % "; ".join(problems),
            "OpenCore would stop at boot on this. Rebuild the EFI in "
            "OpCoreForge, or pick another folder.")

    staging = Path(staging)
    target = staging / "EFI"
    if target.exists():
        shutil.rmtree(target)
    ignore = shutil.ignore_patterns(*JUNK)
    shutil.copytree(Path(source) / "OC", target / "OC", ignore=ignore)
    shutil.copytree(Path(source) / "BOOT", target / "BOOT", ignore=ignore)

    notes = []
    if launcher_full:
        config_path = target / "OC" / "config.plist"
        config = eficheck.load_config(config_path)
        boot = config.setdefault("Misc", {}).setdefault("Boot", {})
        if boot.get("LauncherOption") in (None, "", "Disabled"):
            notes.append("LauncherOption %s -> Full, so OpenCore adds itself "
                         "to the firmware boot menu"
                         % (boot.get("LauncherOption") or "unset"))
            boot["LauncherOption"] = "Full"
        if not boot.get("LauncherPath"):
            boot["LauncherPath"] = "Default"
        if notes:
            eficheck.save_config(config_path, config)
    return notes


def folder_kb(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total // 1024 + 1


def backup_root() -> Path:
    # Application Support rather than Documents: Documents is privacy
    # protected, and a root shell started by osascript writing there can be
    # refused without any prompt at all.
    return (Path.home() / "Library" / "Application Support"
            / "OpCoreForge Companion" / "EFI backups")


def new_backup_dir(device: str, root: Path = None) -> Path:
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H.%M.%S")
    return Path(root or backup_root()) / ("%s %s" % (stamp, device))


# ---------------------------------------------------------------------------
# the privileged part
# ---------------------------------------------------------------------------

def install_script(staging: Path, device: str, backup: Path, owner: str,
                   needed_kb: int, mount_point: str = "", tools=None) -> str:
    """The shell script that installs *staging* onto *device*. Runs nothing.

    ``mount_point`` is where the partition is already mounted, if it is; the
    script then uses it and leaves it mounted. Otherwise it mounts it on a
    private folder and unmounts it again however it exits.
    """
    tools = dict(TOOLS, **(tools or {}))
    q = shlex.quote
    return "\n".join([
        "#!/bin/sh",
        "# OpCoreForge Companion: install OpenCore to %s" % device,
        "set -u",
        "DISKUTIL=%s" % q(tools["DISKUTIL"]),
        "DITTO=%s" % q(tools["DITTO"]),
        "DEVICE=%s" % q(device),
        "STAGING=%s" % q(str(staging)),
        "BACKUP=%s" % q(str(backup)),
        "OWNER=%s" % q(owner),
        "NEEDED_KB=%d" % int(needed_kb),
        "ESP=%s" % q(mount_point or ""),
        "MOUNTED_HERE=0",
        "CHANGED=0",
        "",
        'say() { echo "OCF: $*"; }',
        'fail() { echo "OCF-ERROR: $*" >&2; exit 1; }',
        "",
        "finish() {",
        "    status=$?",
        '    if [ "$status" -ne 0 ] && [ "$CHANGED" -eq 1 ]; then',
        '        say "The copy failed - putting the previous EFI back"',
        '        rm -rf "$ESP/EFI/OC" "$ESP/EFI/BOOT"',
        '        [ -d "$BACKUP/EFI/OC" ] && $DITTO "$BACKUP/EFI/OC" "$ESP/EFI/OC"',
        '        [ -d "$BACKUP/EFI/BOOT" ] && $DITTO "$BACKUP/EFI/BOOT" "$ESP/EFI/BOOT"',
        "    fi",
        '    [ -d "$BACKUP" ] && chown -R "$OWNER" "$BACKUP" 2>/dev/null',
        '    if [ "$MOUNTED_HERE" -eq 1 ]; then',
        "        sync",
        '        $DISKUTIL unmount "$DEVICE" >/dev/null 2>&1',
        '        rmdir "$ESP" 2>/dev/null',
        "    fi",
        '    exit "$status"',
        "}",
        "trap finish EXIT",
        "",
        'if [ -z "$ESP" ]; then',
        '    ESP=$(mktemp -d /tmp/ocf-esp.XXXXXX) || fail "Could not make a mount point"',
        '    $DISKUTIL mount -mountPoint "$ESP" "$DEVICE" >/dev/null 2>&1 '
        '|| fail "$DEVICE could not be mounted"',
        "    MOUNTED_HERE=1",
        "fi",
        'case "$ESP" in ""|/) fail "No mount point for $DEVICE";; esac',
        '[ -d "$ESP" ] || fail "$DEVICE is not mounted at $ESP"',
        '[ -f "$STAGING/EFI/OC/OpenCore.efi" ] || fail "The prepared EFI is incomplete"',
        "",
        "# Room for the new OC and BOOT, counting the space the old ones free.",
        "existing_kb=0",
        'for d in "$ESP/EFI/OC" "$ESP/EFI/BOOT"; do',
        '    if [ -d "$d" ]; then',
        "        kb=$(du -sk \"$d\" | awk '{print $1}')",
        "        existing_kb=$((existing_kb + kb))",
        "    fi",
        "done",
        "avail_kb=$(df -kP \"$ESP\" | awk 'NR==2 {print $4}')",
        'if [ $((avail_kb + existing_kb)) -lt $((NEEDED_KB + 1024)) ]; then',
        '    fail "Not enough room on $DEVICE: needs ${NEEDED_KB} KB, has '
        '$((avail_kb + existing_kb)) KB"',
        "fi",
        "",
        'say "Backing up the current EFI folder"',
        'mkdir -p "$BACKUP" || fail "Could not create the backup folder"',
        'if [ -d "$ESP/EFI" ]; then',
        '    $DITTO "$ESP/EFI" "$BACKUP/EFI" || fail "The backup failed; nothing was changed"',
        "fi",
        "",
        'say "Copying OpenCore"',
        "CHANGED=1",
        'rm -rf "$ESP/EFI/OC" "$ESP/EFI/BOOT"',
        'mkdir -p "$ESP/EFI" || fail "Could not create EFI on $DEVICE"',
        '$DITTO "$STAGING/EFI/OC" "$ESP/EFI/OC" || fail "Copying EFI/OC failed"',
        '$DITTO "$STAGING/EFI/BOOT" "$ESP/EFI/BOOT" || fail "Copying EFI/BOOT failed"',
        'cmp -s "$STAGING/EFI/OC/config.plist" "$ESP/EFI/OC/config.plist" '
        '|| fail "config.plist did not copy intact"',
        '[ -f "$ESP/EFI/BOOT/BOOTx64.efi" ] || fail "BOOTx64.efi did not copy"',
        "sync",
        "CHANGED=0",
        'say "Installed"',
        "",
    ])


def _applescript_string(text: str) -> str:
    return '"%s"' % text.replace("\\", "\\\\").replace('"', '\\"')


def admin_command(script: Path) -> list:
    """The ``osascript`` call that runs *script* as root after a password."""
    command = "/bin/sh " + shlex.quote(str(script))
    return [
        "/usr/bin/osascript", "-e",
        "do shell script %s with prompt %s with administrator privileges"
        % (_applescript_string(command),
           _applescript_string("OpCoreForge Companion needs an administrator "
                               "password to write OpenCore to the EFI "
                               "partition.")),
    ]


def run_privileged(script_text: str, runner=None) -> str:
    """Run *script_text* as root. Returns what it printed.

    The script is written to a private temporary folder (mode 0700) that only
    this user can change, and removed afterwards.
    """
    folder = Path(tempfile.mkdtemp(prefix="ocf-install-"))
    script = folder / "install.sh"
    try:
        script.write_text(script_text, encoding="utf-8")
        argv = admin_command(script)
        if runner:
            returncode, out, err = runner(argv)
        else:
            done = subprocess.run(argv, capture_output=True)
            returncode = done.returncode
            out = done.stdout.decode("utf-8", errors="replace")
            err = done.stderr.decode("utf-8", errors="replace")
    finally:
        shutil.rmtree(folder, ignore_errors=True)

    out = out.replace("\r", "\n")
    if returncode != 0:
        if "-128" in err:
            raise Cancelled("Cancelled - nothing was changed.")
        reasons = [line.split("OCF-ERROR:", 1)[1].strip()
                   for line in (err + "\n" + out).replace("\r", "\n").splitlines()
                   if "OCF-ERROR:" in line]
        raise InstallError(
            re.sub(r"\s*\(-?\d+\)\s*$", "", reasons[0]) if reasons
            else "The install script failed.",
            "If OpenCore on the disk was touched, the previous EFI was put "
            "back. The details are in the log below.\n\n%s" % err.strip())
    return out


def install(source: Path, target, launcher_full=True, report=None,
            runner=None, backups: Path = None, tools=None) -> Path:
    """Install *source* onto the EFI partition *target*. Returns the backup.

    *target* is a ``disks.EfiPartition``.
    """
    def say(message):
        if report:
            report(message)

    staging = Path(tempfile.mkdtemp(prefix="ocf-staging-"))
    try:
        say("Checking %s" % source)
        for note in prepare(source, staging, launcher_full):
            say(note)
        backup = new_backup_dir(target.device, backups)
        backup.parent.mkdir(parents=True, exist_ok=True)
        script = install_script(
            staging, target.device, backup,
            owner="%d:%d" % (os.getuid(), os.getgid()),
            needed_kb=folder_kb(staging),
            mount_point=target.mount_point, tools=tools)
        say("This script will run as administrator:\n%s" % script)
        say("Waiting for the administrator password...")
        output = run_privileged(script, runner)
        for line in output.splitlines():
            if line.startswith("OCF: "):
                say(line[5:])
        return backup
    finally:
        shutil.rmtree(staging, ignore_errors=True)
