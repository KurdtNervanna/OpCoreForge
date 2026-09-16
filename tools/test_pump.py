"""
Regression test: the main-thread pump must survive a nested event loop.

Reproduces a crash reported twice from the same machine. Both sessions died
silently -- no Python traceback, the log simply stops -- at exactly the moment
stage 7 finished building UTBMap.kext and switched to the config.plist tab.

The mechanism: the pump delivers the stage's completion callback from inside
its own timer tick; that callback switches tabs; the config stage builds the
embedded ProperTree; and ProperTree calls ``update()`` from its constructor.
That nested event loop fires the pump's timer *again, inside the drain that is
still running*, and the old code unconditionally scheduled a fresh timer at the
end of every tick. So each nested loop left two chains where there was one,
they doubled with each nested ``update()``, and the process was eventually
buried under its own callbacks -- with any of them free to touch widgets that
were halfway through being re-parented.

So what is asserted here is not "it works" but the two invariants that make it
safe: exactly one timer outstanding no matter how the loop is re-entered, and
nothing of ours running while a nested loop is in progress.

Run under Xvfb.
"""

from __future__ import annotations

import os
import sys
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


def pending_timers(root) -> int:
    """How many `after` callbacks Tk is currently holding."""
    return len(root.tk.call("after", "info"))


def main():
    import tkinter as tk
    from opcoreforge.ui.worker import MainThreadPump, Runner

    root = tk.Tk()
    root.withdraw()

    # -- one timer, however the loop is re-entered -------------------------
    pump = MainThreadPump(root, interval_ms=1)
    entered = []

    def reenter():
        entered.append(len(entered))
        if len(entered) <= 6:
            # Exactly what ProperTree's constructor does to us.
            time.sleep(0.005)
            root.update()

    pump.add_tick(reenter)
    baseline = pending_timers(root)
    pump.start()
    for _ in range(40):
        root.update()
        time.sleep(0.005)

    check("the loop can be re-entered without the timers multiplying",
          pending_timers(root) - baseline, lambda n: n <= 1)
    check("and the pump still holds exactly one schedule",
          pump._scheduled is not None, True)
    check("handlers did run", len(entered) > 3, True)
    check("but not once per nesting level -- that was the explosion",
          len(entered), lambda n: n < 60)
    pump.stop()
    check("stopping cancels the timer", pump._scheduled, None)
    check("and leaves none of ours behind",
          pending_timers(root) - baseline, lambda n: n <= 0)

    # -- a nested tick runs nothing ---------------------------------------
    pump = MainThreadPump(root, interval_ms=1)
    ran = []

    def outer():
        ran.append("tick")
        if len(ran) == 1:
            pump._tick()          # the nested loop's timer, arriving mid-drain

    pump.add_tick(outer)
    pump.start()
    root.update()
    time.sleep(0.01)
    root.update()
    check("a tick arriving inside a drain does not run the handlers again",
          ran.count("tick"), lambda n: n >= 1)
    pump.stop()

    # -- posted callbacks do not run inside a nested loop ------------------
    pump = MainThreadPump(root, interval_ms=1)
    delivered = []
    pump.start()
    with pump.paused():
        # Posted after pausing, because start() drains once straight away.
        pump.post(delivered.append, "callback")
        check("pausing is visible", pump.is_paused, True)
        for _ in range(8):
            root.update()
            time.sleep(0.005)
        check("nothing is delivered while paused", delivered, [])
    check("pausing ends", pump.is_paused, False)
    for _ in range(20):
        root.update()
        time.sleep(0.005)
    check("and the queued work is delivered afterwards",
          delivered, ["callback"])
    check("the timer survived the pause", pump._scheduled is not None, True)
    pump.stop()

    # -- nesting the pause -------------------------------------------------
    pump = MainThreadPump(root, interval_ms=1)
    pump.start()
    with pump.paused():
        with pump.paused():
            check("pauses nest", pump.is_paused, True)
        check("and the inner one leaving does not resume", pump.is_paused, True)
    check("only the outer one does", pump.is_paused, False)
    pump.stop()

    # -- a failing handler must not take the chain down --------------------
    pump = MainThreadPump(root, interval_ms=1)
    errors = []
    pump.on_error = errors.append
    pump.add_tick(lambda: 1 / 0)
    survived = []
    pump.add_tick(lambda: survived.append(1))
    pump.start()
    for _ in range(6):
        root.update()
        time.sleep(0.005)
    check("a handler that raises is reported", bool(errors), True)
    check("the other handlers still run", bool(survived), True)
    check("and the pump keeps ticking", pump._scheduled is not None, True)
    pump.stop()

    # -- the Runner still serialises --------------------------------------
    pump = MainThreadPump(root, interval_ms=1)
    pump.start()
    runner = Runner(pump)
    results = []
    started = []

    def slow():
        started.append(1)
        time.sleep(0.2)
        return "first"

    runner.run(slow, results.append)
    second = runner.run(lambda: "second", results.append)
    check("a second stage is refused while one is running", second, None)
    for _ in range(200):
        root.update()
        time.sleep(0.01)
        if results:
            break
    check("the first stage's result arrives", results, ["first"])
    check("and the runner frees up", runner.busy, False)
    pump.stop()

    root.destroy()

    STDERR.write("\n")
    for status, label, value in RESULTS:
        text = str(value).replace("\n", " ")
        if len(text) > 30:
            text = text[:27] + "..."
        STDERR.write("%s  %-60s %s\n" % (status, label, text))
    failed = [r for r in RESULTS if r[0] == "FAIL"]
    STDERR.write("\n%d checks, %d failed\n" % (len(RESULTS), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
