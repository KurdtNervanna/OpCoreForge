"""Reusable widgets."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import theme

#: marker meaning "move the cursor back to the start of this line"
_RETURN = object()


class ScrollFrame(ttk.Frame):
    """A vertically scrollable container. Put content into ``.body``."""

    def __init__(self, parent, style="TFrame", **kw):
        super().__init__(parent, style=style, **kw)
        self.canvas = tk.Canvas(self, background=theme.PANEL, highlightthickness=0,
                                borderwidth=0)
        self.scroll = ttk.Scrollbar(self, orient="vertical",
                                    command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.body = ttk.Frame(self.canvas, style=style)
        self._window = self.canvas.create_window((0, 0), window=self.body,
                                                 anchor="nw")
        self.body.bind("<Configure>", self._on_body)
        self.canvas.bind("<Configure>", self._on_canvas)
        for widget in (self.canvas, self.body):
            widget.bind("<Enter>", self._bind_wheel)
            widget.bind("<Leave>", self._unbind_wheel)

    def _on_body(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event):
        self.canvas.itemconfigure(self._window, width=event.width)

    def _bind_wheel(self, _event=None):
        self.canvas.bind_all("<MouseWheel>", self._wheel)
        self.canvas.bind_all("<Button-4>", self._wheel)
        self.canvas.bind_all("<Button-5>", self._wheel)

    def _unbind_wheel(self, _event=None):
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.unbind_all(sequence)

    def _wheel(self, event):
        if event.num == 4:
            delta = -1
        elif event.num == 5:
            delta = 1
        else:
            delta = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(delta, "units")


class Card(ttk.Frame):
    """A titled panel."""

    def __init__(self, parent, title=None, subtitle=None, **kw):
        super().__init__(parent, style="Card.TFrame", padding=14, **kw)
        if title:
            label = ttk.Label(self, text=title, style="Card.TLabel")
            if theme.FONTS:
                label.configure(font=theme.FONTS.h2)
            label.pack(anchor="w")
        if subtitle:
            ttk.Label(self, text=subtitle, style="CardMuted.TLabel",
                      wraplength=760, justify="left").pack(anchor="w", pady=(2, 0))
        self.body = ttk.Frame(self, style="Card.TFrame")
        self.body.pack(fill="both", expand=True, pady=(10 if title else 0, 0))


class Banner(ttk.Frame):
    """A coloured message strip used for warnings and results."""

    COLORS = {
        "info": (theme.ACCENT, "#141b2b"),
        "good": (theme.GREEN, "#10241c"),
        "warn": (theme.YELLOW, "#241f10"),
        "bad": (theme.RED, "#2a1615"),
    }

    def __init__(self, parent, padx=0, **kw):
        super().__init__(parent, **kw)
        self.canvas = tk.Frame(self, background=theme.PANEL_ALT)
        self.bar = tk.Frame(self.canvas, width=3, background=theme.ACCENT)
        self.bar.pack(side="left", fill="y")
        self.label = tk.Label(self.canvas, text="", justify="left", anchor="w",
                              background=theme.PANEL_ALT, foreground=theme.TEXT,
                              padx=12, pady=9, wraplength=820)
        self.label.pack(side="left", fill="x", expand=True)
        # Pack the (empty) frame straight away so the banner keeps its place in
        # the layout: stages create it first so messages appear directly under
        # the heading, not wherever the widget happened to be shown from.
        self.pack(fill="x", padx=padx)

    def show(self, text, kind="info"):
        color, background = self.COLORS.get(kind, self.COLORS["info"])
        self.bar.configure(background=color)
        self.canvas.configure(background=background)
        self.label.configure(text=text, background=background,
                             foreground=theme.TEXT)
        self.canvas.pack(fill="x", pady=(0, 10))

    def hide(self):
        self.canvas.pack_forget()
        # Removing the only child does not shrink the frame's requested size,
        # so it would keep reserving the height of the message it used to hold.
        self.configure(height=1)


class CheckTable(ttk.Frame):
    """A Treeview used as a multi-select checklist.

    Rows carry a leading marker column, mirroring the markers OpCore-Simplify's
    terminal menus use so the two are recognisably the same list.
    """

    def __init__(self, parent, columns, on_toggle=None, height=18,
                 show_headings=True, **kw):
        super().__init__(parent, **kw)
        self.on_toggle = on_toggle
        self._column_ids = [c[0] for c in columns]

        self.tree = ttk.Treeview(
            self, columns=self._column_ids, show="headings" if show_headings else "",
            selectmode="browse", height=height)
        for key, title, width, anchor in columns:
            self.tree.heading(key, text=title, anchor=anchor)
            self.tree.column(key, width=width, anchor=anchor,
                             stretch=(key == self._column_ids[-1]))

        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tree.tag_configure("selected", foreground=theme.GREEN)
        self.tree.tag_configure("required", foreground=theme.CYAN)
        self.tree.tag_configure("forced", foreground=theme.YELLOW)
        self.tree.tag_configure("unavailable", foreground=theme.FAINT)
        self.tree.tag_configure("category", foreground=theme.MUTED,
                                background=theme.PANEL)
        self.tree.tag_configure("normal", foreground=theme.TEXT)
        self.tree.tag_configure("bad", foreground=theme.RED)

        self.tree.bind("<Button-1>", self._click)
        self.tree.bind("<space>", self._space)
        self.tree.bind("<Return>", self._space)

    def _click(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        item = self.tree.identify_row(event.y)
        if item:
            self._fire(item)

    def _space(self, _event=None):
        item = self.tree.focus()
        if item:
            self._fire(item)
        return "break"

    def _fire(self, item):
        if "category" in self.tree.item(item, "tags"):
            return
        if self.on_toggle:
            self.on_toggle(item)

    def clear(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

    def add_category(self, text):
        # The first column is the narrow marker column, so a heading placed
        # there gets clipped to a few characters. Put it in the wide name
        # column instead.
        values = [""] * len(self._column_ids)
        target = 1 if len(values) > 1 else 0
        values[target] = text.upper()
        return self.tree.insert("", "end", values=values, tags=("category",))

    def add_row(self, iid, values, tags=("normal",)):
        return self.tree.insert("", "end", iid=iid, values=values, tags=tags)

    def selection_iid(self):
        focus = self.tree.focus()
        return focus or None


class LogPane(ttk.Frame):
    """Live view of everything the underlying tools print."""

    def __init__(self, parent, **kw):
        super().__init__(parent, **kw)
        self.text = tk.Text(self, background="#101318", foreground=theme.TEXT,
                            insertbackground=theme.TEXT, borderwidth=0,
                            highlightthickness=0, wrap="word", height=10,
                            padx=10, pady=8, state="disabled")
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=vsb.set)
        self.text.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self._lines = 0
        self._overwrite = False

        self.text.tag_configure("bold", foreground="#ffffff")
        self.text.tag_configure("dim", foreground=theme.FAINT)
        self.text.tag_configure("red", foreground=theme.RED)
        self.text.tag_configure("green", foreground=theme.GREEN)
        self.text.tag_configure("yellow", foreground=theme.YELLOW)
        self.text.tag_configure("cyan", foreground=theme.CYAN)

    #: lines kept before the oldest are trimmed away
    MAX_LINES = 4000

    def append(self, chunk, tag=None):
        """Append one already-batched chunk. Tk thread only.

        Carriage returns are honoured rather than inserted literally: the
        downloader draws its progress bar by reprinting the same line with
        ``\\r``, so without this the pane fills with thousands of near-identical
        progress lines instead of one that updates in place.
        """
        if not chunk:
            return
        # Only auto-scroll when the view is already at the bottom, so reading
        # back through the log is not yanked away by new output.
        try:
            at_bottom = self.text.yview()[1] >= 0.999
        except Exception:
            at_bottom = True

        self.text.configure(state="normal")
        for piece in self._tokenize(chunk):
            if piece is _RETURN:
                # A carriage return only moves the cursor to the start of the
                # line; whatever is there stays visible until something
                # overwrites it. Deleting here instead would wipe the final
                # progress reading, which is the one worth keeping.
                self._overwrite = True
            elif piece == "\n":
                self._overwrite = False
                self.text.insert("end", "\n")
                self._lines += 1
            elif piece:
                if self._overwrite:
                    self.text.delete("end-1c linestart", "end-1c")
                    self._overwrite = False
                self.text.insert("end", piece, (tag,) if tag else ())

        if self._lines > self.MAX_LINES:
            drop = self._lines - self.MAX_LINES // 2
            self.text.delete("1.0", "%d.0" % (drop + 1))
            self._lines -= drop

        if at_bottom:
            self.text.see("end")
        self.text.configure(state="disabled")

    @staticmethod
    def _tokenize(chunk):
        """Split into printable runs, newlines, and carriage-return markers."""
        if "\r" not in chunk and "\n" not in chunk:
            yield chunk
            return
        # A CRLF pair is a line ending, not a cursor return.
        chunk = chunk.replace("\r\n", "\n")
        run = []
        for char in chunk:
            if char in ("\r", "\n"):
                if run:
                    yield "".join(run)
                    run = []
                yield _RETURN if char == "\r" else "\n"
            else:
                run.append(char)
        if run:
            yield "".join(run)

    def clear(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        self._lines = 0
        self._overwrite = False

    def set_text(self, content):
        from ..bridge.console import parse_ansi
        self.clear()
        self.text.configure(state="normal")
        for chunk, tag in parse_ansi(content):
            self.text.insert("end", chunk, (tag,) if tag else ())
        self.text.configure(state="disabled")


class StepBar(ttk.Frame):
    """Progress readout for multi-step operations."""

    def __init__(self, parent, **kw):
        super().__init__(parent, style="Card.TFrame", padding=12, **kw)
        self.title = ttk.Label(self, text="", style="Card.TLabel")
        self.title.pack(anchor="w")
        self.progress = ttk.Progressbar(self, mode="determinate", length=420)
        self.progress.pack(fill="x", pady=(8, 8))
        self.steps_frame = ttk.Frame(self, style="Card.TFrame")
        self.steps_frame.pack(fill="x")
        self._labels = []

    def update_steps(self, title, steps, index, done=False):
        self.title.configure(text=title)
        if len(self._labels) != len(steps):
            for label in self._labels:
                label.destroy()
            self._labels = [
                ttk.Label(self.steps_frame, text="", style="CardMuted.TLabel",
                          anchor="w")
                for _ in steps
            ]
            for label in self._labels:
                label.pack(fill="x")
        total = max(len(steps), 1)
        self.progress.configure(maximum=total,
                                value=total if done else min(index, total))
        for i, (label, step) in enumerate(zip(self._labels, steps)):
            if done or i < index:
                label.configure(text="  [done]  %s" % step, foreground=theme.GREEN)
            elif i == index:
                label.configure(text="  [ >  ]  %s" % step, foreground=theme.YELLOW)
            else:
                label.configure(text="  [    ]  %s" % step, foreground=theme.FAINT)


class KeyValue(ttk.Frame):
    """Two-column summary block."""

    def __init__(self, parent, style="Card.TFrame", label_style="CardMuted.TLabel",
                 value_style="Card.TLabel", **kw):
        super().__init__(parent, style=style, **kw)
        self._style = style
        self._label_style = label_style
        self._value_style = value_style
        self._rows = {}
        self.columnconfigure(1, weight=1)

    def set(self, key, value, tag=None):
        if key not in self._rows:
            row = len(self._rows)
            name = ttk.Label(self, text=key, style=self._label_style, anchor="w")
            name.grid(row=row, column=0, sticky="w", padx=(0, 16), pady=2)
            val = ttk.Label(self, text="", style=self._value_style, anchor="w",
                            wraplength=560, justify="left")
            val.grid(row=row, column=1, sticky="ew", pady=2)
            self._rows[key] = val
        color = {"good": theme.GREEN, "warn": theme.YELLOW,
                 "bad": theme.RED, None: theme.TEXT}.get(tag, theme.TEXT)
        self._rows[key].configure(text=str(value), foreground=color)

    def clear(self):
        for widget in self.winfo_children():
            widget.destroy()
        self._rows = {}
