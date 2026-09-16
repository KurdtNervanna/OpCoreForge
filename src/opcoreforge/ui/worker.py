"""
Background work that keeps the UI responsive.

OpCore-Simplify's stages download files, disassemble ACPI tables with iasl and
walk large plists -- all of which would freeze a Tk mainloop.  Each stage runs
on a worker thread; results and errors are marshalled back to the Tk thread
with ``after``.  The console bridge blocks its worker on an Event while a
prompt dialog is up, so the mainloop must genuinely stay free.
"""

from __future__ import annotations

import contextlib
import os
import queue
import threading
import traceback

from ..bridge.console import WorkflowAborted


_com_host_started = threading.Event()


def _keep_com_alive():
    """Hold the process's COM apartment open for as long as the app runs.

    A multi-threaded apartment exists only while at least one thread has it
    initialised. Worker threads come and go, so if the last one to finish
    uninitialised, the apartment would be torn down -- taking with it the WMI
    connection USBToolBox stores on its map object and reuses in a later
    stage, on a later thread.

    One sleeping daemon thread avoids that, and avoids putting the Tk thread
    into an apartment it did not ask for (``os.startfile`` and the file
    dialogs want a single-threaded one).
    """
    if _com_host_started.is_set() or os.name != "nt":
        return
    _com_host_started.set()

    def host():
        try:
            import pythoncom
            pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
        except Exception:
            return
        threading.Event().wait()      # never set: hold it until the process ends

    threading.Thread(target=host, name="com-apartment", daemon=True).start()


def _com_apartment():
    """Join this thread to the process-wide COM apartment, on Windows.

    USBToolBox reads PnP device properties through WMI, which is COM, and COM
    has to be initialised on whichever thread calls it. Every stage runs on a
    fresh thread, so without this the USB scan failed with
    ``x_wmi_uninitialised_thread`` -- a message that says nothing about
    threads to anyone reading it.

    Multi-threaded rather than the more usual single-threaded apartment,
    deliberately: the map object holds its WMI connection for the life of the
    session and later stages run on *different* threads, and only an MTA lets
    one thread's object be used from another without marshalling.

    Returns a callable to undo it, or None when there is nothing to undo.
    """
    if os.name != "nt":
        return None
    try:
        import pythoncom
    except Exception:
        return None
    _keep_com_alive()
    try:
        pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
    except Exception:
        # Already initialised in an incompatible mode, or unavailable. Either
        # way this is not worth failing a stage over.
        return None
    return pythoncom.CoUninitialize


class MainThreadPump:
    """Carries work from background threads onto the Tk thread.

    Tkinter is not thread-safe. The usual shortcut -- calling ``root.after()``
    from a worker -- reaches into the Tcl interpreter from a foreign thread; it
    appears to work, but every call has to be serialised against the main
    thread and the cost is enormous. Measured on the log path it was ~40ms per
    call, so a download progress bar (four stdout writes per tick, thousands of
    ticks) took *minutes* to render and made the whole app look hung.

    So no worker thread touches Tk at all. They push callables onto a plain
    ``queue.Queue``, and the main thread drains it on a timer. Handlers
    registered with ``add_tick`` run on every drain, which is where per-frame
    work like flushing buffered console output belongs -- one widget update per
    tick instead of one per byte.

    Two things guard against re-entrancy, and they are not theoretical.
    ProperTree pumps the Tk event loop from inside its own constructor, and it
    is constructed from a callback this pump delivered. That nested loop fires
    the timer again, *inside* the drain that is still running:

    - Exactly one timer is ever outstanding (``_scheduled``). Without that,
      each nested tick started a second chain, the chains doubled with every
      nested ``update()``, and the application died with no Python traceback.
    - ``paused`` stops callbacks from running at all during a nested loop, so
      log flushes and stage callbacks cannot touch widgets that are halfway
      through being re-parented.
    """

    def __init__(self, root, interval_ms: int = 30, budget: int = 400):
        self.root = root
        self.interval_ms = interval_ms
        self.budget = budget
        self.queue: "queue.Queue[tuple]" = queue.Queue()
        self._ticks = []
        self._running = False
        self._draining = False
        self._paused = 0
        self._scheduled = None
        self.on_error = None

    def post(self, fn, *args):
        """Schedule *fn* to run on the Tk thread. Safe from any thread."""
        self.queue.put((fn, args))

    def add_tick(self, fn):
        """Run *fn* on the Tk thread on every drain."""
        self._ticks.append(fn)

    def start(self):
        if not self._running:
            self._running = True
            self._tick()

    def stop(self):
        self._running = False
        self._cancel()

    @contextlib.contextmanager
    def paused(self):
        """Run nothing while the body executes, even if it pumps the loop.

        For work that re-enters the Tk event loop -- building the embedded
        editor is the one that matters -- where our callbacks running in the
        middle would touch half-constructed widgets.
        """
        self._paused += 1
        try:
            yield
        finally:
            self._paused = max(0, self._paused - 1)

    @property
    def is_paused(self) -> bool:
        return self._paused > 0

    def _cancel(self):
        if self._scheduled is not None:
            try:
                self.root.after_cancel(self._scheduled)
            except Exception:
                pass
            self._scheduled = None

    def _schedule(self):
        if self._running and self._scheduled is None:
            try:
                self._scheduled = self.root.after(self.interval_ms, self._tick)
            except Exception:
                self._running = False

    def _tick(self):
        self._scheduled = None
        if not self._running:
            return
        # Re-entered from a nested event loop, or deliberately held off. Do
        # nothing but keep the timer alive -- and do not start a second chain.
        if self._draining or self._paused:
            self._schedule()
            return
        self._draining = True
        try:
            # Bounded per tick so a flood of callbacks cannot starve redraws.
            for _ in range(self.budget):
                try:
                    fn, args = self.queue.get_nowait()
                except queue.Empty:
                    break
                self._safely(fn, args)
            for handler in self._ticks:
                self._safely(handler, ())
        finally:
            self._draining = False
            self._schedule()

    def _safely(self, fn, args):
        try:
            fn(*args)
        except Exception:
            if self.on_error:
                try:
                    self.on_error(traceback.format_exc())
                except Exception:
                    pass


class Task:
    def __init__(self, pump, fn, on_done=None, on_error=None, on_abort=None):
        self.pump = pump
        self.fn = fn
        self.on_done = on_done
        self.on_error = on_error
        self.on_abort = on_abort
        self.thread = None
        self.error = None
        self.result = None

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def _run(self):
        release = _com_apartment()
        try:
            try:
                self.result = self.fn()
            except WorkflowAborted as aborted:
                # Carries the reason OpCore-Simplify stopped; the UI must show it.
                self._post(self.on_abort, aborted)
                return
            except Exception as exc:
                detail = traceback.format_exc()
                self._post(self.on_error, exc, detail)
                return
            # Note: the result is always passed as a single argument, so a
            # stage returning a tuple is not accidentally splatted into the
            # callback.
            self._post(self.on_done, self.result)
        finally:
            if release is not None:
                try:
                    release()
                except Exception:
                    pass

    def _post(self, callback, *args):
        if callback is None:
            return
        self.pump.post(callback, *args)


class Runner:
    """Serialises background stages so two cannot mutate state at once."""

    def __init__(self, pump):
        self.pump = pump
        self.root = pump.root
        self._busy = False
        self.on_busy_changed = None

    @property
    def busy(self) -> bool:
        return self._busy

    def run(self, fn, on_done=None, on_error=None, on_abort=None):
        if self._busy:
            return None
        self._set_busy(True)

        def done(result=None):
            self._set_busy(False)
            if on_done:
                on_done(result)

        def error(exc, detail):
            self._set_busy(False)
            if on_error:
                on_error(exc, detail)

        def abort(aborted=None):
            self._set_busy(False)
            if on_abort:
                on_abort(aborted)

        return Task(self.pump, fn, done, error, abort).start()

    def _set_busy(self, value):
        self._busy = value
        if self.on_busy_changed:
            self.on_busy_changed(value)
