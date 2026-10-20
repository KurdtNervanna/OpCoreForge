"""
Disks as macOS reports them: which EFI partitions exist, which one holds the
disk macOS is running from, and which one OpenCore started from.

Pure parsing. The caller runs ``diskutil ... -plist`` and ``nvram`` (see
``system``) and hands the results in, so every rule here can be checked
against captured output without a Mac.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: GPT type of an EFI system partition, which diskutil sometimes reports raw
EFI_TYPE_GUID = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
#: OpenCore's NVRAM vendor GUID; boot-path and opencore-version live under it
OC_VENDOR_GUID = "4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102"

_EFI_CONTENT = {"EFI", EFI_TYPE_GUID, "0XEF"}
_WHOLE_DISK = re.compile(r"^(disk\d+)")
_GPT_PARTITION = re.compile(
    r"HD\(\s*\d+\s*,\s*GPT\s*,\s*([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})")


@dataclass
class EfiPartition:
    """One EFI system partition, and the disk it is on."""

    device: str            # disk0s1
    whole_disk: str        # disk0
    uuid: str              # the partition's own GUID, upper case
    size: int
    mount_point: str = ""  # empty while it is not mounted
    disk_name: str = ""    # the drive's model, from diskutil info
    internal: bool = False
    holds_system: bool = False
    booted: bool = False

    def label(self) -> str:
        parts = ["%s on %s" % (self.device, self.disk_name or self.whole_disk)]
        parts.append("internal" if self.internal else "external")
        if self.holds_system:
            parts.append("the disk macOS is on")
        if self.booted:
            parts.append("OpenCore started from here")
        return "%s  (%s)" % (parts[0], ", ".join(parts[1:]))


def whole_disk_of(device: str) -> str:
    """``disk0s1`` -> ``disk0``. Anything unrecognisable comes back as is."""
    match = _WHOLE_DISK.match(device or "")
    return match.group(1) if match else (device or "")


def efi_partitions(listing: dict) -> list:
    """Every EFI partition in ``diskutil list -plist`` output."""
    found = []
    for disk in listing.get("AllDisksAndPartitions") or []:
        whole = disk.get("DeviceIdentifier") or ""
        for part in disk.get("Partitions") or []:
            content = str(part.get("Content") or "").upper()
            if content not in _EFI_CONTENT:
                continue
            device = part.get("DeviceIdentifier") or ""
            found.append(EfiPartition(
                device=device,
                whole_disk=whole or whole_disk_of(device),
                uuid=str(part.get("DiskUUID") or "").upper(),
                size=int(part.get("Size") or 0),
                mount_point=part.get("MountPoint") or ""))
    return found


def _store_ids(stores) -> list:
    """Device ids from an APFS physical-store list, in either spelling.

    ``diskutil info`` calls the key ``APFSPhysicalStore``, ``diskutil list``
    calls it ``DeviceIdentifier``; both are accepted rather than trusting one.
    """
    out = []
    for store in stores or []:
        if isinstance(store, dict):
            device = store.get("APFSPhysicalStore") or store.get("DeviceIdentifier")
        else:
            device = str(store)
        if device:
            out.append(device)
    return out


def system_whole_disks(root_info: dict, listing: dict) -> list:
    """The physical disk(s) the running macOS is on.

    ``root_info`` is ``diskutil info -plist /``. On APFS the volume lives in a
    synthesized container disk with no EFI partition of its own; the physical
    store behind it is what has one. On HFS+ the parent disk is already the
    physical one.
    """
    stores = _store_ids(root_info.get("APFSPhysicalStores"))
    if not stores:
        parent = root_info.get("ParentWholeDisk") or whole_disk_of(
            root_info.get("DeviceIdentifier") or "")
        for disk in listing.get("AllDisksAndPartitions") or []:
            if disk.get("DeviceIdentifier") == parent:
                stores = _store_ids(disk.get("APFSPhysicalStores"))
                break
        if not stores and parent:
            stores = [parent]
    disks = []
    for store in stores:
        whole = whole_disk_of(store)
        if whole and whole not in disks:
            disks.append(whole)
    return disks


def booted_partition_uuid(boot_path: str) -> str:
    """The GPT partition GUID in OpenCore's ``boot-path`` NVRAM value.

    The value is a UEFI device path ending in the file OpenCore was started
    as, e.g. ``.../HD(1,GPT,6A4C...,0x28,0x64000)/\\EFI\\OC\\OpenCore.efi``.
    Empty when OpenCore did not set it -- which also means this Mac was not
    started by OpenCore, or the NVRAM variable is not exposed.
    """
    match = _GPT_PARTITION.search(boot_path or "")
    return match.group(1).upper() if match else ""


def nvram_value(output: str) -> str:
    """The value from ``nvram <guid>:<name>`` output, NULs and all removed."""
    text = (output or "").strip()
    if "\t" in text:
        text = text.split("\t", 1)[1]
    return text.replace("%00", "").strip()


def annotate(partitions, disk_info: dict, system_disks, booted_uuid: str):
    """Fill in model, internal and role, from per-disk ``diskutil info``.

    ``disk_info`` maps a whole disk id to its ``diskutil info -plist`` dict.
    A disk missing from it is treated as external, which only ever makes it
    less likely to be offered.
    """
    for part in partitions:
        info = disk_info.get(part.whole_disk) or {}
        part.disk_name = (info.get("MediaName") or info.get("IORegistryEntryName")
                          or "").strip()
        part.internal = bool(info.get("Internal")) and not bool(
            info.get("RemovableMedia") or info.get("Removable"))
        part.holds_system = part.whole_disk in system_disks
        part.booted = bool(booted_uuid) and part.uuid == booted_uuid
    return partitions


def install_targets(partitions) -> list:
    """EFI partitions OpenCore may be installed to, best first.

    Internal disks only: the point is to stop needing a USB stick, so offering
    one as the destination would be a trap. The disk macOS is on comes first.
    """
    targets = [p for p in partitions if p.internal]
    targets.sort(key=lambda p: (not p.holds_system, p.device))
    return targets
