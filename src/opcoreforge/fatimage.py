"""
A FAT32 filesystem image, written from scratch.

Needed because the bootable ISO has to carry a FAT image for the firmware to
mount as an EFI System Partition, and Windows ships nothing that makes one.
``mkfs.vfat`` is a Linux tool; the Windows ``format`` command only formats real
volumes, and mounting a file as a volume needs a driver.

So the format is written directly. It is old, small and completely specified,
and only the parts needed to lay out a fixed set of files once are implemented:
no deletion, no fragmentation handling, no resizing. Long filenames *are*
implemented, because macOS recovery looks for ``BaseSystem.dmg``, not
``BASESY~1.DMG``.

Verified against mtools in the test suite -- the image this writes is read back
with an independent implementation rather than with itself.

Reference: Microsoft FAT32 File System Specification, version 1.03.
"""

from __future__ import annotations

import os
import struct
import time
from pathlib import Path

SECTOR = 512
#: FAT32 is only legal above this many clusters; below it the driver is
#: entitled to read the volume as FAT12/16 and find nonsense.
MIN_FAT32_CLUSTERS = 65525
ATTR_DIRECTORY = 0x10
ATTR_ARCHIVE = 0x20
ATTR_LONG_NAME = 0x0F
FREE = 0x00000000
END_OF_CHAIN = 0x0FFFFFFF


def _dos_datetime(when: float):
    parts = time.localtime(when)
    year = max(1980, parts.tm_year)
    date = ((year - 1980) << 9) | (parts.tm_mon << 5) | parts.tm_mday
    stamp = (parts.tm_hour << 11) | (parts.tm_min << 5) | (parts.tm_sec // 2)
    return date, stamp


def _short_name_checksum(short: bytes) -> int:
    total = 0
    for char in short:
        total = (((total & 1) << 7) + (total >> 1) + char) & 0xFF
    return total


class _Entry:
    """One file or directory to be written."""

    def __init__(self, name: str, source: Path = None, children=None):
        self.name = name
        self.source = source
        self.children = children if children is not None else None
        self.size = 0 if source is None else source.stat().st_size
        self.first_cluster = 0

    @property
    def is_dir(self) -> bool:
        return self.children is not None


class FatImage:
    """Builds a FAT32 image containing a directory tree.

    ``add_tree`` collects what should be in it; ``write`` lays the whole thing
    out in one pass, because the sizes are all known by then and nothing ever
    has to be moved afterwards.
    """

    def __init__(self, label: str = "OPENCORE"):
        self.label = label[:11].upper()
        self.root = _Entry("", children=[])
        self._short_names: dict[int, set] = {}

    # -- collecting --------------------------------------------------------

    def add_tree(self, source: Path, into: str = "") -> None:
        """Add a directory's contents, at *into* within the image."""
        source = Path(source)
        parent = self._ensure_dir(into) if into else self.root
        self._collect(source, parent)

    def add_file(self, source: Path, as_path: str) -> None:
        source = Path(source)
        folder, _, name = as_path.replace("\\", "/").rpartition("/")
        parent = self._ensure_dir(folder) if folder else self.root
        parent.children.append(_Entry(name, source))

    def _ensure_dir(self, path: str) -> _Entry:
        node = self.root
        for part in [p for p in path.replace("\\", "/").split("/") if p]:
            existing = next((c for c in node.children
                             if c.is_dir and c.name.lower() == part.lower()),
                            None)
            if existing is None:
                existing = _Entry(part, children=[])
                node.children.append(existing)
            node = existing
        return node

    def _collect(self, source: Path, parent: _Entry) -> None:
        for child in sorted(source.iterdir(), key=lambda p: p.name.lower()):
            if child.is_dir():
                node = _Entry(child.name, children=[])
                parent.children.append(node)
                self._collect(child, node)
            elif child.is_file():
                parent.children.append(_Entry(child.name, child))

    # -- measuring ---------------------------------------------------------

    def content_bytes(self) -> int:
        return self._measure(self.root)

    def _measure(self, node: _Entry) -> int:
        total = 0
        for child in node.children:
            if child.is_dir:
                total += self._measure(child)
            else:
                total += child.size
        return total

    def _entry_count(self, node: _Entry) -> int:
        """Directory entries a folder needs, long names included."""
        count = 2 if node is not self.root else 1   # ".", ".." / volume label
        for child in node.children:
            count += 1 + _long_entries_needed(child.name)
        return count

    # -- writing -----------------------------------------------------------

    def write(self, target: Path, slack: float = 1.08, progress=None) -> Path:
        """Lay the image out at *target*. Returns the path.

        ``progress`` is called as ``progress(bytes_done, bytes_total)`` while
        file contents are copied. A 3 GB recovery takes minutes, and a window
        that says nothing for minutes is indistinguishable from a hung one.
        """
        target = Path(target)
        payload = self.content_bytes()
        cluster_size, floor_clusters = self._pick_cluster_size(payload, slack)

        # Two passes: allocate every cluster so directory entries can point at
        # them, then write. Nothing moves between the two.
        allocation: list = []
        self._allocate(self.root, cluster_size, allocation)

        used_clusters = sum(count for _node, _first, count in allocation)
        data_clusters = max(used_clusters, floor_clusters)
        sectors_per_cluster = cluster_size // SECTOR
        fat_entries = data_clusters + 2
        fat_sectors = (fat_entries * 4 + SECTOR - 1) // SECTOR
        reserved = 32
        total_sectors = (reserved + fat_sectors * 2
                         + data_clusters * sectors_per_cluster)

        fat = bytearray(fat_sectors * SECTOR)
        struct.pack_into("<III", fat, 0, 0x0FFFFFF8, 0x0FFFFFFF, END_OF_CHAIN)

        with open(target, "wb") as image:
            image.truncate(total_sectors * SECTOR)
            self._write_boot_sectors(image, sectors_per_cluster, reserved,
                                     fat_sectors, total_sectors)
            data_start = (reserved + fat_sectors * 2) * SECTOR
            for node, first, count in allocation:
                for index in range(count):
                    cluster = first + index
                    nxt = (END_OF_CHAIN if index == count - 1
                           else first + index + 1)
                    struct.pack_into("<I", fat, cluster * 4, nxt)
            done = [0]
            if progress:
                progress(0, payload)
            for node, first, count in allocation:
                offset = data_start + (first - 2) * cluster_size
                image.seek(offset)
                if node.is_dir:
                    image.write(self._directory_bytes(node, count * cluster_size))
                else:
                    self._copy_file(image, node.source, count * cluster_size,
                                    progress, done, payload)
            if progress:
                progress(payload, payload)
            for copy in range(2):
                image.seek((reserved + copy * fat_sectors) * SECTOR)
                image.write(fat)
        return target

    #: above this, the FAT itself and the layout pass get needlessly big
    MAX_CLUSTERS = 1_000_000

    def _pick_cluster_size(self, payload: int, slack: float):
        """Cluster size and cluster count for a payload of *payload* bytes.

        Two forces pull against each other. The volume must hold at least
        65525 clusters to be FAT32 at all, so a small payload still produces a
        volume of ``65541 x cluster_size`` -- which is why the smallest cluster
        is tried first: 512-byte clusters put the floor at 34 MB rather than
        the 2 GB that 32 KB clusters would demand. Past a point, though, tiny
        clusters mean millions of them and a FAT to match, so the size steps up
        until the count is manageable.
        """
        wanted = int(payload * slack) + 8 * 1024 * 1024
        for size in (512, 1024, 2048, 4096, 8192, 16384, 32768):
            clusters = max(MIN_FAT32_CLUSTERS + 16,
                           (wanted + size - 1) // size)
            if clusters <= self.MAX_CLUSTERS:
                return size, clusters
        size = 32768
        return size, max(MIN_FAT32_CLUSTERS + 16,
                         (wanted + size - 1) // size)

    def _allocate(self, node: _Entry, cluster_size: int, out: list) -> int:
        """Depth-first allocation. Returns the node's first cluster."""
        next_free = 2 + sum(count for _n, _f, count in out)
        if node.is_dir:
            entries = self._entry_count(node)
            needed = max(1, (entries * 32 + cluster_size - 1) // cluster_size)
        else:
            needed = max(1, (node.size + cluster_size - 1) // cluster_size)
        node.first_cluster = next_free
        out.append((node, next_free, needed))
        if node.is_dir:
            for child in node.children:
                # ".." must point at the parent, and the root is written as 0
                # rather than its real cluster -- the specification says so.
                child.parent_cluster = (0 if node is self.root
                                        else node.first_cluster)
                self._allocate(child, cluster_size, out)
        return next_free

    def _write_boot_sectors(self, image, sectors_per_cluster, reserved,
                            fat_sectors, total_sectors) -> None:
        boot = bytearray(SECTOR)
        boot[0:3] = b"\xEB\x58\x90"
        boot[3:11] = b"MSDOS5.0"
        struct.pack_into("<HBHBHHBHHHII", boot, 11,
                         SECTOR,               # bytes per sector
                         sectors_per_cluster,
                         reserved,
                         2,                    # number of FATs
                         0,                    # root entries (0 on FAT32)
                         0,                    # small total sectors
                         0xF8,                 # media descriptor
                         0,                    # sectors per FAT (0 on FAT32)
                         63, 255,              # geometry, cosmetic
                         0,                    # hidden sectors
                         total_sectors)
        struct.pack_into("<IHHIHH", boot, 36,
                         fat_sectors, 0, 0, self.root.first_cluster, 1, 6)
        boot[64] = 0x80
        boot[66] = 0x29
        struct.pack_into("<I", boot, 67, 0x1234ABCD)
        boot[71:82] = self.label.ljust(11).encode("ascii", "replace")
        boot[82:90] = b"FAT32   "
        boot[510:512] = b"\x55\xAA"
        image.seek(0)
        image.write(boot)
        image.seek(6 * SECTOR)          # backup boot sector
        image.write(boot)

        info = bytearray(SECTOR)
        struct.pack_into("<I", info, 0, 0x41615252)
        struct.pack_into("<I", info, 484, 0x61417272)
        struct.pack_into("<II", info, 488, 0xFFFFFFFF, 0xFFFFFFFF)
        info[510:512] = b"\x55\xAA"
        image.seek(1 * SECTOR)
        image.write(info)
        image.seek(7 * SECTOR)
        image.write(info)

    def _copy_file(self, image, source: Path, capacity: int,
                   progress=None, done=None, total=0) -> None:
        written = 0
        with open(source, "rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                image.write(chunk)
                written += len(chunk)
                if progress and done is not None:
                    done[0] += len(chunk)
                    progress(done[0], total)
        if written < capacity:
            image.write(b"\0" * min(capacity - written, SECTOR))

    # -- directory entries -------------------------------------------------

    def _directory_bytes(self, node: _Entry, capacity: int) -> bytes:
        out = bytearray()
        if node is self.root:
            out += _dir_entry(self.label.ljust(11).encode("ascii", "replace"),
                              0x08, 0, 0)
        else:
            out += _dir_entry(b".          ", ATTR_DIRECTORY,
                              node.first_cluster, 0)
            parent_cluster = getattr(node, "parent_cluster", 0)
            out += _dir_entry(b"..         ", ATTR_DIRECTORY,
                              parent_cluster, 0)
        used = set()
        for child in node.children:
            short = _short_name(child.name, used)
            out += _long_name_entries(child.name, short)
            out += _dir_entry(short,
                              ATTR_DIRECTORY if child.is_dir else ATTR_ARCHIVE,
                              child.first_cluster,
                              0 if child.is_dir else child.size)
        return bytes(out).ljust(capacity, b"\0")


def _dir_entry(short: bytes, attributes: int, cluster: int, size: int) -> bytes:
    date, stamp = _dos_datetime(time.time())
    return struct.pack(
        "<11sBBBHHHHHHHI", short, attributes, 0, 0, stamp, date, date,
        (cluster >> 16) & 0xFFFF, stamp, date, cluster & 0xFFFF, size)


def _long_entries_needed(name: str) -> int:
    if _fits_short(name):
        return 0
    return (len(name) + 12) // 13


def _fits_short(name: str) -> bool:
    stem, _, suffix = name.rpartition(".")
    if not stem:
        stem, suffix = name, ""
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!#$%&'()-@^_`{}~")
    if name != name.upper():
        return False
    return (len(stem) <= 8 and len(suffix) <= 3
            and set(stem) <= allowed and set(suffix) <= allowed)


def _short_name(name: str, used: set) -> bytes:
    stem, _, suffix = name.rpartition(".")
    if not stem:
        stem, suffix = name, ""
    clean = "".join(c if c.isalnum() or c in "!#$%&'()-@^_`{}~" else "_"
                    for c in stem.upper())[:8] or "_"
    extension = "".join(c if c.isalnum() else "_"
                        for c in suffix.upper())[:3]
    candidate = clean
    if not _fits_short(name) or candidate in used:
        index = 1
        while True:
            tail = "~%d" % index
            candidate = (clean[:8 - len(tail)] + tail)
            if (candidate, extension) not in used:
                break
            index += 1
    used.add((candidate, extension))
    return (candidate.ljust(8) + extension.ljust(3)).encode("ascii", "replace")


def _long_name_entries(name: str, short: bytes) -> bytes:
    if _fits_short(name):
        return b""
    checksum = _short_name_checksum(short)
    encoded = name.encode("utf-16-le")
    chunks = []
    per_entry = 13 * 2
    for start in range(0, len(encoded), per_entry):
        chunks.append(encoded[start:start + per_entry])
    if chunks and len(chunks[-1]) < per_entry:
        chunks[-1] = chunks[-1] + b"\x00\x00"
        chunks[-1] = chunks[-1].ljust(per_entry, b"\xFF")
    elif len(encoded) % per_entry == 0:
        chunks.append(b"\x00\x00".ljust(per_entry, b"\xFF"))

    out = bytearray()
    total = len(chunks)
    # Long-name entries are stored last-first, so a forward read assembles
    # the name in order.
    for position in range(total, 0, -1):
        chunk = chunks[position - 1]
        sequence = position | (0x40 if position == total else 0)
        entry = bytearray(32)
        entry[0] = sequence
        entry[1:11] = chunk[0:10]
        entry[11] = ATTR_LONG_NAME
        entry[12] = 0
        entry[13] = checksum
        entry[14:26] = chunk[10:22]
        entry[26:28] = b"\x00\x00"
        entry[28:32] = chunk[22:26]
        out += entry
    return bytes(out)


def build(sources, target: Path, label: str = "OPENCORE") -> Path:
    """Convenience: build an image from ``{path_in_image: source_dir}``."""
    image = FatImage(label)
    for into, source in sources.items():
        image.add_tree(Path(source), into)
    return image.write(Path(target))
