"""
Regression test: OpCore-Simplify refusing the hardware must be *visible*.

Six paths in the compatibility checker print an explanation, wait for Enter,
then call exit_program(). OpCoreForge auto-answers "Press Enter" (otherwise
every routine pause becomes a dialog), so if the exit unwinds silently the user
is left staring at a status bar that says "Cancelled" with the reason buried in
a log pane that is hidden by default -- which looks exactly like the app is
waiting for input it never asked for.

Reproduces a real report: Haswell i7-4790K with a GTX 1660 Ti (Turing), which
has no macOS driver and never will.

Run under Xvfb.
"""

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("TERM_PROGRAM", "")

STDERR = sys.stderr
RESULTS = []
SHOTS = ROOT / "_shots"

REPORT = {
    "Motherboard": {"Name": "ASUS Z97-A", "Chipset": "Z97", "Platform": "Desktop"},
    "BIOS": {"Firmware Type": "UEFI", "Secure Boot": "Disabled"},
    "CPU": {
        "Manufacturer": "Intel",
        "Processor Name": "Intel(R) Core(TM) i7-4790K CPU @ 4.00GHz",
        "Codename": "Haswell",
        "Core Count": "4", "CPU Count": "1",
        "SIMD Features": "SSE, SSE2, SSE3, SSSE3, SSE4.1, SSE4.2, AVX, AVX2",
    },
    # Only the Turing card, exactly as reported when the iGPU is off in BIOS.
    "GPU": {
        "NVIDIA GeForce GTX 1660 Ti": {
            "Manufacturer": "NVIDIA", "Codename": "Turing",
            "Device ID": "10DE-2182", "Device Type": "Discrete GPU",
            "PCI Path": "PciRoot(0x0)/Pci(0x1,0x0)/Pci(0x0,0x0)",
        }
    },
    "Monitor": {
        "Cinema HD": {"Connector Type": "DVI", "Resolution": "2560x1600",
                      "Connected GPU": "NVIDIA GeForce GTX 1660 Ti"},
        "DELL P2312H": {"Connector Type": "DVI", "Resolution": "1920x1080",
                        "Connected GPU": "NVIDIA GeForce GTX 1660 Ti"},
    },
    "Network": {"Intel(R) Ethernet Connection I218-V": {
        "Bus Type": "PCI", "Device ID": "8086-15A1"}},
    "USB Controllers": {"Intel(R) USB 3.0 eXtensible Host Controller": {
        "Bus Type": "PCI", "Device ID": "8086-8CB1"}},
    "Input": {"HID Keyboard Device": {"Bus Type": "USB"}},
    "Storage Controllers": {"Intel(R) SATA AHCI Controller": {
        "Bus Type": "PCI", "Device ID": "8086-8C82"}},
}


def check(label, fn, expect=None):
    try:
        value = fn()
    except Exception as exc:
        import traceback
        RESULTS.append(("FAIL", label, "%s: %s" % (type(exc).__name__, exc)))
        traceback.print_exc(file=STDERR)
        return None
    ok = True if expect is None else (expect(value) if callable(expect)
                                      else value == expect)
    RESULTS.append(("PASS" if ok else "FAIL", label, value))
    return value


def shot(name):
    import subprocess
    SHOTS.mkdir(exist_ok=True)
    subprocess.run(["import", "-window", "root", str(SHOTS / ("%s.png" % name))],
                   check=False, capture_output=True)
    return (SHOTS / ("%s.png" % name)).exists()


def main():
    import tkinter as tk

    from opcoreforge import patches, paths as pm
    from opcoreforge.bridge.console import WorkflowAborted
    from opcoreforge.ui import theme
    from opcoreforge.ui.app import App

    patches.bootstrap_sys_path()
    P = pm.init(ROOT / "_run")
    patches.apply_all(P)

    fixture = ROOT / "_fixture_unsupported"
    fixture.mkdir(exist_ok=True)
    report_path = fixture / "Report.json"
    report_path.write_text(json.dumps(REPORT, indent=4))

    root = tk.Tk()
    theme.apply(root)
    app = App(root, P)
    root.update()

    state = {"done": False}

    def wait_idle(then, tries=1200):
        if not app.runner.busy:
            then()
            return
        if tries <= 0:
            RESULTS.append(("FAIL", "wait_idle timed out", "still busy"))
            root.after(100, root.destroy)
            return
        root.after(100, lambda: wait_idle(then, tries - 1))

    def begin():
        hardware = app.stages["hardware"]
        hardware.ensure_built()

        # 1. the controller must raise a *fatal* abort carrying the reason
        def raises():
            try:
                app.ocs.set_report(str(report_path), json.loads(
                    report_path.read_text()))
            except WorkflowAborted as exc:
                return exc
            return None

        aborted = check("compatibility check aborts", raises,
                        lambda v: v is not None)
        check("abort is marked fatal",
              lambda: getattr(aborted, "fatal", False), True)
        check("abort carries the explanation",
              lambda: "without a supported GPU" in (aborted.screen or ""), True)
        check("explanation names the GPU",
              lambda: "GTX 1660 Ti" in (aborted.screen or ""), True)

        # 2. the same thing through the UI must surface, not vanish
        app.ocs.hardware_report = None
        hardware._adopt(str(report_path), json.loads(report_path.read_text()))
        wait_idle(after_ui)

    def after_ui():
        root.update()
        hardware = app.stages["hardware"]

        # The stop is reported with a modal; capture and dismiss it, since a
        # dialog nobody can close is the very failure this test guards against.
        from opcoreforge.ui.prompt import MessageDialog
        dialogs = [w for w in root.winfo_children()
                   if isinstance(w, MessageDialog)]
        check("stop is reported with a dialog", lambda: len(dialogs), 1)
        if dialogs:
            check("dialog shows the reason",
                  lambda: dialogs[0].winfo_children()[0].winfo_children()[2]
                          .winfo_children()[0].get("1.0", "end"),
                  lambda v: "without a supported GPU" in v)
            check("dialog screenshot", lambda: shot("unsupported-dialog"), True)
            dialogs[0].destroy()
            root.update()

        check("status says stopped, not cancelled",
              lambda: app.status_label.cget("text"), "Stopped")
        check("banner is shown in red",
              lambda: hardware.banner.label.cget("text"),
              lambda v: "stopped" in v.lower() and len(v) > 20)
        check("reason is on screen, not just in the log",
              lambda: hardware.report_text.get("1.0", "end"),
              lambda v: "without a supported GPU" in v)
        check("macOS stage stays locked",
              lambda: app.notebook.tab(1, "state"), "disabled")
        check("stage still usable afterwards",
              lambda: str(hardware.detect_button["state"]) != "disabled"
                      or os.name != "nt", True)
        check("screenshot", lambda: shot("unsupported-gpu"), True)

        state["done"] = True
        root.after(200, root.destroy)

    root.after(1000, lambda: wait_idle(begin))
    root.after(400000, root.destroy)
    root.mainloop()

    STDERR.write("\n")
    for status, label, value in RESULTS:
        text = str(value).replace("\n", " ")
        if len(text) > 74:
            text = text[:71] + "..."
        STDERR.write("%s  %-42s %s\n" % (status, label, text))
    failed = [r for r in RESULTS if r[0] == "FAIL"]
    STDERR.write("\n%d checks, %d failed, completed=%s\n"
                 % (len(RESULTS), len(failed), state["done"]))
    return 1 if failed or not state["done"] else 0


if __name__ == "__main__":
    sys.exit(main())
