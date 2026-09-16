"""
The session log: everything that would help someone else diagnose a run.

Two things are wanted from a bug report, and they pull in opposite directions.
A log has to be there *before* the failure -- switching it on afterwards is too
late, and asking someone to reproduce a crash with logging enabled costs them a
whole run. But a file that is always being written is one more thing happening
on the user's disk without them asking.

So the history is always kept in memory, cheaply and with a ceiling, and the
file is optional. "Save diagnostics" therefore works retrospectively: the crash
that just happened is already captured, whether or not logging to disk was on.

What goes in: the console output of all three tools, every stage transition,
every traceback, and a header describing the build and the machine -- version,
OS, Python, the data folder, the clock. Most reports that arrive without that
header need a round of questions before anything can be looked at.
"""

from __future__ import annotations

import datetime
import os
import platform
import sys
import threading
from pathlib import Path

# Roughly a megabyte of text. Long enough to hold a whole session including a
# few kext downloads; small enough that it is never worth thinking about.
DEFAULT_LIMIT = 1_000_000


class SessionLog:
    """Records a run. Safe to call from any thread."""

    def __init__(self, paths, limit: int = DEFAULT_LIMIT):
        self.paths = paths
        self.limit = limit
        self.started = datetime.datetime.now()
        self.path: Path | None = None
        self._handle = None
        self._buffer: list[str] = []
        self._size = 0
        self._lock = threading.Lock()
        self._context = {}

    # -- recording --------------------------------------------------------

    def write(self, text: str) -> None:
        """Console output, verbatim."""
        if not text:
            return
        self._append(text)

    def note(self, kind: str, text: str = "") -> None:
        """A structured event: a stage starting, an error, a user action."""
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        line = "\n[%s] %s%s\n" % (stamp, kind, (": " + text) if text else "")
        self._append(line)

    def context(self, **values) -> None:
        """Facts worth repeating in the header of any report saved later."""
        with self._lock:
            self._context.update({k: v for k, v in values.items()
                                  if v is not None})

    def _append(self, text: str) -> None:
        with self._lock:
            self._buffer.append(text)
            self._size += len(text)
            # Drop from the front rather than stopping at the ceiling: the end
            # of a session is where the failure is.
            while self._size > self.limit and len(self._buffer) > 1:
                self._size -= len(self._buffer.pop(0))
            handle = self._handle
        if handle is not None:
            try:
                handle.write(text)
                handle.flush()
            except Exception:
                # A log that cannot be written must never take the app down.
                with self._lock:
                    self._handle = None

    # -- the file ---------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._handle is not None

    def enable(self) -> Path | None:
        """Start mirroring to disk, backfilling everything recorded so far."""
        if self.enabled:
            return self.path
        try:
            self.paths.logs.mkdir(parents=True, exist_ok=True)
            path = self.paths.logs / (
                "session-%s.log" % self.started.strftime("%Y%m%d-%H%M%S"))
            handle = open(path, "a", encoding="utf-8", errors="replace")
            with self._lock:
                backlog = "".join(self._buffer)
                self._handle = handle
                self.path = path
            handle.write(self.header())
            handle.write(backlog)
            handle.flush()
            self.note("logging enabled", str(path))
            return path
        except Exception:
            return None

    def disable(self) -> None:
        with self._lock:
            handle, self._handle = self._handle, None
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass

    def save_report(self, destination: Path | None = None) -> Path:
        """Write everything recorded so far to a file and return its path.

        Works whether or not logging to disk was ever switched on, which is
        the point: by the time someone wants a log, the interesting part has
        already happened.
        """
        self.paths.logs.mkdir(parents=True, exist_ok=True)
        if destination is None:
            destination = self.paths.logs / (
                "diagnostics-%s.log"
                % datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        with self._lock:
            body = "".join(self._buffer)
        Path(destination).write_text(self.header() + body,
                                     encoding="utf-8", errors="replace")
        return Path(destination)

    # -- the header -------------------------------------------------------

    def header(self) -> str:
        from .version import __released__, __version__

        now = datetime.datetime.now()
        lines = [
            "=" * 72,
            "OpCoreForge %s diagnostics" % __version__,
            "=" * 72,
            "  build released : %s" % __released__,
            "  session began  : %s" % self.started.strftime("%Y-%m-%d %H:%M:%S"),
            "  written        : %s" % now.strftime("%Y-%m-%d %H:%M:%S"),
            "  clock (UTC)    : %s" % datetime.datetime.now(
                datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "  frozen build   : %s" % bool(getattr(sys, "frozen", False)),
            "  executable     : %s" % sys.executable,
            "  python         : %s" % sys.version.split()[0],
            "  platform       : %s" % platform.platform(),
            "  machine        : %s" % platform.machine(),
            "  data folder    : %s" % getattr(self.paths, "data", "?"),
            "  bundle         : %s" % getattr(self.paths, "bundle", "?"),
        ]
        if getattr(self.paths, "using_fallback", False):
            lines.append("  note           : data folder fell back off the "
                         "application directory (not writable)")
        lines.append("  admin          : %s" % _is_admin())

        try:
            from . import seed as seed_module
            lines.append("  payload        : %s"
                         % (seed_module.seed_version(self.paths) or "none"))
        except Exception:
            pass

        with self._lock:
            context = dict(self._context)
        for key in sorted(context):
            lines.append("  %-14s : %s" % (key, context[key]))

        try:
            from . import netdiag
            failure = netdiag.last_failure()
            if failure:
                lines.append("  last net error : %s (%s)"
                             % (failure[1], failure[2]))
        except Exception:
            pass

        lines += ["=" * 72, ""]
        return "\n".join(lines)


def install_crash_handler(paths) -> "Path | None":
    """Catch the failures that leave no Python traceback.

    A segmentation fault or a Tcl panic kills the process outright: the log
    simply stops mid-session and there is nothing to go on -- which is exactly
    what a crash report from the field looked like. ``faulthandler`` writes the
    C-level stack of every thread to a file when that happens, so the next
    report names the call that died instead of the last thing that worked.

    The file stays open for the life of the process by design; that is what
    makes it usable from a signal handler.
    """
    try:
        import faulthandler

        paths.logs.mkdir(parents=True, exist_ok=True)
        target = paths.logs / "crash.log"
        handle = open(target, "a", encoding="utf-8", errors="replace")
        handle.write("\n=== %s : OpCoreForge started ===\n"
                     % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        handle.flush()
        faulthandler.enable(file=handle, all_threads=True)
        return target
    except Exception:
        return None


def is_elevated():
    """True/False if it can be determined, None if it cannot.

    On Windows this decides whether stage 1 can dump the ACPI tables and
    whether stage 7 sees every USB port, so it is worth saying out loud rather
    than letting both fail in their own confusing ways.
    """
    try:
        if os.name == "nt":
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        return os.geteuid() == 0
    except Exception:
        return None


def _is_admin() -> str:
    """Whether the process is elevated -- ACPI dumping needs it on Windows."""
    elevated = is_elevated()
    if elevated is None:
        return "unknown"
    if os.name != "nt":
        return "root" if elevated else "no"
    return str(elevated)
