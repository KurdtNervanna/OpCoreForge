"""
USB port mapping (USBToolBox).

macOS enforces a 15-port-per-controller limit and needs to be told what each
port physically is, or you lose USB 3 speeds, internal Bluetooth or sleep.
OpCore-Simplify builds its EFI with a placeholder UTBDefault.kext and then tells
you to go and do this by hand; here it is the next stage, and the kext it
produces is installed into the EFI that was just built.

Discovery is deliberately interactive: plug a device into a port, watch the row
light up, and that is how you know which entry is which.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import theme
from .stages import Stage
from .widgets import Banner, Card, ScrollFrame


class UsbStage(Stage):
    key = "usb"
    label = "7 - USB map"
    heading = "USB port mapping"
    blurb = ("Plug a device into each port in turn: the port it lands on turns "
             "green. Enable the ports you want macOS to use, set each one's "
             "connector type, then build the map.")

    def __init__(self, app, parent):
        super().__init__(app, parent)
        self.live = False
        self._live_job = None
        self._row_index = {}

    def build(self):
        self.header(self)
        wrap = ttk.Frame(self, style="TFrame")
        wrap.pack(fill="both", expand=True, padx=18, pady=(0, 14))

        self.banner = Banner(wrap)

        if not self.app.usb.supported:
            self.banner.show(self.app.usb.unavailable_reason(), "warn")

        controls = ttk.Frame(wrap, style="TFrame")
        controls.pack(fill="x", pady=(0, 8))
        self.scan_button = ttk.Button(controls, text="Scan ports",
                                      style="Accent.TButton", command=self._scan)
        self.scan_button.pack(side="left")
        self.live_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(controls, text="Keep watching for changes",
                        variable=self.live_var,
                        command=self._toggle_live).pack(side="left", padx=(10, 0))
        ttk.Button(controls, text="Import usb.json...", style="Small.TButton",
                   command=self._import).pack(side="left", padx=(16, 0))
        ttk.Button(controls, text="Clear saved data", style="Small.TButton",
                   command=self._clear).pack(side="left", padx=(6, 0))

        ttk.Button(controls, text="Disable empty", style="Small.TButton",
                   command=lambda: self._bulk("empty")).pack(side="right")
        ttk.Button(controls, text="Enable populated", style="Small.TButton",
                   command=lambda: self._bulk("populated")).pack(side="right", padx=(0, 6))
        ttk.Button(controls, text="None", style="Small.TButton",
                   command=lambda: self._bulk("none")).pack(side="right", padx=(0, 6))
        ttk.Button(controls, text="All", style="Small.TButton",
                   command=lambda: self._bulk("all")).pack(side="right", padx=(0, 6))

        # Reserve the footer before the expanding content, or a tall port list
        # pushes the build button off the bottom of the window.
        footer = ttk.Frame(wrap, style="TFrame")
        footer.pack(fill="x", side="bottom", pady=(12, 0))
        self.status = ttk.Label(footer, text="", style="Muted.TLabel")
        self.status.pack(side="left")
        self.build_button = ttk.Button(footer, text="Build map and add to EFI",
                                       style="Accent.TButton", command=self._build)
        self.build_button.pack(side="right")
        ttk.Button(footer, text="Skip USB mapping", style="Small.TButton",
                   command=self._skip).pack(side="right", padx=(0, 8))

        split = ttk.Frame(wrap, style="TFrame")
        split.pack(fill="both", expand=True)
        split.columnconfigure(0, weight=3, uniform="u")
        split.columnconfigure(1, weight=1, uniform="u")
        split.rowconfigure(0, weight=1)

        tree_box = ttk.Frame(split, style="TFrame")
        tree_box.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.tree = ttk.Treeview(
            tree_box, columns=("mark", "port", "speed", "type", "devices"),
            show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Controller / port", anchor="w")
        self.tree.column("#0", width=280, stretch=False)
        for key, title, width, anchor in (
                ("mark", "On", 44, "center"),
                ("port", "Index", 60, "center"),
                ("speed", "Speed", 100, "w"),
                ("type", "Connector", 190, "w"),
                ("devices", "Attached", 260, "w")):
            self.tree.heading(key, text=title, anchor=anchor)
            self.tree.column(key, width=width, anchor=anchor,
                             stretch=(key == "devices"))
        vsb = ttk.Scrollbar(tree_box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tree.tag_configure("on", foreground=theme.GREEN)
        self.tree.tag_configure("off", foreground=theme.TEXT)
        self.tree.tag_configure("populated", foreground=theme.CYAN)
        self.tree.tag_configure("controller", foreground=theme.MUTED)
        self.tree.tag_configure("over", foreground=theme.RED)
        self.tree.bind("<Button-1>", self._click)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._sync_inspector())
        self.tree.bind("<space>", self._space)

        side = ttk.Frame(split, style="TFrame")
        side.grid(row=0, column=1, sticky="nsew")

        self.inspector = Card(side, "Selected port")
        self.inspector.pack(fill="x")
        self.port_name = ttk.Label(self.inspector.body, text="Nothing selected",
                                   style="Card.TLabel", wraplength=280,
                                   justify="left")
        self.port_name.pack(anchor="w")

        ttk.Label(self.inspector.body, text="Connector type",
                  style="CardMuted.TLabel").pack(anchor="w", pady=(12, 2))
        self.type_var = tk.StringVar()
        self.type_box = ttk.Combobox(self.inspector.body, textvariable=self.type_var,
                                     state="readonly", width=28)
        self.type_box.pack(fill="x")
        self.type_box.bind("<<ComboboxSelected>>", self._apply_type)

        ttk.Label(self.inspector.body, text="Comment",
                  style="CardMuted.TLabel").pack(anchor="w", pady=(12, 2))
        self.comment_var = tk.StringVar()
        comment = ttk.Entry(self.inspector.body, textvariable=self.comment_var)
        comment.pack(fill="x")
        comment.bind("<FocusOut>", self._apply_comment)
        comment.bind("<Return>", self._apply_comment)

        self.toggle_button = ttk.Button(self.inspector.body, text="Enable / disable",
                                        command=self._toggle_selected)
        self.toggle_button.pack(fill="x", pady=(12, 0))

        settings = Card(side, "Options")
        settings.pack(fill="both", expand=True, pady=(12, 0))
        options = ScrollFrame(settings.body, style="Card.TFrame")
        options.canvas.configure(background=theme.PANEL_ALT)
        options.pack(fill="both", expand=True)
        self.setting_vars = {}
        from ..bridge.usbtoolbox import UsbController
        for key, title, blurb in UsbController.SETTINGS:
            var = tk.BooleanVar(value=False)
            self.setting_vars[key] = var
            ttk.Checkbutton(options.body, text=title, variable=var,
                            style="Card.TCheckbutton",
                            command=lambda k=key: self._set_setting(k)
                            ).pack(anchor="w")
            ttk.Label(options.body, text=blurb, style="CardMuted.TLabel",
                      wraplength=250, justify="left").pack(anchor="w",
                                                           padx=(20, 0), pady=(0, 6))

    # -- lifecycle -------------------------------------------------------

    def on_enter(self):
        self.ensure_built()
        if self.app.usb.map is None:
            self.app.run_stage("Starting USBToolBox",
                               self.app.usb.start, lambda _r: self._after_start())
        else:
            self._render()

    def _after_start(self):
        for key, var in self.setting_vars.items():
            var.set(self.app.usb.get_setting(key))
        types = []
        from utb_scripts import shared
        for member in shared.USBPhysicalPortTypes:
            types.append("%d - %s" % (int(member), str(member)))
        self.type_box.configure(values=types)
        self._render()
        if self.app.usb.controllers:
            self.banner.show("Loaded the port data saved from a previous run.",
                             "info")

    def on_leave(self):
        self.live_var.set(False)
        self._toggle_live()
        if self.app.usb.map is not None:
            self.app.usb.save()

    # -- discovery -------------------------------------------------------

    def _scan(self):
        if not self.app.usb.supported:
            self.banner.show(self.app.usb.unavailable_reason(), "warn")
            return
        self.app.run_stage("Scanning USB ports", self.app.usb.discover,
                           lambda _r: self._render())

    def _toggle_live(self):
        self.live = self.live_var.get()
        if self._live_job:
            self.after_cancel(self._live_job)
            self._live_job = None
        if self.live and self.app.usb.supported:
            self._live_tick()

    def _live_tick(self):
        if not self.live:
            return
        if not self.app.runner.busy:
            self.app.runner.run(self.app.usb.discover,
                                on_done=lambda _r: self._render(),
                                on_error=lambda e, d: None)
        self._live_job = self.after(2500, self._live_tick)

    # -- rendering -------------------------------------------------------

    def _render(self):
        selected = self.tree.focus()
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._row_index = {}

        usb = self.app.usb
        total_selected = 0
        for controller, chosen, total in usb.selected_counts():
            total_selected += chosen
            node = self.tree.insert(
                "", "end", text=usb.controller_label(controller),
                values=["", "", "", "", "%d of %d ports enabled" % (chosen, total)],
                open=True,
                tags=("over" if chosen > 15 else "controller",))
            for port in controller["ports"]:
                iid = "p%s" % port["selection_index"]
                self._row_index[iid] = (controller, port)
                devices = self._device_summary(port)
                companion = usb.companion_of(port)
                populated = bool(port["devices"]) or (
                    bool(companion["devices"]) if companion else False)
                if port["selected"]:
                    tag = "on"
                elif populated:
                    tag = "populated"
                else:
                    tag = "off"
                label = port["name"]
                if port.get("comment"):
                    label += "  (%s)" % port["comment"]
                self.tree.insert(node, "end", iid=iid, text="   " + label,
                                 values=["x" if port["selected"] else "",
                                         port["index"],
                                         self._speed(port),
                                         self._type(port),
                                         devices],
                                 tags=(tag,))
        if selected and selected in self._row_index:
            self.tree.focus(selected)
            self.tree.selection_set(selected)

        over = usb.over_limit_controllers()
        if over:
            self.banner.show(
                "%d controller(s) have more than 15 enabled ports. macOS ignores "
                "everything past 15, so disable the ones you do not use - or "
                "enable the XhciPortLimit quirk in stage 8."
                % len(over), "bad")
        elif usb.controllers:
            self.banner.hide()

        self.status.configure(
            text="%d port(s) enabled across %d controller(s)"
                 % (total_selected, len(usb.controllers)))
        self._sync_inspector()

    def _speed(self, port):
        from utb_scripts import shared
        try:
            return str(shared.USBDeviceSpeeds(port["class"]))
        except Exception:
            return "?"

    def _type(self, port):
        from utb_scripts import shared
        if port.get("type") is not None:
            return str(shared.USBPhysicalPortTypes(port["type"]))
        if port.get("guessed") is not None:
            return "%s (guessed)" % shared.USBPhysicalPortTypes(port["guessed"])
        return "not set"

    def _device_summary(self, port):
        names = []
        for device in port.get("devices", []):
            if isinstance(device, str):
                names.append(device)
            elif isinstance(device, dict):
                names.append((device.get("name") or "device").strip())
        return ", ".join(names)

    # -- interaction -----------------------------------------------------

    def _click(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        item = self.tree.identify_row(event.y)
        if not item or item not in self._row_index:
            return
        if self.tree.identify_column(event.x) == "#1":
            self._toggle(item)

    def _space(self, _event=None):
        item = self.tree.focus()
        if item in self._row_index:
            self._toggle(item)
        return "break"

    def _toggle(self, item):
        _controller, port = self._row_index[item]
        self.app.usb.toggle_port(port)
        self._render()

    def _toggle_selected(self):
        item = self.tree.focus()
        if item in self._row_index:
            self._toggle(item)

    def _sync_inspector(self):
        item = self.tree.focus()
        entry = self._row_index.get(item)
        if not entry:
            self.port_name.configure(text="Nothing selected")
            self.comment_var.set("")
            self.type_var.set("")
            return
        controller, port = entry
        companion = self.app.usb.companion_of(port)
        text = "%s\n%s" % (self.app.usb.port_label(port),
                           self.app.usb.controller_label(controller))
        if companion:
            text += "\nCompanion of port %s" % companion["selection_index"]
        self.port_name.configure(text=text)
        self.comment_var.set(port.get("comment") or "")
        from utb_scripts import shared
        value = port.get("type")
        if value is None:
            value = port.get("guessed")
        if value is not None:
            member = shared.USBPhysicalPortTypes(value)
            self.type_var.set("%d - %s" % (int(member), str(member)))
        else:
            self.type_var.set("")

    def _apply_type(self, _event=None):
        item = self.tree.focus()
        entry = self._row_index.get(item)
        if not entry:
            return
        _controller, port = entry
        raw = self.type_var.get().split(" - ")[0]
        if raw.strip().isdigit():
            self.app.usb.set_port_type(port, int(raw))
            self._render()

    def _apply_comment(self, _event=None):
        item = self.tree.focus()
        entry = self._row_index.get(item)
        if not entry:
            return
        _controller, port = entry
        self.app.usb.set_port_comment(port, self.comment_var.get().strip())
        self._render()

    def _bulk(self, mode):
        usb = self.app.usb
        if mode == "all":
            usb.select_all(True)
        elif mode == "none":
            usb.select_all(False)
        elif mode == "populated":
            usb.select_populated()
        elif mode == "empty":
            usb.deselect_empty()
        self._render()

    def _set_setting(self, key):
        self.app.usb.set_setting(key, self.setting_vars[key].get())
        self._render()

    def _import(self):
        path = filedialog.askopenfilename(
            title="Select usb.json",
            filetypes=[("USBToolBox data", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            if self.app.usb.map is None:
                self.app.usb.start()
            self.app.usb.load_existing_map(Path(path))
        except Exception as exc:
            self.banner.show("Could not read that file: %s" % exc, "bad")
            return
        self._render()
        self.banner.show("Imported port data from %s" % Path(path).name, "good")

    def _clear(self):
        if not messagebox.askyesno(
                "Clear saved USB data",
                "Discard the port map captured so far and start over?",
                parent=self.app.root):
            return
        try:
            self.app.usb.map.remove_historical()
        except Exception:
            pass
        self._render()

    # -- build -----------------------------------------------------------

    def _build(self):
        usb = self.app.usb
        if not usb.controllers:
            self.banner.show("Scan for ports first.", "warn")
            return

        errors = usb.validate()
        if errors:
            self.banner.show("  ".join(errors), "bad")
            return

        empty = usb.empty_controllers()
        ignore_empty = True
        if empty:
            ignore_empty = messagebox.askyesno(
                "Controllers with no enabled ports",
                "These controllers have no enabled ports:\n\n%s\n\n"
                "Yes  - leave them out of the map entirely (recommended)\n"
                "No   - include them with every port disabled"
                % "\n".join(c["name"] for c in empty),
                parent=self.app.root)

        def work():
            path = usb.build_kext(ignore_empty_controllers=ignore_empty)
            actions = self.app.ocs.install_usb_map(path)
            usb.save()
            return path, actions

        def done(result):
            path, actions = result
            self.app.usb_map_installed = True
            message = "Built %s and installed it into the EFI. %s" % (
                path.name, " ".join(actions))
            if usb.over_limit_controllers():
                message += (" One or more controllers still map more than 15 "
                            "ports - enable XhciPortLimit in stage 8.")
            self.banner.show(message + " config.plist was updated to match, so "
                                       "the EFI is bootable as it stands; OC "
                                       "Snapshot in stage 8 is still worth "
                                       "running but no longer required.", "good")
            self.app.goto("config")

        self.app.run_stage("Building UTBMap.kext", work, done)

    def _skip(self):
        self.app.usb_map_installed = False
        self.banner.show(
            "Skipped. The EFI keeps UTBDefault.kext, which maps ports "
            "generically - fine to get installed, worth revisiting later.",
            "warn")
        self.app.goto("config")
