"""The Credits window, shared by OpCoreForge and the companion."""

from __future__ import annotations

import threading
import tkinter as tk
import webbrowser
from tkinter import ttk

from .. import credits, project
from ..version import __version__
from . import theme
from .widgets import Card, ScrollFrame

_open = None


def show(root, title="OpCoreForge"):
    """Open the credits, or raise them if they are already open."""
    global _open
    if _open is not None and _open.winfo_exists():
        _open.lift()
        _open.focus_force()
        return _open
    window = _open = tk.Toplevel(root)
    window.title("Credits")
    window.configure(background=theme.BG)
    window.geometry("640x620")
    window.transient(root)
    theme.set_windows_titlebar(window)

    outer = ttk.Frame(window, style="App.TFrame", padding=(18, 14, 18, 14))
    outer.pack(fill="both", expand=True)
    ttk.Label(outer, text="%s %s" % (title, __version__),
              style="H1.TLabel").pack(anchor="w")
    _link(outer, project.REPO_URL, style="App").pack(anchor="w", pady=(2, 10))

    scroll = ScrollFrame(outer)
    scroll.pack(fill="both", expand=True)
    body = scroll.body

    people = Card(body, "Contributors",
                  "Everyone who has committed to OpCoreForge, from GitHub.")
    people.pack(fill="x", pady=(0, 12))
    status = ttk.Label(people.body, text="Asking GitHub...",
                       style="CardMuted.TLabel")
    status.pack(anchor="w")
    rows = ttk.Frame(people.body, style="Card.TFrame")
    rows.pack(fill="x")

    built = Card(body, "Built on",
                 "OpCoreForge is integration code. The real work belongs to "
                 "these projects and their authors.")
    built.pack(fill="x", pady=(0, 12))
    for item in credits.UPSTREAM:
        block = ttk.Frame(built.body, style="Card.TFrame")
        block.pack(fill="x", pady=(0, 8))
        line = ttk.Frame(block, style="Card.TFrame")
        line.pack(fill="x")
        _link(line, item.url, text=item.name).pack(side="left")
        ttk.Label(line, text="  by %s%s" % (
            item.author, "  -  %s" % item.licence if item.licence else ""),
            style="CardMuted.TLabel").pack(side="left")
        ttk.Label(block, text=item.role, style="CardMuted.TLabel",
                  wraplength=540, justify="left").pack(anchor="w")

    ttk.Button(outer, text="Close", command=window.destroy).pack(
        anchor="e", pady=(10, 0))

    def fill(found):
        if not window.winfo_exists():
            return
        status.configure(text="%d contributor(s)" % len(found))
        for person in found:
            line = ttk.Frame(rows, style="Card.TFrame")
            line.pack(fill="x", pady=1)
            _link(line, person.url, text=person.login).pack(side="left")
            extra = []
            if person.contributions:
                extra.append("%d commit%s" % (
                    person.contributions,
                    "" if person.contributions == 1 else "s"))
            if person.note:
                extra.append(person.note)
            if extra:
                ttk.Label(line, text="  " + ", ".join(extra),
                          style="CardMuted.TLabel").pack(side="left")

    def fetch():
        found = credits.fetch_contributors()
        try:
            window.after(0, lambda: fill(found))
        except Exception:
            pass  # the window was closed before GitHub answered

    threading.Thread(target=fetch, daemon=True).start()
    return window


def _link(parent, url, text=None, style="Card"):
    background = theme.BG if style == "App" else theme.PANEL_ALT
    label = tk.Label(parent, text=text or url, foreground=theme.ACCENT,
                     background=background, cursor="hand2")
    if theme.FONTS:
        label.configure(font=theme.FONTS.body)
    label.bind("<Button-1>", lambda _e: webbrowser.open(url))
    return label
