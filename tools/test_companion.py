"""
OpCoreForge Companion: finding the disks, installing OpenCore, and getting the
app onto the install media. Plus the credits.

**Disks.** Everything is parsed from captured ``diskutil -plist`` and ``nvram``
output, so the rules -- which EFI partition holds macOS, which one OpenCore
started from, and that only internal disks are offered as a destination -- are
checked without a Mac.

**Installing.** The script that runs as root is built by a pure function, and
these checks *run* it: against folders standing in for the EFI partition, with
small stand-ins for ``diskutil`` and ``ditto``. That proves the order that
matters -- back up, then replace -- and the promises the app makes on screen:
``EFI/Microsoft`` survives, a failed copy puts the old OpenCore back, and a
partition without room is refused before anything is touched.

**The media.** Stage 10 adds the companion disk image and an untouched copy of
the EFI; the copy the stick boots with is the only one given install settings.

No display, no network, no disks. Runs on Linux, macOS and Windows with a POSIX
shell; without ``sh`` the script checks are skipped and say so.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("TERM_PROGRAM", "")

RESULTS = []


def check(label, got, expect):
    ok = expect(got) if callable(expect) else got == expect
    RESULTS.append(("PASS" if ok else "FAIL", label, got))


# -- captured diskutil output ------------------------------------------------

EFI_UUID = "6A4C2B1E-9D3F-4E21-8C55-0A1B2C3D4E5F"
USB_EFI_UUID = "11111111-2222-3333-4444-555555555555"

LISTING = {
    "AllDisksAndPartitions": [
        {"Content": "GUID_partition_scheme", "DeviceIdentifier": "disk0",
         "Size": 500107862016, "Partitions": [
             {"Content": "EFI", "DeviceIdentifier": "disk0s1",
              "DiskUUID": EFI_UUID.lower(), "Size": 209715200,
              "VolumeName": "EFI"},
             {"Content": "Apple_APFS", "DeviceIdentifier": "disk0s2",
              "DiskUUID": "AAAAAAAA-0000-0000-0000-000000000002",
              "Size": 499898105856}]},
        {"Content": "GUID_partition_scheme", "DeviceIdentifier": "disk1",
         "Size": 256060514304, "Partitions": [
             {"Content": "C12A7328-F81F-11D2-BA4B-00A0C93EC93B",
              "DeviceIdentifier": "disk1s1", "DiskUUID":
              "BBBBBBBB-0000-0000-0000-000000000001", "Size": 104857600,
              "MountPoint": "/Volumes/EFI 1"},
             {"Content": "Microsoft Basic Data", "DeviceIdentifier": "disk1s3",
              "Size": 255000000000}]},
        {"APFSPhysicalStores": [{"DeviceIdentifier": "disk0s2"}],
         "APFSVolumes": [{"DeviceIdentifier": "disk3s1", "MountPoint": "/"}],
         "Content": "", "DeviceIdentifier": "disk3"},
        {"Content": "GUID_partition_scheme", "DeviceIdentifier": "disk4",
         "Size": 16008609792, "Partitions": [
             {"Content": "EFI", "DeviceIdentifier": "disk4s1",
              "DiskUUID": USB_EFI_UUID, "Size": 209715200},
             {"Content": "Microsoft Basic Data", "DeviceIdentifier": "disk4s2",
              "MountPoint": "/Volumes/OPENCORE", "Size": 15000000000}]},
    ],
}

INFO = {
    "/": {"DeviceIdentifier": "disk3s1s1", "ParentWholeDisk": "disk3",
          "APFSPhysicalStores": [{"APFSPhysicalStore": "disk0s2"}]},
    "disk0": {"MediaName": "Samsung SSD 970 EVO Plus 500GB", "Internal": True,
              "RemovableMedia": False},
    "disk1": {"MediaName": "WDC WDS256G", "Internal": True,
              "RemovableMedia": False},
    "disk4": {"MediaName": "SanDisk Cruzer", "Internal": False,
              "RemovableMedia": True},
}

BOOT_PATH = ("4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102:boot-path\t"
             "PciRoot(0x0)/Pci(0x14,0x0)/USB(0x2,0x0)/HD(1,GPT,%s,0x28,"
             "0x64000)/\\EFI\\OC\\OpenCore.efi%%00" % USB_EFI_UUID)


def fake_runner(argv):
    """Answers the commands system.py runs, from the captures above."""
    tool = Path(argv[0]).name
    if tool == "diskutil" and argv[1] == "list":
        return plistlib.dumps(LISTING).decode()
    if tool == "diskutil" and argv[1] == "info":
        return plistlib.dumps(INFO.get(argv[-1], {})).decode()
    if tool == "nvram" and argv[1].endswith("boot-path"):
        return BOOT_PATH
    if tool == "nvram" and argv[1].endswith("opencore-version"):
        return "4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102:opencore-version\tREL-110-2026-10-06"
    if tool == "sw_vers":
        return "15.7\n" if argv[1] == "-productVersion" else "24G222\n"
    return ""


# -- an EFI folder -----------------------------------------------------------

def make_efi(root: Path, launcher="Disabled", kexts=("Lilu.kext",),
             listed=None) -> Path:
    efi = root / "EFI"
    (efi / "BOOT").mkdir(parents=True)
    (efi / "BOOT" / "BOOTx64.efi").write_bytes(b"opencore boot")
    oc = efi / "OC"
    for folder in ("Drivers", "Kexts", "ACPI", "Tools", "Resources"):
        (oc / folder).mkdir(parents=True)
    (oc / "OpenCore.efi").write_bytes(b"opencore")
    for name in kexts:
        (oc / "Kexts" / name / "Contents").mkdir(parents=True)
        (oc / "Kexts" / name / "Contents" / "Info.plist").write_bytes(
            plistlib.dumps({"CFBundleIdentifier": "x." + name}))
    config = {
        "Kernel": {"Add": [{"BundlePath": name, "Enabled": True,
                            "PlistPath": "Contents/Info.plist",
                            "ExecutablePath": ""}
                           for name in (listed if listed is not None else kexts)]},
        "Misc": {"Boot": {"LauncherOption": launcher,
                          "LauncherPath": "Default"}},
        "UEFI": {"Drivers": []},
    }
    with (oc / "config.plist").open("wb") as handle:
        plistlib.dump(config, handle)
    return efi


def load(path: Path) -> dict:
    with Path(path).open("rb") as handle:
        return plistlib.load(handle)


SHIM_DITTO = r"""#!/bin/sh
# ditto, as far as install.sh uses it. OCF_FAIL_ON makes copies *from the
# staging folder* into a destination ending in that suffix fail, so a copy can
# be made to break half way through.
while [ $# -gt 0 ]; do case "$1" in --*) shift;; *) break;; esac; done
src=$1; dst=$2
if [ -n "${OCF_FAIL_ON:-}" ]; then
  case "$src" in *staging*) case "$dst" in *"$OCF_FAIL_ON") exit 1;; esac;; esac
fi
mkdir -p "$(dirname "$dst")"
cp -R "$src" "$dst"
"""

SHIM_DISKUTIL = r"""#!/bin/sh
# diskutil mount/unmount, against a folder standing in for the partition.
echo "$*" >> "$OCF_DISKUTIL_LOG"
if [ "$1" = mount ]; then
  cp -R "$OCF_PARTITION/." "$3"
elif [ "$1" = unmount ]; then
  mp=$(cat "$OCF_MOUNTED_AT")
  rm -rf "$OCF_PARTITION" && mkdir -p "$OCF_PARTITION"
  cp -R "$mp/." "$OCF_PARTITION"
  rm -rf "$mp"/*
fi
"""


def shims(folder: Path) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    tools = {}
    for name, text in (("ditto", SHIM_DITTO), ("diskutil", SHIM_DISKUTIL)):
        path = folder / name
        path.write_text(text)
        path.chmod(0o755)
        tools[name.upper()] = str(path)
    return tools


def script_of(argv) -> str:
    """The script path inside an osascript command line."""
    return re.search(r"/bin/sh '?([^' \\\"]*install\.sh)", argv[2]).group(1)


def run_script(text: str, env=None):
    folder = Path(tempfile.mkdtemp(prefix="ocf-script-"))
    script = folder / "install.sh"
    script.write_text(text)
    done = subprocess.run(["sh", str(script)], capture_output=True,
                          env=dict(os.environ, **(env or {})))
    shutil.rmtree(folder, ignore_errors=True)
    return (done.returncode, done.stdout.decode(errors="replace"),
            done.stderr.decode(errors="replace"))


def partition(root: Path) -> Path:
    """A Windows-and-OpenCore EFI partition."""
    esp = root
    (esp / "EFI" / "Microsoft" / "Boot").mkdir(parents=True)
    (esp / "EFI" / "Microsoft" / "Boot" / "bootmgfw.efi").write_bytes(b"windows")
    (esp / "EFI" / "OC").mkdir(parents=True)
    (esp / "EFI" / "OC" / "OpenCore.efi").write_bytes(b"old opencore")
    (esp / "EFI" / "OC" / "config.plist").write_bytes(b"old config")
    (esp / "EFI" / "BOOT").mkdir(parents=True)
    (esp / "EFI" / "BOOT" / "BOOTx64.efi").write_bytes(b"old boot")
    return esp


# ---------------------------------------------------------------------------

def test_disks():
    from opcoreforge.companion import disks, system

    parts = disks.efi_partitions(LISTING)
    check("every EFI partition is found, by name or by GPT type",
          [p.device for p in parts], ["disk0s1", "disk1s1", "disk4s1"])
    check("partition GUIDs are normalised to upper case",
          parts[0].uuid, EFI_UUID)
    check("a mounted EFI partition keeps its mount point",
          parts[1].mount_point, "/Volumes/EFI 1")
    check("a partition's whole disk is its parent",
          disks.whole_disk_of("disk12s3"), "disk12")

    check("APFS: the physical store behind / is the system disk",
          disks.system_whole_disks(INFO["/"], LISTING), ["disk0"])
    no_store = {"ParentWholeDisk": "disk3", "DeviceIdentifier": "disk3s1s1"}
    check("APFS without stores in diskutil info falls back to diskutil list",
          disks.system_whole_disks(no_store, LISTING), ["disk0"])
    hfs = {"ParentWholeDisk": "disk1", "DeviceIdentifier": "disk1s2"}
    check("HFS+: the parent disk is the physical disk",
          disks.system_whole_disks(hfs, LISTING), ["disk1"])

    check("OpenCore's boot-path names the partition it started from",
          disks.booted_partition_uuid(disks.nvram_value(BOOT_PATH)),
          USB_EFI_UUID)
    check("no boot-path means no booted partition",
          disks.booted_partition_uuid(""), "")
    check("nvram's key and trailing NULs are stripped",
          disks.nvram_value("x:opencore-version\tREL-110%00"), "REL-110")

    snap = system.Snapshot(runner=fake_runner)
    check("snapshot: macOS version and build", snap.macos, "15.7 (24G222)")
    check("snapshot: running OpenCore version", snap.opencore_version,
          "REL-110-2026-10-06")
    check("snapshot: the USB stick is where OpenCore started",
          snap.booted and snap.booted.device, "disk4s1")
    check("snapshot: the stick is not internal",
          snap.booted and snap.booted.internal, False)
    check("targets: internal disks only, the macOS disk first",
          [t.device for t in snap.targets], ["disk0s1", "disk1s1"])
    check("targets: the first one is marked as holding macOS",
          snap.targets[0].holds_system, True)
    check("the label says what the partition is",
          snap.targets[0].label(),
          lambda s: "Samsung" in s and "internal" in s and "macOS is on" in s)
    check("an unknown disk counts as external, never as a target",
          disks.install_targets(disks.annotate(
              disks.efi_partitions(LISTING), {}, ["disk0"], "")), [])


def test_sources(scratch: Path):
    from opcoreforge.companion import installer

    volumes = scratch / "Volumes"
    stick = volumes / "OPENCORE"
    make_efi(stick)
    make_efi(stick / "OpCoreForge")
    (stick / "OpCoreForge" / "media.json").write_text(json.dumps(
        {"opcoreforge": "2.0.0", "created": "2026-10-20T10:00:00"}))
    make_efi(volumes / "Old stick")
    (volumes / "Empty").mkdir()
    os.symlink("/", volumes / "Macintosh HD")

    found = installer.find_sources(volumes)
    check("sources: the as-built EFI comes first",
          [(s.kind, s.path.parent.name) for s in found],
          [("built", "OpCoreForge"), ("media", "OPENCORE"),
           ("media", "Old stick")])
    check("sources: the as-built label says which OpCoreForge made it",
          found[0].label, lambda s: "2.0.0" in s and "2026-10-20" in s)
    check("sources: a folder holding EFI is taken as that EFI",
          installer.chosen_source(stick).path, stick / "EFI")

    check("a complete EFI has no problems",
          installer.check_source(stick / "EFI"), [])
    broken = make_efi(scratch / "broken", kexts=(), listed=("Lilu.kext",))
    check("an EFI whose config names a missing kext is refused",
          installer.check_source(broken), lambda p: p and "Lilu.kext" in p[0])
    (scratch / "noboot" / "EFI" / "OC").mkdir(parents=True)
    check("an EFI without BOOTx64.efi is refused",
          installer.check_source(scratch / "noboot" / "EFI"),
          lambda p: any("BOOTx64.efi" in x for x in p))


def test_prepare(scratch: Path):
    from opcoreforge.companion import installer

    source = make_efi(scratch / "src")
    (source / "OC" / "._config.plist").write_bytes(b"apple double")
    (source / "OC" / ".DS_Store").write_bytes(b"finder")
    staging = scratch / "staging"
    notes = installer.prepare(source, staging)
    staged = load(staging / "EFI" / "OC" / "config.plist")
    check("prepare: LauncherOption Disabled becomes Full",
          staged["Misc"]["Boot"]["LauncherOption"], "Full")
    check("prepare: the change is reported", notes,
          lambda n: len(n) == 1 and "Full" in n[0])
    check("prepare: the source config is not touched",
          load(source / "OC" / "config.plist")["Misc"]["Boot"]["LauncherOption"],
          "Disabled")
    check("prepare: macOS litter is not copied to the partition",
          sorted(p.name for p in (staging / "EFI" / "OC").iterdir()
                 if p.name.startswith(".")), [])

    custom = make_efi(scratch / "custom", launcher="Short")
    installer.prepare(custom, scratch / "staging2")
    check("prepare: a LauncherOption set on purpose is kept",
          load(scratch / "staging2" / "EFI" / "OC" / "config.plist")
          ["Misc"]["Boot"]["LauncherOption"], "Short")
    installer.prepare(source, scratch / "staging3", launcher_full=False)
    check("prepare: with the box unticked nothing is changed",
          load(scratch / "staging3" / "EFI" / "OC" / "config.plist")
          ["Misc"]["Boot"]["LauncherOption"], "Disabled")

    broken = make_efi(scratch / "broken2", kexts=(), listed=("Lilu.kext",))
    try:
        installer.prepare(broken, scratch / "staging4")
        refused = False
    except installer.InstallError:
        refused = True
    check("prepare: an EFI OpenCore would stop on is refused", refused, True)


def test_script(scratch: Path):
    from opcoreforge.companion import installer

    if shutil.which("sh") is None:
        RESULTS.append(("SKIP", "install script checks need a POSIX sh", ""))
        return
    tools = shims(scratch / "bin")
    staging = scratch / "the staging dir"
    installer.prepare(make_efi(scratch / "new"), staging)

    # -- an already mounted partition, installed over ------------------------
    esp = partition(scratch / "esp one")
    backup = scratch / "backups" / "first"
    text = installer.install_script(staging, "disk0s1", backup,
                                    owner="%d:%d" % (os.getuid(), os.getgid()),
                                    needed_kb=installer.folder_kb(staging),
                                    mount_point=str(esp), tools=tools)
    code, out, err = run_script(text)
    check("install: the script succeeds", (code, err.strip()), (0, ""))
    check("install: OpenCore is replaced",
          (esp / "EFI" / "OC" / "OpenCore.efi").read_bytes(), b"opencore")
    check("install: BOOTx64.efi is replaced",
          (esp / "EFI" / "BOOT" / "BOOTx64.efi").read_bytes(), b"opencore boot")
    check("install: the installed config is the prepared one",
          load(esp / "EFI" / "OC" / "config.plist")["Misc"]["Boot"]
          ["LauncherOption"], "Full")
    check("install: Windows' EFI/Microsoft is left alone",
          (esp / "EFI" / "Microsoft" / "Boot" / "bootmgfw.efi").read_bytes(),
          b"windows")
    check("install: the old OpenCore is in the backup",
          (backup / "EFI" / "OC" / "OpenCore.efi").read_bytes(),
          b"old opencore")
    check("install: the whole EFI folder is backed up, Windows included",
          (backup / "EFI" / "Microsoft" / "Boot" / "bootmgfw.efi").exists(),
          True)
    check("install: progress lines are marked for the app",
          [l for l in out.splitlines() if l.startswith("OCF: ")][-1],
          "OCF: Installed")
    check("install: old files from the previous OC do not linger",
          sorted(p.name for p in (esp / "EFI" / "OC").iterdir()),
          sorted(p.name for p in (staging / "EFI" / "OC").iterdir()))

    # -- a copy that fails half way ------------------------------------------
    esp2 = partition(scratch / "esp two")
    text = installer.install_script(staging, "disk0s1",
                                    scratch / "backups" / "second",
                                    owner="%d:%d" % (os.getuid(), os.getgid()),
                                    needed_kb=1, mount_point=str(esp2),
                                    tools=tools)
    code, out, err = run_script(text, {"OCF_FAIL_ON": "/EFI/BOOT"})
    check("failure: the script fails", code, 1)
    check("failure: the reason is marked for the app", err,
          lambda e: "OCF-ERROR: Copying EFI/BOOT failed" in e)
    check("failure: the previous OpenCore is put back",
          (esp2 / "EFI" / "OC" / "OpenCore.efi").read_bytes(), b"old opencore")
    check("failure: the previous BOOTx64.efi is put back",
          (esp2 / "EFI" / "BOOT" / "BOOTx64.efi").read_bytes(), b"old boot")
    check("failure: Windows is still there",
          (esp2 / "EFI" / "Microsoft" / "Boot" / "bootmgfw.efi").exists(), True)

    # -- not enough room ------------------------------------------------------
    esp3 = partition(scratch / "esp three")
    text = installer.install_script(staging, "disk0s1",
                                    scratch / "backups" / "third",
                                    owner="%d:%d" % (os.getuid(), os.getgid()),
                                    needed_kb=10 ** 12, mount_point=str(esp3),
                                    tools=tools)
    code, out, err = run_script(text)
    check("no room: refused", (code, "Not enough room" in err), (1, True))
    check("no room: nothing was changed",
          (esp3 / "EFI" / "OC" / "OpenCore.efi").read_bytes(), b"old opencore")
    check("no room: not even a backup was started",
          (scratch / "backups" / "third").exists(), False)

    # -- not mounted: the script mounts and unmounts it ------------------------
    stand_in = partition(scratch / "esp four")
    log = scratch / "diskutil.log"
    text = installer.install_script(staging, "disk9s1",
                                    scratch / "backups" / "fourth",
                                    owner="%d:%d" % (os.getuid(), os.getgid()),
                                    needed_kb=1, mount_point="", tools=tools)
    # The stand-in diskutil needs to know where it was mounted to unmount it.
    mounted_at = scratch / "mounted-at"
    text = text.replace(
        "    MOUNTED_HERE=1\n",
        "    MOUNTED_HERE=1\n    echo \"$ESP\" > %s\n" % mounted_at)
    code, out, err = run_script(text, {"OCF_DISKUTIL_LOG": str(log),
                                       "OCF_PARTITION": str(stand_in),
                                       "OCF_MOUNTED_AT": str(mounted_at)})
    calls = log.read_text().splitlines() if log.exists() else []
    check("unmounted: it is mounted, then unmounted",
          [c.split()[0] for c in calls], ["mount", "unmount"])
    check("unmounted: mounted on a private folder, by device",
          calls[0] if calls else "",
          lambda c: c.startswith("mount -mountPoint /tmp/ocf-esp.")
          and c.endswith("disk9s1"))
    check("unmounted: OpenCore ends up on the partition",
          (code, (stand_in / "EFI" / "OC" / "OpenCore.efi").read_bytes()),
          (0, b"opencore"))

    # -- quoting --------------------------------------------------------------
    odd = installer.install_script(Path("/tmp/it's \"odd\" $HOME"), "disk0s1",
                                   Path("/tmp/back up`x`"), owner="501:20",
                                   needed_kb=1, mount_point="")
    syntax = subprocess.run(["sh", "-n"], input=odd.encode(),
                            capture_output=True)
    check("quoting: awkward paths still give a valid script",
          syntax.returncode, 0)
    check("quoting: they are single-quoted, never expanded",
          "STAGING='/tmp/it'\"'\"'s \"odd\" $HOME'" in odd, True)


def test_privileged():
    from opcoreforge.companion import installer

    argv = installer.admin_command(Path('/tmp/a "b"\\c/install.sh'))
    check("osascript: run as administrator with a prompt",
          (argv[0], "with administrator privileges" in argv[2],
           "with prompt" in argv[2]),
          ("/usr/bin/osascript", True, True))
    check("osascript: quotes and backslashes are escaped for AppleScript",
          '\\"b\\"\\\\c' in argv[2], True)

    seen = []

    def ok(argv):
        seen.append(Path(script_of(argv)))
        return 0, "OCF: Backing up\rOCF: Installed\r", ""

    out = installer.run_privileged("echo hi\n", ok)
    check("privileged: output lines come back", out.splitlines(),
          ["OCF: Backing up", "OCF: Installed"])
    check("privileged: the script file is removed afterwards",
          seen and seen[0].exists(), False)

    try:
        installer.run_privileged("x", lambda a: (1, "", "User canceled. (-128)"))
        got = None
    except installer.Cancelled:
        got = "cancelled"
    check("privileged: dismissing the prompt is a cancel, not a failure",
          got, "cancelled")
    try:
        installer.run_privileged("x", lambda a: (
            1, "", "0:120: execution error: OCF-ERROR: Not enough room on "
                   "disk0s1: needs 9 KB, has 8 KB (1)"))
        got = None
    except installer.Cancelled:
        got = "cancelled"
    except installer.InstallError as error:
        got = error.message
    check("privileged: the script's own reason is what is shown",
          got, "Not enough room on disk0s1: needs 9 KB, has 8 KB")


def test_install_end_to_end(scratch: Path):
    from opcoreforge.companion import disks, installer

    if shutil.which("sh") is None:
        return
    tools = shims(scratch / "bin2")
    source = make_efi(scratch / "e2e-src")
    esp = partition(scratch / "e2e-esp")
    target = disks.EfiPartition("disk0s1", "disk0", EFI_UUID, 1,
                                mount_point=str(esp), internal=True)

    def run_with_sh(argv):
        script = script_of(argv)
        done = subprocess.run(["sh", script], capture_output=True)
        return (done.returncode, done.stdout.decode(), done.stderr.decode())

    said = []
    backup = installer.install(source, target, report=said.append,
                               runner=run_with_sh, backups=scratch / "e2e-bk",
                               tools=tools)
    check("end to end: installed and backed up",
          ((esp / "EFI" / "OC" / "OpenCore.efi").read_bytes(),
           (backup / "EFI" / "OC" / "OpenCore.efi").read_bytes()),
          (b"opencore", b"old opencore"))
    check("end to end: the log tells the story",
          said, lambda s: "Installed" in s and any("Full" in x for x in s))


def test_selfcopy(scratch: Path):
    from opcoreforge.companion import selfcopy

    exe = "/Volumes/OpCoreForge Companion/OpCoreForge Companion.app/Contents/MacOS/x"
    check("the bundle is found from the executable",
          str(selfcopy.bundle_path(exe)) if selfcopy.bundle_path(exe) else None,
          lambda p: p and p.endswith("OpCoreForge Companion.app"))
    check("running from source there is no bundle",
          selfcopy.bundle_path("/usr/bin/python3"), None)
    check("a bundle in /Applications is installed",
          selfcopy.is_installed(Path("/Applications/A.app")), True)
    check("a bundle on a disk image is not",
          selfcopy.is_installed(Path("/Volumes/X/A.app")), False)

    if shutil.which("sh") is None:
        return
    bundle = scratch / "image" / "Thing.app"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    (bundle / "Contents" / "MacOS" / "Thing").write_text("new")
    apps = scratch / "Applications"
    (apps / "Thing.app" / "Contents").mkdir(parents=True)
    (apps / "Thing.app" / "Contents" / "stale").write_text("old")
    tools = shims(scratch / "bin3")
    script = selfcopy.copy_script(bundle, apps, ditto=tools["DITTO"])
    subprocess.run(["sh", "-c", script], check=False)
    check("copying replaces the old copy rather than merging into it",
          sorted(p.name for p in (apps / "Thing.app" / "Contents").iterdir()),
          ["MacOS"])


class FakePaths:
    def __init__(self, root: Path):
        self.data = root
        self.bin = root / "bin"
        self.results = root / "Results"
        self.bin.mkdir(parents=True, exist_ok=True)

    @property
    def efi_dir(self):
        return self.results / "EFI"


def fake_dmg(path: Path, size=4096):
    data = bytearray(size)
    data[-512:-508] = b"koly"
    Path(path).write_bytes(bytes(data))


class Fetcher:
    def __init__(self, body=None):
        self.body = body
        self.urls = []

    def download_and_save_file(self, url, destination):
        self.urls.append(url)
        if self.body is None:
            return False
        Path(destination).write_bytes(self.body)
        return True


def test_media(scratch: Path):
    from opcoreforge import media, media_companion
    from opcoreforge.version import __version__

    paths = FakePaths(scratch / "data")
    make_efi(paths.results)
    recovery = media.recovery_dir(paths)
    recovery.mkdir(parents=True)
    for name in media.RECOVERY_FILES:
        (recovery / name).write_bytes(b"recovery")

    check("the version's own release is tried before the latest",
          media_companion.download_urls("2.0.0"),
          lambda u: u[0].endswith("/download/v2.0.0/OpCoreForge-Companion.dmg")
          and u[1].endswith("/latest/download/OpCoreForge-Companion.dmg"))

    good = bytearray(4096)
    good[-512:-508] = b"koly"
    fetcher = Fetcher(bytes(good))
    out = scratch / "media-one"
    out.mkdir()
    info = media_companion.add_to_media(paths, out, paths.efi_dir, fetcher)
    folder = out / "OpCoreForge"
    check("media: the companion disk image is added",
          (folder / "OpCoreForge-Companion.dmg").exists(), True)
    check("media: the as-built EFI is added", (folder / "EFI" / "OC"
                                               / "OpenCore.efi").exists(), True)
    check("media: media.json says which OpCoreForge made it",
          json.loads((folder / "media.json").read_text())["opcoreforge"],
          __version__)
    check("media: the download is cached under this version",
          (media_companion.cache_dir(paths)
           / ("OpCoreForge-Companion-%s.dmg" % __version__)).exists(), True)
    second = Fetcher(bytes(good))
    media_companion.find_dmg(paths, second)
    check("media: a cached image is not downloaded again", second.urls, [])

    other = FakePaths(scratch / "data-two")
    make_efi(other.results)
    html = Fetcher(b"<html>Not Found</html>" * 100)
    out2 = scratch / "media-two"
    out2.mkdir()
    info = media_companion.add_to_media(other, out2, other.efi_dir, html)
    check("media: an error page is not shipped as the app",
          ((out2 / "OpCoreForge" / "OpCoreForge-Companion.dmg").exists(),
           info["companion"]), (False, None))
    check("media: both release URLs were tried", len(html.urls), 2)
    check("media: without the app the READ ME says where to get it",
          (out2 / "OpCoreForge" / "READ ME.txt").read_text(),
          lambda t: "/releases" in t and "could not be downloaded" in t)

    manual = FakePaths(scratch / "data-three")
    media_companion.cache_dir(manual).mkdir(parents=True)
    fake_dmg(media_companion.cache_dir(manual) / "my copy.dmg")
    nothing = Fetcher(None)
    check("media: a disk image put in the Companion folder by hand is used",
          media_companion.find_dmg(manual, nothing).name, "my copy.dmg")
    check("media: ...without going to the network", nothing.urls, [])

    # Through stage_media: only the copy the stick boots with is tuned.
    shutil.copy2(media_companion.cache_dir(paths)
                 / ("OpCoreForge-Companion-%s.dmg" % __version__),
                 media_companion.cache_dir(paths) / "x.dmg")
    staged = media.stage_media(paths)
    root_cfg = load(staged / "EFI" / "OC" / "config.plist")
    kept_cfg = load(staged / "OpCoreForge" / "EFI" / "OC" / "config.plist")
    check("stage_media: the stick's own EFI gets the install settings",
          root_cfg["Misc"]["Boot"].get("HideAuxiliary"), False)
    check("stage_media: the as-built copy does not",
          kept_cfg["Misc"]["Boot"].get("HideAuxiliary"), None)
    check("stage_media: the summary says the companion is included",
          media.media_summary(paths)["companion"], True)


def test_credits():
    from opcoreforge import credits

    data = [
        {"login": "someone", "html_url": "https://github.com/someone",
         "contributions": 3, "type": "User"},
        {"login": "dependabot[bot]", "contributions": 40, "type": "Bot"},
        {"login": "KurdtNervanna", "html_url": "https://github.com/KurdtNervanna",
         "contributions": 12, "type": "User"},
    ]
    people = credits.parse_contributors(data)
    check("credits: bots are left out, most commits first",
          [p.login for p in people], ["KurdtNervanna", "someone"])
    check("credits: known roles are kept", people[0].note,
          "creator and maintainer")
    check("credits: GitHub's answer is used when it comes",
          [p.login for p in credits.fetch_contributors(
              opener=lambda url: json.dumps(data).encode())],
          ["KurdtNervanna", "someone"])

    def offline(url):
        raise OSError("no network")

    check("credits: offline, the built-in list is shown",
          [p.login for p in credits.fetch_contributors(opener=offline)],
          [c.login for c in credits.KNOWN_CONTRIBUTORS])
    check("credits: every upstream project has a link and an author",
          all(u.url.startswith("https://") and u.author
              for u in credits.UPSTREAM), True)


def main():
    scratch = Path(tempfile.mkdtemp(prefix="ocf-companion-"))
    try:
        test_disks()
        test_sources(scratch / "sources")
        test_prepare(scratch / "prepare")
        test_script(scratch / "script")
        test_privileged()
        test_install_end_to_end(scratch / "e2e")
        test_selfcopy(scratch / "selfcopy")
        test_media(scratch / "media")
        test_credits()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    width = max(len(label) for _status, label, _got in RESULTS)
    failed = 0
    for status, label, got in RESULTS:
        shown = "" if status == "PASS" else "  got: %r" % (got,)
        print("%-4s  %s%s" % (status, label.ljust(width), shown))
        failed += status == "FAIL"
    print("\n%d checks, %d failed" % (len(RESULTS), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
