"""
Built-in self test.

``OpCoreForge.exe --self-test`` verifies that a packaged build is actually
complete: that all three upstream projects import side by side, that the data
files each of them reads at runtime made it into the bundle, that the portable
data folder is writable, and that ProperTree still embeds into a Tk frame.

This exists because the failure mode of a bad one-file build is not a crash on
startup -- it is a missing ``snapshot.plist`` discovered ten minutes in, when
OC Snapshot silently refuses to run. Checking up front is cheap.
"""

from __future__ import annotations

import os
import sys
import traceback

from . import paths as paths_module
from . import patches


class Report:
    def __init__(self):
        self.rows = []

    def check(self, label, fn, detail=""):
        try:
            ok = bool(fn())
            self.rows.append((ok, label, detail))
        except Exception as exc:
            self.rows.append((False, label, "%s: %s" % (type(exc).__name__, exc)))

    @property
    def failures(self):
        return [r for r in self.rows if not r[0]]

    def render(self, stream):
        for ok, label, detail in self.rows:
            stream.write("  %-4s %-46s %s\n"
                         % ("ok" if ok else "FAIL", label, detail))
        stream.write("\n  %d checks, %d failed\n"
                     % (len(self.rows), len(self.failures)))


def run(stream=None, with_gui=True) -> int:
    stream = stream or sys.__stdout__
    report = Report()

    patches.bootstrap_sys_path()
    resolved = paths_module.init()

    from .version import __version__

    stream.write("\nOpCoreForge %s self test\n" % __version__)
    stream.write("  frozen:       %s\n" % paths_module.is_frozen())
    stream.write("  bundle:       %s\n" % resolved.bundle)
    stream.write("  data folder:  %s\n\n" % resolved.describe())

    # -- the three upstream trees coexisting ------------------------------
    def import_all():
        import ocs_main  # noqa: F401
        import pt_scripts.plist  # noqa: F401
        import utb_base  # noqa: F401
        from ocs_scripts.datasets import kext_data, mac_model_data  # noqa: F401
        from utb_scripts import shared  # noqa: F401
        return True

    report.check("all three projects import together", import_all)
    report.check("kext catalogue loaded", lambda: _kext_count() > 50,
                 "%d kexts" % _kext_count())
    report.check("ACPI patch catalogue loaded", lambda: _patch_count() > 10,
                 "%d patches" % _patch_count())
    report.check("Mac model catalogue loaded", lambda: _model_count() > 40,
                 "%d models" % _model_count())
    report.check("macOS releases listed", lambda: _macos_count() >= 9,
                 _macos_names())

    # -- bundled data the tools read at runtime ---------------------------
    for label, parts in (
        ("ProperTree snapshot schema", ("pt_scripts", "snapshot.plist")),
        ("ProperTree right-click menu", ("pt_scripts", "menu.plist")),
        ("ProperTree version file", ("pt_scripts", "version.json")),
        ("OpenCore picker artwork",
         ("ocs_scripts", "datasets", "background_picker.icns")),
        ("USBToolBox kext template", ("resources", "Info.plist")),
    ):
        path = resolved.resource(*parts)
        report.check(label, lambda p=path: p.exists(), str(path.name))

    usbdump = patches.usbdump_path(resolved)
    report.check("usbdump helper present", lambda: usbdump.exists(),
                 "%s%s" % (usbdump.name,
                           "" if os.name == "nt" else "  (Windows-only)"))

    # Read as source rather than imported, so bytecode in the archive is not
    # enough -- and its absence only shows up on the USB tab, minutes in.
    from .bridge import usbtoolbox as usb_bridge
    report.check("USBToolBox Windows source present",
                 lambda: usb_bridge._windows_map_source().exists(),
                 "utb_windows.py")

    # -- portable data folder ---------------------------------------------
    report.check("data folder is writable", lambda: _writable(resolved))
    patches.apply_all(resolved)
    report.check("helper paths relocated",
                 lambda: _relocated(resolved), str(resolved.bin))

    # -- offline payload ---------------------------------------------------
    from . import seed as seed_module
    version = seed_module.seed_version(resolved)
    if version:
        report.check("bundled payload unpacks",
                     lambda: seed_module.ensure_seed(resolved) or
                     seed_module.installed_version(resolved) == version,
                     "version %s" % version)
        report.check("payload contains OpenCorePkg",
                     lambda: (resolved.ock_files / "OpenCorePkg").exists())
    else:
        stream.write("  note: this build has no bundled payload; OpenCore and\n"
                     "        kexts will be downloaded on first use.\n\n")

    # -- GUI ----------------------------------------------------------------
    if with_gui:
        report.check("tkinter available", _tk_available)
        report.check("ProperTree embeds in a frame",
                     lambda: _embed_check(resolved))

    stream.write("\n")
    report.render(stream)
    if report.failures:
        stream.write("\n  This build is incomplete. Rebuild with "
                     "BUILD_EXE.bat.\n")
    else:
        stream.write("\n  Build looks good.\n")
    stream.flush()
    return 1 if report.failures else 0


# -- helpers ---------------------------------------------------------------

def _kext_count():
    try:
        from ocs_scripts.datasets import kext_data
        return len(kext_data.kexts)
    except Exception:
        return 0


def _patch_count():
    try:
        from ocs_scripts.datasets import acpi_patch_data
        return len(acpi_patch_data.patches)
    except Exception:
        return 0


def _model_count():
    try:
        from ocs_scripts.datasets import mac_model_data
        return len(mac_model_data.mac_devices)
    except Exception:
        return 0


def _macos_count():
    try:
        from ocs_scripts.datasets import os_data
        return len(os_data.macos_versions)
    except Exception:
        return 0


def _macos_names():
    try:
        from ocs_scripts.datasets import os_data
        names = [v.name for v in os_data.macos_versions]
        return "%s ... %s" % (names[0], names[-1])
    except Exception:
        return ""


def _writable(resolved):
    probe = resolved.data / ".selftest"
    probe.write_text("ok")
    probe.unlink()
    return True


def _relocated(resolved):
    from ocs_scripts import kext_maestro, smbios
    maestro = kext_maestro.KextMaestro()
    smb = smbios.SMBIOS()
    return (str(resolved.ock_files) == str(maestro.ock_files_dir)
            and str(resolved.bin) == str(smb.script_dir))


def _tk_available():
    import tkinter
    root = tkinter.Tk()
    root.withdraw()
    root.destroy()
    return True


def _embed_check(resolved):
    import tkinter as tk
    from tkinter import ttk

    from .bridge.propertree import EmbeddedProperTree

    root = tk.Tk()
    root.withdraw()
    try:
        host = ttk.Frame(root)
        host.pack()
        editor = EmbeddedProperTree(resolved)
        editor.start(root, host)
        return bool(editor.window and editor.window.embedded)
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def _present(text, ok):
    """Show the report even when there is no console.

    A windowed one-file build has no stdout on Windows, so printing alone
    would make --self-test look like it did nothing.
    """
    try:
        stream = sys.__stdout__
        if stream is not None and stream.fileno() >= 0:
            stream.write(text)
            stream.flush()
            return
    except Exception:
        pass

    try:
        import tkinter as tk
        from tkinter import ttk
        root = tk.Tk()
        root.title("OpCoreForge self test")
        root.geometry("760x560")
        box = tk.Text(root, wrap="none", background="#101318",
                      foreground="#e7eaf0", borderwidth=0, padx=12, pady=10)
        box.insert("end", text)
        box.configure(state="disabled")
        box.pack(fill="both", expand=True)
        ttk.Button(root, text="Close", command=root.destroy).pack(pady=8)
        root.mainloop()
    except Exception:
        pass


def main(argv=None):
    import io
    buffer = io.StringIO()
    try:
        code = run(stream=buffer)
    except Exception:
        traceback.print_exc(file=buffer)
        code = 1

    text = buffer.getvalue()
    try:
        log = paths_module.get().logs / "selftest.txt"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(text, encoding="utf-8")
        text += "\n  Saved to %s\n" % log
    except Exception:
        pass

    _present(text, code == 0)
    return code
