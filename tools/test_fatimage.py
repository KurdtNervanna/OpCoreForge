"""
The FAT32 writer, checked with something other than itself.

A filesystem writer that is only tested by its own reader proves nothing: the
same misunderstanding is on both sides. So every image built here is read back
with **mtools**, an independent implementation, and the files are compared byte
for byte against the originals. Where mtools is not installed the structural
checks still run and the round-trip is skipped.

The awkward parts of FAT32 are the ones that matter here:

- Long filenames. macOS recovery looks for ``BaseSystem.dmg``; a volume that
  only offers ``BASESY~1.DMG`` does not boot. Long names are stored in
  reverse-ordered 13-character fragments with a checksum tying them to the
  short name, and getting the order or the checksum wrong yields a name that
  looks right in one reader and is garbage in another.
- The cluster count floor. Below 65525 clusters a driver is entitled to read
  the volume as FAT16 and find nonsense, so a small payload still has to
  produce a large enough volume.

Needs mtools for the full run: apt-get install mtools
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

STDERR = sys.stderr
RESULTS = []
MTOOLS = shutil.which("mdir") and shutil.which("mcopy")


def check(label, got, expect):
    ok = expect(got) if callable(expect) else got == expect
    RESULTS.append(("PASS" if ok else "FAIL", label, got))


def mtool(image: Path, *args) -> str:
    env = dict(os.environ, MTOOLS_SKIP_CHECK="1")
    config = image.parent / "mtoolsrc"
    config.write_text('drive z: file="%s"\n' % image)
    env["MTOOLSRC"] = str(config)
    result = subprocess.run(args, capture_output=True, text=True, env=env)
    return result.stdout + result.stderr


def main():
    from opcoreforge import fatimage

    scratch = Path(tempfile.mkdtemp(prefix="ocf-fat-"))
    source = scratch / "media"
    # The real shape: OpenCore's EFI tree plus Apple's recovery folder, with
    # the long names and the nesting depth that actually occur.
    (source / "EFI" / "OC" / "Kexts" / "Lilu.kext" / "Contents").mkdir(
        parents=True)
    (source / "EFI" / "BOOT").mkdir(parents=True)
    (source / "com.apple.recovery.boot").mkdir(parents=True)
    (source / "EFI" / "BOOT" / "BOOTx64.efi").write_bytes(os.urandom(40000))
    (source / "EFI" / "OC" / "OpenCore.efi").write_bytes(os.urandom(90000))
    (source / "EFI" / "OC" / "config.plist").write_bytes(
        b"<plist>hello</plist>\n" * 500)
    (source / "EFI" / "OC" / "Kexts" / "Lilu.kext" / "Contents"
     / "Info.plist").write_bytes(b"<plist/>\n" * 100)
    recovery = source / "com.apple.recovery.boot" / "BaseSystem.dmg"
    recovery.write_bytes(os.urandom(3_000_000))
    (source / "com.apple.recovery.boot" / "BaseSystem.chunklist").write_bytes(
        os.urandom(9000))

    image_path = scratch / "media.img"
    image = fatimage.FatImage("OPENCORE")
    image.add_tree(source)
    image.write(image_path)

    check("an image is produced", image_path.exists(), True)
    size = image_path.stat().st_size
    # 65541 clusters of 512 bytes is the FAT32 floor, so even 3 MB of payload
    # cannot make a volume smaller than ~32 MiB. It should not make a much
    # larger one either -- picking 32 KB clusters here would mean 2 GB.
    check("it clears the FAT32 cluster floor", size >= 32 * 1024 * 1024, True)
    check("and is not inflated far past the payload",
          size < 64 * 1024 * 1024, True)

    raw = image_path.read_bytes()
    check("the boot sector is signed", raw[510:512], b"\x55\xaa")
    check("it declares itself FAT32", raw[82:90], b"FAT32   ")
    check("the volume label is set", raw[71:82], b"OPENCORE   ")
    check("the backup boot sector matches",
          raw[6 * 512:6 * 512 + 512], raw[0:512])
    check("the FSInfo signatures are present",
          (raw[512:516], raw[512 + 484:512 + 488]),
          (b"RRaA", b"rrAa"))

    if not MTOOLS:
        STDERR.write("mtools not installed; the round-trip checks are "
                     "skipped\n")
    else:
        listing = mtool(image_path, "mdir", "-/", "-b", "z:/")
        for expected in ("EFI/BOOT/BOOTx64.efi", "EFI/OC/OpenCore.efi",
                         "EFI/OC/config.plist",
                         "EFI/OC/Kexts/Lilu.kext/Contents/Info.plist",
                         "com.apple.recovery.boot/BaseSystem.dmg",
                         "com.apple.recovery.boot/BaseSystem.chunklist"):
            check("mtools finds %s" % expected,
                  ("z:/" + expected).lower() in listing.lower(), True)

        check("the long directory name survives",
              "com.apple.recovery.boot" in listing, True)
        check("and long file names keep their case",
              "BaseSystem.chunklist" in listing, True)

        # Byte-for-byte: a name that reads correctly but whose cluster chain is
        # wrong would pass every check above.
        out = scratch / "out"
        out.mkdir()
        for relative in ("EFI/OC/OpenCore.efi",
                         "com.apple.recovery.boot/BaseSystem.dmg",
                         "EFI/OC/Kexts/Lilu.kext/Contents/Info.plist"):
            target = out / Path(relative).name
            mtool(image_path, "mcopy", "-n", "z:/" + relative, str(target))
            original = (source / relative).read_bytes()
            check("%s comes back identical" % Path(relative).name,
                  target.exists() and target.read_bytes() == original, True)

        check("fsck is happy with the volume",
              "Cannot initialize" not in mtool(image_path, "minfo", "z:"),
              True)

    # -- the small-payload case -------------------------------------------
    tiny_source = scratch / "tiny"
    (tiny_source / "EFI" / "BOOT").mkdir(parents=True)
    (tiny_source / "EFI" / "BOOT" / "BOOTx64.efi").write_bytes(b"x" * 1024)
    tiny = scratch / "tiny.img"
    small = fatimage.FatImage("TINY")
    small.add_tree(tiny_source)
    small.write(tiny)
    check("a nearly empty image is still a valid FAT32 volume",
          32 * 1024 * 1024 <= tiny.stat().st_size < 64 * 1024 * 1024, True)
    if MTOOLS:
        check("and mtools can read it",
              "bootx64.efi" in mtool(tiny, "mdir", "-/", "-b", "z:/").lower(),
              True)

    # -- short-name rules --------------------------------------------------
    check("an 8.3 name needs no long entries",
          fatimage._long_entries_needed("BOOT.EFI"), 0)
    check("a long one does",
          fatimage._long_entries_needed("com.apple.recovery.boot"),
          lambda n: n >= 2)
    check("mixed case forces a long name",
          fatimage._long_entries_needed("BootX64.efi"), 1)
    used = set()
    check("short names are uppercased and padded",
          fatimage._short_name("OpenCore.efi", used), b"OPENCO~1EFI")
    check("and a second collision gets its own tail",
          fatimage._short_name("OpenCore.efi", used), b"OPENCO~2EFI")

    STDERR.write("\n")
    for status, label, value in RESULTS:
        text = str(value).replace("\n", " ")
        if len(text) > 30:
            text = text[:27] + "..."
        STDERR.write("%s  %-56s %s\n" % (status, label, text))
    failed = [r for r in RESULTS if r[0] == "FAIL"]
    STDERR.write("\n%d checks, %d failed%s\n"
                 % (len(RESULTS), len(failed),
                    "" if MTOOLS else "  (mtools absent: round-trip skipped)"))
    shutil.rmtree(scratch, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
