"""
Tests the session log and the frozen-build lookup of utb_windows.py.

Two unrelated things, both about a build being diagnosable from the outside.

The log exists because a bug report that arrives as a screenshot of a dialog
costs a round of questions before anything can be looked at. Its one real
design decision is that the history is kept in memory whether or not logging to
disk is switched on, so "Save diagnostics" works *after* the crash -- which is
the only time anyone wants it. That is what most of these checks are about.

The utb_windows check is a regression test. Scanning USB ports from the
packaged executable failed with "utb_windows.py not found on sys.path": the
file is read as source (its last line constructs a map object and starts the
tool, so it cannot simply be imported), and a one-file build has no source tree
-- only bytecode in an archive. The fix ships the file as bundled data, so what
matters is that the lookup considers the bundle and not just sys.path.

No display and no network needed.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
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
    """Just enough of Paths for the log to work against a temp directory."""

    def __init__(self, root: Path):
        self.data = root
        self.bundle = root / "bundle"
        self.logs = root / "logs"
        self.state_file = root / "session.json"
        self.using_fallback = False
        self.logs.mkdir(parents=True, exist_ok=True)

    # Borrowed verbatim so the preference round-trip is the real one.
    from opcoreforge.paths import Paths as _Real
    read_state = _Real.read_state
    write_state = _Real.write_state
    del _Real


def main():
    from opcoreforge import logfile

    scratch = Path(tempfile.mkdtemp(prefix="ocf-diag-"))
    paths = FakePaths(scratch)

    # -- recording before anything is switched on -------------------------
    log = logfile.SessionLog(paths)
    log.write("downloading Lilu...\n")
    log.note("stage", "Detecting hardware")
    check("nothing is written to disk until asked",
          list(paths.logs.iterdir()), [])

    report = log.save_report()
    body = report.read_text()
    check("a report can be saved without logging ever being on",
          report.exists(), True)
    check("the report contains output from before the request",
          "downloading Lilu" in body, True)
    check("the report contains the events", "Detecting hardware" in body, True)

    # -- the header is what makes a report answerable ----------------------
    from opcoreforge.version import __version__
    for label, needle in (
        ("the version", __version__),
        ("whether it is a frozen build", "frozen build"),
        ("the Python version", "python"),
        ("the operating system", "platform"),
        ("the data folder", str(scratch)),
        ("the clock", "clock (UTC)"),
        ("whether it is running elevated", "admin"),
    ):
        check("the header carries %s" % label, needle in body, True)

    log.context(cpu="Intel(R) Core(TM) i7-3720QM", gpus="Intel HD 4000")
    body = log.save_report().read_text()
    check("the header carries the machine's hardware",
          "i7-3720QM" in body and "HD 4000" in body, True)
    check("context with a None value is ignored",
          (log.context(motherboard=None), "motherboard" in
           log.save_report().read_text())[1], False)

    # -- mirroring to disk -------------------------------------------------
    path = log.enable()
    check("enabling creates a log file", path is not None and path.exists(), True)
    check("it reports itself as enabled", log.enabled, True)
    check("the file is backfilled with what came before",
          "downloading Lilu" in path.read_text(), True)
    log.write("after enabling\n")
    check("later output reaches the file without a flush call",
          "after enabling" in path.read_text(), True)
    log.disable()
    check("it reports itself as disabled afterwards", log.enabled, False)
    log.write("after disabling\n")
    check("output stops reaching the file once disabled",
          "after disabling" in path.read_text(), False)
    check("but is still recorded for a later report",
          "after disabling" in log.save_report().read_text(), True)

    # -- the ceiling -------------------------------------------------------
    small = logfile.SessionLog(paths, limit=500)
    for index in range(400):
        small.write("line %d ------------------------------------\n" % index)
    kept = small.save_report().read_text()
    check("the buffer is bounded", len(kept) < 20000, True)
    check("the end of the session is what survives",
          "line 399" in kept, True)
    check("the start is what gets dropped", "line 0 " in kept, False)

    # -- a broken log must never take the app down -------------------------
    blocked = FakePaths(Path(tempfile.mkdtemp(prefix="ocf-diag-ro-")))
    blocked.logs = Path("/proc/nonexistent/logs")
    broken = logfile.SessionLog(blocked)
    check("an unwritable log folder is reported, not raised",
          broken.enable(), None)
    broken.write("still recording\n")
    check("recording continues in memory anyway",
          "still recording" in "".join(broken._buffer), True)

    # -- utb_windows.py in a frozen build ---------------------------------
    from opcoreforge import patches
    patches.bootstrap_sys_path()
    patches.apply_all()
    from opcoreforge.bridge import usbtoolbox

    found = usbtoolbox._windows_map_source()
    check("utb_windows.py is found from a checkout",
          found.name, "utb_windows.py")

    # With sys.path emptied of the vendor tree, only the bundle can supply it
    # -- which is exactly the frozen case.
    from opcoreforge import paths as paths_module
    real_path, real_bundle = sys.path, paths_module.bundle_dir
    staged = Path(tempfile.mkdtemp(prefix="ocf-bundle-"))
    (staged / "utb_windows.py").write_text(found.read_text(), encoding="utf-8")
    try:
        sys.path = [p for p in sys.path if "vendor" not in p]
        paths_module.bundle_dir = lambda: staged
        check("it is found in the bundle when sys.path cannot supply it",
              usbtoolbox._windows_map_source().parent, staged)
        paths_module.bundle_dir = lambda: staged / "empty"
        try:
            usbtoolbox._windows_map_source()
            RESULTS.append(("FAIL", "a build missing it says so plainly",
                            "no exception"))
        except ImportError as exc:
            check("a build missing it says so plainly",
                  "usb.json" in str(exc), True)
    finally:
        sys.path = real_path
        paths_module.bundle_dir = real_bundle

    # -- and the spec ships it --------------------------------------------
    spec = (ROOT / "OpCoreForge.spec").read_text()
    check("the PyInstaller spec bundles utb_windows.py",
          "utb_windows.py" in spec, True)
    check("the self-test checks for it",
          "_windows_map_source" in (ROOT / "src" / "opcoreforge" /
                                    "selftest.py").read_text(), True)

    # -- utb_scripts.utils chdir'ing into a directory that is not there ----
    # Its __init__ does os.chdir(dirname(realpath(__file__))) to look for
    # colors.json. In a one-file build that directory does not exist, so
    # building the USB map died with FileNotFoundError before doing anything.
    from opcoreforge import paths as paths_module
    from utb_scripts import utils as utb_utils

    resolved = paths_module.get()
    frozen_like = Path(tempfile.mkdtemp(prefix="ocf-mei-")) / "utb_scripts"
    saved_file, saved_cwd = utb_utils.__file__, os.getcwd()
    try:
        utb_utils.__file__ = str(frozen_like / "utils.py")
        try:
            utb_utils.Utils("probe")
            RESULTS.append(("FAIL", "the frozen layout really does break it",
                            "no exception"))
        except FileNotFoundError:
            check("the frozen layout really does break it", True, True)
        finally:
            os.chdir(saved_cwd)

        patches.patch_usbtoolbox(resolved)
        check("the patch points the module at a directory that exists",
              Path(utb_utils.__file__).parent.is_dir(), True)
        utb_utils.Utils("probe")
        check("constructing Utils works after the patch", True, True)
        check("and leaves the working directory where it found it",
              os.getcwd(), saved_cwd)
    finally:
        os.chdir(saved_cwd)
        if not Path(utb_utils.__file__).parent.is_dir():
            utb_utils.__file__ = saved_file

    # -- what a failed USB start tells the user ---------------------------
    from opcoreforge.bridge.usbtoolbox import (UsbUnavailable,
                                               _explain_start_failure)
    UsbUnavailableAlias = UsbUnavailable

    class FakeWmiError(Exception):
        pass

    FakeWmiError.__name__ = "x_wmi_uninitialised_thread"
    cases = {
        "COM not ready on this thread":
            (FakeWmiError("Unexpected COM Error"), "Restarting OpCoreForge"),
        "a file missing from the build":
            (FileNotFoundError(2, "The system cannot find the file specified"),
             "--self-test"),
        "WMI itself refusing":
            (RuntimeError("<x_wmi: Unexpected COM Error>"), "administrator"),
    }
    for label, (exc, needle) in cases.items():
        explained = _explain_start_failure(exc)
        check("%s is explained, not dumped" % label,
              isinstance(explained, UsbUnavailable), True)
        check("%s says what to do about it" % label,
              needle in explained.hint, True)
        check("%s keeps the original error for me" % label,
              type(exc).__name__ in explained.hint, True)

    # -- one retry with a fresh connection ---------------------------------
    from opcoreforge.bridge import usbtoolbox as usb_bridge

    class FlakyMap:
        """Fails the first scan the way a stale COM connection would."""
        settings = {"show_friendly_types": True}
        controllers_historical = []
        built = 0

        def __init__(self):
            FlakyMap.built += 1
            self.first = FlakyMap.built == 1

        def get_controllers(self):
            if self.first:
                raise RuntimeError(
                    "<x_wmi: Unexpected COM Error (-2147221020,...)>")
            self.controllers_historical = [{"ports": []}]

    controller = usb_bridge.UsbController(resolved)
    saved_offline = usb_bridge._offline_map_class
    usb_bridge._offline_map_class = lambda: FlakyMap
    try:
        FlakyMap.built = 0
        controller.discover()
        check("a stale COM connection is rebuilt rather than reported",
              FlakyMap.built, 2)
        check("and the scan then returns data",
              controller.map.controllers_historical, [{"ports": []}])

        class AlwaysBroken(FlakyMap):
            def __init__(self):
                AlwaysBroken.built += 1
                self.first = True

        usb_bridge._offline_map_class = lambda: AlwaysBroken
        AlwaysBroken.built = 0
        controller.map = None
        try:
            controller.discover()
            RESULTS.append(("FAIL", "a real COM failure is explained after "
                            "one retry", "no exception"))
        except UsbUnavailableAlias as reported:
            check("a real COM failure is explained after one retry",
                  "administrator" in reported.hint, True)
            check("and it is not retried forever", AlwaysBroken.built, 2)
    finally:
        usb_bridge._offline_map_class = saved_offline

    # -- COM initialisation on worker threads ------------------------------
    # wmi.WMI() needs COM ready on the calling thread, and every stage runs on
    # a new one. Verified against a stand-in, since there is no COM here.
    from opcoreforge.ui import worker as worker_module

    class FakePythoncom:
        # The real values, so choosing the wrong apartment shows up.
        COINIT_MULTITHREADED = 0
        COINIT_APARTMENTTHREADED = 2
        calls = []

        @staticmethod
        def CoInitializeEx(flags):
            FakePythoncom.calls.append(("init", flags,
                                        threading.current_thread().name))

        @staticmethod
        def CoUninitialize():
            FakePythoncom.calls.append(("uninit", None,
                                        threading.current_thread().name))

    saved_os, saved_module = worker_module.os, sys.modules.get("pythoncom")
    worker_module.os = type("os", (), {"name": "nt"})
    sys.modules["pythoncom"] = FakePythoncom
    worker_module._com_host_started.clear()
    try:
        release = worker_module._com_apartment()
        check("a worker thread joins the COM apartment",
              any(c[0] == "init" for c in FakePythoncom.calls), True)
        check("it joins the multi-threaded one, so the connection can be "
              "shared between stages",
              FakePythoncom.calls[0][1], FakePythoncom.COINIT_MULTITHREADED)
        check("and hands back the way to leave it", callable(release), True)
        release()
        check("leaving is recorded",
              any(c[0] == "uninit" for c in FakePythoncom.calls), True)

        for _ in range(20):
            if any(c[2] == "com-apartment" for c in FakePythoncom.calls):
                break
            time.sleep(0.05)
        check("something holds the apartment open beyond one worker",
              any(c[2] == "com-apartment" and c[0] == "init"
                  for c in FakePythoncom.calls), True)
        check("the holder never leaves it",
              any(c[2] == "com-apartment" and c[0] == "uninit"
                  for c in FakePythoncom.calls), False)

        before = len(FakePythoncom.calls)
        worker_module._com_apartment()
        check("the holder is started once, not per stage",
              sum(1 for c in FakePythoncom.calls[before:]
                  if c[2] == "com-apartment"), 0)
    finally:
        worker_module.os = saved_os
        if saved_module is None:
            sys.modules.pop("pythoncom", None)
        else:
            sys.modules["pythoncom"] = saved_module

    check("nothing is initialised off Windows",
          worker_module._com_apartment(), None)

    # -- the crash handler -------------------------------------------------
    # A segmentation fault or a Tcl panic leaves no Python traceback: the log
    # just stops, which is exactly what a report from the field looked like.
    import faulthandler

    crash_paths = FakePaths(Path(tempfile.mkdtemp(prefix="ocf-crash-")))
    was_enabled = faulthandler.is_enabled()
    target = logfile.install_crash_handler(crash_paths)
    check("a crash log is opened at startup",
          target is not None and target.exists(), True)
    check("and faulthandler is watching", faulthandler.is_enabled(), True)
    check("the crash log records that the app started",
          "OpCoreForge started" in target.read_text(), True)
    check("a second run appends rather than replaces",
          (logfile.install_crash_handler(crash_paths),
           target.read_text().count("OpCoreForge started"))[1], 2)
    if not was_enabled:
        faulthandler.disable()

    broken_paths = FakePaths(Path(tempfile.mkdtemp(prefix="ocf-crash-ro-")))
    broken_paths.logs = Path("/proc/nonexistent/logs")
    check("an unwritable crash log is not fatal either",
          logfile.install_crash_handler(broken_paths), None)

    # -- elevation ---------------------------------------------------------
    check("elevation is reported as a real answer, not a guess",
          logfile.is_elevated(), lambda v: v in (True, False, None))
    check("and it reaches the header",
          "admin" in logfile.SessionLog(paths).header(), True)

    from opcoreforge.ui.app import App
    check("the administrator prompt names what breaks without it",
          all(word in App.ELEVATION_TEXT
              for word in ("ACPI", "USB", "administrator")), True)
    check("and says nothing is lost by restarting",
          "nothing is lost" in App.ELEVATION_TEXT, True)

    # -- surviving a crash while the editor comes up -----------------------
    # Embedding ProperTree demotes its window with `wm forget`, and that has
    # faulted inside Tk on a real machine. A marker written before the attempt
    # and removed after it turns "crashes every time you reach stage 8" into
    # "opens in its own window instead", without needing the user to know why.
    from opcoreforge.ui.stages_config import ConfigStage

    class FakeApp:
        def __init__(self, where):
            self.paths = where
            self.session_log = logfile.SessionLog(where)

    editor_paths = FakePaths(Path(tempfile.mkdtemp(prefix="ocf-editor-")))
    stage = ConfigStage.__new__(ConfigStage)
    stage.app = FakeApp(editor_paths)
    stage._crashed_before = False

    check("a clean start has nothing to survey",
          stage.survey_last_attempt(), False)
    check("and embeds by default", stage._wants_embedding(), True)

    marker = stage._marker()
    marker.write_text("embed=True\n")
    check("a marker left behind is read as a crash",
          stage.survey_last_attempt(), True)
    check("which switches to a separate window",
          stage._wants_embedding(), False)
    check("and the marker is cleared, so it is a fallback and not a rut",
          marker.exists(), False)
    check("the reason is in the log for anyone reading it",
          "did not survive" in stage.app.session_log.save_report().read_text(),
          True)

    stage._crashed_before = False
    editor_paths.write_state(editor_windowed=True)
    check("a remembered preference is honoured too",
          stage._wants_embedding(), False)
    editor_paths.write_state(editor_windowed=False)
    check("and turning it back off restores embedding",
          stage._wants_embedding(), True)
    check("the preference survives being written and read",
          editor_paths.read_state().get("editor_windowed"), False)

    # With nothing remembered, Windows gets its own window: embedding demotes
    # ProperTree's window with wm forget, and that faulted inside Tk on a real
    # machine three times running. A separate window is ProperTree's own
    # supported configuration.
    from opcoreforge.ui import stages_config as config_module

    fresh = FakePaths(Path(tempfile.mkdtemp(prefix="ocf-default-")))
    clean = ConfigStage.__new__(ConfigStage)
    clean.app = FakeApp(fresh)
    clean._crashed_before = False
    saved_os = config_module.os
    try:
        config_module.os = type("os", (), {"name": "nt"})
        check("Windows defaults to the editor's own window",
              clean._wants_embedding(), False)
        config_module.os = type("os", (), {"name": "posix"})
        check("elsewhere it still embeds", clean._wants_embedding(), True)
        # An explicit choice outranks the platform, in both directions.
        config_module.os = type("os", (), {"name": "nt"})
        fresh.write_state(editor_windowed=False)
        check("but asking for embedding on Windows is honoured",
              clean._wants_embedding(), True)
    finally:
        config_module.os = saved_os

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
