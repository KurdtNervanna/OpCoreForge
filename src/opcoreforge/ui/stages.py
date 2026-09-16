"""
The OpCore-Simplify stages, as GUI screens.

Each stage drives the corresponding step of upstream's main menu and calls the
same functions in the same order.  Nothing here decides anything on the user's
behalf: the kext list, the ACPI patch list, the SMBIOS catalogue and the macOS
version range are all read live from OpCore-Simplify's own models, and toggling
a row calls upstream's dependency-aware helpers rather than flipping a flag.
"""

from __future__ import annotations

import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import theme
from .widgets import Banner, Card, CheckTable, KeyValue, ScrollFrame, StepBar


class Stage(ttk.Frame):
    key = ""
    label = ""
    heading = ""
    blurb = ""

    def __init__(self, app, parent):
        super().__init__(parent, style="TFrame")
        self.app = app
        self.built = False

    def build(self):
        """Create widgets once, lazily."""

    def ensure_built(self):
        if not self.built:
            self.build()
            self.built = True

    def on_enter(self):
        self.ensure_built()

    def header(self, parent):
        box = ttk.Frame(parent, style="TFrame")
        box.pack(fill="x", padx=18, pady=(16, 10))
        ttk.Label(box, text=self.heading, style="H2.TLabel").pack(anchor="w")
        if self.blurb:
            ttk.Label(box, text=self.blurb, style="Muted.TLabel",
                      wraplength=880, justify="left").pack(anchor="w", pady=(3, 0))
        return box


# ---------------------------------------------------------------------------
# 1. Hardware report
# ---------------------------------------------------------------------------

class HardwareStage(Stage):
    key = "hardware"
    label = "1 - Hardware"
    heading = "Hardware report"
    blurb = ("OpCore-Simplify builds the EFI from a hardware report plus a dump of "
             "this machine's ACPI tables. Generate both with Hardware Sniffer, or "
             "import a Report.json exported earlier.")

    def build(self):
        self.header(self)

        content = ScrollFrame(self)
        content.pack(fill="both", expand=True, padx=18, pady=(0, 14))
        body = content.body

        self.banner = Banner(body)

        actions = Card(body, "Get a hardware report")
        actions.pack(fill="x", pady=(0, 12))
        row = ttk.Frame(actions.body, style="Card.TFrame")
        row.pack(fill="x")

        self.detect_button = ttk.Button(
            row, text="Detect this PC's hardware", style="Accent.TButton",
            command=self._detect)
        self.detect_button.pack(side="left")
        ttk.Button(row, text="Import Report.json...",
                   command=self._import).pack(side="left", padx=(8, 0))
        self.acpi_button = ttk.Button(row, text="Load ACPI tables folder...",
                                      command=self._load_acpi)
        self.acpi_button.pack(side="left", padx=(8, 0))

        if os.name != "nt":
            self.detect_button.state(["disabled"])
            ttk.Label(actions.body, style="CardMuted.TLabel", wraplength=820,
                      justify="left",
                      text="Hardware Sniffer is Windows-only. On this system, "
                           "import a Report.json and its ACPI folder instead."
                      ).pack(anchor="w", pady=(8, 0))

        self.summary_card = Card(body, "Detected system")
        self.summary = KeyValue(self.summary_card.body)
        self.summary.pack(fill="x")
        self.summary_card.pack(fill="x", pady=(0, 12))
        self.summary_card.pack_forget()

        self.report_card = Card(body, "Compatibility")
        self.report_text = tk.Text(
            self.report_card.body, background="#101318", foreground=theme.TEXT,
            borderwidth=0, highlightthickness=0, wrap="word", height=18,
            padx=12, pady=10, state="disabled")
        if theme.FONTS:
            self.report_text.configure(font=theme.FONTS.mono)
        for tag, color in (("bold", "#ffffff"), ("dim", theme.FAINT),
                           ("red", theme.RED), ("green", theme.GREEN),
                           ("yellow", theme.YELLOW), ("cyan", theme.CYAN)):
            self.report_text.tag_configure(tag, foreground=color)
        self.report_text.pack(fill="both", expand=True)
        self.report_card.pack(fill="both", expand=True)
        self.report_card.pack_forget()

    # -- actions ---------------------------------------------------------

    def _detect(self):
        self.banner.show("Running Hardware Sniffer. It needs administrator "
                         "rights to dump ACPI tables.", "info")
        def stopped(exc=None):
            self._set_report_text(getattr(exc, "screen", "") or "")
            self.banner.show(
                "OpCore-Simplify stopped during detection - the reason is "
                "shown below.", "bad")

        self.app.run_stage(
            "Detecting hardware",
            lambda: self.app.ocs.run_hardware_sniffer(report=self.app.report),
            self._adopt_detected, on_abort=stopped)

    def _adopt_detected(self, payload):
        path, data = payload
        self._adopt(path, data)

    def _import(self):
        path = filedialog.askopenfilename(
            title="Select a hardware report",
            filetypes=[("Hardware report", "*.json"), ("All files", "*.*")])
        if not path:
            return
        self.app.run_stage(
            "Validating report",
            lambda: self.app.ocs.validate_report(path),
            lambda result: self._after_validate(path, result))

    def _after_validate(self, path, result):
        is_valid, errors, warnings, data, text = result
        self._set_report_text(text)
        if not is_valid or errors:
            self.banner.show(
                "This report cannot be used. Re-export it with the latest "
                "Hardware Sniffer and try again.", "bad")
            return
        if warnings:
            self.banner.show("Report imported with %d warning(s) - see below."
                             % len(warnings), "warn")
        self._adopt(path, data)

    def _adopt(self, path, data):
        def work():
            self.app.ocs.set_report(path, data)
            return self.app.ocs.compatibility_text

        def done(text):
            self._set_report_text(text)
            self._fill_summary()
            # Put the machine's identity in the diagnostics header, so a report
            # sent on says what hardware it came from without being asked.
            report = self.app.ocs.hardware_report or {}
            self.app.session_log.context(
                motherboard=report.get("Motherboard", {}).get("Name"),
                platform=report.get("Motherboard", {}).get("Platform"),
                cpu=report.get("CPU", {}).get("Processor Name"),
                gpus=", ".join(report.get("GPU", {})) or None,
                native_macos=self.app.ocs.native_macos_version,
                oclp_macos=self.app.ocs.ocl_patched_macos_version)
            has_acpi = self.app.ocs.acpi_tables_loaded
            if has_acpi:
                self.banner.show(
                    "Hardware report loaded and ACPI tables read. "
                    "Continue to the macOS version.", "good")
                self.app.unlock("macos")
                self.app.goto("macos")
            else:
                self.banner.show(
                    "Hardware report loaded, but no ACPI tables yet. Load the "
                    "ACPI folder that came with this report to continue - "
                    "OpCore-Simplify needs the DSDT to generate patches.", "warn")

        def stopped(exc=None):
            # OpCore-Simplify refuses to continue with this hardware. Leave the
            # stage usable so a different report can be imported, and keep the
            # reason on screen rather than only in a dialog that gets dismissed.
            self._set_report_text(getattr(exc, "screen", "") or
                                  self.app.ocs.bridge.current_screen())
            self.banner.show(
                "OpCore-Simplify stopped: this hardware cannot be used as it "
                "is. The reason is shown below. You can import a different "
                "report, or change the machine and detect again.", "bad")

        self.app.run_stage("Checking compatibility", work, done,
                           on_abort=stopped)

    def _load_acpi(self):
        folder = filedialog.askdirectory(title="Select the ACPI tables folder")
        if not folder:
            return
        self.app.run_stage(
            "Reading ACPI tables",
            lambda: self.app.ocs.load_acpi_tables(folder),
            self._after_acpi)

    def _after_acpi(self, ok):
        if not ok:
            self.banner.show("No usable DSDT was found in that folder.", "bad")
            return
        self.summary.set("ACPI tables", "loaded", "good")
        if self.app.ocs.hardware_report:
            self.banner.show("ACPI tables loaded. Continue to the macOS version.",
                             "good")
            self.app.unlock("macos")
        else:
            self.banner.show("ACPI tables loaded. Now import the hardware report.",
                             "info")

    # -- display ---------------------------------------------------------

    def _set_report_text(self, text):
        from ..bridge.console import parse_ansi, strip_trailing_prompt
        text = strip_trailing_prompt(text)
        self.report_card.pack(fill="both", expand=True)
        self.report_text.configure(state="normal")
        self.report_text.delete("1.0", "end")
        for chunk, tag in parse_ansi(text or ""):
            self.report_text.insert("end", chunk, (tag,) if tag else ())
        self.report_text.configure(state="disabled")

    def _fill_summary(self):
        report = self.app.ocs.hardware_report or {}
        self.summary_card.pack(fill="x", pady=(0, 12), before=self.report_card)
        board = report.get("Motherboard", {})
        cpu = report.get("CPU", {})
        bios = report.get("BIOS", {})
        self.summary.set("Motherboard", board.get("Name", "Unknown"))
        self.summary.set("Platform", board.get("Platform", "Unknown"))
        self.summary.set("Chipset", board.get("Chipset", "Unknown"))
        self.summary.set("CPU", "%s (%s)" % (cpu.get("Processor Name", "Unknown"),
                                             cpu.get("Codename", "?")))
        self.summary.set("Cores / threads", "%s / %s" % (
            cpu.get("Core Count", "?"), cpu.get("Thread Count", "?")))
        gpus = ", ".join(report.get("GPU", {}).keys()) or "None detected"
        self.summary.set("GPU", gpus)
        firmware = bios.get("Firmware Type", "Unknown")
        self.summary.set("Firmware", firmware,
                         "good" if firmware == "UEFI" else "warn")
        secure_boot = bios.get("Secure Boot", "Unknown")
        self.summary.set("Secure Boot", secure_boot,
                         "good" if secure_boot == "Disabled" else "warn")
        self.summary.set("ACPI tables",
                         "loaded" if self.app.ocs.acpi_tables_loaded else "not loaded",
                         "good" if self.app.ocs.acpi_tables_loaded else "warn")


# ---------------------------------------------------------------------------
# 2. macOS version
# ---------------------------------------------------------------------------

class MacOSStage(Stage):
    key = "macos"
    label = "2 - macOS"
    heading = "macOS version"
    blurb = ("Every later stage depends on this: kexts, ACPI patches, the SMBIOS "
             "shortlist and the config.plist quirks are all chosen for the "
             "release you pick.")

    def build(self):
        self.header(self)
        wrap = ttk.Frame(self, style="TFrame")
        wrap.pack(fill="both", expand=True, padx=18, pady=(0, 14))

        self.banner = Banner(wrap)

        self.table = CheckTable(
            wrap,
            columns=[("mark", "", 40, "center"),
                     ("name", "Release", 260, "w"),
                     ("darwin", "Darwin", 90, "center"),
                     ("note", "Support", 480, "w")],
            on_toggle=self._pick, height=12)
        # Footer first, anchored to the bottom: the packer gives space in
        # the order children are added, so a table that wants more height
        # than there is would otherwise take it all and clip the row of
        # buttons underneath -- which is what opening the tool output pane
        # does to the window.
        footer = ttk.Frame(wrap, style="TFrame")
        footer.pack(fill="x", side="bottom", pady=(12, 0))
        self.choice_label = ttk.Label(footer, text="", style="Muted.TLabel")
        self.choice_label.pack(side="left")
        self.apply_button = ttk.Button(footer, text="Use this version",
                                       style="Accent.TButton", command=self._apply)
        self.apply_button.pack(side="right")

        self.table.pack(fill="both", expand=True)

        self._options = []
        self._selected = None

    def on_enter(self):
        self.ensure_built()
        if not self._options:
            self.app.run_stage("Working out supported macOS versions",
                               self._collect, self._render)

    def _collect(self):
        suggested = self.app.ocs.suggested_macos_version()
        return self.app.ocs.macos_options(), suggested

    def _render(self, payload):
        options, suggested = payload
        self._options = options
        self._selected = self._selected or suggested
        self._refresh()
        name = None
        for option in options:
            if option.darwin_version[:2] == suggested[:2]:
                name = option.name
        # A version the graphics cannot drive is worth saying out loud rather
        # than leaving as a red row someone has to notice.
        if any(not option.graphics_supported for option in options):
            self.banner.show(self.app.ocs.graphics_limit_note(), "warn")
        elif name:
            self.banner.show(
                "OpCore-Simplify recommends %s for this hardware. Newer "
                "releases are listed, but anything flagged below needs "
                "OpenCore Legacy Patcher, which disables SIP and AMFI." % name,
                "info")

    def _refresh(self):
        self.table.clear()
        for option in self._options:
            chosen = self._selected and option.darwin_version[:2] == self._selected[:2]
            note = ("Requires OpenCore Legacy Patcher"
                    if option.requires_oclp else "Natively supported")
            tag = "forced" if option.requires_oclp else "normal"
            if not option.graphics_supported:
                # Listed, because upstream lists it, but honest about why it
                # is not a real choice: the range covers every device, and
                # this one is past what the graphics can drive.
                note = "No graphics support - this machine cannot display it"
                tag = "bad"
            elif chosen:
                # Only a usable row gets the "chosen" colour: showing a
                # refused one in green would say it had been accepted.
                tag = "selected"
            self.table.add_row(
                str(option.darwin_major),
                ["*" if chosen else "", option.name,
                 str(option.darwin_major), note],
                tags=(tag,))
        self._update_choice_label()

    def _update_choice_label(self):
        if not self._selected:
            self.choice_label.configure(text="")
            return
        from ocs_scripts.datasets import os_data
        self.choice_label.configure(
            text="Selected: %s (%s)" % (
                os_data.get_macos_name_by_darwin(self._selected), self._selected))

    def _pick(self, item):
        for option in self._options:
            if str(option.darwin_major) == item:
                self._selected = option.darwin_version
                break
        self._refresh()

    def _apply(self):
        if not self._selected:
            return
        chosen = None
        for option in self._options:
            if option.darwin_version[:2] == self._selected[:2]:
                chosen = option
        if chosen is not None and not chosen.graphics_supported:
            # Refused here as well as in the controller: catching it before
            # the worker starts keeps the answer instant and keeps the tabs
            # from half-unlocking.
            self.banner.show(
                "%s cannot run on this machine's graphics. %s"
                % (chosen.name, self.app.ocs.graphics_limit_note()), "bad")
            return

        first_time = self.app.ocs.macos_version is None
        version = self._selected

        def work():
            self.app.ocs.apply_macos_version(version)
            self.app.ocs.select_acpi_and_kexts(first_time=first_time)
            return self.app.ocs.macos_version

        def done(applied):
            self._selected = applied
            self._refresh()
            if self.app.ocs.needs_oclp:
                self.banner.show(
                    "This configuration needs OpenCore Legacy Patcher. It "
                    "restores GPU and Broadcom Wi-Fi support but disables SIP "
                    "and AMFI, and macOS updates will need full installers.",
                    "warn")
            else:
                self.banner.show("macOS version applied. Kexts and ACPI patches "
                                 "have been selected for it.", "good")
            for key in ("smbios", "acpi", "kexts", "build"):
                self.app.unlock(key)
            self.app.refresh_stage("smbios")
            self.app.refresh_stage("acpi")
            self.app.refresh_stage("kexts")
            self.app.refresh_stage("build")

        self.app.run_stage("Applying macOS version", work, done)


# ---------------------------------------------------------------------------
# 3. SMBIOS
# ---------------------------------------------------------------------------

class SmbiosStage(Stage):
    key = "smbios"
    label = "3 - SMBIOS"
    heading = "SMBIOS model"
    blurb = ("The Mac model this machine reports itself as. OpCore-Simplify picks "
             "one matched to your CPU generation and GPU for power management; "
             "changing it is rarely necessary.")

    def build(self):
        self.header(self)
        wrap = ttk.Frame(self, style="TFrame")
        wrap.pack(fill="both", expand=True, padx=18, pady=(0, 14))

        self.banner = Banner(wrap)

        controls = ttk.Frame(wrap, style="TFrame")
        controls.pack(fill="x", pady=(0, 8))
        self.show_all = tk.BooleanVar(value=False)
        ttk.Checkbutton(controls, text="Show all Mac models",
                        variable=self.show_all,
                        command=self._refresh).pack(side="left")
        ttk.Button(controls, text="Restore recommended", style="Small.TButton",
                   command=self._restore).pack(side="right")

        self.table = CheckTable(
            wrap,
            columns=[("mark", "", 40, "center"),
                     ("name", "Model", 170, "w"),
                     ("cpu", "CPU", 170, "w"),
                     ("gen", "Generation", 200, "w"),
                     ("gpu", "Discrete GPU", 220, "w"),
                     ("support", "Supported", 120, "w")],
            on_toggle=self._pick, height=18)
        # Footer first, anchored to the bottom: the packer gives space in
        # the order children are added, so a table that wants more height
        # than there is would otherwise take it all and clip the row of
        # buttons underneath -- which is what opening the tool output pane
        # does to the window.
        self.current = ttk.Label(wrap, text="", style="Muted.TLabel")
        self.current.pack(anchor="w", side="bottom", pady=(10, 0))

        self.table.pack(fill="both", expand=True)
        self._default = None

    def on_enter(self):
        self.ensure_built()
        self.refresh()

    def refresh(self):
        if not self.built or not self.app.ocs.macos_version:
            return
        self._refresh()

    def _refresh(self):
        rows, default = self.app.ocs.smbios_catalog()
        self._default = default
        selected = self.app.ocs.smbios_model
        self.table.clear()
        for device, supported, platform_ok, is_default in rows:
            visible = (self.show_all.get() or device.name in (selected, default)
                       or (supported and platform_ok))
            if not visible:
                continue
            chosen = device.name == selected
            tag = "selected" if chosen else ("normal" if supported else "unavailable")
            self.table.add_row(
                device.name,
                ["*" if chosen else "", device.name, device.cpu,
                 device.cpu_generation, device.discrete_gpu or "-",
                 "yes" if supported else "no"],
                tags=(tag,))
        self.current.configure(
            text="Current: %s%s" % (selected or "none",
                                    "  (recommended: %s)" % default
                                    if selected != default else "  (recommended)"))

    def _pick(self, item):
        self.app.run_stage("Applying SMBIOS model",
                           lambda: self.app.ocs.set_smbios_model(item),
                           lambda _r: self._after_pick(item))

    def _after_pick(self, name):
        self._refresh()
        if name != self._default:
            self.banner.show(
                "Using %s instead of the recommended %s. Power management and "
                "iGPU behaviour are tuned for the recommended model."
                % (name, self._default), "warn")
        else:
            self.banner.hide()
        self.app.refresh_stage("kexts")
        self.app.refresh_stage("acpi")
        self.app.refresh_stage("build")

    def _restore(self):
        if self._default:
            self._pick(self._default)


# ---------------------------------------------------------------------------
# 4. ACPI patches
# ---------------------------------------------------------------------------

class AcpiStage(Stage):
    key = "acpi"
    label = "4 - ACPI"
    heading = "ACPI patches"
    blurb = ("Selected automatically from your chipset, CPU and disabled devices. "
             "Every patch OpCore-Simplify knows is listed; click a row to toggle "
             "it. Changing these without a reason usually makes things worse.")

    def build(self):
        self.header(self)
        wrap = ttk.Frame(self, style="TFrame")
        wrap.pack(fill="both", expand=True, padx=18, pady=(0, 14))

        self.banner = Banner(wrap)
        self.table = CheckTable(
            wrap,
            columns=[("mark", "", 40, "center"),
                     ("name", "Patch", 170, "w"),
                     ("desc", "What it does", 760, "w")],
            on_toggle=self._toggle, height=20)
        # Footer first, anchored to the bottom: the packer gives space in
        # the order children are added, so a table that wants more height
        # than there is would otherwise take it all and clip the row of
        # buttons underneath -- which is what opening the tool output pane
        # does to the window.
        footer = ttk.Frame(wrap, style="TFrame")
        footer.pack(fill="x", side="bottom", pady=(10, 0))
        self.count = ttk.Label(footer, text="", style="Muted.TLabel")
        self.count.pack(side="left")
        ttk.Button(footer, text="Reset to recommended", style="Small.TButton",
                   command=self._reset).pack(side="right")

        self.table.pack(fill="both", expand=True)

    def on_enter(self):
        self.ensure_built()
        self.refresh()

    def refresh(self):
        if not self.built or not self.app.ocs.macos_version:
            return
        patches = self.app.ocs.acpi_patches
        self.table.clear()
        for index, patch in enumerate(patches):
            self.table.add_row(
                str(index),
                ["x" if patch.checked else "", patch.name, patch.description],
                tags=("selected" if patch.checked else "normal",))
        self.count.configure(text="%d of %d patches enabled" % (
            sum(1 for p in patches if p.checked), len(patches)))

    def _toggle(self, item):
        self.app.ocs.toggle_acpi_patch(int(item))
        self.refresh()

    def _reset(self):
        def work():
            self.app.ocs.ocpe.ac.select_acpi_patches(
                self.app.ocs.customized_hardware, self.app.ocs.disabled_devices)
            self.app.ocs.ocpe.s.smbios_specific_options(
                self.app.ocs.customized_hardware, self.app.ocs.smbios_model,
                self.app.ocs.macos_version, self.app.ocs.acpi_patches,
                self.app.ocs.ocpe.k)

        self.app.run_stage("Restoring recommended patches", work,
                           lambda _r: (self.refresh(),
                                       self.banner.show("Recommended selection "
                                                        "restored.", "good")))


# ---------------------------------------------------------------------------
# 5. Kexts
# ---------------------------------------------------------------------------

class KextStage(Stage):
    key = "kexts"
    label = "5 - Kexts"
    heading = "Kernel extensions"
    blurb = ("Chosen from your devices and the macOS release. Toggling a row uses "
             "OpCore-Simplify's own dependency rules, so selecting a plugin pulls "
             "in what it needs and mutually exclusive drivers turn each other off.")

    MARKERS = {
        "required": ("R", "required"),
        "selected": ("x", "selected"),
        "force_loaded": ("!", "forced"),
        "available": ("", "normal"),
        "force_available": ("?", "normal"),
        "not_needed": ("-", "unavailable"),
        "requires_newer": ("-", "unavailable"),
    }

    def build(self):
        self.header(self)
        wrap = ttk.Frame(self, style="TFrame")
        wrap.pack(fill="both", expand=True, padx=18, pady=(0, 14))

        self.banner = Banner(wrap)

        controls = ttk.Frame(wrap, style="TFrame")
        controls.pack(fill="x", pady=(0, 8))
        ttk.Label(controls, text="Filter:", style="Muted.TLabel").pack(side="left")
        self.filter_var = tk.StringVar()
        entry = ttk.Entry(controls, textvariable=self.filter_var, width=28)
        entry.pack(side="left", padx=(8, 0))
        entry.bind("<KeyRelease>", lambda _e: self.refresh())
        self.only_selected = tk.BooleanVar(value=False)
        ttk.Checkbutton(controls, text="Only selected",
                        variable=self.only_selected,
                        command=self.refresh).pack(side="left", padx=(14, 0))
        ttk.Label(controls, style="Muted.TLabel",
                  text="R required    x selected    ! force-loaded    "
                       "? can be forced    - unavailable"
                  ).pack(side="right")

        self.table = CheckTable(
            wrap,
            columns=[("mark", "", 40, "center"),
                     ("name", "Kext", 230, "w"),
                     ("desc", "Purpose", 640, "w")],
            on_toggle=self._toggle, height=20)
        # Footer first, anchored to the bottom: the packer gives space in
        # the order children are added, so a table that wants more height
        # than there is would otherwise take it all and clip the row of
        # buttons underneath -- which is what opening the tool output pane
        # does to the window.
        self.count = ttk.Label(wrap, text="", style="Muted.TLabel")
        self.count.pack(anchor="w", side="bottom", pady=(10, 0))

        self.table.pack(fill="both", expand=True)

    def on_enter(self):
        self.ensure_built()
        self.refresh()

    @staticmethod
    def _normalise(text):
        return "".join(c for c in (text or "").lower() if c.isalnum())

    def _matches(self, kext, needle):
        """Search name, purpose and category alike.

        Category matters: someone typing "wifi" is looking for the Wi-Fi
        drivers, whose names ("itlwm", "AirportBrcmFixup") contain no such
        word. Punctuation is ignored so "wifi" finds "Wi-Fi".
        """
        haystack = self._normalise("%s %s %s" % (
            kext.name, kext.description or "", kext.category or ""))
        return self._normalise(needle) in haystack

    def refresh(self):
        if not self.built or not self.app.ocs.macos_version:
            return
        needle = self.filter_var.get().strip().lower()
        only_selected = self.only_selected.get()
        kexts = self.app.ocs.kexts
        self.table.clear()
        current_category = None
        for index, kext in enumerate(kexts):
            status = self.app.ocs.kext_status(kext)
            if only_selected and status not in ("required", "selected", "force_loaded"):
                continue
            if needle and not self._matches(kext, needle):
                continue
            if kext.category != current_category:
                current_category = kext.category
                self.table.add_category(current_category or "Uncategorized")
            marker, tag = self.MARKERS.get(status, ("", "normal"))
            self.table.add_row(str(index),
                               [marker, kext.name, kext.description], tags=(tag,))
        self.count.configure(text="%d of %d kexts will be installed" % (
            sum(1 for k in kexts if k.checked), len(kexts)))

    def _toggle(self, item):
        index = int(item)

        def work():
            return self.app.ocs.toggle_kext(index)

        def done(messages):
            self.refresh()
            if messages:
                self.banner.show("  ".join(messages), "warn")
            else:
                self.banner.hide()
            self.app.refresh_stage("build")

        self.app.run_stage("Updating kext selection", work, done)


# ---------------------------------------------------------------------------
# 6. Build
# ---------------------------------------------------------------------------

class BuildStage(Stage):
    key = "build"
    label = "6 - Build EFI"
    heading = "Build the OpenCore EFI"
    blurb = ("Downloads OpenCorePkg and the selected kexts, applies the ACPI "
             "patches, generates config.plist and writes the EFI folder.")

    def build(self):
        self.header(self)
        wrap = ttk.Frame(self, style="TFrame")
        wrap.pack(fill="both", expand=True, padx=18, pady=(0, 14))

        self.banner = Banner(wrap)

        # Pin the action row before the expanding content so the build button
        # can never be pushed below the window edge.
        footer = ttk.Frame(wrap, style="TFrame")
        footer.pack(fill="x", side="bottom", pady=(12, 0))
        self.result_label = ttk.Label(footer, text="", style="Muted.TLabel")
        self.result_label.pack(side="left")
        self.open_button = ttk.Button(footer, text="Open EFI folder",
                                      command=self._open_folder)
        self.build_button = ttk.Button(footer, text="Build OpenCore EFI",
                                       style="Accent.TButton", command=self._build)
        self.build_button.pack(side="right")

        self.steps = StepBar(wrap)
        self.steps.pack(fill="x", side="bottom", pady=(12, 0))
        self.steps.pack_forget()

        columns = ttk.Frame(wrap, style="TFrame")
        columns.pack(fill="both", expand=True)
        columns.columnconfigure(0, weight=3, uniform="c")
        columns.columnconfigure(1, weight=2, uniform="c")

        left = Card(columns, "Configuration")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.summary = KeyValue(left.body)
        self.summary.pack(fill="x")

        right = Card(columns, "Before you use this EFI")
        right.grid(row=0, column=1, sticky="nsew")
        self.requirements = tk.Text(
            right.body, background=theme.PANEL_ALT, foreground=theme.TEXT,
            borderwidth=0, highlightthickness=0, wrap="word", height=14,
            state="disabled")
        if theme.FONTS:
            self.requirements.configure(font=theme.FONTS.small)
        self.requirements.pack(fill="both", expand=True)

    def on_enter(self):
        self.ensure_built()
        self.refresh()

    def refresh(self):
        if not self.built:
            return
        ocs = self.app.ocs
        if not ocs.macos_version:
            return
        from ocs_scripts.datasets import os_data
        self.summary.set("macOS", "%s (%s)" % (
            os_data.get_macos_name_by_darwin(ocs.macos_version), ocs.macos_version))
        self.summary.set("SMBIOS", ocs.smbios_model or "-")
        self.summary.set("Kexts", str(sum(1 for k in ocs.kexts if k.checked)))
        self.summary.set("ACPI patches",
                         str(sum(1 for p in ocs.acpi_patches if p.checked)))
        self.summary.set("OpenCore Legacy Patcher",
                         "required" if ocs.needs_oclp else "not needed",
                         "warn" if ocs.needs_oclp else "good")
        if ocs.disabled_devices:
            self.summary.set("Disabled devices",
                             "\n".join(sorted(ocs.disabled_devices)), "warn")
        self.summary.set("Output folder", str(self.app.paths.results))

        lines = []
        try:
            for requirement in ocs.bios_requirements():
                lines.append("BIOS:  %s" % requirement)
        except Exception:
            pass
        lines.extend([
            "",
            "After the build, stages 7 and 8 finish the job:",
            "  7  map your USB ports and install UTBMap.kext",
            "  8  run OC Snapshot in the config.plist editor and save",
        ])
        self.requirements.configure(state="normal")
        self.requirements.delete("1.0", "end")
        self.requirements.insert("end", "\n".join(lines))
        self.requirements.configure(state="disabled")

    def _build(self):
        ocs = self.app.ocs
        if ocs.needs_oclp and not self._confirm_oclp():
            return

        self.steps.pack(fill="x", side="bottom", pady=(12, 0))
        self.app.bridge.on_progress = self._progress
        self._skipped_kexts = []

        def work():
            from ..bridge.ocs import PayloadUnavailable
            # One unreachable host should not cost the whole build; ask what to
            # do about the kext that failed and carry on with the rest.
            for _attempt in range(12):
                try:
                    ocs.download_payload()
                    break
                except PayloadUnavailable as unavailable:
                    if not self._ask_skip(unavailable.product):
                        raise
                    self._skipped_kexts.extend(
                        ocs.drop_payload_product(unavailable.product))
            return ocs.build_efi()

        def done(result):
            self.app.bridge.on_progress = None
            self.result_label.configure(text="EFI written to %s" % result)
            self.open_button.pack(side="right", padx=(0, 8))
            self.banner.show(
                "EFI built. Next: map your USB ports in stage 7 - an unmapped "
                "system boots with a generic port map and often loses USB 3 "
                "speeds, sleep or Bluetooth.", "good")
            self.app.unlock("usb")
            self.app.unlock("config")
            self.app.unlock("finish")
            self.app.after_build()

        def failed(exc, detail):
            self.app.bridge.on_progress = None
            self.banner.show("Build failed: %s" % exc, "bad")
            self.app.on_error(exc, detail)

        self.app.run_stage("Building EFI", work, done, failed)

    def _ask_skip(self, product):
        return self.app.ask_from_worker(
            "Download failed",
            "%s could not be downloaded.\n\n"
            "Yes  - build without it (the EFI may be missing a driver your "
            "hardware needs)\n"
            "No   - stop and try again later" % product)

    def _confirm_oclp(self):
        return messagebox.askyesno(
            "OpenCore Legacy Patcher required",
            "This configuration needs OpenCore Legacy Patcher.\n\n"
            "It restores GPU and Broadcom Wi-Fi support, but it disables SIP "
            "and AMFI, macOS updates will require full installers, and OCLP "
            "does not officially support Hackintosh systems.\n\n"
            "Continue with this macOS version?",
            parent=self.app.root)

    def _progress(self, title, steps, index, done):
        # Already delivered on the Tk thread by the bridge's pump.
        self.steps.update_steps(title, steps, index, done)

    def _open_folder(self):
        self.app.open_path(self.app.paths.results)
