"""
OpCore-Simplify controller.

Wraps upstream's ``OCPE`` class and exposes its state as something a GUI can
drive.  The guiding rule here is that OpCoreForge *never reimplements a
decision*: every compatibility check, kext dependency resolution, SMBIOS
suggestion, ACPI patch selection and config.plist rule is executed by the
original OpCore-Simplify code, in the original order.  This module only
replaces the terminal menus that sat on top of them.

Where upstream's menu and its logic are welded together -- ``select_macos_version``
prints a menu *and* validates the answer -- we call the upstream function twice:
once with an empty answer to learn the version it would recommend, and once with
the user's pick so upstream remains the authority on what is valid.  That keeps
the version rules in exactly one place.

The stage order below is the one ``OpCore-Simplify.py``'s main loop enforces,
because later stages genuinely depend on earlier ones (kext selection reads the
chosen macOS version and the ACPI patch set; SMBIOS options mutate both the
patch list and the kext list).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .console import WorkflowAborted


class PayloadUnavailable(Exception):
    """One download in the payload could not be completed.

    Upstream aborts the entire gather run when a single asset is unreachable,
    which turns a transient outage on one kext's host into "no EFI at all".
    Raising this instead lets the UI name the kext and offer to continue
    without it -- an informed choice, rather than a silent omission.

    ``hint`` carries the diagnosis of *why* the download failed, worked out at
    the point of failure while the real exception still existed. Without it the
    UI can only repeat "failed to fetch", which sends people to check a
    connection that was never the problem.
    """

    title = "Download failed"

    def __init__(self, product, message, hint=""):
        super().__init__(message)
        self.product = product
        self.message = message
        self.hint = hint

    def __str__(self):
        if self.hint:
            return "%s\n\n%s" % (self.message, self.hint)
        return self.message


class UnsupportedSelection(Exception):
    """A choice this hardware cannot carry out.

    Distinct from a bug: nothing went wrong, the combination simply does not
    exist. It reaches the UI as an explanation rather than a traceback, which
    is what ``hint`` is for.
    """

    title = "Not possible on this hardware"

    def __init__(self, message, hint=""):
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self):
        return "%s\n\n%s" % (self.message, self.hint) if self.hint else self.message


class Stage:
    REPORT = "report"
    MACOS = "macos"
    SMBIOS = "smbios"
    ACPI = "acpi"
    KEXTS = "kexts"
    BUILD = "build"


class MacOSOption:
    def __init__(self, darwin_major: int, name: str, requires_oclp: bool,
                 darwin_version: str, graphics_supported: bool = True):
        self.darwin_major = darwin_major
        self.name = name
        self.requires_oclp = requires_oclp
        self.darwin_version = darwin_version
        # A version can be offered by the version rules and still be
        # impossible: the range upstream lists is the union across every
        # device, so a Wi-Fi card that OCLP supports up to the newest release
        # widens the list past what the graphics can actually do.
        self.graphics_supported = graphics_supported

    def __repr__(self):
        return "<MacOSOption %s %s%s%s>" % (
            self.darwin_major, self.name, " OCLP" if self.requires_oclp else "",
            "" if self.graphics_supported else " NO-GPU")


class OcsController:
    """Owns one OpCore-Simplify session."""

    def __init__(self, paths, bridge):
        self.paths = paths
        self.bridge = bridge
        self.ocpe = None
        self.notices: list[str] = []

        # Session state, mirroring OpCore-Simplify.py's main() locals.
        self.hardware_report_path = None
        self.hardware_report = None
        self.customized_hardware = None
        self.disabled_devices = None
        self.native_macos_version = None
        self.ocl_patched_macos_version = None
        self.macos_version = None
        self.needs_oclp = False
        self.smbios_model = None
        self.acpi_tables_loaded = False
        self.built = False
        self.compatibility_text = ""

    # -- construction ----------------------------------------------------

    def start(self):
        """Build the upstream OCPE object.

        Constructing ACPIGuru builds a DSDT helper, which locates or downloads
        iasl -- so this can touch the network and belongs on a worker thread.
        """
        import ocs_main
        from .. import patches

        self.ocpe = ocs_main.OCPE()
        self._name_release_failures()
        patches.repair_instance_paths(
            self.paths, self.ocpe, self.ocpe.k, self.ocpe.o, self.ocpe.s,
            getattr(self.ocpe.s, "g", None), getattr(self.ocpe.ac, "smbios", None),
        )
        self.ocpe.result_dir = str(self.paths.results)
        return self.ocpe

    @staticmethod
    def _name_release_failures():
        """Make "can't reach GitHub" failures say which repo they were for.

        Upstream raises a bare ValueError from get_latest_release, so a network
        problem for one kext is indistinguishable from any other error and the
        caller cannot tell the user what actually failed.
        """
        from ocs_scripts import github

        from .. import netdiag

        if getattr(github.Github, "_ocf_wrapped", False):
            return
        original = github.Github.get_latest_release

        def get_latest_release(self, owner, repo):
            netdiag.clear()
            try:
                return original(self, owner, repo)
            except Exception as exc:
                raise PayloadUnavailable(
                    repo,
                    "Could not download %s at this time (%s)" % (repo, exc),
                    hint=netdiag.explain(exc))

        github.Github.get_latest_release = get_latest_release
        github.Github._ocf_wrapped = True

    @property
    def utils(self):
        return self.ocpe.u

    # -- stage 1: hardware report ---------------------------------------

    def validate_report(self, path):
        """Run upstream's validator. Returns (ok, errors, warnings, data, text)."""
        path = self.ocpe.u.normalize_path(str(path))
        is_valid, errors, warnings, data = self.ocpe.v.validate_report(path)
        self.bridge.head("Hardware Report")
        self.ocpe.v.show_validation_report(path, is_valid, errors, warnings)
        return is_valid, errors, warnings, data, self.bridge.current_screen()

    def run_hardware_sniffer(self, report=None):
        """Download and run Hardware Sniffer, exactly as upstream option 'E'.

        Returns (report_path, report_data). Windows only -- upstream's
        gather_hardware_sniffer is a no-op elsewhere.

        ``report`` is called with a short status line before each phase. Two of
        these genuinely take a while and produce no output of their own --
        Hardware Sniffer dumping the ACPI tables, and iasl disassembling them --
        so without this the UI would sit on one unchanging message and look
        like it had hung.
        """
        def say(message):
            if report:
                report(message)

        if os.name != "nt":
            raise RuntimeError(
                "Hardware Sniffer only runs on Windows. Import an existing "
                "Report.json instead."
            )
        say("Fetching Hardware Sniffer...")
        from .. import netdiag

        netdiag.clear()
        sniffer = self.ocpe.o.gather_hardware_sniffer()
        if not sniffer:
            # Upstream returns None for any failure, so the reason has to come
            # from what was recorded on the way down.
            raise PayloadUnavailable(
                "Hardware-Sniffer", "Could not obtain Hardware Sniffer.",
                hint=netdiag.explain())

        report_dir = str(self.paths.sysreport)
        say("Scanning hardware and dumping ACPI tables "
            "(usually under a minute)...")
        output = self.ocpe.r.run({"args": [sniffer, "-e", "-o", report_dir]})
        if output[-1] != 0:
            codes = {
                3: "Error collecting hardware.",
                4: "Error generating hardware report.",
                5: "Error dumping ACPI tables.",
            }
            raise RuntimeError(codes.get(output[-1], "Unknown error (%s)." % output[-1]))

        report_path = os.path.join(report_dir, "Report.json")
        acpi_dir = os.path.join(report_dir, "ACPI")
        say("Reading the hardware report...")
        data = self.ocpe.u.read_file(report_path)
        say("Disassembling ACPI tables with iasl...")
        self.ocpe.ac.read_acpi_tables(acpi_dir)
        self.acpi_tables_loaded = bool(self.ocpe.ac.ensure_dsdt())
        return report_path, data

    def load_acpi_tables(self, folder):
        self.ocpe.ac.read_acpi_tables(self.ocpe.u.normalize_path(str(folder)))
        self.acpi_tables_loaded = bool(self.ocpe.ac.ensure_dsdt())
        return self.acpi_tables_loaded

    def clear_acpi_tables(self):
        """Match upstream's reset at the top of select_hardware_report()."""
        self.ocpe.ac.dsdt = self.ocpe.ac.acpi.acpi_tables = None
        self.acpi_tables_loaded = False

    def set_report(self, path, data):
        """Adopt a validated report and run the compatibility check."""
        self.hardware_report_path = str(path)

        self.bridge.head("Compatibility Checker")
        report, native, oclp = self.ocpe.c.check_compatibility(data)
        self.compatibility_text = self.bridge.current_screen()
        self.hardware_report = report
        self.native_macos_version = native
        self.ocl_patched_macos_version = oclp
        return report

    # -- stage 2: macOS version -----------------------------------------

    def suggested_macos_version(self) -> str:
        """Ask upstream which version it would default to.

        ``select_macos_version`` returns ``request_input(...) or suggested``,
        so answering with an empty string yields upstream's own recommendation
        without duplicating the ~25 lines of GPU-specific logic behind it.
        """
        self.bridge.auto_answer(r"macOS version you want to use", "")
        try:
            return self.ocpe.select_macos_version(
                self.hardware_report, self.native_macos_version,
                self.ocl_patched_macos_version)
        finally:
            self.bridge.clear_auto_answers()

    def graphics_ceiling(self):
        """The newest macOS any GPU in this machine can drive.

        Read off the values OpCore-Simplify's own compatibility checker wrote
        onto each GPU -- ``Compatibility`` for native support and ``OCLP
        Compatibility`` for what OpenCore Legacy Patcher restores -- so this
        adds no rule of its own, it only reports the best of them.

        Returns ``(darwin_version, needs_oclp, gpu_name)``, or ``(None, False,
        None)`` when no GPU in the machine can run any macOS at all.
        """
        parse = self.ocpe.u.parse_darwin_version
        best = None
        best_oclp = False
        best_name = None
        for name, props in (self.hardware_report or {}).get("GPU", {}).items():
            native = (props.get("Compatibility") or (None, None))[0]
            patched = (props.get("OCLP Compatibility") or (None, None))[0]
            for value, is_oclp in ((native, False), (patched, True)):
                if not value:
                    continue
                if best is None or parse(value) > parse(best):
                    best, best_oclp, best_name = value, is_oclp, name
        return best, best_oclp, best_name

    def graphics_limit_note(self) -> str:
        """One sentence on what the graphics cap is, for the UI to show."""
        from ocs_scripts.datasets import os_data

        ceiling, needs_oclp, name = self.graphics_ceiling()
        if not ceiling:
            return ("No GPU in this machine has a macOS driver, so no version "
                    "can be built.")
        return ("%s supports up to %s%s. Later releases are listed because "
                "other devices in this machine reach further, but nothing "
                "would display."
                % (name, os_data.get_macos_name_by_darwin(ceiling),
                   " with OpenCore Legacy Patcher" if needs_oclp else ""))

    def macos_options(self) -> list[MacOSOption]:
        """The version list upstream's menu would print, as data."""
        from ocs_scripts.datasets import os_data

        native = self.native_macos_version
        oclp = self.ocl_patched_macos_version
        oclp_min = int(oclp[-1][:2]) if oclp else 99
        oclp_max = int(oclp[0][:2]) if oclp else 0
        low = min(int(native[0][:2]), oclp_min)
        high = max(int(native[-1][:2]), oclp_max)

        ceiling = self.graphics_ceiling()[0]
        ceiling_major = int(ceiling[:2]) if ceiling else -1

        options = []
        for darwin in range(low, high + 1):
            name = os_data.get_macos_name_by_darwin(str(darwin))
            if not name:
                continue
            options.append(MacOSOption(
                darwin_major=darwin,
                name=name,
                requires_oclp=oclp_min <= darwin <= oclp_max,
                darwin_version="%d.99.99" % darwin,
                graphics_supported=darwin <= ceiling_major,
            ))
        return options

    def check_version_is_possible(self, version: str) -> None:
        """Stop a macOS version that would leave the machine with no graphics.

        The list upstream offers is the union of what *every* device supports,
        so one component with a long support life -- a Broadcom Wi-Fi card that
        OpenCore Legacy Patcher carries to the newest release, say -- stretches
        the list past what the GPU can do. Choosing from the far end then makes
        ``hardware_customization`` disable every GPU, and the next step reads
        the GPU list without checking, so the run dies on
        ``'NoneType' object has no attribute 'items'``.

        Rather than patch that read, refuse the impossible choice here, where
        there is still enough context to say why.
        """
        from ocs_scripts.datasets import os_data

        parse = self.ocpe.u.parse_darwin_version
        ceiling, needs_oclp, name = self.graphics_ceiling()
        if ceiling and parse(str(version)) <= parse(ceiling):
            return

        wanted = os_data.get_macos_name_by_darwin(str(version)) or str(version)
        if not ceiling:
            raise UnsupportedSelection(
                "No GPU in this machine can run macOS.",
                "Every graphics device here is unsupported, so there is no "
                "macOS version to build for. The compatibility report on the "
                "Hardware tab lists what was found.")
        raise UnsupportedSelection(
            "%s needs graphics this machine does not have." % wanted,
            "%s is the newest macOS %s can drive%s.\n\n"
            "%s appears in the list because other devices in this machine are "
            "supported that far -- the list is the union of them all -- but "
            "building for it would give you a Mac that boots to a black "
            "screen.\n\nPick %s or older, or fit a supported graphics card."
            % (os_data.get_macos_name_by_darwin(ceiling), name,
               " with OpenCore Legacy Patcher" if needs_oclp else "",
               wanted, os_data.get_macos_name_by_darwin(ceiling)))

    def apply_macos_version(self, version: str):
        """Run upstream's stage-2 sequence for the chosen version.

        Mirrors option 2 of OpCore-Simplify's main menu exactly: version ->
        hardware customisation -> SMBIOS -> required kexts -> SMBIOS options.
        """
        self.check_version_is_possible(version)
        self.bridge.auto_answer(r"macOS version you want to use", str(version))
        try:
            self.macos_version = self.ocpe.select_macos_version(
                self.hardware_report, self.native_macos_version,
                self.ocl_patched_macos_version)
        finally:
            self.bridge.clear_auto_answers()

        self.bridge.head("Hardware Customization")
        (self.customized_hardware,
         self.disabled_devices,
         self.needs_oclp) = self.ocpe.h.hardware_customization(
            self.hardware_report, self.macos_version)

        self.smbios_model = self.ocpe.s.select_smbios_model(
            self.customized_hardware, self.macos_version)
        return self.macos_version

    def select_acpi_and_kexts(self, first_time: bool):
        """The remainder of upstream's option-1 sequence."""
        # select_acpi_patches reads hardware_report["GPU"] without checking,
        # so an empty GPU set crashes it. That should be unreachable now that
        # the version is validated first, but the failure it produces is
        # unreadable, so it is worth naming here rather than trusting one
        # guard in one place.
        if not self.customized_hardware:
            raise UnsupportedSelection(
                "No macOS version has been applied yet.",
                "Choose a release on the macOS tab and press \"Use this "
                "version\" first; the ACPI patches and kexts are picked for "
                "that release.")
        if not self.customized_hardware.get("GPU"):
            raise UnsupportedSelection(
                "This configuration has no usable graphics.",
                "Every GPU was ruled out for the macOS version selected, so "
                "there is nothing left to build an EFI around. Go back to the "
                "macOS tab and choose a release this machine's graphics "
                "support.")
        if first_time:
            if not self.ocpe.ac.ensure_dsdt():
                raise RuntimeError("ACPI tables have not been loaded.")
            self.ocpe.ac.select_acpi_patches(self.customized_hardware,
                                             self.disabled_devices)
        self.needs_oclp = self.ocpe.k.select_required_kexts(
            self.customized_hardware, self.macos_version, self.needs_oclp,
            self.ocpe.ac.patches)
        self.ocpe.s.smbios_specific_options(
            self.customized_hardware, self.smbios_model, self.macos_version,
            self.ocpe.ac.patches, self.ocpe.k)

    # -- stage 3: SMBIOS -------------------------------------------------

    def smbios_catalog(self):
        """(device, supported_on_selected_macos, recommended_for_platform)."""
        from ocs_scripts.datasets.mac_model_data import mac_devices

        parse = self.ocpe.u.parse_darwin_version
        is_laptop = "Laptop" == self.customized_hardware.get(
            "Motherboard", {}).get("Platform")
        default = self.ocpe.s.select_smbios_model(self.customized_hardware,
                                                  self.macos_version)
        rows = []
        for device in mac_devices:
            supported = (parse(device.initial_support) <= parse(self.macos_version)
                         <= parse(device.last_supported_version))
            platform_ok = (device.name.startswith("MacBook") == is_laptop)
            rows.append((device, supported, platform_ok, device.name == default))
        return rows, default

    def set_smbios_model(self, model: str):
        self.smbios_model = model
        self.ocpe.s.smbios_specific_options(
            self.customized_hardware, self.smbios_model, self.macos_version,
            self.ocpe.ac.patches, self.ocpe.k)

    def generate_smbios_preview(self):
        """Serial/MLB/UUID that would be written, for display only."""
        try:
            return self.ocpe.s.generate_smbios(self.smbios_model)
        except Exception as exc:
            return {"error": str(exc)}

    # -- stage 4: ACPI patches -------------------------------------------

    @property
    def acpi_patches(self):
        return self.ocpe.ac.patches

    def toggle_acpi_patch(self, index: int) -> bool:
        patch = self.ocpe.ac.patches[index]
        patch.checked = not patch.checked
        return patch.checked

    # -- stage 5: kexts ---------------------------------------------------

    @property
    def kexts(self):
        return self.ocpe.k.kexts

    def kext_status(self, kext):
        return self.ocpe.k.get_kext_status(kext, self.macos_version)

    def toggle_kext(self, index: int) -> list[str]:
        """Toggle one kext through upstream's dependency-aware helpers.

        Returns human-readable messages, matching what upstream's terminal menu
        would have printed (conflicts disabled, dependants disabled, and so on).
        """
        maestro = self.ocpe.k
        kext = maestro.kexts[index]
        messages: list[str] = []

        if kext.checked and not kext.required:
            dependants = [k.name for k in maestro.kexts
                          if k.checked and kext.name in k.requires_kexts]
            if maestro.uncheck_kext(index):
                if dependants:
                    messages.append("%s and dependent kext%s %s disabled." % (
                        kext.name, "s" if len(dependants) > 1 else "",
                        ", ".join(dependants)))
            else:
                messages.append("%s is required and cannot be disabled." % kext.name)
            return messages

        status = maestro.get_kext_status(kext, self.macos_version)
        if status == "not_needed":
            from ocs_scripts.datasets import os_data
            messages.append("%s is not needed on %s." % (
                kext.name, os_data.get_macos_name_by_darwin(self.macos_version)))
            return messages
        if status == "requires_newer":
            messages.append("%s requires a newer macOS version." % kext.name)
            return messages

        force = maestro.verify_kext_compatibility([kext.name], self.macos_version)
        conflicts = [k.name for k in maestro.kexts
                     if k.checked and kext.conflict_group_id
                     and k.conflict_group_id == kext.conflict_group_id
                     and k.name != kext.name]
        if maestro.check_kext(index, self.macos_version, kext.name in force):
            if conflicts:
                messages.append("%s selected; mutually exclusive kext%s disabled: %s." % (
                    kext.name, "s" if len(conflicts) > 1 else "", ", ".join(conflicts)))
        else:
            messages.append(
                "%s could not be selected because a required dependency is "
                "unavailable." % kext.name)
        return messages

    # -- stage 6: build ---------------------------------------------------

    def bios_requirements(self):
        return self.ocpe.check_bios_requirements(self.hardware_report,
                                                 self.customized_hardware)

    #: products that several kexts share a single download with
    _PRODUCT_ALIASES = {
        "BrcmPatchRAM": lambda n: n.startswith("Brcm") or n == "BlueToolFixup",
        "USBToolBox": lambda n: n in ("USBToolBox", "UTBDefault"),
        "Ath3kBT": lambda n: n.startswith("Ath3kBT"),
        "IntelBluetoothFirmware": lambda n: n.startswith("IntelB"),
        "VoodooPS2": lambda n: "VoodooPS2" in n,
        "VoodooI2C": lambda n: n.startswith("VoodooI2C"),
    }

    def download_payload(self):
        """Fetch OpenCorePkg + the selected kexts (upstream gather step)."""
        self.bridge.head("Gathering Files")
        try:
            return self.ocpe.o.gather_bootloader_kexts(self.ocpe.k.kexts,
                                                       self.macos_version)
        except PayloadUnavailable:
            raise
        except Exception as exc:
            product = self._failed_product(str(exc))
            if product:
                raise PayloadUnavailable(product, str(exc))
            raise

    @staticmethod
    def _failed_product(message):
        marker = "Could not download "
        if marker in message:
            return message.split(marker, 1)[1].split(" at this time")[0].strip()
        return None

    def drop_payload_product(self, product) -> list[str]:
        """Deselect the kexts a failed download would have provided.

        The failing name may be a download product, a GitHub repo or a kext
        name, and one download often serves several kexts, so all three are
        matched.
        """
        predicate = self._PRODUCT_ALIASES.get(
            product, lambda n, p=product: n == p or n.startswith(p))
        dropped = []
        for kext in self.ocpe.k.kexts:
            if not kext.checked or kext.required:
                continue
            repo = (kext.github_repo or {}).get("repo")
            if predicate(kext.name) or repo == product:
                kext.checked = False
                dropped.append(kext.name)
        return dropped

    def build_efi(self):
        self.ocpe.build_opencore_efi(
            self.customized_hardware, self.disabled_devices, self.smbios_model,
            self.macos_version, self.needs_oclp)
        self.built = True
        return Path(self.ocpe.result_dir)

    # -- USB hand-off -----------------------------------------------------

    def install_usb_map(self, kext_path: Path) -> list[str]:
        """Install UTBMap.kext into the built EFI and drop UTBDefault.kext.

        This is the manual step OpCore-Simplify tells the user to perform after
        building ("Add created UTBMap.kext into EFI\\OC\\Kexts, remove
        UTBDefault.kext"). Doing it here is what makes the three tools one
        workflow.

        config.plist is updated in the same breath, and that part is not
        optional. Upstream's checklist says to re-run OC Snapshot afterwards;
        if the user does not, config still lists UTBDefault.kext -- a kext that
        was just deleted -- and OpenCore halts on a critical error before the
        picker is any use. Swapping the entry here means the boot works whether
        or not a snapshot is ever run, and a snapshot afterwards agrees with
        what is already written.
        """
        actions = []
        kexts_dir = self.paths.kexts_dir
        if not kexts_dir.exists():
            raise RuntimeError("Build an EFI before installing the USB map.")

        destination = kexts_dir / kext_path.name
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(kext_path, destination)
        actions.append("Installed %s into EFI/OC/Kexts" % kext_path.name)

        default_kext = kexts_dir / "UTBDefault.kext"
        if default_kext.exists():
            shutil.rmtree(default_kext)
            actions.append("Removed UTBDefault.kext (replaced by your port map)")

        from .. import eficheck
        actions.extend(eficheck.replace_kext(
            self.paths.config_plist, kexts_dir,
            "UTBDefault.kext", kext_path.name))
        actions.extend(eficheck.repair(self.paths.efi_dir))

        return actions

    def config_needs_xhci_port_limit(self, controllers) -> bool:
        """True when any controller maps more than 15 ports.

        Upstream warns about this in its post-build checklist; surfacing it as
        a real check means the user does not have to count ports by hand.
        """
        for controller in controllers or []:
            selected = sum(1 for p in controller.get("ports", []) if p.get("selected"))
            if selected > 15:
                return True
        return False
