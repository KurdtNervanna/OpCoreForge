"""
config.plist editing (ProperTree) and the closing checklist.

ProperTree runs inside the tab rather than as a separate program, so OC
Snapshot -- the step that rewrites config.plist's ACPI, Kexts, Tools and
Drivers entries to match what is actually in the EFI folder -- happens on the
same file the previous stages just produced. That is the step that makes the
USB map from stage 7 take effect.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, ttk

from . import theme
from .stages import Stage
from .widgets import Banner, Card, KeyValue


class ConfigStage(Stage):
    key = "config"
    label = "8 - config.plist"
    heading = "config.plist"
    blurb = ("The full ProperTree editor. Run OC Snapshot after any change to the "
             "EFI folder's contents, then save.")

    #: written just before the editor is built, removed once it is up. Its
    #: presence at startup means the last attempt did not survive.
    MARKER = ".editor-attempt"

    def __init__(self, app, parent):
        super().__init__(app, parent)
        self.loaded_path = None
        self._starting = False
        self._crashed_before = False

    # -- deciding how to open the editor ----------------------------------

    def _marker(self):
        return self.app.paths.logs / self.MARKER

    def survey_last_attempt(self) -> bool:
        """Did the previous run die while the editor was coming up?

        Embedding a whole second Tk application into a tab means demoting its
        window with ``wm forget``, and that has faulted inside Tk's own code on
        at least one machine -- an access violation, which no amount of Python
        error handling can catch. Rather than leave someone with an
        application that dies every time they reach stage 8, the attempt is
        recorded and a repeat opens the editor in its own window instead.
        """
        marker = self._marker()
        try:
            if not marker.exists():
                return False
            marker.unlink()
        except Exception:
            return False
        self._crashed_before = True
        self.app.session_log.note(
            "editor", "last run did not survive opening the editor; "
                      "falling back to a separate window")
        return True

    def _wants_embedding(self) -> bool:
        """Where the editor should open, unless the person has said otherwise.

        Windows gets its own window by default. Embedding demotes ProperTree's
        window with ``wm forget``, and on the machine this was reported from
        that faulted inside Tk itself -- three times, in three different calls,
        every one of them while the first document was being opened, and each
        after a fix that removed the previous one. There is no Python error to
        catch and no Tk version to require.

        A separate window is how ProperTree runs on its own, so it is the
        configuration with millions of hours behind it. The tab is a nicety;
        the editor working is not. "Own window" on the toolbar overrides this
        in either direction.
        """
        if self._crashed_before:
            return False
        stored = self.app.paths.read_state().get("editor_windowed")
        if stored is None:
            return os.name != "nt"
        return not bool(stored)

    def build(self):
        wrap = ttk.Frame(self, style="TFrame")
        wrap.pack(fill="both", expand=True)

        bar = ttk.Frame(wrap, style="Toolbar.TFrame", padding=(12, 8))
        bar.pack(fill="x")

        def button(text, command, style="Small.TButton"):
            widget = ttk.Button(bar, text=text, command=command, style=style)
            widget.pack(side="left", padx=(0, 6))
            return widget

        self.snapshot_button = button("OC Snapshot", self._snapshot,
                                      style="Accent.TButton")
        button("Clean Snapshot", lambda: self._snapshot(clean=True))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y",
                                                   padx=8, pady=2)
        button("Save", self._save)
        button("Reload", self._reload)
        button("Undo", lambda: self.app.ptree.undo())
        button("Redo", lambda: self.app.ptree.redo())
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y",
                                                   padx=8, pady=2)
        button("Find / Replace", lambda: self.app.ptree.toggle_find())
        button("Expand", lambda: self.app.ptree.expand_all())
        button("Collapse", lambda: self.app.ptree.collapse_all())

        # Everything below is used rarely; a menu keeps the bar from
        # overflowing on narrower windows.
        more = tk.Menu(self, tearoff=0, background=theme.PANEL_ALT,
                       foreground=theme.TEXT, activebackground=theme.ACCENT,
                       activeforeground="#0b1020", borderwidth=0)
        more.add_command(label="Toggle plist / data / int type pane",
                         command=lambda: self.app.ptree.toggle_type_pane())
        more.add_separator()
        more.add_command(label="Strip comments",
                         command=lambda: self.app.ptree.strip_comments())
        more.add_command(label="Strip disabled entries",
                         command=lambda: self.app.ptree.strip_disabled())
        more.add_separator()
        more.add_command(label="Hex / Base64 converter",
                         command=lambda: self.app.ptree.show_convert_window())
        more.add_command(label="Editor settings",
                         command=lambda: self.app.ptree.show_settings())

        more_button = ttk.Menubutton(bar, text="More", style="Small.TButton")
        more_button.configure(menu=more)
        more_button.pack(side="right")

        self.own_window = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Own window", variable=self.own_window,
                        command=self._toggle_own_window).pack(side="right",
                                                              padx=(0, 10))

        self.banner = Banner(wrap, padx=12)

        # Status line first and anchored, so a tall editor cannot push it off.
        self.status = ttk.Label(wrap, text="", style="Muted.TLabel")
        self.status.pack(anchor="w", side="bottom", padx=12, pady=(0, 8))

        # One of these two is shown, depending on where the editor went.
        self.host = ttk.Frame(wrap, style="TFrame")
        self.host.pack(fill="both", expand=True, padx=12, pady=(6, 12))

        self.away = Card(wrap, "The editor is in its own window")
        ttk.Label(self.away.body, style="CardMuted.TLabel", wraplength=760,
                  justify="left",
                  text="ProperTree is running as a separate window rather than "
                       "inside this tab. The toolbar above still drives it: OC "
                       "Snapshot, Save, Reload, Undo and the rest all act on "
                       "the same document, and it is still the config.plist "
                       "from the EFI stage 6 built.\n\n"
                       "Closing that window puts it away rather than "
                       "discarding it - bring it back with the button below."
                  ).pack(anchor="w")
        ttk.Button(self.away.body, text="Bring the editor to the front",
                   style="Accent.TButton",
                   command=self._raise_editor).pack(anchor="w", pady=(12, 0))
        # Packed only if the editor actually goes there; _show_mode decides.

    def on_enter(self):
        self.ensure_built()
        # Building the editor pumps the Tk event loop (ProperTree calls
        # update() while laying out its first document), so any pending
        # callback -- including another tab switch -- can re-enter this method
        # mid-construction and start a second editor. Guard against that.
        if self._starting:
            return
        if not self.app.ptree.ready:
            # And do not build it from wherever this was called from. Stage 7
            # switches to this tab from inside a callback the main-thread pump
            # delivered; constructing the editor there means ProperTree's
            # nested event loop runs inside the pump's own drain, and on
            # Windows that killed the process outright. after_idle puts the
            # construction back at the top of the event loop, where a nested
            # loop is harmless.
            self._starting = True
            self.app.set_status("Loading the config.plist editor...")
            self.update_idletasks()
            self.app.root.after_idle(self._start_editor)
            return
        self._open_built_config()
        self._update_status()

    def _start_editor(self):
        # Breadcrumbs: this sequence is the one that killed the process on a
        # machine here, leaving a log that just stopped. Naming each step means
        # the next report says which one, rather than which one finished.
        note = self.app.session_log.note
        embed = self._wants_embedding()
        marker = self._marker()
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("embed=%s\n" % embed, encoding="utf-8")
        except Exception:
            pass
        try:
            note("editor", "constructing ProperTree (embedded=%s)" % embed)
            # Nothing of ours runs while ProperTree pumps the loop -- and it
            # pumps it repeatedly, from its constructor and again while it
            # populates the tree. Its windows are being re-parented and a log
            # flush landing in the middle has no business touching them.
            with self.app.pump.paused():
                self.app.ptree.start(self.app.root, self.host, embed=embed)
                note("editor", "constructed")
                self.app.apply_editor_theme()
                note("editor", "theme applied")
                note("editor", "opening config.plist")
                self._open_built_config()
                note("editor", "config.plist open")
            self.app.set_status("Ready")
        finally:
            self._starting = False
            try:
                marker.unlink()
            except Exception:
                pass
        self._show_mode()
        self._update_status()

    def _show_mode(self):
        """Reflect where the editor actually ended up."""
        windowed = not self.app.ptree.embed
        self.own_window.set(windowed)
        if windowed:
            self.host.pack_forget()
            self.away.pack(fill="both", expand=True, padx=12, pady=(6, 12))
            if self._crashed_before:
                self.banner.show(
                    "The last attempt to put the editor inside this tab did "
                    "not survive, so it has been opened in its own window "
                    "instead. Everything works the same - OC Snapshot still "
                    "points at the EFI stage 6 built.", "warn")
            else:
                self.banner.show(
                    "The editor is open in its own window, which is how "
                    "ProperTree runs normally. The toolbar above drives it, "
                    "and it is the config.plist from the EFI stage 6 built.",
                    "info")
        else:
            self.away.pack_forget()
            self.host.pack(fill="both", expand=True, padx=12, pady=(6, 12))

    def _toggle_own_window(self):
        """Remember the choice; it takes effect next time the app starts."""
        self.app.paths.write_state(editor_windowed=bool(self.own_window.get()))
        messagebox.showinfo(
            "Editor window",
            "The editor opens %s next time OpCoreForge starts.\n\nIt cannot be "
            "moved while it is open - ProperTree builds its window once."
            % ("in its own window" if self.own_window.get()
               else "inside this tab"),
            parent=self.app.root)

    def _open_built_config(self):
        target = self.app.paths.config_plist
        if not target.exists():
            self.banner.show("Build the EFI in stage 6 first - there is no "
                             "config.plist to edit yet.", "warn")
            return
        if self.loaded_path == str(target) and not self.app.efi_changed:
            return
        if self.app.ptree.open_path(target):
            self.loaded_path = str(target)
            self.app.efi_changed = False
            if self.app.usb_map_installed:
                self.banner.show(
                    "UTBMap.kext was added to the EFI. Run OC Snapshot now so "
                    "config.plist lists it, then save.", "warn")
            else:
                self.banner.show(
                    "Editing the config.plist from the EFI you just built. Run "
                    "OC Snapshot whenever you add or remove files in the EFI "
                    "folder.", "info")

    def _raise_editor(self):
        if not self.app.ptree.show_window():
            self.banner.show("The editor window could not be brought "
                             "forward.", "warn")

    def _snapshot(self, clean=False):
        if not self.app.ptree.ready:
            return
        # The OC folder is the one stage 6 built, so do not make the user
        # go and find it; fall back to the picker if it is somehow missing.
        oc_folder = self.app.paths.oc_dir
        try:
            self.app.ptree.snapshot(
                clean=clean,
                oc_folder=str(oc_folder) if oc_folder.exists() else None)
        except Exception as exc:
            self.banner.show("Snapshot failed: %s" % exc, "bad")
            return
        self.banner.show(
            "Snapshot done - ACPI, Kexts, Tools and Drivers now match the EFI "
            "folder. Save the file to keep it.", "good")
        self._update_status()

    def _save(self):
        if not self.app.ptree.ready:
            return
        self.app.ptree.save()
        self.app.unlock("finish")
        # Stage 10 needs the finished EFI, which is what saving produces.
        self.app.unlock("media")
        self._update_status()
        self.banner.show("Saved. Stage 9 has the remaining steps.", "good")

    def _reload(self):
        if self.app.ptree.ready:
            self.app.ptree.reload()
            self._update_status()

    def _update_status(self):
        if not self.app.ptree.ready:
            return
        path = self.app.ptree.current_path() or "no file"
        edited = " - unsaved changes" if self.app.ptree.is_edited() else ""
        self.status.configure(text="%s%s" % (path, edited))


class FinishStage(Stage):
    key = "finish"
    label = "9 - Finish"
    heading = "Finish up"
    blurb = "What was produced, and what is left to do on the target machine."

    def build(self):
        self.header(self)
        from .widgets import ScrollFrame

        # Keep the action buttons pinned; the checklist above them scrolls.
        buttons = ttk.Frame(self, style="TFrame")
        buttons.pack(fill="x", side="bottom", padx=18, pady=(0, 14))
        ttk.Button(buttons, text="Open EFI folder", style="Accent.TButton",
                   command=lambda: self.app.open_path(self.app.paths.results)
                   ).pack(side="left")
        ttk.Button(buttons, text="Open data folder",
                   command=lambda: self.app.open_path(self.app.paths.data)
                   ).pack(side="left", padx=(8, 0))

        scroll = ScrollFrame(self)
        scroll.pack(fill="both", expand=True, padx=18, pady=(0, 10))
        body = scroll.body

        self.banner = Banner(body)

        summary_card = Card(body, "What was built")
        summary_card.pack(fill="x", pady=(0, 12))
        self.summary = KeyValue(summary_card.body)
        self.summary.pack(fill="x")

        steps_card = Card(body, "Remaining steps")
        steps_card.pack(fill="x", pady=(0, 12))
        self.steps = tk.Text(steps_card.body, background=theme.PANEL_ALT,
                             foreground=theme.TEXT, borderwidth=0,
                             highlightthickness=0, wrap="word", height=22,
                             state="disabled")
        if theme.FONTS:
            self.steps.configure(font=theme.FONTS.body)
        self.steps.tag_configure("head", foreground=theme.ACCENT,
                                 font=theme.FONTS.body_bold if theme.FONTS else None)
        self.steps.tag_configure("warn", foreground=theme.YELLOW)
        self.steps.pack(fill="both", expand=True)

    def on_enter(self):
        self.ensure_built()
        self.refresh()

    def refresh(self):
        if not self.built:
            return
        ocs = self.app.ocs
        if ocs.macos_version:
            from ocs_scripts.datasets import os_data
            self.summary.set("macOS", os_data.get_macos_name_by_darwin(
                ocs.macos_version))
            self.summary.set("SMBIOS", ocs.smbios_model or "-")
            self.summary.set("Kexts installed",
                             str(sum(1 for k in ocs.kexts if k.checked)))
            self.summary.set("ACPI patches",
                             str(sum(1 for p in ocs.acpi_patches if p.checked)))
        self.summary.set("USB map",
                         "UTBMap.kext installed" if self.app.usb_map_installed
                         else "not mapped (using UTBDefault.kext)",
                         "good" if self.app.usb_map_installed else "warn")
        self.summary.set("EFI folder", str(self.app.paths.results))

        # An entry in config.plist with no file behind it halts OpenCore before
        # the picker is usable, so it is checked here rather than discovered on
        # the target machine. Dangling entries are dropped -- that is what OC
        # Snapshot would do -- and anything left is shown below.
        repairs, leftovers = [], []
        try:
            from .. import eficheck
            repairs = eficheck.sync_usb_map(self.app.paths.efi_dir)
            repairs += eficheck.repair(
                self.app.paths.efi_dir,
                report=lambda line: self.app.session_log.note("eficheck", line))
            leftovers = eficheck.audit(self.app.paths.efi_dir)
        except Exception:
            pass
        broken = [p for p in leftovers if p.fatal]
        notes = [p for p in leftovers if not p.fatal]
        if broken:
            self.summary.set("EFI check", "%d unresolved problem(s)" % len(broken),
                             "warn")
        elif repairs:
            self.summary.set("EFI check", "repaired %d entry/entries" % len(repairs),
                             "good")
        else:
            self.summary.set("EFI check", "config.plist matches the EFI", "good")

        lines = []

        def section(title):
            lines.append(("head", title + "\n"))

        def item(text, tag=None):
            lines.append((tag, "   %s\n" % text))

        requirements = []
        try:
            requirements = ocs.bios_requirements()
        except Exception:
            pass
        if requirements:
            section("BIOS / UEFI settings to change first")
            for requirement in requirements:
                item(requirement, "warn")
            lines.append((None, "\n"))

        if repairs or broken or notes:
            section("EFI consistency check")
            for action in repairs:
                item(action)
            for problem in broken:
                item("%s - OpenCore will stop on this. Run OC Snapshot in "
                     "stage 8, or put the file back." % problem, "warn")
            for problem in notes:
                item(str(problem), "warn")
            lines.append((None, "\n"))

        section("Copy the EFI onto the boot drive")
        item("Format a USB stick as FAT32 with a GUID/GPT partition map.")
        item("Copy the EFI folder from the Results directory to the root of it.")
        item("Boot from that stick and select macOS in the OpenCore picker.")
        lines.append((None, "\n"))

        if not self.app.usb_map_installed:
            section("USB ports are not mapped")
            item("The EFI ships UTBDefault.kext, a generic map. Expect to lose "
                 "USB 3 speeds, internal Bluetooth or sleep until you go back "
                 "to stage 7 and map the ports on the target machine.", "warn")
            lines.append((None, "\n"))

        if ocs.needs_oclp:
            section("OpenCore Legacy Patcher")
            item("This build needs OCLP root patches after macOS is installed.",
                 "warn")
            item("SIP and AMFI are disabled, and macOS updates will need full "
                 "installers rather than deltas.", "warn")
            lines.append((None, "\n"))

        section("If something does not boot")
        item("Re-run OC Snapshot in stage 8 after any change to the EFI folder.")
        item("Serial numbers are generated fresh in config.plist - do not reuse "
             "one across machines.")
        item("Keep this data folder: it caches OpenCore, kexts and your port map "
             "so a rebuild is quick.")

        self.steps.configure(state="normal")
        self.steps.delete("1.0", "end")
        for tag, text in lines:
            self.steps.insert("end", text, (tag,) if tag else ())
        self.steps.configure(state="disabled")

        if self.app.usb_map_installed:
            self.banner.show("Complete: EFI built, USB ports mapped, "
                             "config.plist snapshotted.", "good")
        else:
            self.banner.show("EFI built. USB mapping is still outstanding.",
                             "warn")
