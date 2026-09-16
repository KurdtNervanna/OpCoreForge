"""
ProperTree embedding.

ProperTree is a complete Tkinter application: it owns a Tk root, builds its own
window per document, installs application-wide keybindings and runs its own
mainloop.  OpCoreForge hosts it *inside a notebook tab* so config.plist editing
is part of the same window as the rest of the workflow, without losing any of
ProperTree's behaviour -- OC Snapshot, undo/redo, find & replace, the
Configuration.tex tooltips, type conversion and the right-click menus all
remain the upstream implementations.

Three things make that possible:

1. **A stand-in root.**  ProperTree packs its hex/base64 "Convert Window"
   widgets directly into its Tk root, so handing it our real application window
   would splatter those widgets across the UI.  Instead ``tkinter.Tk`` is
   briefly redirected to hand back a dedicated hidden ``Toplevel``.  ProperTree
   gets a root that behaves exactly as it expects (it withdraws it at startup
   and shows it on Ctrl+T), and our window stays ours.  ``bind_all`` still
   reaches the whole application, so the keyboard shortcuts keep working.

2. **``wm forget``.**  A document window is created as a child of the tab frame
   and then demoted from a top-level to an ordinary managed widget.  Tk supports
   this directly; the window keeps its widgets, bindings and state, it simply
   gets laid out by the packer instead of the window manager.  Every ``wm``
   method then raises, so the ones ProperTree calls are shimmed to no-ops.

3. **A redirected ``stackorder``.**  Every ProperTree menu command resolves its
   target with ``stackorder(...)[-1]``, and a forgotten window is invisible to
   ``wm stackorder``.  Overriding it to include the embedded window is what
   makes Ctrl+R and friends act on the document in the tab.

``ProperTree.__init__`` also ends by calling ``mainloop()``; that is suppressed
during construction so control returns to us.
"""

from __future__ import annotations

import os
import sys
import tkinter as tk


# wm methods that stop working once a window is demoted with `wm forget`.
_WM_NOOP = (
    "withdraw", "deiconify", "iconify", "geometry", "minsize", "maxsize",
    "resizable", "title", "protocol", "attributes", "wm_withdraw",
    "wm_deiconify", "wm_iconify", "wm_geometry", "wm_minsize", "wm_maxsize",
    "wm_resizable", "wm_title", "wm_protocol", "wm_attributes", "iconbitmap",
    "wm_iconbitmap", "overrideredirect", "wm_overrideredirect", "transient",
    "wm_transient",
)


def _make_embedded_class(plistwindow_module, container, on_close):
    """Build a PlistWindow subclass that lives inside *container*."""

    base = plistwindow_module.PlistWindow

    class EmbeddedPlistWindow(base):
        #: consumed by the first window created; later documents float normally
        _pending_container = container
        embedded = False

        def __init__(self, controller, root, **kw):
            target = EmbeddedPlistWindow._pending_container
            if target is not None:
                EmbeddedPlistWindow._pending_container = None
                base.__init__(self, controller, target, **kw)
                self._embed(target)
            else:
                base.__init__(self, controller, root, **kw)

        # -- embedding ---------------------------------------------------

        def _embed(self, container_widget):
            # Let __init__ finish as a real toplevel (it calls geometry(),
            # minsize(), title() and protocol()), then demote the window.
            self.update_idletasks()
            self.tk.call("wm", "forget", str(self))
            self.tk.call("pack", str(self), "-in", str(container_widget),
                         "-fill", "both", "-expand", "1")
            self.embedded = True

        def _noop(self, *a, **kw):
            return ""

        # -- the one call that faulted -----------------------------------

        def select(self, node, see=True, alternate=True):
            """As upstream, but without dispatching events mid-document-open.

            Upstream calls ``self._tree.update()`` between focusing a node and
            scrolling to it, to force the layout pass ``see()`` depends on.
            ``update()`` also *dispatches* -- focus, configure, expose -- and
            for a window demoted with ``wm forget`` that is where Tk faulted:

                File "plistwindow.py", line 4179 in select
                Windows fatal exception: access violation

            with no Python frame above it, three separate times. There is
            nothing to catch and nothing upstream to fix.
            ``update_idletasks()`` does the same layout pass and dispatches
            nothing, which is all this line was ever for. Only the embedded
            window is treated this way; a standalone one keeps upstream's.
            """
            if not getattr(self, "embedded", False):
                return base.select(self, node, see, alternate)
            self._tree.selection_set(node)
            self._tree.focus(node)
            self._tree.update_idletasks()
            if see:
                self._tree.see(node)
            if alternate:
                self.alternate_colors()

        def wm_state(self, *a, **kw):
            # stackorder() filters on this; an embedded window is never withdrawn.
            return "normal" if self.embedded else base.wm_state(self, *a, **kw)

        def state(self, *a, **kw):
            return self.wm_state(*a, **kw)

        def winfo_toplevel(self):
            return self

        # -- closing -----------------------------------------------------

        def close_window(self, event=None, check_saving=True, check_close=True):
            """Never destroy the editor -- OpCoreForge owns its lifetime.

            True whether it is embedded in the tab or standing in its own
            window: closing it has to leave something the stage can reopen,
            not a destroyed widget the toolbar still points at.
            """
            if check_saving and (self.saving or self.check_save() is None):
                return None
            if on_close:
                on_close(self)
                return True
            return base.close_window(self, event, check_saving, check_close)

    for name in _WM_NOOP:
        if hasattr(base, name):
            original = getattr(base, name)

            def shim(self, *a, _original=original, **kw):
                if getattr(self, "embedded", False):
                    return ""
                return _original(self, *a, **kw)

            setattr(EmbeddedPlistWindow, name, shim)

    return EmbeddedPlistWindow


def _make_root_class(real_root, created):
    """Hand ProperTree a private Toplevel in place of a second Tk root.

    This has to remain a *class*: ProperTree's ``stackorder`` does
    ``isinstance(widget, (tk.Toplevel, tk.Tk))``, so replacing ``tk.Tk`` with a
    plain factory function makes isinstance raise. Subclassing Tk and returning
    a Toplevel from ``__new__`` keeps ``tk.Tk`` a type while still handing back
    the window we want.
    """

    class PatchedTk(tk.Tk):
        def __new__(cls, *args, **kwargs):
            window = tk.Toplevel(real_root)
            window.title("ProperTree - Convert")
            window.withdraw()
            created.append(window)
            return window

    return PatchedTk


class EmbeddedProperTree:
    """Owns the ProperTree instance hosted in a tab."""

    def __init__(self, paths):
        self.paths = paths
        self.pt = None
        self.window = None
        self.pt_root = None
        #: False once the editor has been opened as its own window instead
        self.embed = True
        #: MainThreadPump, set by the app; keeps worker threads off Tk
        self.pump = None
        self._starting = False
        self._container = None
        self._on_dirty = None

    # -- construction ----------------------------------------------------

    def start(self, root: tk.Misc, container: tk.Misc, embed: bool = True):
        """Bring the editor up, in the tab or in a window of its own.

        ``embed`` False skips the ``wm forget`` demotion entirely and lets
        ProperTree open its document the way it does when run on its own. That
        is its supported configuration, so it is the safe harbour when
        embedding turns out not to work on a particular machine -- everything
        else here (OC Snapshot pointed at the built EFI, save, reload, the
        stage order) is identical either way.
        """
        import pt_app
        from pt_scripts import plistwindow
        from .. import patches

        # ProperTree's constructor pumps the event loop, so a caller can be
        # re-entered and ask for a second editor. There is only ever one.
        if self._starting:
            return self.pt
        if self.pt is not None:
            return self.pt
        self._starting = True

        self.embed = embed
        self._container = container if embed else None

        embedded_cls = _make_embedded_class(
            plistwindow, self._container, on_close=self._handle_close)

        created: list = []
        real_tk_class = tk.Tk
        real_mainloop = tk.mainloop
        real_plistwindow = plistwindow.PlistWindow
        real_app_plistwindow = getattr(pt_app, "plistwindow", None)

        tk.Tk = _make_root_class(root, created)
        tk.mainloop = lambda *a, **kw: None
        self._disable_self_update(pt_app)
        self._confine_titlebar_tinting(pt_app)
        plistwindow.PlistWindow = embedded_cls
        if real_app_plistwindow is not None:
            pt_app.plistwindow.PlistWindow = embedded_cls

        try:
            self.pt = pt_app.ProperTree([])
        finally:
            self._starting = False
            tk.Tk = real_tk_class
            tk.mainloop = real_mainloop
            # Leave PlistWindow patched: later documents must use the subclass
            # so isinstance checks and the close override still apply.
            self._real_plistwindow = real_plistwindow

        self.pt_root = created[0] if created else None
        self.window = self.pt.start_window
        self._install_overrides(embedded_cls)
        patches.load_propertree_settings(self.pt, self.paths)
        if not embed:
            self._dress_own_window(root)
        return self.pt

    def _dress_own_window(self, root):
        """Make the standalone editor read as part of OpCoreForge."""
        window = self.window
        try:
            window.title("OpCoreForge - config.plist")
            window.protocol("WM_DELETE_WINDOW", lambda: self.hide_window())
            window.transient(root)
            window.deiconify()
            window.lift()
        except Exception:
            pass

    def show_window(self):
        """Bring the standalone editor back to the front."""
        if self.window is None or self.embed:
            return False
        try:
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()
            return True
        except Exception:
            return False

    def hide_window(self):
        if self.window is None or self.embed:
            return
        try:
            self.window.withdraw()
        except Exception:
            pass

    def _install_overrides(self, embedded_cls):
        pt = self.pt
        embedded = self.window
        original_stackorder = type(pt).stackorder

        def stackorder(instance, root=None, include_defaults=False):
            """Make the embedded document visible to ProperTree's commands.

            ``wm stackorder`` cannot see a window that is no longer a
            top-level, so upstream's lookup would return nothing and every menu
            command would silently do nothing.
            """
            try:
                windows = original_stackorder(instance, root, include_defaults)
            except Exception:
                windows = []
            if embedded is not None and embedded not in windows:
                if include_defaults or isinstance(embedded, embedded_cls):
                    windows = list(windows) + [embedded]
            return windows

        pt.stackorder = stackorder.__get__(pt, type(pt))

        def check_close(instance_self=None, lift_last=True):
            # Upstream quits the application when the last document closes;
            # here the tab always holds one, so there is nothing to do.
            return None

        pt.check_close = check_close

        def quit(*args, **kwargs):
            # Ctrl+Q inside the editor must not take the application down.
            return None

        pt.quit = quit

        pt.settings["check_for_updates_at_startup"] = False
        try:
            pt.reset_update_button()
        except Exception:
            pass

        # ProperTree claims WM_DELETE_WINDOW on its root; that root is our
        # private Toplevel, so just make closing it hide the convert window.
        try:
            self.pt_root.protocol(
                "WM_DELETE_WINDOW", lambda: self.pt_root.withdraw())
        except Exception:
            pass

    def _handle_close(self, window):
        # Standing in its own window, "close" means put it away; the tab's
        # toolbar still drives it and the stage can bring it back.
        self.hide_window()
        if self._on_dirty:
            self._on_dirty()

    # -- replacements for ProperTree's self-update machinery ---------------

    def _disable_self_update(self, pt_app):
        """Remove ProperTree's self-updater before the instance is built.

        Upstream checks for updates by spawning
        ``sys.executable Scripts/update_check.py`` through ``multiprocessing``.
        Inside a one-file build ``sys.executable`` is *this* .exe, so that
        relaunches the entire application. It cannot be neutralised after
        construction because ``__init__`` reaches it synchronously (via
        check_open -> new_plist -> open_plist), so the class is patched first.
        OpCoreForge is versioned as a unit and updates ProperTree with it.
        """
        owner = self

        def check_for_updates(pt_self, user_initiated=False):
            if user_initiated:
                owner._update_unavailable()
            return None

        def get_latest_tex(pt_self):
            return owner._download_tex()

        pt_app.ProperTree.check_for_updates = check_for_updates
        pt_app.ProperTree.get_latest_tex = get_latest_tex

    @staticmethod
    def _confine_titlebar_tinting(pt_app):
        """Keep ProperTree's dark-titlebar tinting away from the embedded window.

        ``set_win_titlebar`` hands each window's handle to
        ``DwmSetWindowAttribute``. That API is defined for *top-level* windows;
        the embedded document is a child frame inside a tab, and passing its
        handle faults inside dwmapi -- an access violation, which upstream's
        bare ``except:`` cannot catch because it is not a Python exception. It
        killed the application outright the moment config.plist was opened:

            File "pt_app.py", line 699 in set_win_titlebar
            File "pt_app.py", line 1944 in open_plist_with_path
            Windows fatal exception: access violation

        Patched on the class rather than the instance, because
        ``check_dark_mode`` starts a 1.5-second timer from ``__init__`` that
        calls it again for as long as the program runs.

        The windows that really are top-level -- the converter, the settings
        window -- keep the feature. The embedded one has no titlebar to tint.
        """
        if getattr(pt_app.ProperTree, "_ocf_titlebar_confined", False):
            return
        original = pt_app.ProperTree.set_win_titlebar

        def set_win_titlebar(pt_self, windows=None, mode=None):
            if windows is None:
                try:
                    windows = pt_self.stackorder(pt_self.tk, include_defaults=True)
                except Exception:
                    return
            elif not isinstance(windows, (list, tuple)):
                windows = [windows]
            # "wm" means the window manager still owns it. An embedded window
            # reports whichever geometry manager holds it instead -- which is
            # exactly the distinction the Windows API cares about.
            top_level = []
            for window in windows:
                try:
                    if window.winfo_manager() == "wm":
                        top_level.append(window)
                except Exception:
                    continue
            if top_level:
                original(pt_self, top_level, mode)

        pt_app.ProperTree.set_win_titlebar = set_win_titlebar
        pt_app.ProperTree._ocf_titlebar_confined = True

    def _update_unavailable(self):
        from tkinter import messagebox
        messagebox.showinfo(
            "Updates",
            "ProperTree is bundled inside OpCoreForge and is updated with it, "
            "so it does not update itself.")

    def _download_tex(self):
        """Fetch Configuration.tex on a thread.

        Upstream shells out to a helper script through multiprocessing, which
        a one-file build cannot do. The file only needs to land at
        get_best_tex_path(); config.plist tooltips read it from disk on demand.
        """
        import threading
        from tkinter import messagebox

        target = self.paths.propertree / "Configuration.tex"
        button = getattr(self.pt, "tex_button", None)
        if button is not None:
            try:
                button.configure(state="disabled", text="Downloading...")
            except Exception:
                pass

        def finish(error=None):
            if button is not None:
                try:
                    self.pt.reset_tex_button()
                except Exception:
                    pass
            if error:
                messagebox.showerror("Could not download Configuration.tex", error)
            else:
                version = None
                try:
                    version = self.pt.get_tex_version(file_path=str(target))
                except Exception:
                    pass
                messagebox.showinfo(
                    "Updated Configuration.tex",
                    "Configuration.tex%s saved to:\n\n%s"
                    % (" (%s)" % version if version else "", target))

        def worker():
            try:
                import urllib.request
                target.parent.mkdir(parents=True, exist_ok=True)
                with urllib.request.urlopen(self.pt.tex_url, timeout=30) as response:
                    data = response.read()
                target.write_bytes(data)
                error = None
            except Exception as exc:
                error = str(exc)
            if self.pump is not None:
                self.pump.post(finish, error)
            elif self.pt_root is not None:
                self.pt_root.after(0, lambda: finish(error))

        threading.Thread(target=worker, daemon=True).start()

    # -- document operations ---------------------------------------------

    @property
    def ready(self) -> bool:
        return self.pt is not None and self.window is not None

    def open_path(self, path) -> bool:
        if not self.ready:
            return False
        path = str(path)
        if not os.path.exists(path):
            return False
        result = self.pt.open_plist_with_path(None, path, self.window)
        return result is not None

    def current_path(self):
        return getattr(self.window, "current_plist", None) if self.window else None

    def is_edited(self) -> bool:
        return bool(getattr(self.window, "edited", False)) if self.window else False

    def save(self):
        return self.pt.save_plist(None)

    def save_as(self):
        return self.pt.save_plist_as(None)

    def reload(self):
        return self.pt.reload_from_disk(None)

    def snapshot(self, clean: bool = False, oc_folder=None):
        """Run OC Snapshot, the step that syncs config.plist with EFI/OC.

        Standalone ProperTree has to ask which OC folder to scan. Inside
        OpCoreForge that folder is known -- it is the EFI that was just built --
        so the picker is answered automatically and the user simply gets a
        snapshot of the right thing. Passing no folder restores the prompt.
        """
        from pt_scripts import plistwindow

        if oc_folder is None:
            return self.pt.oc_snapshot(None, clean)

        original = plistwindow.fd.askdirectory
        plistwindow.fd.askdirectory = lambda *a, **kw: str(oc_folder)
        try:
            return self.pt.oc_snapshot(None, clean)
        finally:
            plistwindow.fd.askdirectory = original

    def toggle_find(self):
        return self.pt.hide_show_find(None)

    def toggle_type_pane(self):
        return self.pt.hide_show_type(None)

    def strip_comments(self):
        return self.pt.strip_comments(None)

    def strip_disabled(self):
        return self.pt.strip_disabled(None)

    def undo(self):
        return self.pt.undo(None)

    def redo(self):
        return self.pt.redo(None)

    def show_settings(self):
        return self.pt.show_window(self.pt.settings_window)

    def show_convert_window(self):
        return self.pt.show_window(self.pt_root)

    def expand_all(self):
        try:
            self.window.expand_all()
        except Exception:
            pass

    def collapse_all(self):
        try:
            self.window.collapse_all()
        except Exception:
            pass

    # -- shutdown ---------------------------------------------------------

    def shutdown(self):
        from .. import patches
        if self.pt is not None:
            patches.save_propertree_settings(self.pt, self.paths)
