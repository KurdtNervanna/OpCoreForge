"""
The bootable ISO, checked by reading the bytes a firmware would read.

An ISO that merely "builds" proves nothing -- the question is whether a UEFI
machine would boot it, and that comes down to a chain of structures no
convenience API exposes:

    sector 17    a Boot Record Volume Descriptor saying EL TORITO
      -> LBA     of the boot catalog
        -> an entry declaring platform 0xEF, meaning UEFI rather than BIOS
          -> LBA of a boot image the firmware mounts as an EFI System
             Partition -- so it has to be a FAT volume with the media in it

So this test walks that chain by hand, then lifts the boot image straight out
of the ISO and hands it to mtools. If the files come back, the thing the
firmware is pointed at really does contain the install media.

Needs pycdlib to build; mtools for the last step.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

STDERR = sys.stderr
RESULTS = []
SECTOR = 2048
MTOOLS = shutil.which("mdir")


def check(label, got, expect):
    ok = expect(got) if callable(expect) else got == expect
    RESULTS.append(("PASS" if ok else "FAIL", label, got))


def mtool(image: Path, *args) -> str:
    env = dict(os.environ, MTOOLS_SKIP_CHECK="1")
    config = image.parent / "mtoolsrc"
    config.write_text('drive z: file="%s"\n' % image)
    env["MTOOLSRC"] = str(config)
    done = subprocess.run(args, capture_output=True, text=True, env=env)
    return done.stdout + done.stderr


def main():
    from opcoreforge import isoimage

    if not isoimage.available():
        STDERR.write("pycdlib is not installed; skipping\n")
        return 0

    scratch = Path(tempfile.mkdtemp(prefix="ocf-iso-"))
    media = scratch / "media"
    (media / "EFI" / "BOOT").mkdir(parents=True)
    (media / "EFI" / "OC").mkdir(parents=True)
    (media / "com.apple.recovery.boot").mkdir(parents=True)
    (media / "EFI" / "BOOT" / "BOOTx64.efi").write_bytes(os.urandom(50000))
    (media / "EFI" / "OC" / "OpenCore.efi").write_bytes(os.urandom(80000))
    (media / "EFI" / "OC" / "config.plist").write_bytes(b"<plist/>\n" * 400)
    (media / "com.apple.recovery.boot" / "BaseSystem.dmg").write_bytes(
        os.urandom(2_000_000))
    (media / "com.apple.recovery.boot" / "BaseSystem.chunklist").write_bytes(
        os.urandom(6000))

    said = []
    iso_path = isoimage.build(media, scratch / "OpCoreForge.iso",
                              report=said.append)

    check("an ISO is produced", iso_path.exists(), True)
    check("progress is reported for the two slow steps",
          len(said), lambda n: n >= 2)
    check("the intermediate boot image is cleaned up",
          list(scratch.glob("*.efiboot.img")), [])

    raw = iso_path.read_bytes()
    check("it starts with an ISO9660 primary descriptor",
          raw[16 * SECTOR + 1:16 * SECTOR + 6], b"CD001")

    # -- the El Torito chain, walked by hand -------------------------------
    boot_record = None
    for index in range(16, 32):
        descriptor = raw[index * SECTOR:(index + 1) * SECTOR]
        if descriptor[0:1] == b"\x00" and descriptor[1:6] == b"CD001":
            boot_record = descriptor
            break
    check("there is a boot record volume descriptor",
          boot_record is not None, True)
    check("naming the El Torito specification",
          boot_record[7:30].rstrip(b"\0") if boot_record else None,
          b"EL TORITO SPECIFICATION")

    catalog_lba = struct.unpack("<I", boot_record[71:75])[0]
    catalog = raw[catalog_lba * SECTOR:catalog_lba * SECTOR + 2048]
    check("the boot catalog is inside the image",
          0 < catalog_lba < len(raw) // SECTOR, True)
    check("its validation entry is signed",
          catalog[30:32], b"\x55\xaa")

    # Platform 0xEF is the whole point: without it a UEFI firmware ignores the
    # entry. It may be declared by the validation entry (in which case the
    # default entry that follows is the UEFI one) or by a later section
    # header. Both are legal El Torito, so accept either.
    efi_image_lba = None
    efi_media_type = None
    for offset in range(0, 2048, 32):
        entry = catalog[offset:offset + 32]
        if len(entry) < 32:
            break
        if entry[0] in (0x01, 0x90, 0x91) and entry[1] == 0xEF:
            following = catalog[offset + 32:offset + 64]
            if following[0:1] == b"\x88":
                efi_media_type = following[1]
                efi_image_lba = struct.unpack("<I", following[8:12])[0]
                break
    check("the catalog declares a UEFI boot entry",
          efi_image_lba is not None, True)
    check("as a no-emulation entry, so it is mounted as an ESP",
          efi_media_type, 0)

    if efi_image_lba:
        boot_image = raw[efi_image_lba * SECTOR:]
        check("and points at a FAT volume",
              boot_image[82:90], b"FAT32   ")
        check("with the expected label", boot_image[71:82], b"OPENCORE   ")
        check("that is signed as a boot sector",
              boot_image[510:512], b"\x55\xaa")

        # -- the decisive check: read the firmware's view with mtools ------
        if MTOOLS:
            extracted = scratch / "esp.img"
            extracted.write_bytes(boot_image)
            listing = mtool(extracted, "mdir", "-/", "-b", "z:/")
            for expected in ("EFI/BOOT/BOOTx64.efi", "EFI/OC/OpenCore.efi",
                             "com.apple.recovery.boot/BaseSystem.dmg"):
                check("the boot image contains %s" % expected,
                      ("z:/" + expected).lower() in listing.lower(), True)
            copied = scratch / "back.efi"
            mtool(extracted, "mcopy", "-n", "z:/EFI/OC/OpenCore.efi",
                  str(copied))
            check("and its contents are byte-identical",
                  copied.exists() and copied.read_bytes()
                  == (media / "EFI" / "OC" / "OpenCore.efi").read_bytes(),
                  True)
        else:
            STDERR.write("mtools not installed; the boot image is not read "
                         "back\n")

    # -- refusing to build something that cannot boot ----------------------
    empty = scratch / "empty"
    (empty / "EFI").mkdir(parents=True)
    try:
        isoimage.build(empty, scratch / "bad.iso")
        RESULTS.append(("FAIL", "media with no bootloader is refused",
                        "no exception"))
    except FileNotFoundError as exc:
        check("media with no bootloader is refused",
              "BOOTx64.efi" in str(exc), True)

    STDERR.write("\n")
    for status, label, value in RESULTS:
        text = str(value).replace("\n", " ")
        if len(text) > 30:
            text = text[:27] + "..."
        STDERR.write("%s  %-54s %s\n" % (status, label, text))
    failed = [r for r in RESULTS if r[0] == "FAIL"]
    STDERR.write("\n%d checks, %d failed\n" % (len(RESULTS), len(failed)))
    shutil.rmtree(scratch, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
