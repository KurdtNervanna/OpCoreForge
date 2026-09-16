"""
OpCoreForge main window.

One window, one ordered workflow, three upstream tools:

    stages 1-6   OpCore-Simplify   analyse hardware, choose macOS, build the EFI
    stage 7      USBToolBox        map the USB ports, install UTBMap.kext
    stage 8      ProperTree        OC Snapshot and edit config.plist
    stage 9                        what is left to do on the target machine
    stage 10                       the macOS install media to boot it from

The order is not cosmetic. OpCore-Simplify's own closing instructions are to go
away and run the other two tools afterwards, in that order, because the USB
kext has to exist before the snapshot that registers it in config.plist.
Stages stay locked until their inputs exist, which is what stops the two most
common ways of getting this wrong: snapshotting before the port map is
installed, and shipping an EFI that still contains the placeholder
UTBDefault.kext.
"""

from __future__ import annotations

import os
import sys
import time
import tkinter as tk
from tkinter import messagebox, ttk

from .. import logfile, patches, paths as paths_module
from ..bridge import console as console_bridge
from ..bridge.ocs import OcsController
from ..bridge.propertree import EmbeddedProperTree
from ..bridge.usbtoolbox import UsbController
from . import prompt as prompt_ui
from . import theme
from .stages import (AcpiStage, BuildStage, HardwareStage, KextStage,
                     MacOSStage, SmbiosStage)
from .stages_config import ConfigStage, FinishStage
from .stages_media import MediaStage
from .stages_usb import UsbStage
from .widgets import LogPane
from .worker import MainThreadPump, Runner

from ..version import __version__ as VERSION

APP_TITLE = "OpCoreForge"

USAGE = """OpCoreForge %s - OpCore-Simplify, USBToolBox and ProperTree in one program.

  OpCoreForge                      start the application
  OpCoreForge --data-dir PATH      keep working files somewhere specific
                                   (default: OpCoreForge_Data beside the .exe)
  OpCoreForge --log                start with logging to a file switched on
                                   (OpCoreForge_Data\\logs). The same switch is
                                   in the status bar, and "Save diagnostics..."
                                   writes a report whether or not it was on.
  OpCoreForge --self-test          verify this build is complete, then exit
  OpCoreForge --version            print the version and exit
  OpCoreForge --help               show this message
""" % VERSION

#: Put in front of a finished stage's tab label. Plain ASCII would do, but a
#: tick reads as "finished" without anyone having to learn what it means.
DONE_MARK = "✓"


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return "%ds" % seconds
    return "%d:%02d" % (seconds // 60, seconds % 60)

STAGE_CLASSES = [
    HardwareStage, MacOSStage, SmbiosStage, AcpiStage, KextStage,
    BuildStage, UsbStage, ConfigStage, FinishStage, MediaStage,
]


class App:
    def __init__(self, root: tk.Tk, paths, log_to_file=False):
        self.root = root
        self.paths = paths
        self.efi_changed = False
        self.usb_map_installed = False
        self._unlocked = {"hardware"}
        self._tab_pending = False
        # Progress and completion state. A step that takes minutes and says
        # nothing is indistinguishable from one that has died.
        self._done = set()
        self._stage_started = None
        self._status_text = "Starting..."
        self._status_tick = None
        self._percent = None
        self._progress_last = 0.0

        # Recording starts before anything else does, so a failure during
        # startup is in the report too.
        self.session_log = logfile.SessionLog(paths)
        self.session_log.note("session started", "OpCoreForge %s" % VERSION)
        crash_log = logfile.install_crash_handler(paths)
        if crash_log:
            self.session_log.context(crash_log=crash_log)
        self._editor_crashed_last_time = False

        # Every hand-off from a background thread to Tk goes through this one
        # pump; nothing else may touch Tk off the main thread.
        self.pump = MainThreadPump(root)
        self.bridge = console_bridge.ConsoleBridge()
        self.bridge.pump = self.pump
        self.ocs = OcsController(paths, self.bridge)
        self.usb = UsbController(paths)
        self.ptree = EmbeddedProperTree(paths)
        self.ptree.pump = self.pump
        self.runner = Runner(self.pump)
        self.runner.on_busy_changed = self._busy_changed

        self._build_ui()
        # Read this before anything else can write the marker again: it is the
        # only evidence that the previous run died bringing the editor up.
        self._editor_crashed_last_time = \
            self.stages["config"].survey_last_attempt()
        self._wire_bridge()
        if log_to_file:
            self.log_to_file.set(True)
            self._toggle_file_log()
        self._start_backend()

    # -- layout ----------------------------------------------------------

    def _build_ui(self):
        self.root.title("%s %s" % (APP_TITLE, VERSION))
        self.root.geometry("1280x820")
        self.root.minsize(1060, 680)
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        theme.set_windows_titlebar(self.root)

        top = ttk.Frame(self.root, style="App.TFrame", padding=(18, 14, 18, 0))
        top.pack(fill="x")
        ttk.Label(top, text=APP_TITLE, style="H1.TLabel").pack(side="left")
        ttk.Label(top, style="MutedApp.TLabel",
                  text="   OpCore-Simplify  +  USBToolBox  +  ProperTree"
                  ).pack(side="left", padx=(4, 0), pady=(8, 0))
        self.data_label = ttk.Label(top, style="MutedApp.TLabel", text="")
        self.data_label.pack(side="right", pady=(8, 0))

        # The status bar is packed BEFORE the notebook, and this order matters.
        # The packer hands out space in the order children were added, so a
        # tab whose content asks for more height than the window has -- the USB
        # map and the config.plist editor both do -- would take the whole
        # cavity and leave the status bar with no height at all. It simply
        # vanished, taking "Save diagnostics..." with it.
        bottom = ttk.Frame(self.root, style="App.TFrame")
        bottom.pack(fill="x", side="bottom")

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=12, pady=(10, 0))
        self.notebook.bind("<<NotebookTabChanged>>", self._tab_changed)

        self.stages = {}
        self.stage_order = []
        for index, cls in enumerate(STAGE_CLASSES):
            stage = cls(self, self.notebook)
            self.stages[cls.key] = stage
            self.stage_order.append(cls.key)
            self.notebook.add(stage, text=cls.label)
            if cls.key not in self._unlocked:
                self.notebook.tab(index, state="disabled")

        self.log_visible = tk.BooleanVar(value=False)
        self.log_frame = ttk.Frame(bottom, style="App.TFrame")
        self.log = LogPane(self.log_frame)
        self.log.pack(fill="both", expand=True, padx=12)

        status = self.status_bar = ttk.Frame(bottom, style="Toolbar.TFrame",
                                             padding=(12, 6))
        status.pack(fill="x", side="bottom")
        self.status_label = ttk.Label(status, text="Starting...",
                                      style="Status.TLabel")
        self.status_label.pack(side="left")
        self.spinner = ttk.Progressbar(status, mode="indeterminate", length=120)
        ttk.Checkbutton(status, text="Show tool output",
                        variable=self.log_visible,
                        command=self._toggle_log).pack(side="right")

        # Diagnostics. "Save" is deliberately not conditional on the checkbox:
        # by the time anyone wants a log, the failure has already happened, and
        # it is all still in memory.
        self.log_to_file = tk.BooleanVar(value=False)
        ttk.Button(status, text="Save diagnostics...",
                   command=self.save_diagnostics).pack(side="right", padx=(0, 12))
        ttk.Checkbutton(status, text="Log to file",
                        variable=self.log_to_file,
                        command=self._toggle_file_log).pack(side="right",
                                                            padx=(0, 12))

        self.fonts = theme.FONTS
        self.data_label.configure(text="Working folder: %s" % self.paths.describe())

    #: the most of the window the tool output may take
    LOG_SHARE = 0.30
    LOG_MIN_LINES = 4
    LOG_MAX_LINES = 10

    def _toggle_log(self):
        if self.log_visible.get():
            self._fit_log_height()
            self.log_frame.pack(fill="x", pady=(6, 0))
        else:
            self.log_frame.pack_forget()

    def _fit_log_height(self):
        """Never let the output pane take more than its share of the window.

        Ten lines is comfortable on a desktop and a third of the window on a
        laptop, and the stage above it then keeps enough room for its buttons
        instead of having them clipped off the bottom.
        """
        try:
            line = max(1, self.log.text.winfo_reqheight() //
                       max(1, int(self.log.text.cget("height"))))
            available = int(self.root.winfo_height() * self.LOG_SHARE)
            lines = max(self.LOG_MIN_LINES,
                        min(self.LOG_MAX_LINES, available // line))
            self.log.text.configure(height=lines)
        except Exception:
            pass

    # -- diagnostics ------------------------------------------------------

    def _toggle_file_log(self):
        if self.log_to_file.get():
            path = self.session_log.enable()
            if path is None:
                self.log_to_file.set(False)
                messagebox.showwarning(
                    "Log file",
                    "The log file could not be created in %s.\n\nEverything is "
                    "still being recorded in memory - \"Save diagnostics...\" "
                    "will write it out." % self.paths.logs,
                    parent=self.root)
                return
            self.set_status("Logging to %s" % path.name)
        else:
            self.session_log.note("logging to file stopped")
            self.session_log.disable()
            self.set_status("Ready")

    def save_diagnostics(self):
        """Write the whole session to a file and offer to open the folder."""
        try:
            path = self.session_log.save_report()
        except Exception as exc:
            messagebox.showerror("Diagnostics",
                                 "Could not write the report:\n\n%s" % exc,
                                 parent=self.root)
            return
        if messagebox.askyesno(
                "Diagnostics saved",
                "Saved to:\n\n%s\n\nIt holds this build's version, this PC's "
                "details and everything the tools printed this session - send "
                "that file with a bug report.\n\nOpen the folder now?"
                % path, parent=self.root):
            self.open_path(path.parent)

    # -- bridge wiring ---------------------------------------------------

    def _wire_bridge(self):
        console_bridge.install(self.bridge, self._utils_module())

        # The bridge posts these through the pump, so they already arrive on
        # the Tk thread.
        self.bridge.on_prompt = lambda item: prompt_ui.show(self.root, item)

        # Console output is *not* pushed per write. OpCore-Simplify's download
        # progress bar prints four times per chunk; turning each of those into
        # a UI callback made detection take minutes. Instead the worker buffers
        # and the pump drains once per tick, so a whole progress bar's worth of
        # output costs one widget update.
        self.pump.add_tick(self._flush_log)
        self.pump.on_error = self._pump_error
        self.pump.start()

        # "Press Enter to continue" carries no decision -- answering it
        # automatically avoids a stream of pointless dialogs, and the text is
        # still visible in the tool output pane.
        self.bridge.auto_answer(r"^\s*$|press enter", "")

    def _pump_error(self, detail):
        self.log.append(detail, "red")
        self.session_log.note("callback failed", "\n" + detail)

    def _flush_log(self):
        text = self.bridge.drain_output()
        if not text:
            return
        from ..bridge.console import parse_ansi, strip_ansi
        # The file gets the plain text: colour codes belong on a terminal, not
        # in something someone will open in Notepad.
        self.session_log.write(strip_ansi(text))
        for chunk, tag in parse_ansi(text):
            self.log.append(chunk, tag)

    def _utils_module(self):
        from ocs_scripts import utils
        return utils

    # -- startup ---------------------------------------------------------

    def _start_backend(self):
        from ..seed import ensure_seed

        def work():
            ensure_seed(self.paths, report=self._seed_progress)
            self.ocs.start()
            return True

        def done(_result):
            self.set_status("Ready")
            self.stages["hardware"].on_enter()
            self._warn_about_clock()
            # After the clock warning, because a wrong clock stops everything
            # and missing admin rights only stops two stages. Deferred so the
            # modal's own event loop is not nested inside the pump's drain,
            # which would freeze the tool output behind the dialog.
            self.root.after_idle(self._check_elevation)

        def failed(exc, detail):
            self.set_status("Startup failed")
            self.on_error(exc, detail)

        self.set_status("Preparing OpenCore payload...")
        self.runner.run(work, done, failed)

    ELEVATION_TEXT = (
        "OpCoreForge is not running as administrator.\n\n"
        "Two steps need it and will fail or come back incomplete without it:\n\n"
        "  •  Stage 1 dumps this PC's ACPI tables, which Windows only "
        "hands to an elevated process.\n"
        "  •  Stage 7 reads USB controller and port properties, and "
        "quietly reports fewer ports without it.\n\n"
        "Restart as administrator now? Nothing has been done yet, so nothing "
        "is lost.")

    def _check_elevation(self):
        """Say plainly when the two stages that need admin rights will not work.

        Left to itself this surfaces as an ACPI dump that produces nothing and
        a port list that is mysteriously short -- neither of which points at
        the cause.
        """
        if os.name != "nt" or logfile.is_elevated() is not False:
            return
        self.session_log.note("elevation", "not running as administrator")
        self.stages["hardware"].banner.show(
            "Not running as administrator. Stage 1 cannot dump the ACPI "
            "tables and stage 7 may miss USB ports. Close this and re-open it "
            "with right-click › Run as administrator.", "warn")
        if messagebox.askyesno("Administrator rights", self.ELEVATION_TEXT,
                               parent=self.root, icon="warning"):
            if self._relaunch_elevated():
                self.quit()
            else:
                messagebox.showinfo(
                    "Administrator rights",
                    "The restart was refused or cancelled, so this is still "
                    "running without administrator rights.\n\nYou can carry "
                    "on -- importing a Report.json and a usb.json captured "
                    "elsewhere both work fine -- or close it and use "
                    "right-click › Run as administrator.",
                    parent=self.root)

    def _relaunch_elevated(self) -> bool:
        """Ask Windows to start this program again, elevated. UAC decides."""
        try:
            import ctypes

            if getattr(sys, "frozen", False):
                target, arguments = sys.executable, sys.argv[1:]
            else:
                target, arguments = sys.executable, sys.argv
            quoted = " ".join('"%s"' % a for a in arguments)
            # Above 32 means it started; anything else is a Windows error code.
            result = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", target, quoted or None,
                str(self.paths.app), 1)
            return int(result) > 32
        except Exception as exc:
            self.session_log.note("elevation", "relaunch failed: %s" % exc)
            return False

    def _warn_about_clock(self):
        """Say so at once if the system date makes downloads impossible.

        A clock set before this build was released is proof the date is wrong,
        and every HTTPS download will fail on certificate validity until it is
        fixed. Catching it here costs nothing and saves the person watching a
        download retry three times and then blaming their connection.
        """
        from .. import netdiag
        from ..version import __released__

        warning = netdiag.preflight(__released__)
        if warning:
            self.stages["hardware"].banner.show(warning, "bad")

    def report(self, message):
        """Set the status line from any thread."""
        self.pump.post(self.set_status, message)

    _seed_progress = report

    # -- stage plumbing --------------------------------------------------

    def _tab_changed(self, _event=None):
        """Hand entering a stage to the idle queue rather than doing it here.

        This runs inside ttk::notebook's own tab-change event, while the C
        widget is still in the middle of switching. Stage entry is not light
        work -- stage 8 builds the whole embedded editor, which pumps the Tk
        event loop and can even change tabs again -- and doing that inside the
        notebook's own bookkeeping is how a re-entrant crash happens. Idle
        callbacks run once the notebook has finished, which is soon enough for
        anyone watching and much safer.
        """
        if self._tab_pending:
            return
        self._tab_pending = True
        try:
            self.root.after_idle(self._enter_current_tab)
        except Exception:
            self._tab_pending = False
            self._enter_current_tab()

    def _enter_current_tab(self):
        self._tab_pending = False
        try:
            current = self.notebook.index(self.notebook.select())
        except Exception:
            return
        key = self.stage_order[current]
        for other_key, stage in self.stages.items():
            if other_key != key and hasattr(stage, "on_leave"):
                try:
                    stage.on_leave()
                except Exception:
                    pass
        self.stages[key].on_enter()

    def unlock(self, key):
        if key in self._unlocked:
            return
        self._unlocked.add(key)
        index = self.stage_order.index(key)
        self.notebook.tab(index, state="normal")
        # Reaching a stage means the one before it is finished -- that is the
        # only condition under which anything unlocks -- so tick it.
        if index > 0:
            self.mark_done(self.stage_order[index - 1])

    def goto(self, key):
        self.unlock(key)
        self.notebook.select(self.stage_order.index(key))

    def refresh_stage(self, key):
        stage = self.stages.get(key)
        if stage and stage.built and hasattr(stage, "refresh"):
            try:
                stage.refresh()
            except Exception:
                pass

    def after_build(self):
        self.efi_changed = True
        self.refresh_stage("finish")

    def run_stage(self, status, work, on_done=None, on_error=None,
                  on_abort=None):
        """Run *work* off the Tk thread with a status message."""
        if self.runner.busy:
            messagebox.showinfo(
                "Busy", "Another step is still running. Wait for it to finish.",
                parent=self.root)
            return
        started = time.monotonic()
        self.set_status(status + "...")
        self.session_log.note("stage", status)

        def done(result):
            # Say what finished, not just "Ready": a banner can scroll away or
            # be missed, and "Ready" looks the same whether the step worked,
            # was skipped, or never ran.
            taken = time.monotonic() - started
            self.set_status("%s %s - finished in %s"
                            % (DONE_MARK, status, _duration(taken)))
            self.session_log.note("stage finished",
                                  "%s (%.1fs)" % (status, taken))
            if on_done:
                on_done(result)

        def failed(exc, detail):
            self.set_status("Ready")
            self.session_log.note("stage failed", "%s\n%s" % (status, detail))
            if on_error:
                on_error(exc, detail)
            else:
                self.on_error(exc, detail)

        def aborted(exc=None):
            fatal = exc is not None and getattr(exc, "fatal", False)
            self.set_status("Stopped" if fatal else "Cancelled")
            self.session_log.note(
                "stage stopped" if fatal else "stage cancelled",
                "%s%s" % (status, ": " + str(exc) if exc else ""))
            # Let the stage put the reason on screen first, so it is still
            # there once the dialog is dismissed rather than vanishing with it.
            if on_abort:
                on_abort(exc)
            if fatal:
                # OpCore-Simplify stopped itself: it printed the reason and
                # would have waited for the operator to read it. Show that,
                # rather than failing quietly.
                self.show_stopped(exc)

        self.runner.run(work, done, failed, aborted)

    # -- feedback --------------------------------------------------------

    def set_status(self, text):
        self._status_text = text
        self.status_label.configure(text=self._decorate_status(text))

    def _decorate_status(self, text) -> str:
        """The status line plus however long the current step has been going.

        Elapsed time is the cheapest way to tell "working" from "hung", and
        several steps here legitimately take minutes.
        """
        if self._stage_started is None:
            return text
        seconds = int(time.monotonic() - self._stage_started)
        if seconds < 2:
            return text
        clock = ("%d:%02d" % (seconds // 60, seconds % 60) if seconds >= 60
                 else "%ds" % seconds)
        if self._percent is not None:
            return "%s  %d%%  -  %s" % (text, self._percent, clock)
        return "%s  -  %s" % (text, clock)

    def _tick_status(self):
        """Redraw the elapsed time once a second while a step is running."""
        self._status_tick = None
        if self._stage_started is None:
            return
        self.status_label.configure(text=self._decorate_status(self._status_text))
        self._status_tick = self.root.after(1000, self._tick_status)

    #: how often a worker may push a progress update at the window
    PROGRESS_INTERVAL = 0.1

    def progress(self, done, total):
        """Report fractional progress from any thread.

        Throttled here rather than in the callers: the copy loops report every
        megabyte, which on a 3 GB recovery is three thousand hand-offs to the
        main thread for a bar that has a hundred positions. The last call
        always goes through, so it never stops short of the end.
        """
        now = time.monotonic()
        if done < total and now - self._progress_last < self.PROGRESS_INTERVAL:
            return
        self._progress_last = now
        self.pump.post(self._set_progress, done, total)

    def _set_progress(self, done, total):
        if not total or self._stage_started is None:
            return
        percent = max(0, min(100, int(done * 100 / total)))
        if percent == self._percent:
            return
        self._percent = percent
        try:
            if str(self.spinner.cget("mode")) != "determinate":
                self.spinner.stop()
                self.spinner.configure(mode="determinate", maximum=100)
            self.spinner.configure(value=percent)
        except Exception:
            pass
        self.status_label.configure(text=self._decorate_status(self._status_text))

    def _busy_changed(self, busy):
        if busy:
            self._stage_started = time.monotonic()
            self._percent = None
            self.spinner.configure(mode="indeterminate", value=0)
            self.spinner.pack(side="right", padx=(0, 12))
            self.spinner.start(12)
            if self._status_tick is None:
                self._status_tick = self.root.after(1000, self._tick_status)
        else:
            self.spinner.stop()
            self.spinner.pack_forget()
            self._stage_started = None
            self._percent = None
            if self._status_tick is not None:
                try:
                    self.root.after_cancel(self._status_tick)
                except Exception:
                    pass
                self._status_tick = None

    def mark_done(self, key):
        """Put a tick on a stage's tab so finished work is visible at a glance.

        The complaint this answers is a fair one: several steps end with a
        banner that scrolls out of sight, and nothing else on screen changes,
        so there is no way to tell a finished stage from an abandoned one.
        """
        if key in self._done or key not in self.stages:
            return
        self._done.add(key)
        try:
            index = self.stage_order.index(key)
            self.notebook.tab(index, text="%s %s" % (DONE_MARK,
                                                     self.stages[key].label))
        except Exception:
            self._done.discard(key)

    def ask_from_worker(self, title, message) -> bool:
        """Ask a yes/no question from a worker thread.

        Tk may only be touched from the main thread, so the dialog is scheduled
        there and the caller blocks on an Event until it is answered.
        """
        import threading
        answered = threading.Event()
        box = {"value": False}

        def ask():
            try:
                box["value"] = messagebox.askyesno(title, message,
                                                   parent=self.root)
            finally:
                answered.set()

        self.pump.post(ask)
        answered.wait()
        return box["value"]

    def show_stopped(self, exc):
        """Explain a self-inflicted stop from OpCore-Simplify."""
        screen = getattr(exc, "screen", "") or ""
        prompt_ui.show_message(
            self.root, "OpCore-Simplify stopped", screen,
            headline="This configuration cannot go any further. The tool's own "
                     "explanation is below.",
            kind="bad")

    def on_error(self, exc, detail):
        self.log.append("\n%s\n" % detail, "red")
        self.session_log.note("error", "\n" + detail)

        # A failure with a diagnosis attached is not a crash, and showing it as
        # one buries the one sentence that would fix it under a traceback.
        # Give it a readable dialog of its own instead.
        hint = getattr(exc, "hint", "")
        if hint:
            prompt_ui.show_message(
                self.root, getattr(exc, "title", "There is a problem"),
                hint, headline=getattr(exc, "message", None) or str(exc),
                kind="bad")
            return

        self.log_visible.set(True)
        self._toggle_log()
        messagebox.showerror(
            "Something went wrong",
            "%s\n\n%s\n\nThe full traceback is in the tool output pane. "
            "\"Save diagnostics...\" writes it, and everything else from this "
            "session, to a file you can send on."
            % (type(exc).__name__, exc),
            parent=self.root)

    def open_path(self, path):
        try:
            if os.name == "nt":
                os.startfile(str(path))
            elif sys.platform == "darwin":
                import subprocess
                subprocess.run(["open", str(path)], check=False)
            else:
                import subprocess
                subprocess.run(["xdg-open", str(path)], check=False)
        except Exception as exc:
            messagebox.showinfo("Folder", "%s\n\n(%s)" % (path, exc),
                                parent=self.root)

    def apply_editor_theme(self):
        """Push the application palette into ProperTree's own settings."""
        try:
            self.ptree.pt.settings.update(theme.PROPERTREE_SETTINGS)
            self.ptree.pt.update_settings()
        except Exception:
            pass

    # -- shutdown --------------------------------------------------------

    def quit(self):
        if self.ptree.ready and self.ptree.is_edited():
            answer = messagebox.askyesnocancel(
                "Unsaved changes",
                "config.plist has unsaved changes. Save before quitting?",
                parent=self.root)
            if answer is None:
                return
            if answer:
                try:
                    self.ptree.save()
                except Exception:
                    pass
        if self.runner.busy:
            if not messagebox.askyesno(
                    "Still working",
                    "A step is still running. Quit anyway?", parent=self.root):
                return
        self.pump.stop()
        self.bridge.abort()
        try:
            self.ptree.shutdown()
        except Exception:
            pass
        try:
            if self.usb.map is not None:
                self.usb.save()
        except Exception:
            pass
        self.root.destroy()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    if "--help" in argv or "-h" in argv:
        print(USAGE)
        return 0

    if "--version" in argv or "-V" in argv:
        print("OpCoreForge %s" % VERSION)
        return 0

    if "--self-test" in argv:
        from ..selftest import main as selftest_main
        return selftest_main()

    data_dir = None
    if "--data-dir" in argv:
        index = argv.index("--data-dir")
        if index + 1 < len(argv):
            data_dir = argv[index + 1]

    patches.bootstrap_sys_path()
    resolved = paths_module.init(data_dir)
    patches.apply_all(resolved)

    root = tk.Tk()
    theme.apply(root)
    App(root, resolved, log_to_file="--log" in argv)
    root.mainloop()
    return 0
