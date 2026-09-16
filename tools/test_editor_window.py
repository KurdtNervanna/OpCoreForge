"""
The config.plist editor, opened as its own window instead of inside the tab.

This is the safe harbour. Embedding ProperTree means demoting its window with
``wm forget``, and on one machine that faulted inside Tk's own code -- an
access violation while the document was being opened, which no Python error
handling can catch because it is not a Python error. Twice, in two different
calls.

So the editor can also run the way ProperTree runs on its own: a real
top-level window, no demotion, nothing exotic. Everything that makes it part
of OpCoreForge is unchanged -- the same document, OC Snapshot still pointed at
the EFI stage 6 built, the same toolbar -- and the checks below are that this
is true rather than a stub.

Run under Xvfb.
"""

from __future__ import annotations

import os
import plistlib
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("TERM_PROGRAM", "")

from opcoreforge import patches, paths  # noqa: E402

patches.bootstrap_sys_path()
P = paths.init(ROOT / "_testdata_window")
patches.apply_all(P)

from opcoreforge.bridge.propertree import EmbeddedProperTree  # noqa: E402

STDERR = sys.stderr
RESULTS = []

sample = P.data / "config.plist"
plistlib.dump({
    "ACPI": {"Add": [], "Quirks": {"ResetLogoStatus": True}},
    "Kernel": {"Add": [{"BundlePath": "Lilu.kext", "Enabled": True}]},
    "PlatformInfo": {"Generic": {"SystemProductName": "iMacPro1,1"}},
    "UEFI": {"Drivers": []},
}, sample.open("wb"))


def check(label, fn, expect=None):
    try:
        value = fn()
    except Exception as exc:
        RESULTS.append(("FAIL", label, "%s: %s" % (type(exc).__name__, exc)))
        return
    ok = True if expect is None else (expect(value) if callable(expect)
                                      else value == expect)
    RESULTS.append(("PASS" if ok else "FAIL", label, value))


root = tk.Tk()
root.geometry("900x600")
host = ttk.Frame(root)
host.pack(fill="both", expand=True)
editor = EmbeddedProperTree(P)


def run():
    editor.start(root, host, embed=False)
    root.update()

    check("the editor comes up", lambda: editor.ready, True)
    check("it knows it is not embedded", lambda: editor.embed, False)
    check("its window is a real top-level",
          lambda: editor.window.winfo_manager(), "wm")
    check("it is not packed into the tab",
          lambda: editor.window.winfo_parent().startswith(str(host)), False)
    check("no wm shim is pretending", lambda: editor.window.embedded, False)

    check("the document opens", lambda: editor.open_path(sample), True)
    root.update()
    check("and is the file we asked for",
          lambda: os.path.normpath(editor.current_path()),
          os.path.normpath(str(sample)))
    check("the tree has content",
          lambda: len(editor.window._tree.get_children("")), 1)

    # The toolbar in the tab drives this window, so the commands have to
    # resolve to it exactly as they do when it is embedded.
    check("commands resolve to this window",
          lambda: editor.pt.stackorder(editor.pt.tk)[-1] is editor.window,
          True)
    check("expand all", lambda: editor.expand_all() or "ok", "ok")

    # The embedded window replaces select() to avoid dispatching events into
    # a window with no window manager. A standalone one has one, so it keeps
    # upstream's version -- including the update() this test would otherwise
    # never notice going missing.
    dispatched = []
    real_update = type(editor.window._tree).update
    try:
        type(editor.window._tree).update = \
            lambda self: dispatched.append("update")
        editor.window.select(editor.window.get_root_node())
        check("a standalone window still uses upstream's select",
              lambda: dispatched, ["update"])
    finally:
        type(editor.window._tree).update = real_update
    check("undo stack reachable",
          lambda: len(editor.window.undo_stack), lambda v: v >= 0)

    # Closing must put it away, not destroy it: the tab's toolbar still
    # points at it and the stage offers to bring it back.
    check("closing hides rather than destroys",
          lambda: (editor.window.close_window(check_saving=False),
                   editor.window.winfo_exists())[1], 1)
    root.update()
    check("and it really is hidden",
          lambda: editor.window.winfo_ismapped(), 0)
    check("bringing it back works", lambda: editor.show_window(), True)
    root.update()
    check("it is visible again",
          lambda: editor.window.winfo_ismapped(), 1)

    check("hiding it again", lambda: editor.hide_window() or "ok", "ok")
    root.update()

    # Windows titlebar tinting is legitimate here -- it really is a top-level.
    seen = []

    class FakeProperTree:
        def set_win_titlebar(self, windows=None, mode=None):
            seen.append(list(windows or []))

        def stackorder(self, root=None, include_defaults=False):
            return [editor.window]

    class FakeModule:
        ProperTree = FakeProperTree

    EmbeddedProperTree._confine_titlebar_tinting(FakeModule)
    fake = FakeProperTree()
    fake.tk = root
    fake.set_win_titlebar()
    check("a standalone editor window may be tinted",
          lambda: seen and editor.window in seen[-1], True)

    root.after(50, root.destroy)


root.after(300, run)
root.mainloop()

STDERR.write("\n")
for status, label, value in RESULTS:
    text = str(value).replace("\n", " ")
    if len(text) > 34:
        text = text[:31] + "..."
    STDERR.write("%s  %-46s %s\n" % (status, label, text))
failed = [r for r in RESULTS if r[0] == "FAIL"]
STDERR.write("\n%d checks, %d failed\n" % (len(RESULTS), len(failed)))
sys.exit(1 if failed else 0)
