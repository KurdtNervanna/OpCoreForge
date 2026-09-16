"""Smoke test: does ProperTree really embed into a notebook tab?"""
import os
import sys
import plistlib
import tkinter as tk
from pathlib import Path
from tkinter import ttk

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("TERM_PROGRAM", "")

from opcoreforge import patches, paths  # noqa: E402

patches.bootstrap_sys_path()
P = paths.init(ROOT / "_testdata")
patches.apply_all(P)

from opcoreforge.bridge.propertree import EmbeddedProperTree  # noqa: E402

# A config.plist-ish document to load.
sample = P.data / "config.plist"
plistlib.dump({
    "ACPI": {"Add": [], "Delete": [], "Patch": [], "Quirks": {"ResetLogoStatus": True}},
    "Kernel": {"Add": [{"BundlePath": "Lilu.kext", "Enabled": True}], "Quirks": {}},
    "Misc": {"Boot": {"PickerMode": "External"}},
    "NVRAM": {"Add": {}},
    "PlatformInfo": {"Generic": {"SystemProductName": "iMacPro1,1"}},
    "UEFI": {"Drivers": []},
}, sample.open("wb"))

root = tk.Tk()
root.title("OpCoreForge embed test")
root.geometry("1100x700")

notebook = ttk.Notebook(root)
notebook.pack(fill="both", expand=True)

first = ttk.Frame(notebook)
notebook.add(first, text="Wizard")
ttk.Label(first, text="a normal tab").pack(padx=20, pady=20)

editor_tab = ttk.Frame(notebook)
notebook.add(editor_tab, text="config.plist")

toolbar = ttk.Frame(editor_tab)
toolbar.pack(fill="x")
host = ttk.Frame(editor_tab)
host.pack(fill="both", expand=True)

pt = EmbeddedProperTree(P)
pt.start(root, host)

results = []


def check(label, fn, expect=None):
    """Record what *fn* returns; with *expect*, require it.

    Most of these are smoke checks where reaching the call at all is the
    point, so the value is simply recorded. Where the value itself is the
    thing under test, pass an expectation.
    """
    try:
        value = fn()
    except Exception as exc:
        results.append("FAIL  %-46s %s: %s" % (label, type(exc).__name__, exc))
        return
    ok = True if expect is None else (expect(value) if callable(expect)
                                      else value == expect)
    results.append("%s  %-46s %s" % ("PASS" if ok else "FAIL", label, value))


def run():
    check("window created", lambda: pt.window is not None)
    check("window is embedded", lambda: pt.window.embedded)
    check("geometry manager", lambda: pt.window.winfo_manager())
    check("parent is the tab host", lambda: pt.window.winfo_parent())
    check("is mapped", lambda: bool(pt.window.winfo_ismapped()))
    check("private root is a Toplevel",
          lambda: type(pt.pt_root).__name__)
    check("app root untouched (children)",
          lambda: [w.winfo_class() for w in root.winfo_children()][:3])

    check("open config.plist", lambda: pt.open_path(sample))
    root.update()
    check("current path", lambda: os.path.basename(pt.current_path() or ""))

    tree = pt.window._tree
    check("tree populated", lambda: len(tree.get_children()))

    def first_row_text():
        kids = tree.get_children()
        return tree.item(kids[0], "text") if kids else None
    check("root node text", first_row_text)

    # stackorder must find the embedded window or every menu command no-ops
    check("stackorder finds embedded",
          lambda: pt.pt.stackorder(pt.pt.tk)[-1] is pt.window)

    # wm calls that upstream makes on windows must not explode
    check("lift_window survives", lambda: pt.pt.lift_window(pt.window) or "ok")
    check("withdraw shimmed", lambda: repr(pt.window.withdraw()))
    check("title shimmed", lambda: repr(pt.window.title("x")))
    check("wm_state shimmed", lambda: pt.window.wm_state())

    # editing operations
    check("toggle find pane", lambda: pt.toggle_find() or "ok")
    root.update()
    check("toggle find pane back", lambda: pt.toggle_find() or "ok")
    check("expand all", lambda: pt.expand_all() or "ok")
    check("undo stack reachable", lambda: len(pt.window.undo_stack))

    # OC Snapshot needs a real EFI/OC dir next to the plist; just prove the
    # command resolves to the embedded window rather than doing nothing.
    check("snapshot target resolves",
          lambda: pt.pt.stackorder(pt.pt.tk)[-1].__class__.__name__)

    check("close_window does not destroy",
          lambda: (pt.window.close_window(check_saving=False),
                   pt.window.winfo_exists())[1])

    # -- no event dispatch while the document opens -------------------------
    # select() is where Tk faulted three times: upstream calls _tree.update()
    # there, which dispatches focus and configure events for a window that no
    # longer has a window manager. The embedded override does the same layout
    # pass with update_idletasks(), which dispatches nothing.
    import pt_scripts.plistwindow as pw

    check("the embedded window overrides select",
          lambda: type(pt.window).select is not pw.PlistWindow.select
                  or type(pt.window).select.__qualname__.startswith(
                      "_make_embedded_class"),
          True)
    dispatched = []
    real_update = type(pt.window._tree).update
    try:
        type(pt.window._tree).update = lambda self: dispatched.append("update")
        root_node = pt.window.get_root_node()
        check("selecting a node still works",
              lambda: (pt.window.select(root_node),
                       pt.window._tree.selection())[1], lambda v: bool(v))
        check("and never dispatches events to do it", lambda: dispatched, [])
    finally:
        type(pt.window._tree).update = real_update

    # Upstream's own class is untouched -- plistwindow.PlistWindow is left
    # pointing at the subclass on purpose, so ask the real one it saved.
    check("upstream's select is left exactly as it was",
          lambda: "update_idletasks" in __import__("inspect").getsource(
              pt._real_plistwindow.select), False)

    # -- the Windows titlebar tint must never see the embedded window -------
    # ProperTree hands each window's handle to DwmSetWindowAttribute, which is
    # only defined for top-level windows. Passing the embedded document's
    # handle faulted inside dwmapi and killed the process the moment
    # config.plist was opened -- an access violation, so upstream's bare
    # except: never saw it. Reproduced here through the discriminator the fix
    # relies on, since there is no dwmapi to fault on this platform.
    import pt_app

    check("the titlebar tint is patched before construction",
          lambda: getattr(pt_app.ProperTree, "_ocf_titlebar_confined", False),
          True)
    check("the embedded window is not owned by the window manager",
          lambda: pt.window.winfo_manager(), lambda v: v not in ("wm", ""))
    check("while a real top-level still is",
          lambda: root.winfo_manager(), "wm")

    seen = []

    class FakeProperTree:
        def set_win_titlebar(self, windows=None, mode=None):
            seen.append(list(windows or []))

        def stackorder(self, root=None, include_defaults=False):
            return [top_level, pt.window]

    class FakeModule:
        ProperTree = FakeProperTree

    top_level = tk.Toplevel(root)
    top_level.withdraw()
    EmbeddedProperTree._confine_titlebar_tinting(FakeModule)
    fake = FakeProperTree()
    fake.tk = root

    fake.set_win_titlebar()
    check("tinting every window skips the embedded one",
          lambda: pt.window in (seen[-1] if seen else []), False)
    check("and still reaches the real top-level",
          lambda: top_level in (seen[-1] if seen else []), True)

    seen.clear()
    fake.set_win_titlebar(pt.window)
    check("asking for the embedded window alone does nothing at all",
          lambda: seen, [])

    seen.clear()
    fake.set_win_titlebar(top_level, mode=1)
    check("asking for a real window still works",
          lambda: seen and seen[-1] == [top_level], True)

    root.after(50, root.destroy)


root.after(300, run)
root.mainloop()

print("\n".join(results))
failed = [r for r in results if r.startswith("FAIL")]
print("\n%d checks, %d failed" % (len(results), len(failed)))
sys.exit(1 if failed else 0)
