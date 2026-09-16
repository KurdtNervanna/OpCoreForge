"""
The EFI consistency check: does config.plist name anything the folder lacks?

This exists because of a real dead boot. The EFI is built with UTBDefault.kext
and config.plist gets an entry for it. The USB stage then swaps in the real
port map and deletes UTBDefault.kext from the Kexts folder. Upstream says to
re-run OC Snapshot afterwards; nothing forced it, so config.plist kept pointing
at a kext that was no longer there and OpenCore stopped with

    OC: Plist Kexts\\UTBDefault.kext\\Contents\\Info.plist is missing for
    injected kext UTBDefault.kext ()
    Halting on critical error

-- after the picker had appeared, which made it look like a macOS problem
rather than a file that was not copied.

So the checks below are in two groups. First that ``replace_kext`` rewrites
config.plist at the moment the folder changes, in place, keeping load order and
the kernel range -- the actual fix. Then that ``audit``/``repair`` catch the
same shape of mistake from any other cause: a kext folder with no Info.plist, a
disabled entry (not fatal, OpenCore skips it), a missing ACPI table, driver or
tool, and a kext sitting in the folder that nothing loads.

No display, no network, no disks.
"""

from __future__ import annotations

import os
import plistlib
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
    return got


def make_kext(kexts: Path, name: str, identifier: str, executable=None):
    """A kext on disk. Codeless unless an executable name is given."""
    contents = kexts / name / "Contents"
    contents.mkdir(parents=True, exist_ok=True)
    info = {"CFBundleIdentifier": identifier, "CFBundleVersion": "1.0"}
    if executable:
        info["CFBundleExecutable"] = executable
        binary = contents / "MacOS" / executable
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(b"\xcf\xfa\xed\xfe not really mach-o")
    with (contents / "Info.plist").open("wb") as handle:
        plistlib.dump(info, handle)
    return kexts / name


def build_efi(root: Path) -> Path:
    """A small but structurally honest EFI/OC tree."""
    efi = root / "EFI"
    oc = efi / "OC"
    for folder in ("Kexts", "ACPI", "Drivers", "Tools"):
        (oc / folder).mkdir(parents=True, exist_ok=True)

    kexts = oc / "Kexts"
    make_kext(kexts, "Lilu.kext", "as.vit9696.Lilu", executable="Lilu")
    make_kext(kexts, "USBToolBox.kext", "com.dhinakg.USBToolBox.kext",
              executable="USBToolBox")
    make_kext(kexts, "UTBDefault.kext", "com.dhinakg.UTBDefault.kext")

    (oc / "ACPI" / "SSDT-PLUG.aml").write_bytes(b"SSDT")
    (oc / "Drivers" / "OpenRuntime.efi").write_bytes(b"MZ")
    (oc / "Tools" / "OpenShell.efi").write_bytes(b"MZ")

    def add(name, plist="Contents/Info.plist", executable="", enabled=True,
            minimum="", maximum=""):
        return {"Arch": "Any", "BundlePath": name, "Comment": "",
                "Enabled": enabled, "ExecutablePath": executable,
                "MaxKernel": maximum, "MinKernel": minimum, "PlistPath": plist}

    config = {
        "ACPI": {"Add": [{"Comment": "", "Enabled": True,
                          "Path": "SSDT-PLUG.aml"}]},
        "Kernel": {"Add": [
            add("Lilu.kext", executable="Contents/MacOS/Lilu"),
            add("USBToolBox.kext", executable="Contents/MacOS/USBToolBox"),
            add("UTBDefault.kext", minimum="19.0.0", maximum="24.99.99"),
        ]},
        "Misc": {"Tools": [{"Enabled": True, "Path": "OpenShell.efi",
                            "Name": "Shell"}]},
        "UEFI": {"Drivers": [{"Enabled": True, "Path": "OpenRuntime.efi"}]},
    }
    with (oc / "config.plist").open("wb") as handle:
        plistlib.dump(config, handle, sort_keys=False)
    return efi


def read(efi: Path) -> dict:
    with (efi / "OC" / "config.plist").open("rb") as handle:
        return plistlib.load(handle)


def bundles(efi: Path) -> list:
    return [e.get("BundlePath") for e in read(efi)["Kernel"]["Add"]]


def main():
    from opcoreforge import eficheck

    scratch = Path(tempfile.mkdtemp(prefix="ocf-eficheck-"))

    # -- a consistent EFI is quiet ------------------------------------------
    root = scratch / "clean"
    efi = build_efi(root)
    check("a matching EFI and config raise nothing", eficheck.audit(efi), [])
    check("and there is nothing to repair", eficheck.repair(efi), [])
    check("summary of a clean EFI is empty",
          eficheck.summary(eficheck.audit(efi)), "")

    # -- reading a kext into an entry ---------------------------------------
    kexts = efi / "OC" / "Kexts"
    entry = eficheck.kext_entry(kexts, "Lilu.kext")
    check("an entry names the bundle", entry["BundlePath"], "Lilu.kext")
    check("and finds the Info.plist", entry["PlistPath"], "Contents/Info.plist")
    check("and the executable when there is one",
          entry["ExecutablePath"], "Contents/MacOS/Lilu")
    codeless = eficheck.kext_entry(kexts, "UTBDefault.kext")
    check("a codeless kext gets an empty executable path",
          codeless["ExecutablePath"], "")
    check("the kernel range is left open", (codeless["MinKernel"],
                                            codeless["MaxKernel"]), ("", ""))
    check("a kext that is not there cannot be described",
          lambda: eficheck.kext_entry(kexts, "Nope.kext"),
          lambda f: raises(f, FileNotFoundError))

    # -- the real bug: swapping the USB map ---------------------------------
    root = scratch / "swap"
    efi = build_efi(root)
    kexts = efi / "OC" / "Kexts"
    config_path = efi / "OC" / "config.plist"
    make_kext(kexts, "UTBMap.kext", "com.dhinakg.UTBMap.kext")
    shutil.rmtree(kexts / "UTBDefault.kext")

    check("before the swap the config still lists the deleted kext",
          "UTBDefault.kext" in bundles(efi), True)
    check("which is exactly what halts OpenCore",
          [p.fatal for p in eficheck.audit(efi)
           if p.entry == "UTBDefault.kext" and p.kind == "missing"], [True])

    actions = eficheck.replace_kext(config_path, kexts,
                                    "UTBDefault.kext", "UTBMap.kext")
    check("the swap reports what it did", len(actions), 1)
    check("the placeholder is gone from Kernel -> Add",
          "UTBDefault.kext" in bundles(efi), False)
    check("and the port map took its place", "UTBMap.kext" in bundles(efi), True)
    check("in the same position, so load order is kept",
          bundles(efi).index("UTBMap.kext"), 2)
    check("after USBToolBox.kext, which it depends on",
          bundles(efi).index("UTBMap.kext")
          > bundles(efi).index("USBToolBox.kext"), True)

    swapped = [e for e in read(efi)["Kernel"]["Add"]
               if e["BundlePath"] == "UTBMap.kext"][0]
    check("the kernel range carries over from the entry replaced",
          (swapped["MinKernel"], swapped["MaxKernel"]), ("19.0.0", "24.99.99"))
    check("the codeless map gets no executable path",
          swapped["ExecutablePath"], "")
    check("and it is enabled", swapped["Enabled"], True)
    check("the EFI now passes its own check", eficheck.audit(efi), [])
    check("running the swap twice changes nothing more",
          eficheck.replace_kext(config_path, kexts,
                                "UTBDefault.kext", "UTBMap.kext"), [])

    # -- swapping when the map was never built ------------------------------
    root = scratch / "no-map"
    efi = build_efi(root)
    kexts = efi / "OC" / "Kexts"
    shutil.rmtree(kexts / "UTBDefault.kext")
    actions = eficheck.replace_kext(efi / "OC" / "config.plist", kexts,
                                    "UTBDefault.kext", "UTBMap.kext")
    check("with no replacement to install the dead entry is still dropped",
          "UTBDefault.kext" in bundles(efi), False)
    check("and that is reported", len(actions), 1)
    check("nothing invented in its place", "UTBMap.kext" in bundles(efi), False)

    # -- a map installed but never listed -----------------------------------
    root = scratch / "unlisted"
    efi = build_efi(root)
    kexts = efi / "OC" / "Kexts"
    make_kext(kexts, "UTBMap.kext", "com.dhinakg.UTBMap.kext")
    problems = eficheck.audit(efi)
    check("a kext nothing loads is noticed",
          [p.entry for p in problems if p.kind == "unlisted"], ["UTBMap.kext"])
    check("but it does not stop a boot, so it is not fatal",
          [p.fatal for p in problems if p.kind == "unlisted"], [False])
    check("and repair leaves it alone rather than guessing load order",
          eficheck.repair(efi), [])
    actions = eficheck.replace_kext(efi / "OC" / "config.plist", kexts,
                                    "Nothing.kext", "UTBMap.kext")
    check("adding it explicitly appends it after everything listed",
          bundles(efi)[-1], "UTBMap.kext")
    check("and says so", actions, lambda v: len(v) == 1 and "added" in v[0])

    # -- repairing an EFI built before any of this existed -------------------
    # UTBMap.kext copied in, UTBDefault.kext deleted, config.plist told about
    # neither. One call has to fix both halves: the dead entry that halts the
    # boot, and the port map that is installed but not loaded.
    root = scratch / "legacy"
    efi = build_efi(root)
    kexts = efi / "OC" / "Kexts"
    make_kext(kexts, "UTBMap.kext", "com.dhinakg.UTBMap.kext")
    shutil.rmtree(kexts / "UTBDefault.kext")
    actions = eficheck.sync_usb_map(efi)
    check("the old EFI is brought into line in one call", len(actions), 1)
    check("the dead entry is gone", "UTBDefault.kext" in bundles(efi), False)
    check("and the port map is actually loaded now",
          "UTBMap.kext" in bundles(efi), True)
    check("which leaves nothing to report", eficheck.audit(efi), [])
    check("and calling it again is a no-op", eficheck.sync_usb_map(efi), [])

    root = scratch / "no-utbmap"
    efi = build_efi(root)
    check("an EFI with no port map installed is left alone",
          eficheck.sync_usb_map(efi), [])
    check("so UTBDefault.kext keeps loading",
          "UTBDefault.kext" in bundles(efi), True)

    # -- other ways a config can outrun its folder --------------------------
    root = scratch / "broken"
    efi = build_efi(root)
    oc = efi / "OC"
    (oc / "Kexts" / "UTBDefault.kext" / "Contents" / "Info.plist").unlink()
    (oc / "ACPI" / "SSDT-PLUG.aml").unlink()
    (oc / "Drivers" / "OpenRuntime.efi").unlink()
    (oc / "Tools" / "OpenShell.efi").unlink()
    problems = eficheck.audit(efi)
    kinds = sorted({p.section for p in problems if p.kind == "missing"})
    check("every section that names a file is checked", kinds,
          ["ACPI -> Add", "Kernel -> Add", "Misc -> Tools", "UEFI -> Drivers"])
    check("a kext folder with no Info.plist is caught too",
          [p.detail for p in problems if p.entry == "UTBDefault.kext"],
          lambda v: v and "Contents/Info.plist" in v[0])
    check("the summary says how many and which",
          eficheck.summary(problems), lambda v: v.startswith("config.plist names 4"))

    actions = eficheck.repair(efi)
    check("repair drops all four", len(actions), 4)
    check("leaving no missing-file problems",
          [p for p in eficheck.audit(efi) if p.kind == "missing"], [])
    check("the kexts that were fine are untouched",
          bundles(efi), ["Lilu.kext", "USBToolBox.kext"])
    check("the ACPI list is emptied rather than left dangling",
          read(efi)["ACPI"]["Add"], [])
    check("repairing an already-repaired EFI does nothing",
          eficheck.repair(efi), [])

    # -- a broken executable path -------------------------------------------
    root = scratch / "exec"
    efi = build_efi(root)
    (efi / "OC" / "Kexts" / "Lilu.kext" / "Contents" / "MacOS" / "Lilu").unlink()
    problems = eficheck.audit(efi)
    check("a missing binary is fatal too",
          [(p.entry, p.fatal) for p in problems if p.kind == "missing"],
          [("Lilu.kext", True)])

    # -- disabled entries ---------------------------------------------------
    root = scratch / "disabled"
    efi = build_efi(root)
    config = read(efi)
    for entry in config["Kernel"]["Add"]:
        if entry["BundlePath"] == "UTBDefault.kext":
            entry["Enabled"] = False
    with (efi / "OC" / "config.plist").open("wb") as handle:
        plistlib.dump(config, handle, sort_keys=False)
    shutil.rmtree(efi / "OC" / "Kexts" / "UTBDefault.kext")
    problems = [p for p in eficheck.audit(efi) if p.kind == "missing"]
    check("a disabled entry pointing at nothing is reported",
          [p.entry for p in problems], ["UTBDefault.kext"])
    check("but OpenCore skips it, so it is not fatal",
          [p.fatal for p in problems], [False])
    check("it is still tidied away", len(eficheck.repair(efi)), 1)

    # -- refusing to guess --------------------------------------------------
    empty = scratch / "empty" / "EFI"
    (empty / "OC").mkdir(parents=True)
    check("an EFI with no config.plist is a problem, not a crash",
          [p.detail for p in eficheck.audit(empty)],
          lambda v: len(v) == 1 and "no config.plist" in v[0])
    check("and there is nothing to repair in it", eficheck.repair(empty), [])

    (empty / "OC" / "config.plist").write_text("this is not a plist")
    check("an unreadable config says so rather than throwing",
          [p.section for p in eficheck.audit(empty)], ["config.plist"])
    check("repair leaves a config it cannot parse alone",
          eficheck.repair(empty), [])
    check("and the file is not truncated by the attempt",
          (empty / "OC" / "config.plist").read_text(), "this is not a plist")

    STDERR.write("\n")
    for status, label, value in RESULTS:
        text = str(value).replace("\n", " ")
        if len(text) > 32:
            text = text[:29] + "..."
        STDERR.write("%s  %-58s %s\n" % (status, label, text))
    failed = [r for r in RESULTS if r[0] == "FAIL"]
    STDERR.write("\n%d checks, %d failed\n" % (len(RESULTS), len(failed)))
    shutil.rmtree(scratch, ignore_errors=True)
    return 1 if failed else 0


def raises(call, kind):
    try:
        call()
    except kind:
        return True
    except Exception:
        return False
    return False


if __name__ == "__main__":
    sys.exit(main())
