"""Visual theme.

A single dark palette applied through ttk's ``clam`` theme, which is the only
built-in that lets element colours be restyled consistently on Windows.
ProperTree draws its own tree using colours from its settings, so the palette
here is also pushed into ProperTree's settings to keep the embedded editor from
looking like a different program.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

BG = "#16181d"
PANEL = "#1e2128"
PANEL_ALT = "#242832"
BORDER = "#333844"
TEXT = "#e7eaf0"
MUTED = "#98a1b3"
FAINT = "#6b7385"
ACCENT = "#4c8dff"
ACCENT_DIM = "#2f5fb0"
GREEN = "#4cc98a"
YELLOW = "#e3b341"
RED = "#f2685f"
CYAN = "#3fc7d4"
SELECT = "#2a3142"

ROW_ALT = "#1b1e25"

# ProperTree stores its tree colours in its own settings dictionary.
PROPERTREE_SETTINGS = {
    "alternating_color_1": "#191c22",
    "alternating_color_2": "#1e2128",
    "highlight_color": ACCENT,
    "background_color": PANEL,
    "invert_background_text_color": True,
    "invert_row1_text_color": True,
    "invert_row2_text_color": True,
    "invert_hl_text_color": False,
    "header_text_ignore_bg_color": False,
    "expand_all_items_on_open": True,
    "check_for_updates_at_startup": False,
}


class Fonts:
    def __init__(self, root: tk.Misc):
        base = tkfont.nametofont("TkDefaultFont")
        family = base.cget("family")
        for candidate in ("Segoe UI", "Inter", "DejaVu Sans", "Helvetica"):
            if candidate in tkfont.families(root):
                family = candidate
                break
        mono = "Courier"
        for candidate in ("Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Menlo"):
            if candidate in tkfont.families(root):
                mono = candidate
                break

        self.body = tkfont.Font(root=root, family=family, size=10)
        self.body_bold = tkfont.Font(root=root, family=family, size=10, weight="bold")
        self.small = tkfont.Font(root=root, family=family, size=9)
        self.h1 = tkfont.Font(root=root, family=family, size=16, weight="bold")
        self.h2 = tkfont.Font(root=root, family=family, size=12, weight="bold")
        self.mono = tkfont.Font(root=root, family=mono, size=9)
        self.mono_bold = tkfont.Font(root=root, family=mono, size=9, weight="bold")


#: populated by apply(); widgets read fonts from here
FONTS: "Fonts | None" = None


def apply(root: tk.Misc) -> Fonts:
    global FONTS
    fonts = FONTS = Fonts(root)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(background=BG)

    style.configure(".", background=PANEL, foreground=TEXT,
                    fieldbackground=PANEL_ALT, bordercolor=BORDER,
                    lightcolor=PANEL, darkcolor=PANEL, font=fonts.body)

    style.configure("TFrame", background=PANEL)
    style.configure("App.TFrame", background=BG)
    style.configure("Card.TFrame", background=PANEL_ALT, relief="flat")
    style.configure("Toolbar.TFrame", background=PANEL_ALT)

    style.configure("TLabel", background=PANEL, foreground=TEXT)
    style.configure("App.TLabel", background=BG, foreground=TEXT)
    style.configure("Card.TLabel", background=PANEL_ALT, foreground=TEXT)
    style.configure("H1.TLabel", background=BG, foreground=TEXT, font=fonts.h1)
    style.configure("H2.TLabel", background=PANEL, foreground=TEXT, font=fonts.h2)
    style.configure("Muted.TLabel", background=PANEL, foreground=MUTED,
                    font=fonts.small)
    style.configure("MutedApp.TLabel", background=BG, foreground=MUTED,
                    font=fonts.small)
    style.configure("CardMuted.TLabel", background=PANEL_ALT, foreground=MUTED,
                    font=fonts.small)
    style.configure("Good.TLabel", background=PANEL, foreground=GREEN)
    style.configure("Warn.TLabel", background=PANEL, foreground=YELLOW)
    style.configure("Bad.TLabel", background=PANEL, foreground=RED)
    style.configure("Status.TLabel", background=PANEL_ALT, foreground=MUTED,
                    font=fonts.small)

    style.configure("TButton", background=PANEL_ALT, foreground=TEXT,
                    bordercolor=BORDER, focuscolor=PANEL_ALT,
                    padding=(12, 6), relief="flat")
    style.map("TButton",
              background=[("pressed", SELECT), ("active", SELECT),
                          ("disabled", PANEL)],
              foreground=[("disabled", FAINT)])

    style.configure("Accent.TButton", background=ACCENT, foreground="#0b1020",
                    font=fonts.body_bold, padding=(16, 8))
    style.map("Accent.TButton",
              background=[("pressed", ACCENT_DIM), ("active", "#63a0ff"),
                          ("disabled", PANEL_ALT)],
              foreground=[("disabled", FAINT)])

    style.configure("Small.TButton", padding=(8, 3), font=fonts.small)

    style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(6, 6, 6, 0))
    style.configure("TNotebook.Tab", background=BG, foreground=MUTED,
                    padding=(14, 7), borderwidth=0, font=fonts.body)
    style.map("TNotebook.Tab",
              background=[("selected", PANEL), ("active", PANEL_ALT)],
              foreground=[("selected", TEXT), ("disabled", FAINT)],
              expand=[("selected", (0, 0, 0, 0))])

    style.configure("Treeview", background=PANEL_ALT, fieldbackground=PANEL_ALT,
                    foreground=TEXT, borderwidth=0, rowheight=22,
                    font=fonts.body)
    style.map("Treeview",
              background=[("selected", SELECT)],
              foreground=[("selected", TEXT)])
    style.configure("Treeview.Heading", background=PANEL, foreground=MUTED,
                    relief="flat", font=fonts.small, padding=(6, 5))
    style.map("Treeview.Heading", background=[("active", PANEL_ALT)])

    style.configure("Vertical.TScrollbar", background=PANEL_ALT,
                    troughcolor=PANEL, bordercolor=PANEL,
                    arrowcolor=MUTED, relief="flat", width=12)
    style.map("Vertical.TScrollbar", background=[("active", BORDER)])
    style.configure("Horizontal.TScrollbar", background=PANEL_ALT,
                    troughcolor=PANEL, bordercolor=PANEL,
                    arrowcolor=MUTED, relief="flat")

    style.configure("TProgressbar", background=ACCENT, troughcolor=PANEL,
                    bordercolor=PANEL, lightcolor=ACCENT, darkcolor=ACCENT)

    style.configure("TEntry", fieldbackground=PANEL_ALT, foreground=TEXT,
                    bordercolor=BORDER, insertcolor=TEXT, padding=5)
    style.configure("TCombobox", fieldbackground=PANEL_ALT, background=PANEL_ALT,
                    foreground=TEXT, arrowcolor=MUTED, padding=4)
    root.option_add("*TCombobox*Listbox.background", PANEL_ALT)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)

    style.configure("TCheckbutton", background=PANEL, foreground=TEXT,
                    indicatorcolor=PANEL_ALT, focuscolor=PANEL)
    style.map("TCheckbutton",
              indicatorcolor=[("selected", ACCENT)],
              background=[("active", PANEL)])
    style.configure("Card.TCheckbutton", background=PANEL_ALT, foreground=TEXT,
                    indicatorcolor=PANEL, focuscolor=PANEL_ALT)
    style.map("Card.TCheckbutton",
              indicatorcolor=[("selected", ACCENT)],
              background=[("active", PANEL_ALT)])

    style.configure("TRadiobutton", background=PANEL, foreground=TEXT,
                    focuscolor=PANEL)
    style.map("TRadiobutton", background=[("active", PANEL)])

    style.configure("TSeparator", background=BORDER)
    style.configure("TPanedwindow", background=BG)
    style.configure("Sash", gripcount=0, background=BORDER)

    return fonts


def set_windows_titlebar(window, dark=True):
    """Match the Windows title bar to the dark theme."""
    import os
    if os.name != "nt":
        return
    try:
        import ctypes
        window.update_idletasks()
        value = ctypes.c_int(1 if dark else 0)
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        for attribute in (20, 19):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass
