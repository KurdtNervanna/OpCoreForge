"""
Regression test: a macOS version no GPU can drive must be refused, not crashed.

Reproduces a real report. The machine's version list ran to macOS Tahoe 26, all
of Monterey onward flagged "Requires OpenCore Legacy Patcher". Choosing Tahoe
produced::

    AttributeError: 'NoneType' object has no attribute 'items'

from ``acpi_guru.select_acpi_patches``, which reads ``hardware_report["GPU"]``
without checking.

The cause is that the offered range is the *union* across every device. A
Broadcom Wi-Fi card that OpenCore Legacy Patcher supports to the newest release
stretches the list to Tahoe, while the Intel HD 4000 stops at Sequoia. Choosing
past the graphics ceiling makes ``hardware_customization`` disable every GPU,
and the next step then reads an empty GPU set.

So the checks below assert three things: that the ceiling is read off
OpCore-Simplify's own compatibility values, that versions above it are flagged
in the list rather than silently offered, and that choosing one produces an
explanation instead of a traceback.

No display needed; no network beyond what the fixture already has cached.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("TERM_PROGRAM", "")

STDERR = sys.stderr
RESULTS = []


def check(label, got, expect):
    ok = expect(got) if callable(expect) else got == expect
    RESULTS.append(("PASS" if ok else "FAIL", label, got))


# An Ivy Bridge laptop: HD 4000 graphics (OCLP to Sequoia) and a Broadcom
# wireless card (OCLP to the newest release) -- the exact shape that stretches
# the list past the graphics.
def build_report():
    data = json.loads((ROOT / "_fixture" / "Report.json").read_text())
    data["Motherboard"] = {"Name": "HP EliteBook 8570w", "Chipset": "QM77",
                           "Platform": "Laptop"}
    data["CPU"] = {
        "Manufacturer": "Intel",
        "Processor Name": "Intel(R) Core(TM) i7-3720QM CPU @ 2.60GHz",
        "Codename": "Ivy Bridge", "Core Count": "4", "CPU Count": "1",
        "SIMD Features": "SSE, SSE2, SSE3, SSSE3, SSE4.1, SSE4.2, AVX",
    }
    data["GPU"] = {"Intel(R) HD Graphics 4000": {
        "Manufacturer": "Intel", "Codename": "Ivy Bridge",
        "Device ID": "8086-0166", "Device Type": "Integrated GPU",
        "PCI Path": "PciRoot(0x0)/Pci(0x2,0x0)"}}
    data["Monitor"] = {"Internal": {
        "Connector Type": "Internal", "Resolution": "1920x1080",
        "Connected GPU": "Intel(R) HD Graphics 4000"}}
    data["Network"] = {"Broadcom 802.11ac Wireless Network Adapter": {
        "Bus Type": "PCI", "Device ID": "14E4-43B1"}}
    return data


def main():
    from opcoreforge import patches
    patches.bootstrap_sys_path()
    resolved = patches.apply_all()

    from opcoreforge.bridge import console as console_bridge
    from opcoreforge.bridge.ocs import OcsController, UnsupportedSelection
    from ocs_scripts import utils as ocs_utils

    bridge = console_bridge.ConsoleBridge()
    console_bridge.install(bridge, ocs_utils)
    # The specific answer is registered first: matching is in insertion order,
    # so a catch-all added first would swallow the version prompt.
    bridge.auto_answer(r"macOS version you want to use", "24.99.99")
    bridge.auto_answer(r".*", "")

    ocs = OcsController(resolved, bridge)
    ocs.start()
    ocs.set_report("fixture.json", build_report())

    # -- the shape that caused it -----------------------------------------
    check("the list runs past what the graphics can do",
          ocs.ocl_patched_macos_version[0][:2], "25")
    check("native support stops at Big Sur",
          ocs.native_macos_version[-1][:2], "20")

    ceiling, needs_oclp, name = ocs.graphics_ceiling()
    check("the graphics ceiling is read from the GPU's own values",
          ceiling[:2], "24")
    check("the ceiling knows it needs OCLP", needs_oclp, True)
    check("the ceiling names the GPU it came from",
          "HD Graphics 4000" in (name or ""), True)

    options = ocs.macos_options()
    by_major = {option.darwin_major: option for option in options}
    check("Tahoe is still listed, as upstream lists it",
          25 in by_major, True)
    check("Tahoe is flagged as having no graphics support",
          by_major[25].graphics_supported, False)
    check("Sequoia is not flagged", by_major[24].graphics_supported, True)
    check("Big Sur is not flagged", by_major[20].graphics_supported, True)
    check("the flag is not just the OCLP flag",
          by_major[24].requires_oclp and by_major[24].graphics_supported, True)

    note = ocs.graphics_limit_note()
    check("the explanation names the GPU",
          "HD Graphics 4000" in note, True)
    check("the explanation names the ceiling", "Sequoia" in note, True)

    # -- the refusal -------------------------------------------------------
    try:
        ocs.apply_macos_version("25.99.99")
        RESULTS.append(("FAIL", "choosing Tahoe is refused", "no exception"))
    except UnsupportedSelection as refused:
        check("choosing Tahoe is refused", True, True)
        check("the refusal names what was asked for",
              "Tahoe" in refused.message, True)
        check("the refusal says what the machine can do",
              "Sequoia" in refused.hint, True)
        check("the refusal explains why it was offered",
              "union" in refused.hint or "other devices" in refused.hint, True)
        check("the refusal carries a dialog title",
              bool(getattr(refused, "title", "")), True)
    except Exception as other:
        RESULTS.append(("FAIL", "choosing Tahoe is refused",
                        "%s: %s" % (type(other).__name__, other)))

    check("a refused choice changes nothing", ocs.macos_version, None)

    # -- the highest possible version still works end to end ---------------
    try:
        applied = ocs.apply_macos_version("24.99.99")
        check("the highest supported version applies", applied, "24.99.99")
        check("a GPU survives customisation",
              bool(ocs.customized_hardware.get("GPU")), True)
        ocs.load_acpi_tables(ROOT / "_fixture" / "ACPI")
        ocs.select_acpi_and_kexts(first_time=True)
        check("ACPI patches and kexts are selected without crashing", True, True)
        check("it knows OCLP is needed", ocs.needs_oclp, True)
    except Exception:
        RESULTS.append(("FAIL", "the highest supported version applies",
                        traceback.format_exc()[-300:]))

    # -- the guard behind the guard ---------------------------------------
    # Even reached directly, the step that crashed must explain itself.
    intact = ocs.customized_hardware
    ocs.customized_hardware = {"CPU": {}}
    try:
        ocs.select_acpi_and_kexts(first_time=True)
        RESULTS.append(("FAIL", "an empty GPU set is named, not crashed",
                        "no exception"))
    except UnsupportedSelection as refused:
        check("an empty GPU set is named, not crashed",
              "no usable graphics" in refused.message.lower(), True)
    except Exception as other:
        RESULTS.append(("FAIL", "an empty GPU set is named, not crashed",
                        type(other).__name__))
    finally:
        ocs.customized_hardware = intact

    ocs.customized_hardware = None
    try:
        ocs.select_acpi_and_kexts(first_time=True)
        RESULTS.append(("FAIL", "no version chosen yet is named too",
                        "no exception"))
    except UnsupportedSelection as refused:
        check("no version chosen yet is named too",
              "no macos version" in refused.message.lower(), True)
    except Exception as other:
        RESULTS.append(("FAIL", "no version chosen yet is named too",
                        type(other).__name__))
    finally:
        ocs.customized_hardware = intact

    STDERR.write("\n")
    for status, label, value in RESULTS:
        text = str(value).replace("\n", " ")
        if len(text) > 34:
            text = text[:31] + "..."
        STDERR.write("%s  %-58s %s\n" % (status, label, text))
    failed = [r for r in RESULTS if r[0] == "FAIL"]
    STDERR.write("\n%d checks, %d failed\n" % (len(RESULTS), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
