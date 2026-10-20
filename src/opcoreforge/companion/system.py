"""
The macOS commands the companion reads from, and nothing else.

Each function runs one tool and returns its output as data; ``disks`` and
``installer`` decide what it means. ``run`` is the single place a process is
started, so a test can swap it for canned output.
"""

from __future__ import annotations

import plistlib
import subprocess

from . import disks

DISKUTIL = "/usr/sbin/diskutil"
NVRAM = "/usr/sbin/nvram"
SW_VERS = "/usr/bin/sw_vers"


def run(argv, timeout=60) -> str:
    """stdout of *argv*, or an empty string if it failed or does not exist."""
    try:
        result = subprocess.run(argv, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.decode("utf-8", errors="replace")


def _plist(argv, runner=run) -> dict:
    text = runner(argv)
    if not text:
        return {}
    try:
        return plistlib.loads(text.encode("utf-8"))
    except Exception:
        return {}


def disk_list(runner=run) -> dict:
    return _plist([DISKUTIL, "list", "-plist"], runner)


def disk_info(target: str, runner=run) -> dict:
    return _plist([DISKUTIL, "info", "-plist", target], runner)


def opencore_nvram(name: str, runner=run) -> str:
    return disks.nvram_value(
        runner([NVRAM, "%s:%s" % (disks.OC_VENDOR_GUID, name)]))


def macos_version(runner=run) -> str:
    name = runner([SW_VERS, "-productVersion"]).strip()
    build = runner([SW_VERS, "-buildVersion"]).strip()
    if name and build:
        return "%s (%s)" % (name, build)
    return name


class Snapshot:
    """What the companion knows about this Mac, read in one go."""

    def __init__(self, runner=run):
        listing = disk_list(runner)
        root = disk_info("/", runner)
        self.macos = macos_version(runner)
        self.opencore_version = opencore_nvram("opencore-version", runner)
        self.boot_path = opencore_nvram("boot-path", runner)
        self.booted_uuid = disks.booted_partition_uuid(self.boot_path)
        self.system_disks = disks.system_whole_disks(root, listing)

        partitions = disks.efi_partitions(listing)
        info = {}
        for whole in {p.whole_disk for p in partitions}:
            info[whole] = disk_info(whole, runner)
        self.partitions = disks.annotate(partitions, info, self.system_disks,
                                         self.booted_uuid)
        self.disk_names = {whole: (data.get("MediaName") or whole)
                           for whole, data in info.items()}

    @property
    def targets(self):
        return disks.install_targets(self.partitions)

    @property
    def booted(self):
        for part in self.partitions:
            if part.booted:
                return part
        return None
