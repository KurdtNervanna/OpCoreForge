"""
Writing the install media to a USB stick.

This is the only thing in OpCoreForge that can destroy data, so the rules are
stricter than anywhere else and they are enforced here rather than in the
dialog that calls it:

- Removable drives only. A fixed disk is refused even if it is external, even
  if it is empty, and there is no override.
- Never disk 0, and never a disk holding the Windows drive or the folder
  OpCoreForge is running from -- those are checked separately from
  "removable", because a badly behaved enclosure can report either.
- The diskpart script is built by a pure function that runs no commands, so
  what will be executed can be read, logged and tested without a disk.
- The caller has to pass the disk's identity back in ``confirm``. Passing the
  wrong one is refused. It is deliberately impossible to write to "the
  selected drive" without naming it.

Windows only: it is diskpart that does the partitioning. Everywhere else the
enumeration returns nothing and the stage offers the folder instead.
"""

from __future__ import annotations

import os
import subprocess
import shutil
import time
from pathlib import Path

#: recovery plus OpenCore, with room to breathe
MINIMUM_BYTES = 2 * 1024 ** 3
VOLUME_LABEL = "OPENCORE"


class UnsafeTarget(Exception):
    """The chosen disk is not one this is willing to write to."""

    title = "That drive cannot be used"

    def __init__(self, message, hint=""):
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self):
        return "%s\n\n%s" % (self.message, self.hint) if self.hint else self.message


class Disk:
    """One candidate drive, as shown to the user."""

    def __init__(self, index, model, size, removable, interface, letters=()):
        self.index = int(index)
        self.model = (model or "Unknown device").strip()
        self.size = int(size or 0)
        self.removable = bool(removable)
        self.interface = interface or ""
        self.letters = tuple(letters)

    @property
    def size_gb(self) -> float:
        return self.size / 1024 ** 3

    def label(self) -> str:
        where = (" (%s)" % ", ".join(self.letters)) if self.letters else ""
        return "Disk %d - %s - %.1f GB%s" % (
            self.index, self.model, self.size_gb, where)

    def __repr__(self):
        return "<Disk %d %s %.1fGB removable=%s>" % (
            self.index, self.model, self.size_gb, self.removable)


def protected_letters() -> set:
    """Drive letters that must never be on the target disk."""
    letters = set()
    for value in (os.environ.get("SystemDrive"), os.environ.get("windir")):
        if value and len(value) >= 2 and value[1] == ":":
            letters.add(value[0].upper())
    try:
        here = Path(__file__).resolve()
        if here.drive:
            letters.add(here.drive[0].upper())
    except Exception:
        pass
    return letters


def list_disks(wmi_module=None) -> list:
    """Removable drives, or an empty list where this cannot be asked."""
    if os.name != "nt" and wmi_module is None:
        return []
    if wmi_module is None:
        import wmi as wmi_module
    client = wmi_module.WMI()
    disks = []
    for drive in client.Win32_DiskDrive():
        interface = getattr(drive, "InterfaceType", "") or ""
        media = getattr(drive, "MediaType", "") or ""
        removable = ("removable" in media.lower()
                     or interface.upper() == "USB")
        letters = []
        try:
            for partition in drive.associators("Win32_DiskDriveToDiskPartition"):
                for logical in partition.associators(
                        "Win32_LogicalDiskToPartition"):
                    if getattr(logical, "DeviceID", None):
                        letters.append(logical.DeviceID.rstrip("\\"))
        except Exception:
            pass
        disks.append(Disk(getattr(drive, "Index", -1),
                          getattr(drive, "Model", ""),
                          getattr(drive, "Size", 0) or 0,
                          removable, interface, letters))
    return [d for d in disks if d.removable]


def check_target(disk: Disk, needed_bytes: int = 0) -> None:
    """Raise unless *disk* is safe to erase. Every rule, in one place."""
    if disk is None:
        raise UnsafeTarget("No drive is selected.",
                           "Choose the USB stick to write to.")
    if not disk.removable:
        raise UnsafeTarget(
            "%s is not a removable drive." % disk.model,
            "OpCoreForge only writes to removable drives. If this really is a "
            "USB stick that Windows reports as fixed, copy the InstallMedia "
            "folder onto it yourself instead - the layout is the same.")
    if disk.index == 0:
        raise UnsafeTarget(
            "Disk 0 is never written to.",
            "That is where Windows normally lives, whatever it reports about "
            "itself.")
    protected = protected_letters() & {l[0].upper() for l in disk.letters}
    if protected:
        raise UnsafeTarget(
            "%s holds drive %s." % (disk.model,
                                    ", ".join(sorted(protected)) + ":"),
            "That is this PC's system or program drive, so it is refused "
            "regardless of anything else.")
    if disk.size < MINIMUM_BYTES:
        raise UnsafeTarget(
            "%s is only %.1f GB." % (disk.model, disk.size_gb),
            "The recovery and OpenCore need about 2 GB. Use a larger stick.")
    if needed_bytes and disk.size < needed_bytes * 1.05:
        raise UnsafeTarget(
            "%s is too small for this media (%.1f GB of %.1f GB)."
            % (disk.model, needed_bytes / 1024 ** 3, disk.size_gb),
            "Use a larger USB stick.")


def diskpart_script(disk: Disk, label: str = VOLUME_LABEL) -> str:
    """Exactly what would be handed to diskpart. Runs nothing.

    Kept separate so it can be shown to the user, written to the log and
    asserted on in tests without a disk anywhere near it.
    """
    check_target(disk)
    return "\n".join([
        "select disk %d" % disk.index,
        "clean",
        "convert gpt",
        "create partition primary",
        "format fs=fat32 quick label=%s" % label[:11],
        "assign",
        "exit",
        "",
    ])


def write_media(disk: Disk, media: Path, confirm: str, report=None,
                runner=None, progress=None) -> str:
    """Erase *disk* and copy *media* onto it. Returns the drive letter.

    ``confirm`` must equal the disk's own label. It is not a formality: it is
    the difference between "write to the selected drive" and "write to this
    specific drive I have just read back", and it is what stops a refreshed
    list from silently moving the target.
    """
    def say(message):
        if report:
            report(message)

    needed = 0
    media = Path(media)
    if media.exists():
        from .media import folder_size
        needed = folder_size(media)
    check_target(disk, needed)
    if confirm != disk.label():
        raise UnsafeTarget(
            "The drive that was confirmed is not the drive selected.",
            "Nothing has been changed. Pick the drive again and confirm it.")
    if not (media / "EFI").exists():
        raise UnsafeTarget(
            "There is no install media to write.",
            "Prepare the folder first.")

    script = diskpart_script(disk)
    say("Erasing %s..." % disk.label())
    run = runner or _run_diskpart
    output = run(script)

    letter = _new_letter(output)
    say("Formatted. Copying %.1f GB..." % (needed / 1024 ** 3))
    if letter is None:
        raise UnsafeTarget(
            "The drive was formatted but Windows did not give it a letter.",
            "Open Disk Management, assign one, and copy the InstallMedia "
            "folder's contents onto it by hand.")
    root = Path("%s\\" % letter)
    for _attempt in range(20):
        if root.exists():
            break
        time.sleep(0.5)
    done = [0]
    if progress:
        progress(0, needed)

    def copy(src, dst):
        # Chunked rather than shutil.copy2 so a 3 GB recovery is not a single
        # silent step; the whole point of the progress line is the long file.
        with open(src, "rb") as reader, open(dst, "wb") as writer:
            while True:
                chunk = reader.read(1024 * 1024)
                if not chunk:
                    break
                writer.write(chunk)
                done[0] += len(chunk)
                if progress:
                    progress(min(done[0], needed), needed)
        return dst

    for child in sorted(media.iterdir()):
        destination = root / child.name
        if child.is_dir():
            shutil.copytree(child, destination, dirs_exist_ok=True,
                            copy_function=copy)
        else:
            copy(child, destination)
    if progress:
        progress(needed, needed)
    say("Flushing to %s - do not unplug the drive yet..." % letter)
    _flush(letter)
    say("Written to %s" % letter)
    return letter


def _flush(letter: str) -> bool:
    """Push Windows' write cache out to the stick before saying it is done.

    Removable media is written lazily, so "copied" and "safe to unplug" are not
    the same moment. Best effort: it needs the elevation diskpart already
    needed, and a failure here is not a failure of the write.
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateFileW(
            "\\\\.\\%s" % letter.rstrip("\\"),
            0x40000000,                       # GENERIC_WRITE
            0x00000001 | 0x00000002,          # FILE_SHARE_READ | WRITE
            None, 3,                          # OPEN_EXISTING
            0x00000080, None)                 # FILE_ATTRIBUTE_NORMAL
        if handle in (0, wintypes.HANDLE(-1).value, -1):
            return False
        try:
            return bool(kernel32.FlushFileBuffers(handle))
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


def _run_diskpart(script: str) -> str:
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                     encoding="ascii") as handle:
        handle.write(script)
        path = handle.name
    try:
        done = subprocess.run(["diskpart", "/s", path],
                              capture_output=True, text=True, timeout=600)
        if done.returncode != 0:
            raise UnsafeTarget(
                "diskpart could not prepare the drive.",
                "%s\n\n%s" % (done.stdout.strip(), done.stderr.strip()))
        return done.stdout
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _new_letter(diskpart_output: str):
    """The letter diskpart assigned, read back from Windows rather than it."""
    import string
    if os.name != "nt":
        return None
    for letter in string.ascii_uppercase[3:]:      # never A, B or C
        root = Path("%s:\\" % letter)
        try:
            if root.exists() and _volume_label(root) == VOLUME_LABEL:
                return "%s:" % letter
        except OSError:
            continue
    return None


def _volume_label(root: Path) -> str:
    try:
        import ctypes
        buffer = ctypes.create_unicode_buffer(261)
        ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(str(root)), buffer, 261,
            None, None, None, None, 0)
        return buffer.value.upper()
    except Exception:
        return ""
