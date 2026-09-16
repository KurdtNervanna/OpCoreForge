"""
End-to-end workflow test.

Drives the real controllers against the synthetic fixture: import a hardware
report, read ACPI tables, pick a macOS release, let OpCore-Simplify select
kexts and ACPI patches, build a complete EFI, map USB ports with USBToolBox,
install UTBMap.kext, then load the result into the embedded ProperTree and run
OC Snapshot on it.

Run headless under Xvfb.
"""

import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("TERM_PROGRAM", "")

STDERR = sys.stderr
RESULTS = []


def check(label, fn, expect=None):
    try:
        value = fn()
    except Exception as exc:
        RESULTS.append(("FAIL", label, "%s: %s" % (type(exc).__name__, exc)))
        traceback.print_exc(file=STDERR)
        return None
    ok = True
    if expect is not None:
        ok = expect(value) if callable(expect) else (value == expect)
    RESULTS.append(("PASS" if ok else "FAIL", label, value))
    return value


def main():
    import tkinter as tk
    from tkinter import ttk

    from opcoreforge import patches, paths as pm
    from opcoreforge.bridge import console as console_bridge
    from opcoreforge.bridge.ocs import OcsController
    from opcoreforge.bridge.propertree import EmbeddedProperTree
    from opcoreforge.bridge.usbtoolbox import UsbController
    from opcoreforge.ui import theme

    patches.bootstrap_sys_path()
    P = pm.init(ROOT / "_run")
    patches.apply_all(P)

    fixture = ROOT / "_fixture"
    report_path = fixture / "Report.json"

    bridge = console_bridge.ConsoleBridge()
    console_bridge.install(bridge, __import__("ocs_scripts.utils",
                                              fromlist=["utils"]))
    answers = []

    def on_prompt(item):
        # Headless: answer inline decisions with upstream's own default where
        # one is offered, otherwise the first listed option.
        default = item.default
        if item.is_acknowledge:
            value = ""
        elif item.yes_no:
            value = "yes"
        elif default:
            value = default
        else:
            choices = item.numbered_choices()
            value = choices[0][0] if choices else ""
        answers.append((item.prompt.strip()[:70], value))
        item.answer = value
        item.event.set()

    bridge.on_prompt = on_prompt
    bridge.auto_answer(r"^\s*$|press enter", "")

    ocs = OcsController(P, bridge)
    check("OCPE constructed", lambda: type(ocs.start()).__name__, "OCPE")
    check("iasl located", lambda: bool(ocs.ocpe.ac.acpi.iasl), True)

    # -- stage 1 ---------------------------------------------------------
    result = check("report validates",
                   lambda: ocs.validate_report(report_path)[0], True)
    data = ocs.validate_report(report_path)[3]
    check("ACPI tables load",
          lambda: ocs.load_acpi_tables(fixture / "ACPI"), True)
    check("compatibility check runs",
          lambda: bool(ocs.set_report(report_path, data)), True)
    check("native macOS range", lambda: ocs.native_macos_version,
          lambda v: isinstance(v, tuple) and len(v) == 2)

    # -- stage 2 ---------------------------------------------------------
    suggested = check("suggested macOS version", ocs.suggested_macos_version,
                      lambda v: v and v[0].isdigit())
    options = check("macOS options offered",
                    lambda: [(o.darwin_major, o.name, o.requires_oclp)
                             for o in ocs.macos_options()],
                    lambda v: len(v) >= 3)
    check("applies chosen version",
          lambda: ocs.apply_macos_version(suggested), suggested)
    check("hardware customised",
          lambda: sorted(ocs.customized_hardware.keys()),
          lambda v: "CPU" in v and "GPU" in v)
    check("SMBIOS auto-selected", lambda: ocs.smbios_model,
          lambda v: bool(v) and "," in v)

    ocs.select_acpi_and_kexts(first_time=True)
    check("ACPI patches selected",
          lambda: [p.name for p in ocs.acpi_patches if p.checked],
          lambda v: len(v) >= 3)
    check("kexts selected",
          lambda: [k.name for k in ocs.kexts if k.checked],
          lambda v: "Lilu" in v and "VirtualSMC" in v and "WhateverGreen" in v)

    # -- stage 3/4/5 interactions ---------------------------------------
    check("SMBIOS catalogue", lambda: len(ocs.smbios_catalog()[0]),
          lambda v: v > 40)
    before = sum(1 for p in ocs.acpi_patches if p.checked)
    ocs.toggle_acpi_patch(0)
    after = sum(1 for p in ocs.acpi_patches if p.checked)
    check("ACPI patch toggles", lambda: before != after, True)
    ocs.toggle_acpi_patch(0)

    lilu_index = next(i for i, k in enumerate(ocs.kexts) if k.name == "Lilu")
    check("required kext cannot be disabled",
          lambda: (ocs.toggle_kext(lilu_index), ocs.kexts[lilu_index].checked)[1],
          True)

    wg_index = next(i for i, k in enumerate(ocs.kexts)
                    if k.name == "WhateverGreen")
    ocs.toggle_kext(wg_index)
    check("optional kext toggles off",
          lambda: ocs.kexts[wg_index].checked, False)
    ocs.toggle_kext(wg_index)
    check("optional kext toggles back on",
          lambda: ocs.kexts[wg_index].checked, True)

    # -- stage 6 ---------------------------------------------------------
    from opcoreforge.seed import ensure_seed
    check("bundled seed unpacks",
          lambda: ensure_seed(P, report=lambda m: STDERR.write("    %s\n" % m)),
          lambda v: v in (True, False))
    check("seeded OpenCorePkg present",
          lambda: (P.ock_files / "OpenCorePkg").exists(), True)

    STDERR.write("\n--- gathering payload\n")
    started = time.time()
    skipped_kexts = []

    def gather():
        from opcoreforge.bridge.ocs import PayloadUnavailable
        for _ in range(40):
            try:
                return ocs.download_payload()
            except PayloadUnavailable as exc:
                # This sandbox cannot reach some kext hosts; exercise the same
                # skip-and-continue path the UI offers the user.
                dropped = ocs.drop_payload_product(exc.product)
                STDERR.write("    skip %s -> %s\n"
                             % (exc.product, dropped or "NOTHING TO DROP"))
                if not dropped:
                    raise
                skipped_kexts.extend(dropped)
        return False

    check("payload gathers", gather, lambda v: v is not False)
    STDERR.write("--- gather took %.0fs, skipped: %s\n"
                 % (time.time() - started, ", ".join(skipped_kexts) or "none"))

    efi = check("EFI builds", ocs.build_efi, lambda v: Path(v).exists())
    check("config.plist exists", lambda: P.config_plist.exists(), True)
    check("OpenCore.efi present",
          lambda: (P.oc_dir / "OpenCore.efi").exists(), True)
    check("BOOTx64.efi present",
          lambda: (P.efi_dir / "BOOT" / "BOOTx64.efi").exists(), True)
    check("kexts installed",
          lambda: sorted(p.name for p in P.kexts_dir.glob("*.kext")),
          lambda v: "Lilu.kext" in v and len(v) >= 4)
    # OpCore-Simplify ships UTBDefault.kext as the placeholder port map that
    # stage 7 is meant to replace. This sandbox cannot reach its release page,
    # so stand one in when it is absent - the point of the check below is that
    # installing a real map removes the placeholder.
    placeholder = P.kexts_dir / "UTBDefault.kext"
    if not placeholder.exists():
        (placeholder / "Contents").mkdir(parents=True)
        import plistlib as _pl
        _pl.dump({"CFBundleIdentifier": "com.dhinakg.UTBDefault.kext"},
                 (placeholder / "Contents" / "Info.plist").open("wb"))
        STDERR.write("    (stood in a placeholder UTBDefault.kext: its release "
                     "page is unreachable from this sandbox)\n")
    check("UTBDefault placeholder present", lambda: placeholder.exists(), True)

    import plistlib
    config = plistlib.load(P.config_plist.open("rb"))
    check("config has Kernel->Add entries",
          lambda: len(config["Kernel"]["Add"]), lambda v: v >= 4)
    check("config SMBIOS matches choice",
          lambda: config["PlatformInfo"]["Generic"]["SystemProductName"],
          ocs.smbios_model)
    check("config serial generated",
          lambda: config["PlatformInfo"]["Generic"]["SystemSerialNumber"],
          lambda v: bool(v) and v != "")
    check("ACPI Add entries",
          lambda: [e["Path"] for e in config["ACPI"]["Add"]],
          lambda v: len(v) >= 1)
    check("SSDTs written to EFI",
          lambda: sorted(p.name for p in (P.oc_dir / "ACPI").glob("*.aml")),
          lambda v: len(v) >= 1)
    check("boot-args present",
          lambda: config["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"]
          ["boot-args"], lambda v: isinstance(v, str))

    # -- stage 7 ---------------------------------------------------------
    usb = UsbController(P)
    usb.start()
    synthetic = [{
        "name": "Intel USB 3.2 xHCI",
        "identifiers": {"instance_id": "PCI\\VEN_8086&DEV_06ED",
                        "acpi_path": "\\_SB.PCI0.XHCI",
                        "pci_id": ["8086", "06ED"]},
        "class": 0x30,
        "ports": [
            {"index": 1, "name": "HS01", "class": 2, "type": None,
             "guessed": 3, "comment": None, "devices": ["Keyboard"]},
            {"index": 2, "name": "HS02", "class": 2, "type": None,
             "guessed": 3, "comment": None, "devices": []},
            {"index": 17, "name": "SS01", "class": 3, "type": None,
             "guessed": 3, "comment": None, "devices": []},
        ],
    }]
    (P.usbmap / "usb.json").write_text(json.dumps(synthetic))
    check("usb.json imports",
          lambda: len(usb.load_existing_map(P.usbmap / "usb.json")), 1)
    check("ports enumerated", lambda: len(usb.ports()), 3)

    _controller, port = usb.ports()[1]
    check("port toggles", lambda: usb.toggle_port(port), lambda v: v in (True, False))
    for _c, prt in usb.ports():
        prt["selected"] = True
        usb.set_port_type(prt, 3)
    check("validation passes", usb.validate, [])
    kext = check("UTBMap.kext builds", lambda: usb.build_kext(True),
                 lambda v: Path(v).exists())
    personalities = plistlib.load(
        (Path(kext) / "Contents" / "Info.plist").open("rb"))["IOKitPersonalities"]
    check("map has one personality", lambda: len(personalities), 1)
    check("map lists three ports",
          lambda: len(list(personalities.values())[0]
                      ["IOProviderMergeProperties"]["ports"]), 3)
    check("installs into EFI + removes placeholder",
          lambda: ocs.install_usb_map(Path(kext)), lambda v: len(v) >= 2)
    check("UTBMap.kext in EFI",
          lambda: (P.kexts_dir / "UTBMap.kext").exists(), True)
    check("UTBDefault.kext removed",
          lambda: (P.kexts_dir / "UTBDefault.kext").exists(), False)

    # -- stage 8 ---------------------------------------------------------
    root = tk.Tk()
    theme.apply(root)
    host = ttk.Frame(root)
    host.pack(fill="both", expand=True)
    ptree = EmbeddedProperTree(P)
    ptree.start(root, host)
    check("editor embedded", lambda: ptree.window.embedded, True)
    check("opens built config.plist",
          lambda: ptree.open_path(P.config_plist), True)
    root.update()

    before_snapshot = len(plistlib.load(P.config_plist.open("rb"))["Kernel"]["Add"])

    def snapshot_and_save():
        ptree.pt.settings["snapshot_version"] = "Latest"
        ptree.snapshot(oc_folder=str(P.oc_dir))
        root.update()
        ptree.window.current_plist = str(P.config_plist)
        ptree.save()
        root.update()
        return True

    check("OC Snapshot + save", snapshot_and_save, True)
    after_config = plistlib.load(P.config_plist.open("rb"))
    names = [e["BundlePath"] for e in after_config["Kernel"]["Add"]]
    check("snapshot picked up UTBMap.kext",
          lambda: "UTBMap.kext" in names, True)
    check("snapshot dropped UTBDefault.kext",
          lambda: "UTBDefault.kext" not in names, True)
    check("snapshot kept the other kexts",
          lambda: len(names), lambda v: v >= before_snapshot - 1)

    root.destroy()

    # -- report ----------------------------------------------------------
    STDERR.write("\n")
    for status, label, value in RESULTS:
        text = str(value)
        if len(text) > 90:
            text = text[:87] + "..."
        STDERR.write("%s  %-42s %s\n" % (status, label, text))
    failed = [r for r in RESULTS if r[0] == "FAIL"]
    STDERR.write("\n%d checks, %d failed\n" % (len(RESULTS), len(failed)))
    if answers:
        STDERR.write("\ninline prompts answered during the run:\n")
        for question, value in answers:
            STDERR.write("   %-72s -> %r\n" % (question, value))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
