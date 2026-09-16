"""Isolates which part of the log path costs 42ms per write."""
import os
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("TERM_PROGRAM", "")

from opcoreforge.ui import theme  # noqa: E402
from opcoreforge.ui.widgets import LogPane  # noqa: E402

STDERR = sys.stderr
N = 1500


def timed(label, fn):
    start = time.time()
    fn()
    STDERR.write("  %-52s %7.2fs\n" % (label, time.time() - start))
    STDERR.flush()


def main():
    root = tk.Tk()
    theme.apply(root)
    root.geometry("900x400")
    pane = LogPane(root)
    pane.pack(fill="both", expand=True)
    root.update()

    # A. direct calls on the main thread, current implementation
    def direct():
        for _ in range(N):
            pane.append("x")
    timed("A  main thread, append() as written", direct)
    pane.clear()

    # B. same, but without see() and without the index() line count
    text = pane.text

    def bare():
        text.configure(state="normal")
        for _ in range(N):
            text.insert("end", "x")
        text.configure(state="disabled")
    timed("B  main thread, plain inserts (no see, no index)", bare)
    pane.clear()

    # C. cost of see("end") alone
    def with_see():
        text.configure(state="normal")
        for _ in range(N):
            text.insert("end", "x")
            text.see("end")
        text.configure(state="disabled")
    timed("C  main thread, inserts + see() each time", with_see)
    pane.clear()

    # D. cost of the index() line-count query alone
    def with_index():
        text.configure(state="normal")
        for _ in range(N):
            text.insert("end", "x")
            int(text.index("end-1c").split(".")[0])
        text.configure(state="disabled")
    timed("D  main thread, inserts + index() each time", with_index)
    pane.clear()

    # E. scheduled from the main thread via after(0)
    def scheduled_main():
        for _ in range(N):
            root.after(0, lambda: pane.append("x"))
        root.update()
    timed("E  after(0) from the MAIN thread, then update()", scheduled_main)
    pane.clear()

    # F. scheduled from a worker thread via after(0) - what the app does today
    def scheduled_worker():
        done = threading.Event()

        def worker():
            for _ in range(N):
                root.after(0, lambda: pane.append("x"))
            done.set()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        while not done.is_set():
            root.update()
            time.sleep(0.001)
        root.update()
    timed("F  after(0) from a WORKER thread (current app path)", scheduled_worker)
    pane.clear()

    # G. worker thread appends to a list; main thread flushes in batches
    def batched():
        buffer = []
        lock = threading.Lock()
        done = threading.Event()

        def worker():
            for _ in range(N):
                with lock:
                    buffer.append("x")
            done.set()

        def flush():
            with lock:
                if not buffer:
                    return
                chunk = "".join(buffer)
                del buffer[:]
            pane.append(chunk)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        while not done.is_set():
            flush()
            root.update()
            time.sleep(0.01)
        flush()
        root.update()
    timed("G  worker buffers, main thread batch-flushes (proposed)", batched)

    root.destroy()


if __name__ == "__main__":
    STDERR.write("\n%d writes each:\n" % N)
    main()
