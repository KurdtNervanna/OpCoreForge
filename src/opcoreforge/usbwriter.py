"""
Writing the install media to a USB stick.

This is the only thing in OpCoreForge that can destroy data, so the rules are
stricter than anywhere else and they are enforced here rather than in the
dialog that calls it:

- USB drives only, decided by the bus Windows reports the disk on -- not by
  what the drive says about itself. SATA, NVMe, SAS and RAID disks are never
  listed and are refused if one is passed in anyway; nothing overrides that.
- USB *sticks* are offered by default. USB *hard drives* (fixed media on the
  USB bus, which is what most external SSDs and enclosures are) only when the
  caller asks for them -- the "List USB hard drives" box, as in Rufus.
- Never disk 0, never the disk Windows marks as its boot or system disk, never
  a disk holding the Windows drive or the folder OpCoreForge is running from,
  and never a disk with a Windows installation on any of its partitions.
  Those are checked separately from "USB", because a badly behaved enclosure
  can report anything, and Windows To Go lives on USB disks.
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

#: Windows' format refuses FAT32 volumes larger than this...
FAT32_LIMIT = 32 * 1024 ** 3
#: ...so on a bigger drive the media gets a partition this size and the rest
#: stays unallocated. Several times what the media needs.
PARTITION_MB = 16 * 1024

#: MSFT_Disk.BusType values, from the Storage Management API
BUS_TYPES = {1: "SCSI", 2: "ATAPI", 3: "ATA", 4: "IEEE 1394", 5: "SSA",
             6: "Fibre Channel", 7: "USB", 8: "RAID", 9: "iSCSI", 10: "SAS",
             11: "SATA", 12: "SD", 13: "MMC", 15: "File-backed virtual",
             16: "Storage Spaces", 17: "NVMe"}


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
    """One candidate drive, as shown to the user.

    ``removable`` is the media type (a stick or card, as opposed to a hard
    drive); ``bus`` is how the disk is connected. When the bus is not known it
    is taken from the interface, which says USB only for drives that use the
    older USB mass-storage protocol -- so an unknown bus can only ever make a
    drive *less* likely to be offered.
    """

    def __init__(self, index, model, size, removable, interface, letters=(),
                 bus=None, system=False, windows=(), volumes=()):
        self.index = int(index)
        self.model = (model or "Unknown device").strip()
        self.size = int(size or 0)
        self.removable = bool(removable)
        self.interface = interface or ""
        self.letters = tuple(letters)
        self.bus = bus or ("USB" if self.interface.upper() == "USB"
                           else self.interface or "unknown")
        #: Windows says it boots from, or keeps its system partition on, this
        self.system = bool(system)
        #: letters on this disk that hold a Windows installation
        self.windows = tuple(windows)
        #: (letter, label, size, free) for each volume, for the confirmation
        self.volumes = tuple(volumes)

    @property
    def usb(self) -> bool:
        return self.bus.upper() == "USB"

    @property
    def hard_drive(self) -> bool:
        return self.usb and not self.removable

    @property
    def kind(self) -> str:
        return "USB hard drive" if self.hard_drive else "USB stick"

    def contents(self) -> list:
        """One line per volume, for the person deciding whether to erase it."""
        lines = []
        for letter, label, size, free in self.volumes:
            used = max(0, int(size or 0) - int(free or 0))
            lines.append("%s  %s  -  %.1f GB, %.1f GB used" % (
                letter, label or "(no name)", int(size or 0) / 1024 ** 3,
                used / 1024 ** 3))
        return lines

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


def _storage_disks(wmi_module) -> dict:
    """``{disk number: (bus name, boot or system)}`` from the Storage API.

    ``MSFT_Disk`` reports the real bus -- a UAS enclosure is USB here while
    Win32_DiskDrive calls it SCSI -- and Windows' own opinion of whether it
    boots from the disk. Windows 8 and later; an empty answer leaves the
    interface to decide, which errs towards offering less.
    """
    try:
        storage = wmi_module.WMI(namespace="root/Microsoft/Windows/Storage")
        found = {}
        for disk in storage.MSFT_Disk():
            bus = BUS_TYPES.get(int(getattr(disk, "BusType", 0) or 0), "other")
            system = bool(getattr(disk, "IsBoot", False)
                          or getattr(disk, "IsSystem", False))
            found[int(disk.Number)] = (bus, system)
        return found
    except Exception:
        return {}


def _has_windows(letter: str) -> bool:
    return os.path.isdir("%s\\Windows\\System32" % letter.rstrip("\\"))


def list_disks(wmi_module=None, include_hard_drives=False,
               has_windows=_has_windows) -> list:
    """USB drives that may be offered, or an empty list where this cannot be asked.

    Sticks always; USB hard drives only with *include_hard_drives*. Anything
    not on the USB bus is never returned at all.
    """
    if os.name != "nt" and wmi_module is None:
        return []
    if wmi_module is None:
        import wmi as wmi_module
    client = wmi_module.WMI()
    storage = _storage_disks(wmi_module)
    disks = []
    for drive in client.Win32_DiskDrive():
        index = getattr(drive, "Index", -1)
        interface = getattr(drive, "InterfaceType", "") or ""
        media = getattr(drive, "MediaType", "") or ""
        pnp = (getattr(drive, "PNPDeviceID", "") or "").upper()
        bus, system = storage.get(int(index), (None, False))
        if bus is None and (interface.upper() == "USB"
                            or pnp.startswith("USBSTOR\\")):
            bus = "USB"
        letters, volumes = [], []
        try:
            for partition in drive.associators("Win32_DiskDriveToDiskPartition"):
                for logical in partition.associators(
                        "Win32_LogicalDiskToPartition"):
                    letter = (getattr(logical, "DeviceID", None) or "").rstrip("\\")
                    if letter:
                        letters.append(letter)
                        volumes.append((letter,
                                        getattr(logical, "VolumeName", "") or "",
                                        getattr(logical, "Size", 0) or 0,
                                        getattr(logical, "FreeSpace", 0) or 0))
        except Exception:
            pass
        windows = [letter for letter in letters if has_windows(letter)]
        disks.append(Disk(index, getattr(drive, "Model", ""),
                          getattr(drive, "Size", 0) or 0,
                          "removable" in media.lower(), interface, letters,
                          bus=bus, system=system, windows=windows,
                          volumes=volumes))
    return [d for d in disks
            if d.usb and (d.removable or include_hard_drives)]


def check_target(disk: Disk, needed_bytes: int = 0,
                 allow_hard_drives: bool = False) -> None:
    """Raise unless *disk* is safe to erase. Every rule, in one place."""
    if disk is None:
        raise UnsafeTarget("No drive is selected.",
                           "Choose the USB stick to write to.")
    if not disk.usb:
        raise UnsafeTarget(
            "%s is not connected over USB (%s)." % (disk.model, disk.bus),
            "OpCoreForge only ever writes to USB drives, so internal SATA, "
            "NVMe and RAID disks cannot be erased here by any setting. If this "
            "really is a USB drive, copy the InstallMedia folder onto it "
            "yourself instead - the layout is the same.")
    if disk.hard_drive and not allow_hard_drives:
        raise UnsafeTarget(
            "%s is a USB hard drive, not a stick." % disk.model,
            "Tick \"List USB hard drives\" to use one. Erasing it erases "
            "every partition on it, not just one.")
    if disk.index == 0:
        raise UnsafeTarget(
            "Disk 0 is never written to.",
            "That is where Windows normally lives, whatever it reports about "
            "itself.")
    if disk.system:
        raise UnsafeTarget(
            "Windows starts from %s." % disk.model,
            "It is this PC's boot or system disk, so it is refused regardless "
            "of anything else.")
    protected = protected_letters() & {l[0].upper() for l in disk.letters}
    if protected:
        raise UnsafeTarget(
            "%s holds drive %s." % (disk.model,
                                    ", ".join(sorted(protected)) + ":"),
            "That is this PC's system or program drive, so it is refused "
            "regardless of anything else.")
    if disk.windows:
        raise UnsafeTarget(
            "%s has Windows installed on %s." % (disk.model,
                                                 ", ".join(disk.windows)),
            "A drive with a Windows installation on it is never erased here, "
            "even one that is not the Windows running now.")
    if disk.size < MINIMUM_BYTES:
        raise UnsafeTarget(
            "%s is only %.1f GB." % (disk.model, disk.size_gb),
            "The recovery and OpenCore need about 2 GB. Use a larger stick.")
    if needed_bytes and disk.size < needed_bytes * 1.05:
        raise UnsafeTarget(
            "%s is too small for this media (%.1f GB of %.1f GB)."
            % (disk.model, needed_bytes / 1024 ** 3, disk.size_gb),
            "Use a larger USB stick.")


def diskpart_script(disk: Disk, label: str = VOLUME_LABEL,
                    allow_hard_drives: bool = False) -> str:
    """Exactly what would be handed to diskpart. Runs nothing.

    Kept separate so it can be shown to the user, written to the log and
    asserted on in tests without a disk anywhere near it.

    Windows will not format FAT32 larger than 32 GB, so on a bigger drive the
    partition is capped and the rest of the drive left unallocated.
    """
    check_target(disk, allow_hard_drives=allow_hard_drives)
    create = "create partition primary"
    if disk.size > FAT32_LIMIT:
        create += " size=%d" % PARTITION_MB
    return "\n".join([
        "select disk %d" % disk.index,
        "clean",
        "convert gpt",
        create,
        "format fs=fat32 quick label=%s" % label[:11],
        "assign",
        "exit",
        "",
    ])


def write_media(disk: Disk, media: Path, confirm: str, report=None,
                runner=None, progress=None, allow_hard_drives=False,
                find_letter=None) -> str:
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
    check_target(disk, needed, allow_hard_drives)
    if confirm != disk.label():
        raise UnsafeTarget(
            "The drive that was confirmed is not the drive selected.",
            "Nothing has been changed. Pick the drive again and confirm it.")
    if not (media / "EFI").exists():
        raise UnsafeTarget(
            "There is no install media to write.",
            "Prepare the folder first.")

    script = diskpart_script(disk, allow_hard_drives=allow_hard_drives)
    say("Erasing %s..." % disk.label())
    run = runner or _run_diskpart
    run(script)

    letter = (find_letter or _new_letter)(disk.index)
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


#: How often diskpart is tried when Windows says "Access is denied", and the
#: pause between tries.
DISKPART_ATTEMPTS = 3
DISKPART_RETRY_PAUSE = 3.0


def _run_diskpart(script: str, run=subprocess.run, sleep=time.sleep) -> str:
    """Run *script* through diskpart, retrying a refused ``clean``.

    "Access is denied" from ``clean`` -- with admin rights -- means something
    has the drive open. Just after a card or stick goes in, Explorer's AutoPlay
    or an antivirus scan often still does, and the same script works a few
    seconds later (a real report: the manual retry 4 s on went straight
    through). So that case is retried before it is reported. The script starts
    from ``clean``, so running it again from the top is safe.
    """
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                     encoding="ascii") as handle:
        handle.write(script)
        path = handle.name
    try:
        for attempt in range(1, DISKPART_ATTEMPTS + 1):
            done = run(["diskpart", "/s", path],
                       capture_output=True, text=True, timeout=600)
            if done.returncode == 0:
                return done.stdout
            output = "%s\n\n%s" % ((done.stdout or "").strip(),
                                    (done.stderr or "").strip())
            denied = "access is denied" in output.lower()
            if not denied:
                raise UnsafeTarget("diskpart could not prepare the drive.",
                                   output.strip())
            if attempt < DISKPART_ATTEMPTS:
                sleep(DISKPART_RETRY_PAUSE)
        raise UnsafeTarget(
            "Windows would not let diskpart erase the drive.",
            "diskpart said \"Access is denied\" %d times in a row. Nothing was "
            "written. Something has the drive open: close any File Explorer "
            "window showing it and any program using it, let an antivirus "
            "scan of it finish, or unplug it and plug it back in, then try "
            "again.\n\ndiskpart said:\n%s" % (DISKPART_ATTEMPTS, output.strip()))
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _new_letter(disk_index: int, wmi_module=None, tries: int = 20):
    """The letter Windows gave the volume just made on disk *disk_index*.

    Read from that disk's own partitions, not by searching every drive for
    the OPENCORE label: with a second OpenCore stick plugged in, the label
    search could find that one and copy the media onto the wrong drive.
    """
    if os.name != "nt" and wmi_module is None:
        return None
    if wmi_module is None:
        import wmi as wmi_module
    for _attempt in range(tries):
        try:
            client = wmi_module.WMI()
            for drive in client.Win32_DiskDrive(Index=disk_index):
                for partition in drive.associators(
                        "Win32_DiskDriveToDiskPartition"):
                    for logical in partition.associators(
                            "Win32_LogicalDiskToPartition"):
                        letter = getattr(logical, "DeviceID", None)
                        if letter:
                            return letter.rstrip("\\")
        except Exception:
            pass
        time.sleep(0.5)        # Windows mounts the new volume a moment later
    return None
