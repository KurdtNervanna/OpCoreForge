"""
Telling "working" from "hung": progress, elapsed time and completion marks.

The report this answers: "I would click the Build ISO button, but I was never
given an option for save location or any indication that the process had
completed. A lot of times it is difficult to understand if a step has completed
or not."

Both halves were real. The ISO went to a fixed path in the data folder with no
dialog, and a step that copies 3 GB twice reported nothing at all between "..."
and the next thing the user happened to notice -- while the status bar said
"Ready" whether a stage had finished, been skipped, or never run.

So: every long operation now takes a ``progress(done, total)`` callback, the
status line carries a percentage and a clock, a finished stage says so with a
duration, and its tab gets a tick. The window code is checked here against
stand-in widgets -- no display, no Tk main loop -- and the byte-counting is
checked against real files.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("TERM_PROGRAM", "")

STDERR = sys.stderr
RESULTS = []


def check(label, got, expect):
    ok = expect(got) if callable(expect) else got == expect
    RESULTS.append(("PASS" if ok else "FAIL", label, got))
    return got


class FakeLabel:
    def __init__(self):
        self.text = ""

    def configure(self, **kw):
        if "text" in kw:
            self.text = kw["text"]


class FakeBar:
    """Enough ttk.Progressbar for the status bar's use of one."""

    def __init__(self):
        self.mode = "indeterminate"
        self.value = 0
        self.packed = False
        self.running = False

    def configure(self, **kw):
        self.mode = kw.get("mode", self.mode)
        self.value = kw.get("value", self.value)

    def cget(self, key):
        return {"mode": self.mode, "value": self.value}[key]

    def start(self, _interval=None):
        self.running = True

    def stop(self):
        self.running = False

    def pack(self, **kw):
        self.packed = True

    def pack_forget(self):
        self.packed = False


class FakeNotebook:
    def __init__(self, labels):
        self.labels = list(labels)

    def tab(self, index, **kw):
        if "text" in kw:
            self.labels[index] = kw["text"]
        return self.labels[index]


class FakeStage:
    def __init__(self, label):
        self.label = label


class FakePump:
    """Records what a worker thread handed to the main thread."""

    def __init__(self):
        self.posted = []

    def post(self, fn, *args):
        self.posted.append(args)
        fn(*args)


class FakeRoot:
    """after()/after_cancel() without an event loop: nothing ever fires."""

    def __init__(self):
        self.pending = {}
        self.next_id = 1

    def after(self, _ms, _fn):
        token = "after#%d" % self.next_id
        self.next_id += 1
        self.pending[token] = _fn
        return token

    def after_cancel(self, token):
        self.pending.pop(token, None)


def make_app(app_module):
    """An App-shaped stub carrying only what the status code touches."""
    app = app_module.App.__new__(app_module.App)
    app.root = FakeRoot()
    app.status_label = FakeLabel()
    app.spinner = FakeBar()
    app.stage_order = ["hardware", "macos", "smbios"]
    app.stages = {"hardware": FakeStage("1 - Hardware"),
                  "macos": FakeStage("2 - macOS"),
                  "smbios": FakeStage("3 - SMBIOS")}
    app.notebook = FakeNotebook([s.label for s in app.stages.values()])
    app._unlocked = {"hardware"}
    app._done = set()
    app._progress_last = 0.0
    app.pump = FakePump()
    app._stage_started = None
    app._status_text = ""
    app._status_tick = None
    app._percent = None
    return app


def main():
    import time

    from opcoreforge import fatimage, isoimage, media
    from opcoreforge.ui import app as app_module

    scratch = Path(tempfile.mkdtemp(prefix="ocf-progress-"))

    # -- the status line ----------------------------------------------------
    app = make_app(app_module)
    app.set_status("Building the ISO...")
    check("with nothing running the status is just the text",
          app.status_label.text, "Building the ISO...")

    app._busy_changed(True)
    check("starting a step shows the bar", app.spinner.packed, True)
    check("and it spins, because there is no percentage yet",
          (app.spinner.mode, app.spinner.running), ("indeterminate", True))
    check("and a tick is scheduled to redraw the clock",
          app._status_tick, lambda v: v is not None)

    app._stage_started = time.monotonic() - 75      # pretend it has been a while
    app.set_status("Building the ISO...")
    check("a slow step shows how long it has been going",
          app.status_label.text, lambda v: v.endswith("1:15"))

    app._set_progress(512, 2048)
    check("progress switches the bar to a real measure",
          (app.spinner.mode, app.spinner.value), ("determinate", 25))
    check("and the percentage is on the status line",
          app.status_label.text, lambda v: "25%" in v and "1:15" in v)
    app._set_progress(2048, 2048)
    check("finishing fills it", app.spinner.value, 100)
    app._set_progress(5000, 0)
    check("a total of zero is ignored rather than dividing by it",
          app.spinner.value, 100)

    app._busy_changed(False)
    check("the bar goes away when the step ends", app.spinner.packed, False)
    check("the clock stops", app._stage_started, None)
    check("and its tick is cancelled", app._status_tick, None)
    app.set_status("Ready")
    check("so the next message is plain again", app.status_label.text, "Ready")

    check("a short step reads in seconds", app_module._duration(9), "9s")
    check("a long one in minutes", app_module._duration(3 * 60 + 5), "3:05")

    # -- the flood a 3 GB copy would otherwise cause ------------------------
    # Every megabyte copied calls progress(). Left alone that is thousands of
    # hand-offs to the main thread for a bar with a hundred positions.
    app = make_app(app_module)
    app._busy_changed(True)
    for megabyte in range(1, 3001):
        app.progress(megabyte, 3000)
    check("a worker reporting every megabyte does not flood the window",
          len(app.pump.posted), lambda n: n < 50)
    check("but the final call always gets through",
          app.pump.posted[-1], (3000, 3000))
    check("so the bar really does reach the end", app.spinner.value, 100)

    # -- completion marks ---------------------------------------------------
    app = make_app(app_module)
    app.mark_done("hardware")
    check("a finished stage is ticked on its tab",
          app.notebook.labels[0], "%s 1 - Hardware" % app_module.DONE_MARK)
    app.mark_done("hardware")
    check("and is not ticked twice",
          app.notebook.labels[0], "%s 1 - Hardware" % app_module.DONE_MARK)
    check("stages that have not finished are untouched",
          app.notebook.labels[1], "2 - macOS")
    app.mark_done("nonexistent")
    check("an unknown stage is ignored rather than raising",
          "nonexistent" in app._done, False)

    app = make_app(app_module)
    app.unlock("smbios")
    check("unlocking a stage ticks the one before it",
          app.notebook.labels[1], "%s 2 - macOS" % app_module.DONE_MARK)
    check("but not the one just unlocked", app.notebook.labels[2], "3 - SMBIOS")
    app.unlock("smbios")
    check("unlocking again changes nothing", len(app._done), 1)

    # -- bytes, counted for real --------------------------------------------
    payload = scratch / "media"
    (payload / "EFI" / "BOOT").mkdir(parents=True)
    (payload / "EFI" / "BOOT" / "BOOTx64.efi").write_bytes(b"MZ" + b"\0" * 900)
    big = payload / "com.apple.recovery.boot"
    big.mkdir(parents=True)
    with (big / "BaseSystem.dmg").open("wb") as handle:
        handle.write(b"D" * (5 * 1024 * 1024 + 7))
    total_bytes = sum(f.stat().st_size for f in payload.rglob("*") if f.is_file())

    seen = []
    image = fatimage.FatImage("OPENCORE")
    image.add_tree(payload)
    image.write(scratch / "boot.img", progress=lambda d, t: seen.append((d, t)))
    check("the image writer reports progress", len(seen), lambda n: n > 3)
    check("starting at nothing", seen[0], (0, total_bytes))
    check("ending at everything", seen[-1], (total_bytes, total_bytes))
    check("never going backwards",
          all(b[0] >= a[0] for a, b in zip(seen, seen[1:])), True)
    check("and never past the total",
          max(d for d, _t in seen), lambda v: v <= total_bytes)

    # -- what an ISO will cost ----------------------------------------------
    size, image_size, peak = isoimage.space_needed(payload)
    check("the payload is measured, not guessed", size, total_bytes)
    check("the boot image is bigger than the payload",
          image_size > total_bytes, True)
    check("and the peak covers the image and the ISO at once",
          peak > image_size * 2, True)

    try:
        isoimage.check_space(scratch, peak)
        check("a drive with room does not object", True, True)
    except isoimage.NotEnoughSpace:
        check("a drive with room does not object", False, True)

    try:
        isoimage.check_space(scratch, 1 << 60)       # an exabyte
        check("a drive without room refuses before copying anything",
              False, True)
    except isoimage.NotEnoughSpace as refused:
        check("a drive without room refuses before copying anything",
              "needs about" in refused.message, True)
        check("and says what to do instead",
              "USB" in refused.hint, True)

    check("an unreadable drive is not treated as a full one",
          isoimage.check_space(scratch / "does-not-exist", 1 << 60), None)

    # -- the counting writer passes bytes through untouched ------------------
    marks = []
    out = scratch / "counted.bin"
    with out.open("wb") as handle:
        writer = isoimage._CountingWriter(
            handle, 10, lambda d, t: marks.append((d, t)), throttle=0)
        writer.write(b"12345")
        writer.write(b"67890")
        check("it forwards attributes to the real file",
              writer.tell(), 10)
    check("every byte arrives", out.read_bytes(), b"1234567890")
    check("and progress was reported as it went", marks, [(5, 10), (10, 10)])

    # -- staging the media --------------------------------------------------
    class FakePaths:
        def __init__(self, root):
            self.data = root
            self.results = root / "Results"

        @property
        def efi_dir(self):
            return self.results / "EFI"

    paths = FakePaths(scratch / "stage")
    shutil.copytree(payload / "EFI", paths.efi_dir)
    recovery = media.recovery_dir(paths)
    recovery.mkdir(parents=True, exist_ok=True)
    shutil.copy2(big / "BaseSystem.dmg", recovery / "BaseSystem.dmg")

    seen = []
    staged = media.stage_media(paths, progress=lambda d, t: seen.append((d, t)))
    check("staging reports progress too", len(seen), lambda n: n > 3)
    check("and finishes at the total", seen[-1][0], seen[-1][1])
    check("the recovery really is copied, not just counted",
          (staged / media.RECOVERY_DIR / "BaseSystem.dmg").stat().st_size,
          (5 * 1024 * 1024 + 7))
    check("and so is the EFI",
          (staged / "EFI" / "BOOT" / "BOOTx64.efi").read_bytes(),
          b"MZ" + b"\0" * 900)

    # -- flushing a stick is best effort, never a failure -------------------
    from opcoreforge import usbwriter
    check("flushing is a no-op away from Windows", usbwriter._flush("E:"), False)

    STDERR.write("\n")
    for status, label, value in RESULTS:
        text = str(value).replace("\n", " ")
        if len(text) > 32:
            text = text[:29] + "..."
        STDERR.write("%s  %-58s %s\n" % (status, label, text))
    failed = [r for r in RESULTS if r[0] == "FAIL"]
    STDERR.write("\n%d checks, %d failed\n" % (len(RESULTS), len(failed)))
    shutil.rmtree(scratch, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
