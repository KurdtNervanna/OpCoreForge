"""
Portable path resolution for OpCoreForge.

The application must run from anywhere -- a desktop, a USB stick, a downloads
folder -- and keep its working data beside itself.  Everything the three
upstream tools want to read or write is funnelled through this module so that
nothing lands inside the PyInstaller extraction directory (which is a temp dir
wiped on exit) or the user's home by accident.

Layout, relative to whatever folder the executable is run from::

    OpCoreForge.exe
    OpCoreForge_Data/
        OCK_Files/        OpenCorePkg + kexts cache (OpCore-Simplify)
        Results/          the EFI folder that gets built
        SysReport/        Hardware Sniffer output (Report.json + ACPI tables)
        USBMap/           usb.json, settings.json, UTBMap.kext (USBToolBox)
        ProperTree/       settings.json, Configuration.tex
        bin/              iasl.exe, macserial.exe, Hardware-Sniffer-CLI.exe
        logs/

If the folder holding the executable is not writable (read-only media, a
locked-down Program Files install) we transparently fall back to
``%LOCALAPPDATA%\\OpCoreForge`` on Windows or ``~/.local/share/OpCoreForge``
elsewhere, and record that fact so the UI can mention it.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

APP_NAME = "OpCoreForge"
DATA_DIR_NAME = "OpCoreForge_Data"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """Where read-only bundled resources live.

    Frozen: PyInstaller's extraction dir (``sys._MEIPASS``).
    Source: the ``src`` directory of the checkout.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


def app_dir() -> Path:
    """The folder the user launched us from -- next to the .exe when frozen."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


def _writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / (".ocf_write_test_%d" % os.getpid())
        probe.write_text("ok")
        probe.unlink()
        return True
    except Exception:
        return False


def _fallback_root() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_NAME
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / APP_NAME
    home = Path.home()
    if str(home) not in ("", "/"):
        return home / ".local" / "share" / APP_NAME
    return Path(tempfile.gettempdir()) / APP_NAME


class Paths:
    """Resolved, created-on-demand locations for one run of the application."""

    def __init__(self, data_dir: Path | None = None):
        self.app = app_dir()
        self.bundle = bundle_dir()
        self.using_fallback = False

        if data_dir is not None:
            self.data = Path(data_dir).resolve()
        else:
            preferred = self.app / DATA_DIR_NAME
            if _writable(preferred):
                self.data = preferred
            else:
                self.data = _fallback_root()
                self.using_fallback = True
                _writable(self.data)

        self.ock_files = self.data / "OCK_Files"
        self.results = self.data / "Results"
        self.sysreport = self.data / "SysReport"
        self.usbmap = self.data / "USBMap"
        self.propertree = self.data / "ProperTree"
        self.bin = self.data / "bin"
        self.logs = self.data / "logs"
        self.state_file = self.data / "session.json"
        self.seed_stamp = self.data / ".seed_version"

        for directory in (self.ock_files, self.results, self.sysreport,
                          self.usbmap, self.propertree, self.bin, self.logs):
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

    # -- convenience -----------------------------------------------------

    @property
    def efi_dir(self) -> Path:
        return self.results / "EFI"

    @property
    def oc_dir(self) -> Path:
        return self.results / "EFI" / "OC"

    @property
    def kexts_dir(self) -> Path:
        return self.results / "EFI" / "OC" / "Kexts"

    @property
    def config_plist(self) -> Path:
        return self.results / "EFI" / "OC" / "config.plist"

    @property
    def utb_map_kext(self) -> Path:
        return self.usbmap / "UTBMap.kext"

    def read_state(self) -> dict:
        """Small preferences that should outlive one run. Never load-bearing."""
        import json
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def write_state(self, **values) -> None:
        import json
        state = self.read_state()
        state.update(values)
        try:
            self.state_file.write_text(json.dumps(state, indent=2),
                                       encoding="utf-8")
        except Exception:
            pass

    def resource(self, *parts: str) -> Path:
        """A read-only file shipped inside the bundle."""
        return self.bundle.joinpath(*parts)

    def binary(self, name: str) -> Path:
        """A helper executable, preferring the writable copy then the bundle."""
        local = self.bin / name
        if local.exists():
            return local
        shipped = self.bundle / "bin" / name
        if shipped.exists():
            return shipped
        return local

    def describe(self) -> str:
        note = " (executable folder is not writable)" if self.using_fallback else ""
        return "%s%s" % (self.data, note)


_PATHS: Paths | None = None


def get() -> Paths:
    global _PATHS
    if _PATHS is None:
        _PATHS = Paths()
    return _PATHS


def init(data_dir: Path | None = None) -> Paths:
    global _PATHS
    _PATHS = Paths(data_dir)
    return _PATHS
