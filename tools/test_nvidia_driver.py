"""
NullMoth NVIDIA driver integration -- pure-logic regression test.

No Tk, no Xvfb: it exercises ``opcoreforge.nvidia`` directly and drives the
patched ``CompatibilityChecker`` with a Utils instance whose only overrides are
``request_input`` (to answer the offer) and ``exit_program`` (to make the stop
observable). Everything else is the real upstream code.

Covers:
  * apply_to_config writes the README's exact values, merges boot-args, strips
    the card's bad flags, and is idempotent.
  * a Turing-only machine: declining reproduces upstream's hard stop; accepting
    reclassifies the card to Sequoia and pins the native window to it.
  * a machine with another supported GPU never stops and never engages.
"""

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from opcoreforge import patches  # noqa: E402

patches.bootstrap_sys_path()
patches.patch_compatibility_checker()

from opcoreforge import nvidia  # noqa: E402

RESULTS = []


def check(label, got, expect):
    ok = expect(got) if callable(expect) else got == expect
    RESULTS.append(("PASS" if ok else "FAIL", label, got))


def turing_only_report():
    return {
        "CPU": {"Processor Name": "Intel(R) Core(TM) i7-4790K CPU @ 4.00GHz",
                "SIMD Features": "SSE, SSE2, AVX, AVX2"},
        "Motherboard": {"Platform": "Desktop"},
        "GPU": {
            # Codename "Unknown" is what a real Hardware Sniffer report carries
            # for this card -- the device id is what identifies it as Turing.
            "NVIDIA GeForce GTX 1660 Ti": {
                "Manufacturer": "NVIDIA", "Codename": "Unknown",
                "Device ID": "10DE-2182", "Device Type": "Discrete GPU"},
        },
        "Monitor": {
            "DELL P2312H": {"Connector Type": "DVI", "Resolution": "1920x1080",
                            "Connected GPU": "NVIDIA GeForce GTX 1660 Ti"}},
    }


def mixed_report():
    report = turing_only_report()
    report["GPU"]["AMD Radeon RX 6600"] = {
        "Manufacturer": "AMD", "Codename": "Navi 23",
        "Device ID": "1002-73FF", "Device Type": "Discrete GPU"}
    return report


def run_checker(report, answer):
    """Run the patched check_gpu_compatibility; return (stopped, checker)."""
    from ocs_scripts import compatibility_checker
    from ocs_scripts.datasets import os_data

    checker = compatibility_checker.CompatibilityChecker()
    checker.hardware_report = report
    checker.ocl_patched_macos_version = None
    checker.min_native_macos_version = os_data.get_lowest_darwin_version()
    checker.max_native_macos_version = os_data.get_latest_darwin_version()

    class _Stop(Exception):
        pass

    def request_input(prompt="Press Enter to continue...", *a, **k):
        return answer if "NullMoth" in prompt else ""

    def exit_program(*a, **k):
        raise _Stop()

    checker.utils.request_input = request_input
    checker.utils.exit_program = exit_program

    stopped = False
    try:
        checker.check_gpu_compatibility()
    except _Stop:
        stopped = True
    return stopped, checker


def main():
    # -- apply_to_config -----------------------------------------------------
    config = {"NVRAM": {"Add": {nvidia.NVRAM_GUID: {"boot-args": "-v nv_disable=1 keepsyms=1"}}}}
    changes = nvidia.apply_to_config(config, "24.1.0")
    section = config["NVRAM"]["Add"][nvidia.NVRAM_GUID]
    args = section["boot-args"].split()
    check("boot-args keeps unrelated flags", "keepsyms=1" in args and "-v" in args, True)
    check("boot-args drops nv_disable=1", "nv_disable=1" not in args, True)
    check("boot-args adds driver flags", all(t in args for t in nvidia.DRIVER_BOOT_ARGS), True)
    check("csr-active-config is 430A0000", section.get("csr-active-config"), nvidia.CSR_ACTIVE_CONFIG)
    check("NVRAM Delete lists both keys",
          sorted(config["NVRAM"]["Delete"][nvidia.NVRAM_GUID]),
          ["boot-args", "csr-active-config"])
    block = config["Kernel"]["Block"]
    check("IONDRVSupport blocked (Exclude)",
          any(e["Identifier"] == nvidia.BLOCKED_KEXT_ID and e["Strategy"] == "Exclude"
              and e["Enabled"] for e in block), True)
    check("SecureBootModel disabled",
          config["Misc"]["Security"]["SecureBootModel"], "Disabled")
    check("apply reported changes", len(changes) > 0, True)
    check("apply is idempotent", nvidia.apply_to_config(config, "24.1.0"), [])
    check("non-Sequoia target untouched", nvidia.apply_to_config({}, "23.0.0"), [])

    check("targets_sequoia(24.x)", nvidia.targets_sequoia("24.5.0"), True)
    check("targets_sequoia(25.x)", nvidia.targets_sequoia("25.0.0"), False)

    # -- device-id detection (codename "Unknown" is common in real reports) --
    def gpu(vendor_dev, codename="Unknown", mfr="NVIDIA"):
        return {"Manufacturer": mfr, "Codename": codename, "Device ID": vendor_dev}
    check("Turing by id (1660 Ti, Unknown codename)",
          nvidia.is_turing_or_newer(gpu("10DE-2182")), True)
    check("Ada by id (RTX 4090)", nvidia.is_turing_or_newer(gpu("10DE-2684")), True)
    check("Blackwell by id (RTX 5090)", nvidia.is_turing_or_newer(gpu("10DE-2B85")), True)
    check("Pascal by id (GTX 1060) rejected",
          nvidia.is_turing_or_newer(gpu("10DE-1C03")), False)
    check("Maxwell by id (GTX 970) rejected",
          nvidia.is_turing_or_newer(gpu("10DE-13C2")), False)
    check("AMD rejected", nvidia.is_turing_or_newer(gpu("1002-73FF", mfr="AMD")), False)
    check("codename fallback when id unparseable",
          nvidia.is_turing_or_newer(gpu("10DE-XXXX", codename="Ampere")), True)

    # -- checker: decline keeps the hard stop --------------------------------
    import contextlib
    nvidia.reset()
    declined = io.StringIO()
    with contextlib.redirect_stdout(declined):
        stopped, _ = run_checker(turing_only_report(), "no")
    check("decline -> upstream stop", stopped, True)
    check("decline -> not engaged", nvidia.engaged(), False)
    check("decline -> no 'spam my inbox' text", "spam" not in declined.getvalue(), True)

    # -- checker: accept reclassifies to Sequoia, output is clean ------------
    nvidia.reset()
    report = turing_only_report()
    accepted = io.StringIO()
    with contextlib.redirect_stdout(accepted):
        stopped, checker = run_checker(report, "yes")
    out = accepted.getvalue()
    check("accept -> no stop", stopped, False)
    check("accept -> engaged", nvidia.engaged(), True)
    card = report["GPU"]["NVIDIA GeForce GTX 1660 Ti"]
    check("accept -> card reclassified to Sequoia", card.get("Compatibility"), nvidia.COMPATIBILITY)
    check("accept -> native max pinned to Sequoia", checker.max_native_macos_version, "24.99.99")
    check("accept -> native min pinned to Sequoia", checker.min_native_macos_version, "24.0.0")
    check("accept -> output names the driver", "NullMoth NVIDIA driver enabled" in out, True)
    check("accept -> no 'Unsupported' shown for the card",
          "Unsupported" not in out, True)
    check("accept -> no 'cannot install' text", "cannot install" not in out, True)

    # -- another supported GPU: never stops, never engages -------------------
    nvidia.reset()
    stopped, _ = run_checker(mixed_report(), "yes")
    check("supported dGPU present -> no stop", stopped, False)
    check("supported dGPU present -> not engaged", nvidia.engaged(), False)

    print("")
    for status, label, value in RESULTS:
        text = str(value).replace("\n", " ")
        if len(text) > 50:
            text = text[:47] + "..."
        print("%s  %-46s %s" % (status, label, text))
    failed = [r for r in RESULTS if r[0] == "FAIL"]
    print("\n%d checks, %d failed" % (len(RESULTS), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
