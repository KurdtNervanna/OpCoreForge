"""
Dialog for questions raised from inside OpCore-Simplify.

Whatever the underlying tool printed before it asked is shown verbatim (with
its ANSI colours preserved), and its options are turned into buttons where the
screen can be parsed as a menu.  A free-text field is always present, so a
prompt this code has never seen -- including one added by a future upstream
release -- is still answerable rather than being a dead end.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..bridge.console import parse_ansi, strip_trailing_prompt
from . import theme


class PromptDialog(tk.Toplevel):
    def __init__(self, parent, prompt):
        super().__init__(parent)
        self.prompt = prompt
        self.result = None

        self.title(prompt.title)
        self.configure(background=theme.PANEL)
        self.transient(parent)
        self.resizable(True, True)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text=prompt.title, style="H2.TLabel").pack(anchor="w")

        # Collapse the run of blank lines upstream prints to position its
        # terminal cursor; they are noise in a dialog.
        screen = "\n".join(
            line.rstrip() for line in (prompt.screen or "").splitlines()
        ).strip("\n")
        while "\n\n\n" in screen:
            screen = screen.replace("\n\n\n", "\n\n")

        if screen:
            wrapper = ttk.Frame(outer)
            wrapper.pack(fill="both", expand=True, pady=(10, 12))
            body = tk.Text(wrapper, background="#101318", foreground=theme.TEXT,
                           borderwidth=0, highlightthickness=0, wrap="word",
                           padx=12, pady=10,
                           height=max(4, min(20, screen.count("\n") + 2)))
            if theme.FONTS:
                body.configure(font=theme.FONTS.mono)
            for tag, color in (("bold", "#ffffff"), ("dim", theme.FAINT),
                               ("red", theme.RED), ("green", theme.GREEN),
                               ("yellow", theme.YELLOW), ("cyan", theme.CYAN)):
                body.tag_configure(tag, foreground=color)
            for chunk, tag in parse_ansi(screen):
                body.insert("end", chunk, (tag,) if tag else ())
            body.configure(state="disabled")

            scrollbar = ttk.Scrollbar(wrapper, orient="vertical",
                                      command=body.yview)
            body.configure(yscrollcommand=scrollbar.set)
            body.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")

        question = prompt.prompt.strip()
        if question:
            ttk.Label(outer, text=question, style="TLabel",
                      wraplength=720, justify="left").pack(anchor="w", pady=(0, 10))

        self.entry_var = tk.StringVar(value=prompt.default or "")
        buttons = ttk.Frame(outer)
        buttons.pack(fill="x")

        if prompt.is_acknowledge:
            self._acknowledge(buttons)
        else:
            self._choices(outer, buttons)

        self.bind("<Escape>", lambda _e: self._cancel())
        self.update_idletasks()
        self._center(parent)
        theme.set_windows_titlebar(self)
        self.grab_set()

    # -- layouts ---------------------------------------------------------

    def _acknowledge(self, buttons):
        ttk.Button(buttons, text="Continue", style="Accent.TButton",
                   command=lambda: self._answer("")).pack(side="right")
        self.bind("<Return>", lambda _e: self._answer(""))

    def _choices(self, outer, buttons):
        yes_no = self.prompt.yes_no
        numbered = self.prompt.numbered_choices()
        letters = [(v, l) for v, l in self.prompt.letter_choices()
                   if v.lower() not in ("q",)]

        if yes_no:
            ttk.Button(buttons, text="Yes", style="Accent.TButton",
                       command=lambda: self._answer("yes")).pack(side="right")
            ttk.Button(buttons, text="No",
                       command=lambda: self._answer("no")).pack(side="right", padx=(0, 8))
            return

        if numbered:
            grid = ttk.Frame(outer)
            grid.pack(fill="x", pady=(0, 12))
            default = self.prompt.default
            for index, (value, label) in enumerate(numbered):
                text = "%s. %s" % (value, label if len(label) < 70 else label[:67] + "...")
                is_default = default and (default == value or default in label)
                ttk.Button(grid, text=text,
                           style="Accent.TButton" if is_default else "TButton",
                           command=lambda v=value: self._answer(v)
                           ).grid(row=index, column=0, sticky="ew", pady=2)
            grid.columnconfigure(0, weight=1)

        for value, label in letters:
            ttk.Button(buttons, text="%s (%s)" % (label, value),
                       style="Small.TButton",
                       command=lambda v=value: self._answer(v)
                       ).pack(side="left", padx=(0, 6))

        row = ttk.Frame(outer)
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text="Answer:", style="Muted.TLabel").pack(side="left",
                                                                  padx=(0, 8))
        entry = ttk.Entry(row, textvariable=self.entry_var)
        entry.pack(side="left", fill="x", expand=True)
        entry.focus_set()
        entry.bind("<Return>", lambda _e: self._answer(self.entry_var.get()))
        ttk.Button(row, text="Send", style="Accent.TButton",
                   command=lambda: self._answer(self.entry_var.get())
                   ).pack(side="left", padx=(8, 0))

    def _center(self, parent):
        self.update_idletasks()
        width = min(max(self.winfo_reqwidth(), 560), 1000)
        height = min(max(self.winfo_reqheight(), 200), 760)
        try:
            x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
            y = parent.winfo_rooty() + (parent.winfo_height() - height) // 3
        except Exception:
            x = y = 120
        self.geometry("%dx%d+%d+%d" % (width, height, max(x, 0), max(y, 0)))

    # -- results ---------------------------------------------------------

    def _answer(self, value):
        self.result = value
        self.prompt.answer = value
        self.grab_release()
        self.destroy()
        self.prompt.event.set()

    def _cancel(self):
        self.prompt.aborted = True
        self.grab_release()
        self.destroy()
        self.prompt.event.set()


class MessageDialog(tk.Toplevel):
    """Shows captured console output as a message, with no question attached.

    Used when OpCore-Simplify stops the workflow itself. Those paths print the
    explanation and then exit, so the text is the whole point -- it has to end
    up in front of the user rather than only in the log pane.
    """

    def __init__(self, parent, title, screen, headline="", kind="bad"):
        super().__init__(parent)
        self.title(title)
        self.configure(background=theme.PANEL)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        accent = {"bad": theme.RED, "warn": theme.YELLOW,
                  "good": theme.GREEN, "info": theme.ACCENT}.get(kind, theme.RED)

        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)

        heading = ttk.Label(outer, text=title, style="H2.TLabel")
        heading.pack(anchor="w")
        heading.configure(foreground=accent)

        if headline:
            ttk.Label(outer, text=headline, style="TLabel", wraplength=720,
                      justify="left").pack(anchor="w", pady=(6, 0))

        screen = strip_trailing_prompt(screen)
        screen = "\n".join(line.rstrip() for line in
                           screen.splitlines()).strip("\n")
        while "\n\n\n" in screen:
            screen = screen.replace("\n\n\n", "\n\n")

        if screen:
            wrapper = ttk.Frame(outer)
            wrapper.pack(fill="both", expand=True, pady=(12, 12))
            body = tk.Text(wrapper, background="#101318", foreground=theme.TEXT,
                           borderwidth=0, highlightthickness=0, wrap="word",
                           padx=12, pady=10,
                           height=max(4, min(20, screen.count("\n") + 2)))
            if theme.FONTS:
                body.configure(font=theme.FONTS.mono)
            for tag, color in (("bold", "#ffffff"), ("dim", theme.FAINT),
                               ("red", theme.RED), ("green", theme.GREEN),
                               ("yellow", theme.YELLOW), ("cyan", theme.CYAN)):
                body.tag_configure(tag, foreground=color)
            for chunk, tag in parse_ansi(screen):
                body.insert("end", chunk, (tag,) if tag else ())
            body.configure(state="disabled")
            scrollbar = ttk.Scrollbar(wrapper, orient="vertical",
                                      command=body.yview)
            body.configure(yscrollcommand=scrollbar.set)
            body.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Close", style="Accent.TButton",
                   command=self.destroy).pack(side="right")

        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<Return>", lambda _e: self.destroy())
        self.update_idletasks()
        width = min(max(self.winfo_reqwidth(), 560), 1000)
        height = min(max(self.winfo_reqheight(), 200), 760)
        try:
            x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
            y = parent.winfo_rooty() + (parent.winfo_height() - height) // 3
        except Exception:
            x = y = 120
        self.geometry("%dx%d+%d+%d" % (width, height, max(x, 0), max(y, 0)))
        theme.set_windows_titlebar(self)
        self.grab_set()


def show_message(parent, title, screen, headline="", kind="bad"):
    dialog = MessageDialog(parent, title, screen, headline, kind)
    parent.wait_window(dialog)


def show(parent, prompt):
    """Display *prompt* and release the waiting worker thread when answered."""
    dialog = PromptDialog(parent, prompt)
    parent.wait_window(dialog)
    if not prompt.event.is_set():
        prompt.aborted = True
        prompt.event.set()
    return prompt.answer
