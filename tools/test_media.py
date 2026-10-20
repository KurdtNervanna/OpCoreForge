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
    from opcoreforge import media, media_companion, usbwriter

    # Preparing the media also fetches the companion app from GitHub. That is
    # tools/test_companion.py's subject; here it is simply not available.
    media_companion.find_dmg = lambda *args, **kwargs: None

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

    # "Get macOS and prepare" skips the download it has already done -- but
    # only for the same board, since another board means another macOS.
    check("a recovery with no record of its board is not trusted",
          media.downloaded_board(paths), "")
    media.board_marker(paths).write_text("Mac-AA95B1DDAB278B95")
    check("the board a recovery was downloaded for is remembered",
          media.downloaded_board(paths), "Mac-AA95B1DDAB278B95")
    base = media.recovery_dir(paths) / "BaseSystem.dmg"
    kept = base.read_bytes()
    base.unlink()
    check("and forgotten once the recovery itself is gone",
          media.downloaded_board(paths), "")
    base.write_bytes(kept)

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

    # Not on USB means never, whatever the caller allows -- including the
    # "List USB hard drives" box, which only ever adds USB disks.
    never = {
        "an NVMe disk": usbwriter.Disk(3, "Samsung 990 PRO", 1024 ** 4, False,
                                       "SCSI", ["D:"], bus="NVMe"),
        "a SATA disk": usbwriter.Disk(4, "Crucial MX500", 1024 ** 4, False,
                                      "IDE", ["E:"], bus="SATA"),
        "a card in a PCIe reader (eMMC shares its bus)":
            usbwriter.Disk(5, "SD Card", 64 * 1024 ** 3, True, "SCSI", ["F:"],
                           bus="SD"),
        "a USB disk Windows boots from":
            usbwriter.Disk(6, "Windows To Go", 128 * 1024 ** 3, False, "SCSI",
                           ["G:"], bus="USB", system=True),
        "a USB disk with Windows installed on it":
            usbwriter.Disk(7, "Old laptop drive", 500 * 1024 ** 3, False,
                           "USB", ["H:", "I:"], windows=("I:",)),
    }
    for label, disk in never.items():
        try:
            usbwriter.check_target(disk, allow_hard_drives=True)
            RESULTS.append(("FAIL", "%s is refused, even with hard drives "
                            "allowed" % label, "no exception"))
        except usbwriter.UnsafeTarget as refused:
            check("%s is refused, even with hard drives allowed" % label,
                  bool(refused.hint), True)

    usb_hdd = usbwriter.Disk(8, "Samsung T7", 1024 ** 4, False, "SCSI",
                             ["J:"], bus="USB")
    try:
        usbwriter.check_target(usb_hdd)
        got = None
    except usbwriter.UnsafeTarget as refused:
        got = refused.hint
    check("a USB hard drive is refused unless they are listed",
          got, lambda v: bool(v) and "List USB hard drives" in v)
    check("and accepted once they are",
          usbwriter.check_target(usb_hdd, allow_hard_drives=True), None)
    check("it is described as a hard drive", usb_hdd.kind, "USB hard drive")

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
    check("a 32 GB stick gets one partition across the whole stick",
          "create partition primary\n" in script, True)
    big_stick = usbwriter.Disk(9, "SanDisk 128GB", 128 * 1024 ** 3, True,
                               "USB", [])
    check("anything bigger gets a 16 GB one, since Windows will not format "
          "FAT32 past 32 GB",
          "create partition primary size=16384"
          in usbwriter.diskpart_script(big_stick), True)
    check("a USB hard drive only gets a script when they are allowed",
          "select disk 8" in usbwriter.diskpart_script(
              usb_hdd, allow_hard_drives=True), True)
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

    # -- diskpart refused by a drive that is briefly in use ------------------
    class Done:
        def __init__(self, code, out):
            self.returncode, self.stdout, self.stderr = code, out, ""

    denied = Done(2, "Disk 3 is now the selected disk.\n\n"
                     "DiskPart has encountered an error: Access is denied.")

    def scripted(*outcomes):
        calls = []

        def run(args, **_kw):
            calls.append(args)
            return outcomes[min(len(calls), len(outcomes)) - 1]
        return run, calls

    pauses = []
    run, calls = scripted(denied, Done(0, "DiskPart successfully cleaned"))
    check("access denied is retried, and the retry can succeed",
          usbwriter._run_diskpart("select disk 3", run=run,
                                  sleep=pauses.append),
          "DiskPart successfully cleaned")
    check("with a pause before the retry", len(pauses), 1)

    run, calls = scripted(denied)
    try:
        usbwriter._run_diskpart("select disk 3", run=run, sleep=lambda _s: None)
        RESULTS.append(("FAIL", "access denied every time is reported",
                        "no exception"))
    except usbwriter.UnsafeTarget as refused:
        check("access denied every time is reported in plain words",
              "has the drive open" in refused.hint
              and "Nothing was written" in refused.hint, True)
        check("and still shows what diskpart said",
              "Access is denied" in refused.hint, True)
    check("it gives up after a fixed number of tries", len(calls),
          usbwriter.DISKPART_ATTEMPTS)

    run, calls = scripted(Done(2, "Virtual Disk Service error: "
                                  "The media is write protected."))
    try:
        usbwriter._run_diskpart("select disk 3", run=run, sleep=lambda _s: None)
    except usbwriter.UnsafeTarget as refused:
        check("other diskpart errors are not retried", len(calls), 1)
        check("and pass diskpart's words through",
              "write protected" in refused.hint, True)

    check("the label a user confirms names the disk and its size",
          good.label(), "Disk 2 - SanDisk Ultra USB - 32.0 GB (E:)")

    # -- enumeration ---------------------------------------------------------
    class Node:
        def __init__(self, children=(), **values):
            self.__dict__.update(values)
            self.children = list(children)

        def associators(self, _name):
            return self.children

    def volume(letter, label, size, free):
        return Node(DeviceID=letter, VolumeName=label, Size=size,
                    FreeSpace=free)

    def drive(index, model, size, media, interface, pnp="", volumes=()):
        return Node([Node(list(volumes))], Index=index, Model=model,
                    Size=size, MediaType=media, InterfaceType=interface,
                    PNPDeviceID=pnp)

    GB = 1024 ** 3
    drives = [
        drive(0, "Samsung SSD 970", 500 * GB, "Fixed hard disk media",
              "SCSI", volumes=[volume("C:", "Windows", 500 * GB, 100 * GB)]),
        drive(1, "SanDisk Cruzer", 16 * GB, "Removable Media", "USB",
              "USBSTOR\\DISK&VEN_SANDISK"),
        drive(2, "WD Elements", 2048 * GB, "External hard disk media", "USB",
              "USBSTOR\\DISK&VEN_WD",
              [volume("E:", "Backups", 2048 * GB, 1024 * GB)]),
        # UAS: Win32_DiskDrive says SCSI, the storage API says USB.
        drive(3, "Samsung T7", 1024 * GB, "Fixed hard disk media", "SCSI",
              "SCSI\\DISK&VEN_SAMSUNG"),
        drive(4, "Crucial MX500", 1024 * GB, "Fixed hard disk media", "SCSI"),
        drive(5, "SD Card", 64 * GB, "Removable Media", "SCSI"),
        drive(6, "Old laptop drive", 500 * GB, "External hard disk media",
              "USB", "USBSTOR\\DISK&VEN_WDC",
              [volume("F:", "", 500 * GB, 300 * GB)]),
    ]
    buses = {0: (17, True), 1: (7, False), 2: (7, False), 3: (7, False),
             4: (11, False), 5: (12, False), 6: (7, False)}

    class FakeWmi:
        def __init__(self, storage=True):
            self.storage = storage

        def WMI(self, namespace=None):
            if namespace and not self.storage:
                raise RuntimeError("no Storage API on this Windows")
            return self

        def Win32_DiskDrive(self, Index=None):
            return [d for d in drives if Index is None or d.Index == Index]

        def MSFT_Disk(self):
            return [Node(Number=n, BusType=b, IsBoot=s, IsSystem=False)
                    for n, (b, s) in buses.items()]

    def windows_on(letter):
        return letter == "F:"

    found = usbwriter.list_disks(wmi_module=FakeWmi(), has_windows=windows_on)
    check("by default only USB sticks are offered",
          [d.index for d in found], [1])
    check("a USB stick is described for a human",
          found[0].label(), lambda v: "SanDisk Cruzer" in v and "16.0 GB" in v)
    found = usbwriter.list_disks(wmi_module=FakeWmi(), include_hard_drives=True,
                                 has_windows=windows_on)
    check("listing USB hard drives adds them, UAS ones included - and nothing "
          "internal, ever", [d.index for d in found], [1, 2, 3, 6])
    by_index = {d.index: d for d in found}
    check("a hard drive's volumes are named for the confirmation",
          by_index[2].contents(), ["E:  Backups  -  2048.0 GB, 1024.0 GB used"])
    check("Windows on a USB drive is noticed", by_index[6].windows, ("F:",))
    try:
        usbwriter.check_target(by_index[6], allow_hard_drives=True)
        got = None
    except usbwriter.UnsafeTarget as refused:
        got = refused.message
    check("and that drive is refused", got,
          lambda v: bool(v) and "Windows installed" in v)
    found = usbwriter.list_disks(wmi_module=FakeWmi(storage=False),
                                 include_hard_drives=True,
                                 has_windows=windows_on)
    check("without the storage API the interface decides, which only ever "
          "offers less", [d.index for d in found], [1, 2, 6])

    # -- after formatting, the letter comes from the disk that was formatted --
    check("the new volume is found on the disk just formatted",
          usbwriter._new_letter(2, wmi_module=FakeWmi(), tries=1), "E:")
    check("not on some other drive",
          usbwriter._new_letter(1, wmi_module=FakeWmi(), tries=1), None)

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
