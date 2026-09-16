"""
USBToolBox controller.

USBToolBox's port model, companion-port binding, controller matching-key
selection and Info.plist generation are subtle and easy to get wrong, so
OpCoreForge runs the *original* ``BaseUSBMap.build_kext`` rather than
reimplementing it.

The obstacle is that ``build_kext`` stops three times to talk to a terminal:
once to ask what to do about controllers with no enabled ports, once for a
model identifier when using native Apple classes, and once for a final
"press a key" menu.  So instead of forking the function, this module swaps
``utb_scripts.utils.TUIMenu`` / ``TUIOnlyPrint`` for scriptable stand-ins that
answer from values the GUI has already collected, and records anything
unexpected so it can be surfaced instead of silently hanging on ``input()``.

The same trick captures ``validate_selections``' error list, so the port-type
validation rules stay upstream's.
"""

from __future__ import annotations

import copy
import os
import re
import sys
import types
from pathlib import Path


class MenuBroker:
    """Answers USBToolBox's terminal menus from scripted responses."""

    def __init__(self):
        self.scripted: dict[str, object] = {}
        self.captured: dict[str, list[str]] = {}
        self.unanswered: list[str] = []

    def script(self, title: str, response):
        self.scripted[title] = response

    def clear(self):
        self.scripted.clear()
        self.captured.clear()
        self.unanswered.clear()

    def _record(self, title, in_between):
        lines: list[str] = []
        if callable(in_between):
            pass
        else:
            lines = [str(i) for i in (in_between or [])]
        self.captured.setdefault(title, []).extend(lines)

    def answer(self, title, in_between, exit_menu):
        self._record(title, in_between)
        if title in self.scripted:
            return self.scripted[title]
        self.unanswered.append(title)
        return exit_menu


def install_menu_broker(utils_module) -> MenuBroker:
    """Replace USBToolBox's terminal menus with scriptable stand-ins."""
    broker = MenuBroker()
    real_menu = utils_module.TUIMenu

    class GuiTUIMenu(real_menu):
        def head(self):
            pass

        def print_options(self):
            pass

        def select(self):
            return broker.answer(self.title, self.in_between, real_menu.EXIT_MENU)

        def start(self):
            return broker.answer(self.title, self.in_between, real_menu.EXIT_MENU)

    class GuiTUIOnlyPrint(utils_module.TUIOnlyPrint):
        def start(self):
            broker._record(self.title, self.in_between)
            value = broker.scripted.get(self.title)
            if value is None:
                broker.unanswered.append(self.title)
                return ""
            return value

    utils_module.TUIMenu = GuiTUIMenu
    utils_module.TUIOnlyPrint = GuiTUIOnlyPrint
    # These shell out to `cls`/`clear`, which would flash a console window.
    utils_module.cls = lambda: None
    utils_module.header = lambda text, width=55: None
    utils_module.Utils.cls = lambda self: None
    utils_module.Utils.head = lambda self, text=None, width=55: None
    utils_module.Utils.custom_quit = lambda self: None
    return broker


def _windows_map_source() -> Path:
    """Locate ``utb_windows.py`` as a file, frozen or not.

    Running from a checkout it sits on ``sys.path``. Inside a one-file build
    there is no source tree at all -- modules live as bytecode in the archive
    -- so the spec ships this one file as data and it lands in the extraction
    folder. Both are searched, because both are normal ways to run this.
    """
    from .. import paths as paths_module

    roots = [Path(entry) for entry in sys.path]
    roots.append(paths_module.bundle_dir())
    seen = set()
    for root in roots:
        try:
            candidate = (root / "utb_windows.py").resolve()
        except OSError:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.exists():
            return candidate
    raise ImportError(
        "utb_windows.py is missing from this build. USB port discovery "
        "cannot run without it; a report saved as usb.json on the target "
        "machine can still be imported.")


def _load_windows_map_class():
    """Import ``utb_windows`` without its module-level instantiation.

    Upstream's Windows entry point ends with ``e = WindowsUSBMap()`` so that
    running the file starts the tool.  Importing it as a module would build a
    WMI connection and construct a map object we do not want, so the trailing
    statement is dropped before execution.  If upstream ever removes that line
    the filter simply matches nothing.
    """
    import utb_base  # noqa: F401  (ensures the base class is importable first)

    source_path = _windows_map_source()

    lines = source_path.read_text(encoding="utf-8").splitlines()
    kept = [ln for ln in lines
            if not re.match(r"^\s*[A-Za-z_]\w*\s*=\s*WindowsUSBMap\(\s*\)\s*$", ln)]
    module = types.ModuleType("utb_windows")
    module.__file__ = str(source_path)
    sys.modules["utb_windows"] = module
    exec(compile("\n".join(kept), str(source_path), "exec"), module.__dict__)
    return module.WindowsUSBMap


def _offline_map_class():
    """A map that cannot discover ports but can still edit and build one.

    Live discovery needs Windows PnP data, but a ``usb.json`` captured on the
    target machine is fully portable. This variant lets that file be loaded,
    edited and turned into a kext anywhere -- which is also what makes the USB
    stage testable off-Windows.
    """
    import utb_base

    class OfflineUSBMap(utb_base.BaseUSBMap):
        def get_controllers(self):
            if self.controllers_historical is None:
                self.controllers_historical = []
            self.controllers = self.controllers_historical

        def update_devices(self):
            self.get_controllers()

    return OfflineUSBMap


class UsbUnavailable(Exception):
    """Port discovery could not start. Carries an explanation, not a trace."""

    title = "USB port discovery could not start"

    def __init__(self, message, hint=""):
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self):
        return "%s\n\n%s" % (self.message, self.hint) if self.hint else self.message


def _looks_like_com(exc: Exception) -> bool:
    """Whether this failure is COM/WMI complaining, rather than a real fault."""
    text = ("%s %s" % (type(exc).__name__, exc)).lower()
    return any(needle in text for needle in
               ("x_wmi", "com_error", "com error", "coinitialize",
                "uninitialised_thread", "uninitialized_thread"))


def _explain_start_failure(exc: Exception) -> UsbUnavailable:
    """Turn the ways this fails into something a person can act on."""
    text = "%s: %s" % (type(exc).__name__, exc)
    lowered = text.lower()

    if "uninitialised_thread" in lowered or "coinitialize" in lowered:
        return UsbUnavailable(
            "Windows would not answer the device query.",
            "The COM subsystem this uses was not ready on the thread that "
            "asked. Restarting OpCoreForge normally clears it. If it keeps "
            "happening, run usbtoolbox on the target machine and import its "
            "usb.json here instead.\n\n%s" % text)
    if isinstance(exc, FileNotFoundError):
        return UsbUnavailable(
            "Part of USBToolBox is missing from this build.",
            "Run OpCoreForge.exe --self-test: it lists every bundled file the "
            "three tools read, and names the one that did not make it into "
            "the executable.\n\n%s" % text)
    if "x_wmi" in lowered or "com_error" in lowered or "wmi" in lowered:
        return UsbUnavailable(
            "Windows Management Instrumentation did not respond.",
            "That is the service USBToolBox reads port data from. Try running "
            "OpCoreForge as administrator; if WMI itself is broken on this "
            "machine, \"Import usb.json...\" accepts a map captured with "
            "USBToolBox directly.\n\n%s" % text)
    return UsbUnavailable("USB port discovery could not start.", text)


class UsbController:
    """GUI-facing wrapper around one USBToolBox map."""

    def __init__(self, paths):
        self.paths = paths
        self.map = None
        self.broker = None
        self.last_errors: list[str] = []

    # -- availability -----------------------------------------------------

    @property
    def supported(self) -> bool:
        """Port discovery reads live Windows PnP data; there is no substitute."""
        return os.name == "nt"

    def unavailable_reason(self) -> str:
        if self.supported:
            return ""
        return ("USB port discovery needs Windows: it reads live controller and "
                "port data through WMI and usbdump. Run this stage on the "
                "target machine (booted into Windows) to build UTBMap.kext.")

    # -- lifecycle --------------------------------------------------------

    def start(self):
        from utb_scripts import utils as utb_utils
        self.broker = install_menu_broker(utb_utils)

        if self.supported:
            cls = _load_windows_map_class()
        else:
            cls = _offline_map_class()
        try:
            self.map = cls()
        except Exception as exc:
            raise _explain_start_failure(exc) from exc
        # settings live beside usb.json in the portable data dir
        if not self.map.settings.get("show_friendly_types"):
            self.map.settings["show_friendly_types"] = True
        return self.map

    def discover(self):
        """Refresh controllers and attached devices (upstream get_controllers).

        The WMI connection is made once and reused, but each stage runs on its
        own thread. If that connection ever turns out not to survive the trip,
        rebuilding it costs a second and is a great deal better than telling
        someone their USB ports cannot be read -- so a COM failure gets exactly
        one retry with a fresh connection before it is reported.
        """
        if self.map is None:
            self.start()
        try:
            self.map.get_controllers()
        except Exception as exc:
            if isinstance(exc, UsbUnavailable):
                raise
            if not _looks_like_com(exc):
                raise _explain_start_failure(exc) from exc
            saved = self.map
            self.map = None
            try:
                self.start()
                self.map.get_controllers()
            except Exception as retry_failed:
                self.map = saved
                raise _explain_start_failure(retry_failed) from retry_failed
        self._ensure_selection_indices()
        return self.map.controllers_historical

    @property
    def controllers(self):
        if self.map is None:
            return []
        return self.map.controllers_historical or []

    def _ensure_selection_indices(self):
        """Assign the numbering upstream's select_ports() would assign."""
        if not self.map or not self.map.controllers_historical:
            return
        index = 1
        for controller in self.map.controllers_historical:
            for port in controller["ports"]:
                if "selected" not in port:
                    companion = self.map.get_companion_port(port)
                    port["selected"] = bool(port["devices"]) or (
                        bool(companion["devices"]) if companion else False)
                port["selection_index"] = index
                index += 1

    def save(self):
        if self.map:
            self.map.dump_historical()
            self.map.dump_settings()

    # -- port model -------------------------------------------------------

    def ports(self):
        """Flat list of (controller, port) in the order upstream numbers them."""
        out = []
        for controller in self.controllers:
            for port in controller["ports"]:
                out.append((controller, port))
        return out

    def port_label(self, port) -> str:
        return self.map.port_to_str(port)

    def controller_label(self, controller) -> str:
        return self.map.controller_to_str(controller)

    def companion_of(self, port):
        return self.map.get_companion_port(port)

    def toggle_port(self, port):
        """Toggle one port, honouring the companion-binding setting."""
        new_state = not port["selected"]
        port["selected"] = new_state
        if self.map.settings.get("auto_bind_companions"):
            companion = self.map.get_companion_port(port)
            if companion:
                companion["selected"] = new_state
        return new_state

    def set_port_type(self, port, port_type):
        from utb_scripts import shared
        value = shared.USBPhysicalPortTypes(int(port_type))
        port["type"] = value
        if self.map.settings.get("auto_bind_companions"):
            companion = self.map.get_companion_port(port)
            if companion:
                companion["type"] = value
        return value

    def set_port_comment(self, port, comment):
        port["comment"] = comment or None

    def select_all(self, value: bool):
        for _, port in self.ports():
            port["selected"] = value

    def select_populated(self):
        for _, port in self.ports():
            companion = self.map.get_companion_port(port)
            if port["devices"] or (companion["devices"] if companion else False):
                port["selected"] = True

    def deselect_empty(self):
        for _, port in self.ports():
            companion = self.map.get_companion_port(port)
            if not port["devices"] and not (companion["devices"] if companion else False):
                port["selected"] = False

    def selected_counts(self):
        return [(controller,
                 sum(1 for p in controller["ports"] if p["selected"]),
                 len(controller["ports"]))
                for controller in self.controllers]

    def over_limit_controllers(self):
        return [c for c, sel, _ in self.selected_counts() if sel > 15]

    # -- validation + build ----------------------------------------------

    def validate(self) -> list[str]:
        """Run upstream's validation, capturing its error list."""
        self.broker.clear()
        ok = self.map.validate_selections()
        errors: list[str] = []
        for title, lines in self.broker.captured.items():
            if title == "Selection Validation":
                errors.extend(l for l in lines if l.strip())
        self.last_errors = errors
        return [] if ok else (errors or ["Selection is not valid."])

    def empty_controllers(self):
        return [c for c in self.controllers
                if not any(p["selected"] for p in c["ports"])]

    def build_kext(self, ignore_empty_controllers: bool = True,
                   model_identifier: str | None = None) -> Path:
        """Run upstream's build_kext with its prompts pre-answered."""
        self.broker.clear()
        self.broker.script("Selection Validation",
                           "I" if ignore_empty_controllers else "D")
        self.broker.script("Enter Model Identifier", model_identifier or "")
        # The trailing "Building USBMap" menu just waits for a keypress.
        self.broker.script("Building USBMap", None)

        self.map.build_kext()

        name = "UTBMap.kext"
        if self.map.settings.get("use_native"):
            name = "USBMapLegacy.kext" if self.map.settings.get("use_legacy_native") \
                else "USBMap.kext"
        produced = Path(self.paths.usbmap) / name
        if not produced.exists():
            raise RuntimeError("USBToolBox did not produce %s" % name)
        return produced

    # -- settings ---------------------------------------------------------

    SETTINGS = [
        ("show_friendly_types", "Show friendly connector type names",
         "Show 'USB 3 Type A' instead of the raw number."),
        ("use_native", "Use native Apple classes",
         "Use AppleUSBHostMergeProperties instead of the USBToolBox kext. "
         "Produces USBMap.kext and does not need USBToolBox.kext."),
        ("use_legacy_native", "Use legacy native classes",
         "Use AppleUSBMergeNub, for older macOS. Requires native classes."),
        ("add_comments_to_map", "Add port comments to the map",
         "Write your per-port notes into the generated kext."),
        ("auto_bind_companions", "Bind companion ports",
         "Enabling, disabling or retyping a port does the same to its "
         "USB 2 / USB 3 companion."),
    ]

    def get_setting(self, key):
        return bool(self.map.settings.get(key)) if self.map else False

    def set_setting(self, key, value):
        if self.map:
            self.map.settings[key] = bool(value)
            self.map.dump_settings()

    # -- import / export --------------------------------------------------

    def load_existing_map(self, path: Path):
        """Adopt a usb.json captured earlier (e.g. on the target machine)."""
        import json
        data = json.load(Path(path).open())
        if not isinstance(data, list):
            raise ValueError("Not a USBToolBox usb.json file.")
        self.map.controllers_historical = data
        self._ensure_selection_indices()
        return data

    def snapshot(self):
        return copy.deepcopy(self.controllers)
