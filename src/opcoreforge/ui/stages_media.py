"""
Stage 10: the macOS install media.

Three outputs from the same folder -- copy it to a USB by hand, let OpCoreForge
write the USB, or build an ISO for a virtual machine. The folder is prepared
first in every case, so the safe path is always available and the destructive
one is never the only way through.

What this produces is a macOS *Recovery* installer. That is worth being plain
about on screen as well as here: the machine boots into Recovery over OpenCore
and downloads macOS from Apple. A full offline installer cannot be built from
Windows at all -- Apple's createinstallmedia is a macOS binary writing an APFS
volume -- which is why OpenCore Legacy Patcher can offer one and this cannot.
"""

from __future__ import annotations

import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .. import media as media_module
from .. import isoimage, usbwriter
from .stages import Stage
from .widgets import Banner, Card, CheckTable, KeyValue, ScrollFrame


class MediaStage(Stage):
    key = "media"
    label = "10 - Install media"
    heading = "macOS install media"
    blurb = ("Puts the EFI you just built together with a macOS recovery, as a "
             "folder, a bootable USB stick, or an ISO for a virtual machine.")

    def __init__(self, app, parent):
        super().__init__(app, parent)
        self._disks = []
        self._selected_disk = None
        self._boards = []

    # -- layout ------------------------------------------------------------

    def build(self):
        self.header(self)
        content = ScrollFrame(self)
        content.pack(fill="both", expand=True, padx=18, pady=(0, 14))
        body = content.body

        self.banner = Banner(body)

        note = Card(body, "What this makes")
        note.pack(fill="x", pady=(0, 12))
        ttk.Label(
            note.body, style="CardMuted.TLabel", wraplength=880,
            justify="left",
            text="A macOS Recovery installer: the machine boots into Recovery "
                 "through OpenCore and downloads macOS from Apple over the "
                 "network. A full offline installer cannot be made on Windows "
                 "- Apple's createinstallmedia is a macOS program - so this is "
                 "the route the OpenCore guides use. You install the same "
                 "macOS either way; it just downloads during setup."
        ).pack(anchor="w")

        # -- 1. recovery ----------------------------------------------------
        step1 = Card(body, "1. macOS recovery")
        step1.pack(fill="x", pady=(0, 12))
        row = ttk.Frame(step1.body, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="Mac model:", style="CardMuted.TLabel").pack(
            side="left")
        self.board_var = tk.StringVar()
        # Read-only: the value has to be one of the listed models, because a
        # typed board ID that is nearly right downloads a different macOS
        # without ever looking wrong.
        self.board_box = ttk.Combobox(row, textvariable=self.board_var,
                                      width=42, state="readonly")
        self.board_box.pack(side="left", padx=(8, 8))
        ttk.Button(row, text="What is this?",
                   style="Small.TButton",
                   command=lambda: self.app.open_path(
                       media_module.DORTANIA_URL)
                   ).pack(side="left")
        self.download_button = ttk.Button(
            row, text="Download recovery", style="Accent.TButton",
            command=self._download)
        self.download_button.pack(side="right")
        self.board_note = ttk.Label(step1.body, style="CardMuted.TLabel",
                                    wraplength=880, justify="left", text="")
        self.board_note.pack(anchor="w", pady=(8, 0))

        # -- 2. folder ------------------------------------------------------
        step2 = Card(body, "2. Put the media together")
        step2.pack(fill="x", pady=(0, 12))
        row2 = ttk.Frame(step2.body, style="Card.TFrame")
        row2.pack(fill="x")
        self.stage_button = ttk.Button(row2, text="Prepare the folder",
                                       style="Accent.TButton",
                                       command=self._stage)
        self.stage_button.pack(side="left")
        ttk.Button(row2, text="Open it",
                   command=lambda: self.app.open_path(
                       media_module.media_dir(self.app.paths))
                   ).pack(side="left", padx=(8, 0))
        self.media_summary = KeyValue(step2.body)
        self.media_summary.pack(fill="x", pady=(10, 0))

        # -- 3. USB ---------------------------------------------------------
        step3 = Card(body, "3. Write a USB stick  (erases it completely)")
        step3.pack(fill="x", pady=(0, 12))
        row3 = ttk.Frame(step3.body, style="Card.TFrame")
        row3.pack(fill="x")
        ttk.Button(row3, text="Rescan drives", style="Small.TButton",
                   command=self._rescan).pack(side="left")
        self.usb_note = ttk.Label(step3.body, style="CardMuted.TLabel",
                                  wraplength=880, justify="left", text="")
        self.usb_note.pack(anchor="w", pady=(8, 0))
        self.disk_table = CheckTable(
            step3.body,
            columns=[("mark", "", 40, "center"),
                     ("drive", "Removable drive", 420, "w"),
                     ("size", "Size", 110, "center"),
                     ("letters", "Currently", 160, "w")],
            on_toggle=self._pick_disk, height=4)
        self.disk_table.pack(fill="x", pady=(8, 0))
        self.write_button = ttk.Button(step3.body, text="Erase and write",
                                       command=self._write_usb)
        self.write_button.pack(anchor="e", pady=(10, 0))

        # -- 4. ISO ---------------------------------------------------------
        step4 = Card(body, "4. Or build an ISO  (for virtual machines)")
        step4.pack(fill="x", pady=(0, 12))
        ttk.Label(step4.body, style="CardMuted.TLabel", wraplength=880,
                  justify="left",
                  text="Boots in VMware, VirtualBox or QEMU. Real hardware "
                       "needs the USB - an ISO would have to be burned to a "
                       "disc, and a disc cannot be written to during install."
                       "\n\nYou choose where to save it. It takes a few "
                       "minutes and needs about twice the size of the media "
                       "free on that drive, because the boot image and the "
                       "ISO both exist while it works; the status bar shows "
                       "the percentage as it goes."
                  ).pack(anchor="w")
        self.iso_button = ttk.Button(step4.body, text="Build the ISO...",
                                     command=self._build_iso)
        self.iso_button.pack(anchor="w", pady=(10, 0))

    # -- entering ----------------------------------------------------------

    def on_enter(self):
        self.ensure_built()
        self._refresh_boards()
        self._refresh_summary()
        if os.name != "nt":
            self.usb_note.configure(
                text="Writing a USB stick needs Windows - it is diskpart that "
                     "partitions the drive. The folder and the ISO work here.")
            self.write_button.state(["disabled"])
        elif not self._disks:
            self._rescan()

    def _refresh_boards(self):
        """List the Macs that can be asked for the release chosen in stage 2.

        Every Mac whose board reaches that release, not every Mac: the board ID
        is what Apple uses to decide what to send, so one that stops earlier
        quietly returns an older macOS. The SMBIOS the EFI presents is offered
        first when it qualifies, and simply is not in the list when it does
        not -- a MacBookPro10,1 is a fine SMBIOS for an Ivy Bridge laptop and a
        useless board for anything past Catalina.
        """
        from .. import boards as board_table

        version = self.app.ocs.macos_version or ""
        self._boards = board_table.options_for(version) if version else []
        self.board_box.configure(
            values=["%s   (%s)" % (label, board)
                    for board, _model, label in self._boards])

        wanted = self._macos_name() or "the release chosen in stage 2"
        if not self._boards:
            self.board_note.configure(
                text="No macOS version has been chosen yet - do stage 2 "
                     "first. The board ID is what tells Apple which release "
                     "to send, so it follows that choice."
                     if not version else
                     "No Mac model in the list can be asked for %s." % wanted)
            self.board_var.set("")
            return

        smbios = self.app.ocs.smbios_model or ""
        chosen = board_table.preferred(self._boards, smbios)
        current = self._board_id()
        if not current or current not in [b for b, _m, _l in self._boards]:
            for board, _model, label in self._boards:
                if board == chosen:
                    self.board_var.set("%s   (%s)" % (label, board))
                    break
        picked = self._model_for(self._board_id())
        if picked and picked == smbios:
            self.board_note.configure(
                text="%s is the SMBIOS stage 3 chose, and its board can be "
                     "asked for %s - so the recovery matches the machine the "
                     "EFI presents." % (picked, wanted))
        else:
            self.board_note.configure(
                text="Asking for %s. %s cannot be used for it, so a Mac that "
                     "can is offered instead - the board only decides which "
                     "macOS Apple sends, not what the EFI presents."
                     % (wanted, smbios or "The SMBIOS from stage 3"))

    def _model_for(self, board_id):
        for board, model, _label in self._boards:
            if board == board_id:
                return model
        return None

    def _macos_name(self):
        version = self.app.ocs.macos_version
        if not version:
            return ""
        from ocs_scripts.datasets import os_data
        return os_data.get_macos_name_by_darwin(version) or ""

    def _board_id(self):
        text = self.board_var.get()
        if "(" in text and text.rstrip().endswith(")"):
            return text.rsplit("(", 1)[1].rstrip(") ").strip()
        return text.strip()

    def _refresh_summary(self):
        state = media_module.media_summary(self.app.paths)
        size = state["recovery_size"]
        self.media_summary.set("EFI folder",
                               "built" if state["efi"] else "not built yet")
        self.media_summary.set(
            "macOS recovery",
            "%.1f GB downloaded" % (size / 1024 ** 3) if state["recovery"]
            else "not downloaded yet")
        self.media_summary.set(
            "Install media",
            str(state["staged_path"]) if state["staged"]
            else "not prepared yet")
        self.stage_button.state(
            ["!disabled"] if state["efi"] and state["recovery"]
            else ["disabled"])
        for button in (self.iso_button,):
            button.state(["!disabled"] if state["staged"] else ["disabled"])
        if os.name == "nt":
            self.write_button.state(
                ["!disabled"] if state["staged"] and self._selected_disk
                else ["disabled"])

    # -- actions -----------------------------------------------------------

    def _download(self):
        def work():
            media_module.ensure_macrecovery(self.app.paths,
                                            report=self.app.report)
            self.app.pump.post(self._refresh_boards)
            board = self._board_id()
            if not board:
                raise media_module.MediaError(
                    "No Mac model has been chosen.",
                    "Pick one from the list - its board ID is what tells "
                    "Apple which macOS to send.")
            return media_module.download_recovery(self.app.paths, board,
                                                  report=self.app.report)

        def done(_folder):
            self._refresh_summary()
            self.banner.show(
                "Recovery downloaded. Prepare the folder next, then write a "
                "USB or build an ISO from it.", "good")

        self.app.run_stage("Downloading the macOS recovery", work, done)

    def _stage(self):
        self.app.run_stage(
            "Preparing the install media",
            lambda: media_module.stage_media(self.app.paths,
                                             report=self.app.report,
                                             progress=self.app.progress),
            self._staged)

    def _staged(self, folder):
        self._refresh_summary()
        self.banner.show(
            "Install media ready in %s. Copy both folders to a FAT32 USB "
            "stick, or use the buttons below. The copy of config.plist on the "
            "media shows auxiliary entries, so macOS Recovery appears in the "
            "picker without pressing Space - the EFI for the installed system "
            "is untouched." % folder, "good")

    def _rescan(self):
        try:
            self._disks = usbwriter.list_disks()
        except Exception as exc:
            self._disks = []
            self.usb_note.configure(
                text="The drive list could not be read (%s). The folder above "
                     "can still be copied to a USB stick by hand." % exc)
        self._selected_disk = None
        self.disk_table.clear()
        for disk in self._disks:
            self.disk_table.add_row(
                str(disk.index),
                ["", disk.model, "%.1f GB" % disk.size_gb,
                 ", ".join(disk.letters) or "no letter"],
                tags=("normal",))
        if not self._disks and os.name == "nt":
            self.usb_note.configure(
                text="No removable drives found. Plug the USB stick in and "
                     "press Rescan. Only removable drives are listed - a "
                     "fixed disk is never offered, whatever it is.")
        self._refresh_summary()

    def _pick_disk(self, item):
        for disk in self._disks:
            if str(disk.index) == item:
                self._selected_disk = disk
                break
        self.disk_table.clear()
        for disk in self._disks:
            chosen = disk is self._selected_disk
            self.disk_table.add_row(
                str(disk.index),
                ["*" if chosen else "", disk.model,
                 "%.1f GB" % disk.size_gb,
                 ", ".join(disk.letters) or "no letter"],
                tags=("selected" if chosen else "normal",))
        self._refresh_summary()

    def _write_usb(self):
        disk = self._selected_disk
        if disk is None:
            self.banner.show("Choose the USB stick to write to first.", "warn")
            return
        try:
            usbwriter.check_target(disk)
        except usbwriter.UnsafeTarget as refused:
            self.banner.show("%s %s" % (refused.message, refused.hint), "bad")
            return

        # The drive is named in full, twice: once in the question and once in
        # what has to be typed. Erasing the wrong stick should take effort.
        if not messagebox.askyesno(
                "Erase this drive?",
                "Everything on this drive will be destroyed:\n\n    %s\n\n"
                "It will be repartitioned and formatted, then the install "
                "media copied onto it.\n\nContinue?" % disk.label(),
                parent=self.app.root, icon="warning", default="no"):
            return

        label = disk.label()
        media_path = media_module.media_dir(self.app.paths)
        self.app.session_log.note("usb", "erasing %s" % label)
        self.app.session_log.note("usb", "diskpart script:\n"
                                  + usbwriter.diskpart_script(disk))

        def done(letter):
            self._rescan()
            self.app.mark_done(self.key)
            self.banner.show(
                "Done. Written to %s and flushed, so the drive is safe to "
                "remove. Boot the target machine from it and pick \"macOS "
                "Base System\" in the OpenCore picker. If the picker looks "
                "empty, press Space - that reveals auxiliary entries, and "
                "macOS Recovery is one." % letter, "good")
            messagebox.showinfo(
                "USB stick ready",
                "The install media is written to %s and safe to unplug.\n\n"
                "Boot the target machine from it and pick \"macOS Base "
                "System\"." % letter, parent=self.app.root)

        self.app.run_stage(
            "Writing the USB stick",
            lambda: usbwriter.write_media(disk, media_path, confirm=label,
                                          report=self.app.report,
                                          progress=self.app.progress),
            done)

    def _build_iso(self):
        if not isoimage.available():
            self.banner.show(
                "This build cannot write ISOs - the pycdlib library is "
                "missing. Rebuild with BUILD_EXE.bat, which installs it.",
                "bad")
            return
        media_path = media_module.media_dir(self.app.paths)
        payload, image, peak = isoimage.space_needed(media_path)

        # Ask where it goes. It used to land in the data folder without a word
        # about it, which is indistinguishable from nothing having happened.
        target = filedialog.asksaveasfilename(
            parent=self.app.root,
            title="Save the macOS install ISO as",
            initialdir=str(self.app.paths.data),
            initialfile="OpCoreForge-macOS.iso",
            defaultextension=".iso",
            filetypes=[("Disc image", "*.iso"), ("All files", "*.*")])
        if not target:
            self.banner.show("Cancelled - no ISO was written.", "warn")
            return
        target = Path(target)

        try:
            isoimage.check_space(target.parent, peak)
        except isoimage.NotEnoughSpace as refused:
            self.banner.show("%s %s" % (refused.message, refused.hint), "bad")
            return

        self.banner.show(
            "Building %s. This copies about %.1f GB twice - the boot image, "
            "then the ISO - so give it a few minutes; the status bar at the "
            "bottom shows how far it has got."
            % (target.name, payload / 1024 ** 3), "info")

        def done(path):
            size = path.stat().st_size / 1024 ** 3
            self.app.mark_done(self.key)
            self.banner.show(
                "Done. %s is written (%.1f GB). Attach it to a virtual "
                "machine set to boot with EFI firmware." % (path, size), "good")
            if messagebox.askyesno(
                    "ISO ready",
                    "%s\n\n%.1f GB.\n\nOpen the folder it is in?"
                    % (path, size), parent=self.app.root):
                self.app.open_path(path.parent)

        self.app.run_stage(
            "Building the ISO",
            lambda: isoimage.build(media_path, target,
                                   report=self.app.report,
                                   progress=self.app.progress),
            done)
