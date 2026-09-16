"""
A UEFI-bootable ISO wrapping the install media.

Only useful in a virtual machine -- VMware, VirtualBox, QEMU all boot an ISO
directly, and that is a far quicker way to see whether a config.plist works
than writing a USB stick and rebooting. Real hardware wants the USB.

How a UEFI machine boots an ISO: the ISO9660 filesystem carries an El Torito
boot catalog, and for UEFI the catalog's entry points at a *FAT image* embedded
in the ISO. Firmware mounts that image as though it were an EFI System
Partition. So the whole install media goes into the FAT image (see
``fatimage``), and the ISO is a thin wrapper around it -- which also means the
files are visible to anything that mounts the ISO normally.
"""

from __future__ import annotations

from pathlib import Path

from . import fatimage


def available() -> bool:
    """Whether the ISO writer can run in this build."""
    try:
        import pycdlib  # noqa: F401
        return True
    except Exception:
        return False


#: what a FAT image of a payload costs, as ``fatimage`` sizes it
SLACK = 1.08
HEADROOM = 8 * 1024 * 1024


def space_needed(media_dir: Path) -> tuple:
    """(payload, boot image, peak disk) in bytes for an ISO of *media_dir*.

    The peak matters because the FAT image and the ISO exist at the same
    moment -- the ISO is written *from* the image -- so a 3 GB recovery wants
    something like 7 GB free, and finding that out after twenty minutes of
    copying is the worst possible time.
    """
    payload = 0
    for child in Path(media_dir).rglob("*"):
        if child.is_file():
            payload += child.stat().st_size
    image = int(payload * SLACK) + HEADROOM
    return payload, image, image * 2 + 4 * 1024 * 1024


class NotEnoughSpace(Exception):
    """The drive cannot hold what the ISO build is about to write."""

    title = "Not enough disk space"

    def __init__(self, message, hint=""):
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self):
        return "%s\n\n%s" % (self.message, self.hint) if self.hint else self.message


def check_space(folder: Path, needed: int) -> None:
    """Refuse before the copying starts rather than halfway through it."""
    import shutil
    try:
        free = shutil.disk_usage(str(folder)).free
    except Exception:
        return          # unknown is not the same as too small
    if free >= needed:
        return
    raise NotEnoughSpace(
        "Building the ISO needs about %.1f GB free on that drive and there "
        "is %.1f GB." % (needed / 1024 ** 3, free / 1024 ** 3),
        "The boot image and the ISO exist at the same time, which is why it "
        "wants roughly twice the size of the media. Choose somewhere with "
        "more room, or write a USB stick instead.")


class _CountingWriter:
    """Passes writes through, reporting how far along they are.

    pycdlib writes the ISO in one call with nothing to hook into, so the
    progress has to come from the file object it writes to.
    """

    def __init__(self, handle, total, progress, throttle=0.15):
        self._handle = handle
        self._total = max(1, total)
        self._progress = progress
        self._throttle = throttle
        self._done = 0
        self._last = 0.0

    def write(self, data):
        written = self._handle.write(data)
        self._done += len(data)
        if self._progress:
            import time
            now = time.monotonic()
            if now - self._last >= self._throttle:
                self._last = now
                self._progress(min(self._done, self._total), self._total)
        return written

    def __getattr__(self, name):
        return getattr(self._handle, name)


def build(media_dir: Path, target: Path, label: str = "OPENCORE",
          report=None, keep_image: bool = False, progress=None) -> Path:
    """Write *media_dir* as a bootable ISO at *target*.

    ``report`` is called with a short status line before each long step and
    ``progress`` with ``(done, total)`` bytes during it. Both steps copy the
    whole payload, neither prints anything of its own, and on a 3 GB recovery
    they take minutes -- so without this the window simply sits there.
    """
    def say(message):
        if report:
            report(message)

    media_dir = Path(media_dir)
    target = Path(target)
    if not (media_dir / "EFI" / "BOOT" / "BOOTx64.efi").exists():
        raise FileNotFoundError(
            "%s has no EFI/BOOT/BOOTx64.efi, so nothing would boot from it"
            % media_dir)

    try:
        import pycdlib
    except Exception as exc:
        raise RuntimeError(
            "This build cannot write ISOs: the pycdlib library is missing. "
            "Rebuild with BUILD_EXE.bat, which installs it, or use the USB "
            "or folder output instead.") from exc

    target.parent.mkdir(parents=True, exist_ok=True)
    boot_image = target.with_suffix(".efiboot.img")

    payload, image_size, peak = space_needed(media_dir)
    check_space(target.parent, peak)

    say("Building the EFI boot image (%.1f GB)..." % (payload / 1024 ** 3))
    image = fatimage.FatImage(label)
    image.add_tree(media_dir)
    image.write(boot_image, progress=progress)

    say("Writing the ISO (%.1f GB)..." % (boot_image.stat().st_size / 1024 ** 3))
    iso = pycdlib.PyCdlib()
    # Joliet and Rock Ridge so the long names survive for anything that mounts
    # the ISO to look inside; the boot path does not depend on them.
    iso.new(interchange_level=3, joliet=3, rock_ridge="1.09",
            vol_ident=label[:32])
    try:
        with open(boot_image, "rb") as handle:
            size = boot_image.stat().st_size
            iso.add_fp(handle, size, "/EFIBOOT.IMG;1",
                       rr_name="efiboot.img", joliet_path="/efiboot.img")
            # platform_id 0xEF is what marks the entry as UEFI rather than
            # BIOS -- without it the catalog says x86 and no UEFI firmware
            # looks at the image. "noemul" means the firmware mounts it as an
            # EFI System Partition instead of pretending it is a floppy.
            iso.add_eltorito("/EFIBOOT.IMG;1", efi=True, platform_id=0xEF,
                             boot_load_size=0, media_name="noemul")
            with open(target, "wb") as out:
                iso.write_fp(_CountingWriter(out, size, progress))
            if progress:
                progress(size, size)
    finally:
        iso.close()
        if not keep_image:
            try:
                boot_image.unlink()
            except Exception:
                pass
    return target
