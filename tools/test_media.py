"""
Install media: the recovery pipeline, and every way the USB writer says no.

Two halves.

**The media.** What can be built from Windows is a *recovery* installer, not a
full offline one -- ``createinstallmedia`` is a macOS binary. The recovery is
fetched by OpenCore's own ``macrecovery.py``, and the board id that decides
which macOS Apple serves is read from what OpenCorePkg ships rather than from a
table written from memory. These checks are mostly about that refusal to guess:
a bad board id is rejected before any download starts, and an unparseable board
list yields nothing rather than something plausible.

**The USB writer.** This is the only part of OpCoreForge that can destroy data,
so the checks below are largely a list of things it must refuse: fixed disks,
disk 0, anything holding the system drive, anything too small, and a
confirmation that does not match the drive selected. ``diskpart`` is never run
-- the script it would be given is a pure function, which is the point of
building it that way.

No display, no network, no disks.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("TERM_PROGRAM", "")

STDERR = sys.stderr
RESULTS = []


def check(label, got, expect):
    ok = expect(got) if callable(expect) else got == expect
    RESULTS.append(("PASS" if ok else "FAIL", label, got))


class FakePaths:
    def __init__(self, root: Path):
        self.data = root
        self.bin = root / "bin"
        self.results = root / "Results"
        self.bin.mkdir(parents=True, exist_ok=True)

    @property
    def efi_dir(self):
        return self.results / "EFI"


def main():
    from opcoreforge import media, usbwriter

    scratch = Path(tempfile.mkdtemp(prefix="ocf-media-"))
    paths = FakePaths(scratch)

    # -- board ids are read, never invented ---------------------------------
    check("with nothing extracted there are no board options",
          media.board_options(paths), [])

    folder = media.macrecovery_dir(paths)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "boards.json").write_text(json.dumps({
        "Mac-E43C1C25D4880AD6": {"os": "macOS Big Sur"},
        "Mac-7BA5B2D9E42DDD94": {"os": "macOS High Sierra"},
    }))
    options = media.board_options(paths)
    check("a board list keyed by board id is read",
          sorted(b for b, _ in options),
          ["Mac-7BA5B2D9E42DDD94", "Mac-E43C1C25D4880AD6"])
    check("and keeps the macOS each one names",
          dict(options)["Mac-E43C1C25D4880AD6"], "macOS Big Sur")

    (folder / "boards.json").write_text(json.dumps(
        {"macOS Ventura": "Mac-4B682C642B45593E"}))
    check("a list keyed the other way round is read too",
          media.board_options(paths), [("Mac-4B682C642B45593E",
                                        "macOS Ventura")])

    (folder / "boards.json").write_text("{not json at all")
    (folder / "README.md").write_text(
        "| Mac-7BA5B2DFE22DDD8C | macOS Catalina |\n"
        "| Mac-E43C1C25D4880AD6 | macOS Big Sur  |\n")
    fallback = media.board_options(paths)
    check("an unreadable json falls back to the shipped documentation",
          [b for b, _ in fallback],
          ["Mac-7BA5B2DFE22DDD8C", "Mac-E43C1C25D4880AD6"])
    check("and the macOS names come with it",
          dict(fallback)["Mac-7BA5B2DFE22DDD8C"], lambda v: "Catalina" in v)

    (folder / "README.md").write_text("nothing useful here\n")
    (folder / "boards.json").unlink()
    check("when nothing can be parsed, nothing is offered",
          media.board_options(paths), [])

    # Matching the stage-2 choice to a board, without pretending to be sure.
    options = [("Mac-A", "macOS Big Sur"), ("Mac-B", "macOS Sequoia 15"),
               ("Mac-C", "macOS Ventura")]
    check("the chosen macOS finds its board",
          media.match_board(options, "macOS Sequoia 15"), "Mac-B")
    check("a release with no board yields nothing rather than a guess",
          media.match_board(options, "macOS Tahoe 26"), None)
    check("and no choice at all yields nothing",
          media.match_board(options, ""), None)

    # -- the shipped board table -------------------------------------------
    # Transcribed from Dortania's SMBIOS page and joined with
    # OpCore-Simplify's own support ranges. The point of the join is that the
    # board follows the *macOS wanted*, not the SMBIOS the EFI presents: those
    # differ constantly, and a board that stops earlier quietly returns an
    # older release instead of failing.
    import sys as _sys
    _sys.path.insert(0, str(ROOT / "src" / "vendor" / "ocs"))
    from opcoreforge import boards as board_table

    check("every board id is well formed",
          all(media.BOARD_PATTERN.fullmatch(b)
              for b in board_table.BOARD_IDS.values()), True)
    check("the table covers the models OpCore-Simplify knows about",
          board_table.board_for("MacBookPro10,1"), "Mac-C3EC7CD22292981F")
    check("including the newest ones",
          board_table.board_for("iMac20,1"), "Mac-CFF7D910A743CAAF")
    check("and says nothing about a model it does not list",
          board_table.board_for("MacBookPro99,9"), None)

    sequoia = board_table.options_for("24.99.99")
    catalina = board_table.options_for("19.99.99")
    check("boards are offered for Sequoia", len(sequoia), lambda n: n > 5)
    check("more are offered for Catalina", len(catalina) > len(sequoia), True)
    check("an Ivy Bridge Mac is offered for Catalina",
          any(m == "MacBookPro10,1" for _b, m, _l in catalina), True)
    check("and is not offered for Sequoia, which its board cannot fetch",
          any(m == "MacBookPro10,1" for _b, m, _l in sequoia), False)
    check("each option names the newest macOS it reaches",
          all("up to" in label for _b, _m, label in sequoia), True)
    check("an unknown macOS yields nothing rather than a guess",
          board_table.options_for("99.0.0"), [])
    check("and so does no macOS at all", board_table.options_for(""), [])

    check("the SMBIOS is preferred when its board reaches the release",
          board_table.preferred(catalina, "MacBookPro10,1"),
          "Mac-C3EC7CD22292981F")
    check("and something that can is used when it cannot",
          board_table.preferred(sequoia, "MacBookPro10,1"),
          lambda b: b in [board for board, _m, _l in sequoia])
    check("with no SMBIOS at all it still offers one",
          board_table.preferred(sequoia, ""), lambda b: bool(b))

    check("the stage asks for boards by macOS version",
          [b for b, _l in media.board_options(paths, "24.99.99")],
          [b for b, _m, _l in sequoia])

    # -- a bad board id never reaches Apple ---------------------------------
    for bad in ("", "Mac", "iMac19,1", "Mac-ZZZZ", "12345678"):
        try:
            media.download_recovery(paths, bad)
            RESULTS.append(("FAIL", "%r is refused as a board id" % bad,
                            "no exception"))
        except media.MediaError as refused:
            check("%r is refused as a board id" % bad,
                  "board id" in refused.message or "board id" in refused.hint,
                  True)

    try:
        media.download_recovery(paths, "Mac-E43C1C25D4880AD6")
        RESULTS.append(("FAIL", "a missing macrecovery.py is reported",
                        "no exception"))
    except media.MediaError as refused:
        check("a missing macrecovery.py is reported",
              "macrecovery" in refused.message, True)

    # -- staging the folder --------------------------------------------------
    try:
        media.stage_media(paths)
        RESULTS.append(("FAIL", "staging without an EFI is refused",
                        "no exception"))
    except media.MediaError as refused:
        check("staging without an EFI is refused",
              "EFI folder" in refused.message, True)

    (paths.efi_dir / "OC").mkdir(parents=True)
    (paths.efi_dir / "BOOT").mkdir(parents=True)
    import plistlib as _plist
    with (paths.efi_dir / "OC" / "config.plist").open("wb") as handle:
        # As OpenCore ships it: auxiliary entries hidden, which is right for
        # an installed system and wrong for an installer.
        _plist.dump({"Misc": {"Boot": {"HideAuxiliary": True, "Timeout": 5,
                                       "PollAppleHotKeys": False},
                              "Security": {"ScanPolicy": 17760515}}}, handle)
    (paths.efi_dir / "BOOT" / "BOOTx64.efi").write_bytes(b"MZ" + b"\0" * 900)
    try:
        media.stage_media(paths)
        RESULTS.append(("FAIL", "staging without a recovery is refused",
                        "no exception"))
    except media.MediaError as refused:
        check("staging without a recovery is refused",
              "recovery" in refused.message.lower(), True)

    recovery = media.recovery_dir(paths)
    recovery.mkdir(parents=True, exist_ok=True)
    (recovery / "BaseSystem.dmg").write_bytes(b"D" * 5000)
    (recovery / "BaseSystem.chunklist").write_bytes(b"C" * 100)

    said = []
    staged = media.stage_media(paths, report=said.append)
    check("the media folder is built", staged.exists(), True)
    check("with the EFI in it",
          (staged / "EFI" / "OC" / "config.plist").exists(), True)
    check("and the recovery where a Mac looks for it",
          (staged / media.RECOVERY_DIR / "BaseSystem.dmg").exists(), True)
    check("the chunklist comes too",
          (staged / media.RECOVERY_DIR / "BaseSystem.chunklist").exists(),
          True)
    check("progress is reported", len(said), lambda n: n >= 2)

    # -- the media's config is tuned for installing -------------------------
    # macOS Recovery is an auxiliary entry and OpenCore's sample config hides
    # auxiliary entries, so the picker comes up with nothing on it. Reported
    # exactly that way: OpenCore booted, no macOS option.
    import plistlib
    media_config = staged / "EFI" / "OC" / "config.plist"
    with media_config.open("rb") as handle:
        tuned = plistlib.load(handle)
    check("the media shows auxiliary entries, so recovery is visible",
          tuned["Misc"]["Boot"]["HideAuxiliary"], False)
    check("with time to choose", tuned["Misc"]["Boot"]["Timeout"], 15)
    check("and everything is scanned",
          tuned["Misc"]["Security"]["ScanPolicy"], 0)
    check("the EFI for the installed system keeps its own settings",
          plistlib.loads((paths.efi_dir / "OC" / "config.plist").read_bytes())
          ["Misc"]["Boot"]["HideAuxiliary"], True)

    # A config already set up this way is left alone rather than rewritten.
    check("nothing is changed twice",
          media.tune_config_for_install(media_config), [])
    check("a missing config is not an error",
          media.tune_config_for_install(staged / "nope.plist"), [])

    summary = media.media_summary(paths)
    check("the summary sees the EFI", summary["efi"], True)
    check("the summary sees the recovery", summary["recovery"], True)
    check("the summary sees the staged folder", summary["staged"], True)
    check("and knows how big the recovery is",
          summary["recovery_size"], 5000)

    # Re-staging replaces rather than merges, so a stale file cannot survive.
    (staged / "EFI" / "leftover.txt").write_text("old")
    media.stage_media(paths)
    check("re-staging clears what was there before",
          (staged / "EFI" / "leftover.txt").exists(), False)

    # -- a config that outran its folder never reaches a USB stick ----------
    # The field failure: UTBDefault.kext was deleted when the port map was
    # installed but its Kernel -> Add entry stayed, and OpenCore halted on the
    # kext it could not find -- after the picker, which made it look like a
    # macOS problem. Staging is the last moment to catch that.
    with (paths.efi_dir / "OC" / "config.plist").open("rb") as handle:
        source = plistlib.load(handle)
    source["Kernel"] = {"Add": [{"Arch": "Any", "BundlePath": "UTBDefault.kext",
                                 "Comment": "", "Enabled": True,
                                 "ExecutablePath": "", "MaxKernel": "",
                                 "MinKernel": "",
                                 "PlistPath": "Contents/Info.plist"}]}
    with (paths.efi_dir / "OC" / "config.plist").open("wb") as handle:
        plistlib.dump(source, handle, sort_keys=False)
    said = []
    staged = media.stage_media(paths, report=said.append)
    check("staging repairs a config that names a missing kext",
          plistlib.loads((staged / "EFI" / "OC" / "config.plist").read_bytes())
          ["Kernel"]["Add"], [])
    check("and says so rather than fixing it silently",
          [line for line in said if "UTBDefault.kext" in line],
          lambda v: len(v) == 1)
    check("the EFI that goes on the internal disk is fixed too",
          plistlib.loads((paths.efi_dir / "OC" / "config.plist").read_bytes())
          ["Kernel"]["Add"], [])
    said = []
    media.stage_media(paths, report=said.append)
    check("and a second staging has nothing left to fix",
          [line for line in said if line.startswith("Fixed:")], [])

    # -- the OpenCorePkg lookup ---------------------------------------------
    # This is what failed in the field: the release page is scraped, so its
    # entries carry product_name and url, not the name a REST API would give.
    class FakeFetcher:
        def __init__(self, index=None):
            self.index = index

        def fetch_and_parse_content(self, url, kind=None):
            return self.index

    class FakeGithub:
        def __init__(self, assets):
            self.assets = assets

        def get_latest_release(self, owner, repo):
            return {"assets": self.assets}

    scraped = [{"product_name": "OpenCorePkg", "id": 1,
                "url": "https://github.com/acidanthera/OpenCorePkg/releases/"
                       "download/1.0.4/OpenCore-1.0.4-RELEASE.zip"}]
    check("the release zip is found in a scraped asset list",
          media._opencorepkg_url(FakeGithub(scraped), FakeFetcher()),
          lambda u: u.endswith("OpenCore-1.0.4-RELEASE.zip"))
    check("the build index is preferred when it answers",
          media._opencorepkg_url(FakeGithub(scraped), FakeFetcher(
              {"OpenCorePkg": {"versions": [
                  {"links": {"release": "https://example/oc.zip"}}]}})),
          "https://example/oc.zip")
    check("a DEBUG build is not mistaken for the release",
          media._opencorepkg_url(FakeGithub([
              {"product_name": "OpenCorePkg", "url":
               "https://x/OpenCore-1.0.4-DEBUG.zip"}]), FakeFetcher()),
          None)
    check("and nothing at all is reported as nothing",
          media._opencorepkg_url(FakeGithub([]), FakeFetcher()), None)

    # -- the USB writer's refusals ------------------------------------------
    good = usbwriter.Disk(2, "SanDisk Ultra USB", 32 * 1024 ** 3, True, "USB",
                          ["E:"])
    check("a real USB stick is accepted",
          usbwriter.check_target(good), None)

    refusals = {
        "a fixed disk": usbwriter.Disk(3, "Samsung SSD", 500 * 1024 ** 3,
                                       False, "SCSI", ["D:"]),
        "disk 0": usbwriter.Disk(0, "Removable somehow", 64 * 1024 ** 3,
                                 True, "USB", []),
        "a stick that is too small":
            usbwriter.Disk(4, "Tiny", 512 * 1024 ** 2, True, "USB", []),
    }
    for label, disk in refusals.items():
        try:
            usbwriter.check_target(disk)
            RESULTS.append(("FAIL", "%s is refused" % label, "no exception"))
        except usbwriter.UnsafeTarget as refused:
            check("%s is refused" % label, bool(refused.hint), True)

    # The system drive is refused even when everything else looks fine.
    system = os.environ.get("SystemDrive")
    os.environ["SystemDrive"] = "E:"
    try:
        usbwriter.check_target(good)
        RESULTS.append(("FAIL", "a disk holding the system drive is refused",
                        "no exception"))
    except usbwriter.UnsafeTarget as refused:
        check("a disk holding the system drive is refused",
              "system" in refused.hint.lower(), True)
    finally:
        if system is None:
            os.environ.pop("SystemDrive", None)
        else:
            os.environ["SystemDrive"] = system

    try:
        usbwriter.check_target(good, needed_bytes=64 * 1024 ** 3)
        RESULTS.append(("FAIL", "media too big for the stick is refused",
                        "no exception"))
    except usbwriter.UnsafeTarget as refused:
        check("media too big for the stick is refused",
              "larger USB" in refused.hint, True)

    try:
        usbwriter.check_target(None)
        RESULTS.append(("FAIL", "no drive selected is refused", "no exception"))
    except usbwriter.UnsafeTarget:
        check("no drive selected is refused", True, True)

    # -- what diskpart would be told, without running it --------------------
    script = usbwriter.diskpart_script(good)
    check("the script selects the chosen disk by index",
          "select disk 2" in script, True)
    check("it names no other disk",
          [line for line in script.splitlines()
           if line.startswith("select disk")], ["select disk 2"])
    for required in ("clean", "convert gpt", "create partition primary",
                     "format fs=fat32 quick", "assign"):
        check("it does %r" % required, required in script, True)
    check("and labels the volume OPENCORE",
          "label=OPENCORE" in script, True)
    for disk in refusals.values():
        try:
            usbwriter.diskpart_script(disk)
            RESULTS.append(("FAIL", "no script is produced for a refused disk",
                            "no exception"))
            break
        except usbwriter.UnsafeTarget:
            pass
    else:
        check("no script is produced for a refused disk", True, True)

    # -- confirmation has to name the drive ---------------------------------
    ran = []
    try:
        usbwriter.write_media(good, staged, confirm="Disk 2 - something else",
                              runner=ran.append)
        RESULTS.append(("FAIL", "a mismatched confirmation writes nothing",
                        "no exception"))
    except usbwriter.UnsafeTarget as refused:
        check("a mismatched confirmation writes nothing",
              "Nothing has been changed" in refused.hint, True)
    check("and diskpart was never invoked", ran, [])

    check("the label a user confirms names the disk and its size",
          good.label(), "Disk 2 - SanDisk Ultra USB - 32.0 GB (E:)")

    # -- enumeration ---------------------------------------------------------
    class FakeDrive:
        def __init__(self, index, model, size, media_type, interface):
            self.Index, self.Model, self.Size = index, model, size
            self.MediaType, self.InterfaceType = media_type, interface

        def associators(self, _name):
            return []

    class FakeWmi:
        def WMI(self):
            return self

        def Win32_DiskDrive(self):
            return [
                FakeDrive(0, "Samsung SSD 970", 500 * 1024 ** 3,
                          "Fixed hard disk media", "SCSI"),
                FakeDrive(1, "SanDisk Cruzer", 16 * 1024 ** 3,
                          "Removable Media", "USB"),
                FakeDrive(2, "WD Elements", 2 * 1024 ** 4,
                          "External hard disk media", "USB"),
            ]

    found = usbwriter.list_disks(wmi_module=FakeWmi())
    check("fixed internal disks are not even offered",
          [d.index for d in found], [1, 2])
    check("a USB stick is described for a human",
          found[0].label(), lambda v: "SanDisk Cruzer" in v and "16.0 GB" in v)

    STDERR.write("\n")
    for status, label, value in RESULTS:
        text = str(value).replace("\n", " ")
        if len(text) > 32:
            text = text[:29] + "..."
        STDERR.write("%s  %-56s %s\n" % (status, label, text))
    failed = [r for r in RESULTS if r[0] == "FAIL"]
    STDERR.write("\n%d checks, %d failed\n" % (len(RESULTS), len(failed)))
    shutil.rmtree(scratch, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
