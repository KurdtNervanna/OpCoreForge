"""
The companion's window.

One screen, top to bottom in the order things are done: what this Mac is and
how it started, putting OpenCore on its disk, then keeping the app. Long work
runs on a thread and reports back through a queue the Tk loop drains, so the
window never freezes behind a password prompt or a copy.
"""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ..ui import credits as credits_window
from ..ui import theme
from ..ui.widgets import Banner, Card, KeyValue, LogPane, ScrollFrame
from ..version import __version__
from . import installer, selfcopy, system

TITLE = "OpCoreForge Companion"

USAGE = """\
OpCoreForge Companion %s - the macOS half of OpCoreForge.

  --version     print the version and exit
  --self-test   check the bundle and read this Mac's disks, without a window
""" % __version__


class CompanionApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.queue = queue.Queue()
        self.busy = False
        self.snapshot = None
        self.sources = []
        self.targets = []

        theme.apply(root)
        ttk.Style(root).configure("Card.TCheckbutton",
                                  background=theme.PANEL_ALT)
        root.title("%s %s" % (TITLE, __version__))
        root.geometry("900x840")
        root.minsize(760, 640)

        outer = ttk.Frame(root, style="App.TFrame", padding=(18, 14, 18, 10))
        outer.pack(fill="both", expand=True)
        head = ttk.Frame(outer, style="App.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text=TITLE, style="H1.TLabel").pack(side="left")
        ttk.Button(head, text="Credits", style="Small.TButton",
                   command=lambda: credits_window.show(root, TITLE)
                   ).pack(side="right")
        ttk.Label(outer, style="MutedApp.TLabel",
                  text="Finishes what OpCoreForge started, from inside macOS: "
                       "puts OpenCore on this Mac's own disk so the USB stick "
                       "is no longer needed to boot.").pack(anchor="w",
                                                            pady=(2, 10))

        # The log is packed first, at the bottom, so it keeps its few lines
        # and the cards get the rest of the window.
        self.log = LogPane(outer)
        self.log.text.configure(height=6)
        self.log.pack(side="bottom", fill="x", pady=(10, 0))
        scroll = ScrollFrame(outer)
        scroll.pack(fill="both", expand=True)
        body = scroll.body

        self.banner = Banner(body)
        self._build_mac_card(body)
        self._build_install_card(body)
        self._build_keep_card(body)

        later = Card(body, "Coming next")
        later.pack(fill="x", pady=(0, 12))
        ttk.Label(later.body, style="CardMuted.TLabel", wraplength=780,
                  justify="left",
                  text="Updating OpenCore and kexts on the installed EFI, and "
                       "post-install patches for hardware that needs them."
                  ).pack(anchor="w")

        self.root.after(100, self._drain)
        self.refresh()

    # -- layout ----------------------------------------------------------

    def _build_mac_card(self, body):
        card = Card(body, "This Mac")
        card.pack(fill="x", pady=(0, 12))
        self.facts = KeyValue(card.body)
        self.facts.pack(fill="x")
        ttk.Button(card.body, text="Refresh", style="Small.TButton",
                   command=self.refresh).pack(anchor="e", pady=(8, 0))

    def _build_install_card(self, body):
        card = Card(body, "1. Install OpenCore to this Mac's disk")
        card.pack(fill="x", pady=(0, 12))
        grid = ttk.Frame(card.body, style="Card.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)

        ttk.Label(grid, text="Copy from:", style="CardMuted.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=3)
        self.source_var = tk.StringVar()
        self.source_box = ttk.Combobox(grid, textvariable=self.source_var,
                                       state="readonly")
        self.source_box.grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Button(grid, text="Choose folder...", style="Small.TButton",
                   command=self._choose_source).grid(row=0, column=2,
                                                     padx=(8, 0))

        ttk.Label(grid, text="Install to:", style="CardMuted.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=3)
        self.target_var = tk.StringVar()
        self.target_box = ttk.Combobox(grid, textvariable=self.target_var,
                                       state="readonly")
        self.target_box.grid(row=1, column=1, columnspan=2, sticky="ew",
                             pady=3)

        self.launcher_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            card.body, variable=self.launcher_var, style="Card.TCheckbutton",
            text="Add OpenCore to the firmware boot menu "
                 "(sets LauncherOption to Full)").pack(anchor="w",
                                                       pady=(8, 0))
        ttk.Label(card.body, style="CardMuted.TLabel", wraplength=780,
                  justify="left",
                  text="The current EFI folder is backed up first. Only "
                       "EFI/OC and EFI/BOOT are replaced; everything else on "
                       "the partition, including Windows' EFI/Microsoft, is "
                       "left alone. If a copy fails, the previous files are "
                       "put back.").pack(anchor="w", pady=(6, 0))

        row = ttk.Frame(card.body, style="Card.TFrame")
        row.pack(fill="x", pady=(10, 0))
        self.install_button = ttk.Button(row, text="Install OpenCore",
                                         style="Accent.TButton",
                                         command=self._install)
        self.install_button.pack(side="right")
        ttk.Button(row, text="Show backups",
                   command=self._show_backups).pack(side="right", padx=(0, 8))

    def _build_keep_card(self, body):
        card = Card(body, "2. Keep this app on the Mac")
        card.pack(fill="x", pady=(0, 12))
        self.keep_note = ttk.Label(card.body, style="CardMuted.TLabel",
                                   wraplength=780, justify="left", text="")
        self.keep_note.pack(anchor="w")
        self.keep_button = ttk.Button(card.body, text="Copy to Applications",
                                      command=self._copy_to_applications)
        self.keep_button.pack(anchor="e", pady=(8, 0))

    # -- reading the Mac -------------------------------------------------

    def refresh(self):
        self._work("Reading this Mac's disks", self._read, self._show)

    def _read(self):
        return system.Snapshot(), installer.find_sources()

    def _show(self, result):
        self.snapshot, found = result
        snap = self.snapshot
        chosen = [s for s in self.sources if s.kind == "chosen"]
        self.sources = found + chosen

        self.facts.set("macOS", snap.macos or "unknown")
        if snap.opencore_version:
            self.facts.set("OpenCore", snap.opencore_version, "good")
        else:
            self.facts.set("OpenCore", "not detected - this Mac may not have "
                                       "been started by OpenCore", "warn")
        booted = snap.booted
        if booted is None:
            self.facts.set("Started from", "unknown")
        elif booted.internal:
            self.facts.set("Started from", booted.label(), "good")
        else:
            self.facts.set("Started from", booted.label(), "warn")
        self.facts.set("macOS is on", ", ".join(
            "%s (%s)" % (snap.disk_names.get(d, d), d)
            for d in snap.system_disks) or "unknown")

        self.source_box.configure(values=[s.label for s in self.sources])
        if self.sources and self.source_var.get() not in [s.label for s in self.sources]:
            self.source_var.set(self.sources[0].label)
        self.targets = snap.targets
        self.target_box.configure(values=[t.label() for t in self.targets])
        if self.targets:
            self.target_var.set(self.targets[0].label())
        else:
            self.target_var.set("")

        if not self.targets:
            self.banner.show("No internal EFI partition was found, so there "
                             "is nowhere to install OpenCore.", "bad")
        elif booted is not None and booted.internal and booted.holds_system:
            self.banner.show("This Mac already started OpenCore from its own "
                             "disk. Installing again replaces that EFI with "
                             "the one chosen above.", "good")
        elif not self.sources:
            self.banner.show("No EFI folder was found on a mounted drive. "
                             "Plug in the OpCoreForge USB stick and press "
                             "Refresh, or choose the folder yourself.", "warn")
        else:
            self.banner.hide()
        self._show_keep()

    def _show_keep(self):
        bundle = selfcopy.bundle_path()
        if bundle is None:
            self.keep_note.configure(text="Running from source, so there is "
                                          "no app bundle to copy.")
            self.keep_button.state(["disabled"])
        elif selfcopy.is_installed(bundle):
            self.keep_note.configure(text="Installed in Applications.")
            self.keep_button.state(["disabled"])
        else:
            self.keep_note.configure(
                text="Running from %s. Copy it to Applications so it is still "
                     "here once the USB stick is unplugged." % bundle.parent)
            self.keep_button.state(["!disabled"])

    # -- actions ---------------------------------------------------------

    def _source(self):
        for source in self.sources:
            if source.label == self.source_var.get():
                return source
        return None

    def _target(self):
        for target in self.targets:
            if target.label() == self.target_var.get():
                return target
        return None

    def _choose_source(self):
        folder = filedialog.askdirectory(
            parent=self.root, title="Choose the EFI folder to install")
        if not folder:
            return
        source = installer.chosen_source(Path(folder))
        problems = installer.check_source(source.path)
        if problems:
            self.banner.show("%s cannot be installed: %s."
                             % (source.path, "; ".join(problems)), "bad")
            return
        self.sources = [s for s in self.sources if s.path != source.path]
        self.sources.append(source)
        self.source_box.configure(values=[s.label for s in self.sources])
        self.source_var.set(source.label)

    def _install(self):
        source, target = self._source(), self._target()
        if source is None or target is None:
            self.banner.show("Choose what to copy and where to install it.",
                             "warn")
            return
        extra = ""
        if source.kind == "media":
            extra = ("\n\nThis is the install stick's own EFI, which shows "
                     "macOS Recovery in the picker and waits 15 seconds. It "
                     "boots fine; an EFI built by OpCoreForge 1.4 or later "
                     "also carries the as-built copy, which is the better "
                     "choice.")
        if not messagebox.askyesno(
                "Install OpenCore?",
                "Copy OpenCore from\n\n    %s\n\nto the EFI partition\n\n"
                "    %s\n\nThe current EFI folder is backed up first.%s"
                % (source.path, target.label(), extra),
                parent=self.root, icon="warning", default="no"):
            return

        def work():
            return installer.install(source.path, target,
                                     launcher_full=self.launcher_var.get(),
                                     report=self.report)

        def done(backup):
            self.banner.show(
                "OpenCore is installed on %s. The previous EFI folder is "
                "backed up in %s.\n\nNext: shut down, unplug the USB stick, "
                "and start the machine from the internal disk - pick it in "
                "the firmware's boot menu (often F8, F11 or F12) the first "
                "time. %s" % (
                    target.device, backup,
                    "After that first start OpenCore adds its own entry to "
                    "the boot menu." if self.launcher_var.get() else ""),
                "good")
            self.refresh()

        self._work("Installing OpenCore", work, done)

    def _show_backups(self):
        folder = installer.backup_root()
        folder.mkdir(parents=True, exist_ok=True)
        subprocess.run(["/usr/bin/open", str(folder)])

    def _copy_to_applications(self):
        bundle = selfcopy.bundle_path()
        if bundle is None:
            return

        def done(target):
            self.keep_note.configure(text="Copied to %s." % target)
            self.keep_button.state(["disabled"])

        self._work("Copying to Applications",
                   lambda: selfcopy.copy_to_applications(bundle), done)

    # -- plumbing --------------------------------------------------------

    def report(self, message):
        """Thread-safe: add a line to the log."""
        self.queue.put(("log", message))

    def _work(self, title, function, on_done):
        if self.busy:
            return
        self.busy = True
        self.install_button.state(["disabled"])
        self.report("%s..." % title)

        def run():
            try:
                self.queue.put(("done", (on_done, function())))
            except installer.Cancelled as cancelled:
                self.queue.put(("cancelled", str(cancelled)))
            except Exception as error:
                self.queue.put(("error", error))

        threading.Thread(target=run, daemon=True).start()

    def _drain(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "log":
                    self.log.append(str(payload) + "\n")
                    continue
                self.busy = False
                self.install_button.state(["!disabled"])
                if kind == "done":
                    on_done, result = payload
                    on_done(result)
                elif kind == "cancelled":
                    self.log.append(payload + "\n")
                    self.banner.show(payload, "warn")
                else:
                    message = getattr(payload, "message", None) or str(payload)
                    hint = getattr(payload, "hint", "")
                    self.log.append("Error: %s\n" % payload, "red")
                    self.banner.show(message + (" " + hint.split("\n\n")[0]
                                                if hint else ""), "bad")
        except queue.Empty:
            pass
        self.root.after(100, self._drain)


def self_test() -> int:
    """Without a window: check the bundle loads, and read this Mac."""
    print("OpCoreForge Companion %s" % __version__)
    print("Python %s on %s" % (sys.version.split()[0], sys.platform))
    import tkinter
    print("Tk %s" % tkinter.Tcl().eval("info patchlevel"))
    if sys.platform != "darwin":
        print("Not macOS: disk checks skipped")
        return 0
    snap = system.Snapshot()
    print("macOS: %s" % (snap.macos or "unknown"))
    print("OpenCore: %s" % (snap.opencore_version or "not detected"))
    print("System disk(s): %s" % (", ".join(snap.system_disks) or "none found"))
    for part in snap.partitions:
        print("EFI partition: %s" % part.label())
    for source in installer.find_sources():
        print("Source: %s" % source.label)
    if not snap.system_disks:
        print("FAIL: could not work out which disk macOS is on")
        return 1
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--help" in argv or "-h" in argv:
        print(USAGE)
        return 0
    if "--version" in argv:
        print("%s %s" % (TITLE, __version__))
        return 0
    if "--self-test" in argv:
        return self_test()

    root = tk.Tk()
    if sys.platform != "darwin":
        root.withdraw()
        messagebox.showerror(
            TITLE, "The companion runs on macOS, on the machine OpCoreForge "
                   "built the EFI for. On Windows, use OpCoreForge itself.")
        return 1
    CompanionApp(root)
    root.mainloop()
    return 0
