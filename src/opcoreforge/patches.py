"""
Runtime adaptation layer for the three vendored upstream projects.

Nothing in ``src/vendor`` is hand-edited -- build/vendor.py only renames the
colliding ``Scripts`` packages.  Every behavioural change OpCoreForge needs is
applied here, at import time, so re-vendoring a newer upstream release cannot
silently drop one of our fixes.

The central problem is that all three tools resolve their working files
relative to their own source directory::

    OpCore-Simplify   OCK_Files, Results, SysReport, iasl.exe, macserial.exe,
                      Hardware-Sniffer-CLI.exe
    USBToolBox        usb.json, settings.json, UTBMap.kext, usbdump.exe
    ProperTree        settings.json, Configuration.tex

Inside a PyInstaller one-file build that source directory is a temporary
extraction folder which is deleted when the process exits, so a naive bundle
would re-download iasl and OpenCorePkg on *every* launch and lose the user's
port map.

Rather than duplicating upstream logic, we exploit the fact that all of those
paths are derived from the module's own ``__file__``.  Reassigning ``__file__``
on the imported module relocates every path that module computes, in one line,
with no forked code to keep in sync:

    <data>/bin/kext_maestro.py   ->  dirname(dirname(f)) = <data>  -> OCK_Files
    <data>/bin/smbios.py         ->  dirname(f)          = bin     -> macserial
    <data>/bin/dsdt.py           ->  dirname(f)          = bin     -> iasl
    <data>/ocs_main.py           ->  dirname(f)          = <data>  -> Results

The one path that must *not* move with it is OpCore-Simplify's bundled
``datasets/background_picker.icns``, so that single asset is staged into the
bin directory alongside the relocated modules.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import paths as _paths

_applied = False


def _stage(src: Path, dst: Path) -> bool:
    """Copy a bundled asset next to the relocated modules, if not already there."""
    try:
        if not src.exists():
            return False
        if dst.exists() and dst.stat().st_size == src.stat().st_size:
            return True
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# OpCore-Simplify
# ---------------------------------------------------------------------------

def patch_opcore_simplify(paths: _paths.Paths) -> None:
    import ocs_main
    from ocs_scripts import dsdt, gathering_files, kext_maestro, resource_fetcher, smbios

    from . import netdiag

    # Watch every request for the reason it failed. Upstream catches and
    # prints its own exceptions, so by the time a caller sees "Failed to
    # fetch release information" the cause is gone -- and the cause is often
    # not a network fault at all (a wrong system clock invalidates every
    # certificate). This only observes; upstream's behaviour is unchanged.
    netdiag.install(resource_fetcher)

    bin_dir = paths.bin
    bin_dir.mkdir(parents=True, exist_ok=True)

    # Relocate every derived working path (see module docstring).
    kext_maestro.__file__ = str(bin_dir / "kext_maestro.py")
    gathering_files.__file__ = str(bin_dir / "gathering_files.py")
    smbios.__file__ = str(bin_dir / "smbios.py")
    dsdt.__file__ = str(bin_dir / "dsdt.py")
    ocs_main.__file__ = str(paths.data / "ocs_main.py")

    # gathering_files also loads datasets/background_picker.icns relative to
    # itself -- that one is a real bundled asset, so stage it into place.
    # A frozen build reports module __file__ inside the archive, so look
    # beside the bundle first and fall back to the module's own directory.
    icns = "background_picker.icns"
    candidates = [paths.bundle / "ocs_scripts" / "datasets" / icns]
    try:
        candidates.append(
            Path(kext_maestro.kext_data.__file__).resolve().parent / icns)
    except Exception:
        pass
    for candidate in candidates:
        if _stage(candidate, bin_dir / "datasets" / icns):
            break

    # Class attributes are computed in __init__, so instances created after
    # this point pick the new locations up automatically. Anything already
    # constructed is repaired defensively below.
    kext_maestro.KextMaestro.ock_files_dir = str(paths.ock_files)


def repair_instance_paths(paths: _paths.Paths, *objects) -> None:
    """Fix up already-constructed OpCore-Simplify objects.

    Some components are instantiated as a side effect of importing others, so
    they may have captured the pre-patch locations.
    """
    for obj in objects:
        if obj is None:
            continue
        if hasattr(obj, "ock_files_dir"):
            obj.ock_files_dir = str(paths.ock_files)
        if hasattr(obj, "download_history_file"):
            obj.download_history_file = str(paths.ock_files / "history.json")
        if hasattr(obj, "script_dir"):
            obj.script_dir = str(paths.bin)
        if hasattr(obj, "result_dir"):
            obj.result_dir = str(paths.results)


# ---------------------------------------------------------------------------
# USBToolBox
# ---------------------------------------------------------------------------

class _UsbdumpSubprocess:
    """Rewrites the helper path USBToolBox uses to invoke usbdump.exe.

    Upstream resolves ``resources/usbdump.exe`` relative to the *current
    working directory* when not frozen, which is wrong for a GUI that must not
    chdir.  Rather than fork ``get_controllers``, we intercept the one
    subprocess call it makes and substitute the real location.
    """

    def __init__(self, real_path: Path):
        self._real_path = real_path

    def __getattr__(self, name):
        return getattr(subprocess, name)

    def run(self, args, *a, **kw):
        try:
            first = args if isinstance(args, (str, os.PathLike)) else args[0]
            if "usbdump" in os.path.basename(str(first)).lower():
                if self._real_path.exists():
                    args = str(self._real_path) if isinstance(args, (str, os.PathLike)) \
                        else [str(self._real_path)] + list(args[1:])
        except Exception:
            pass
        return subprocess.run(args, *a, **kw)


def usbdump_path(paths: _paths.Paths) -> Path:
    for candidate in (paths.bin / "usbdump.exe",
                      paths.bundle / "resources" / "usbdump.exe",
                      paths.bundle / "vendor" / "utb" / "resources" / "usbdump.exe"):
        if candidate.exists():
            return candidate
    return paths.bundle / "resources" / "usbdump.exe"


def patch_usbtoolbox(paths: _paths.Paths) -> None:
    from utb_scripts import shared, utils as utb_utils

    # Where usb.json / settings.json / UTBMap.kext live.
    shared.current_dir = paths.usbmap

    # utb_scripts.utils.Utils.__init__ chdir's into its own module directory to
    # look for colors.json. Inside a one-file build that directory does not
    # exist -- the package is bytecode in an archive -- so constructing the map
    # died with "The system cannot find the file specified: ...\utb_scripts".
    # Point the module at a real directory instead, the same relocation used
    # for OpCore-Simplify's helpers above. There is no colors.json upstream, so
    # the lookup misses either way and the behaviour is unchanged.
    paths.bin.mkdir(parents=True, exist_ok=True)
    utb_utils.__file__ = str(paths.bin / "utb_utils.py")

    # Where the kext Info.plist template lives (read-only, from the bundle).
    for candidate in (paths.bundle / "resources",
                      paths.bundle / "vendor" / "utb" / "resources"):
        if (candidate / "Info.plist").exists():
            shared.resource_dir = candidate
            break

    try:
        from utb_scripts import usbdump
        usbdump.subprocess = _UsbdumpSubprocess(usbdump_path(paths))
    except Exception:
        # usbdump imports ctypes/Windows types; absent off-Windows, which is
        # fine because port discovery is a Windows-only stage anyway.
        pass

    # BaseUSBMap.__init__ ends by launching the terminal menu loop. OpCoreForge
    # drives the same object from the GUI, so neutralise that entry point; the
    # data model and kext builder are untouched.
    import utb_base
    utb_base.BaseUSBMap.monu = lambda self: None


# ---------------------------------------------------------------------------
# ProperTree
# ---------------------------------------------------------------------------

def patch_propertree(paths: _paths.Paths) -> None:
    import pt_app

    tex_path = paths.propertree / "Configuration.tex"

    def get_best_tex_path(self):
        # Upstream looks next to its own source file, which inside a one-file
        # build is a temp dir; keep the downloaded tex with the user's data so
        # config.plist tooltips survive a restart.
        return str(tex_path)

    pt_app.ProperTree.get_best_tex_path = get_best_tex_path


def load_propertree_settings(pt_instance, paths: _paths.Paths) -> None:
    """Merge the user's persisted ProperTree settings over the bundled defaults."""
    import json
    settings_file = paths.propertree / "settings.json"
    if not settings_file.exists():
        return
    try:
        saved = json.load(settings_file.open())
        if isinstance(saved, dict):
            pt_instance.settings.update(saved)
            pt_instance.update_settings()
    except Exception:
        pass


def save_propertree_settings(pt_instance, paths: _paths.Paths) -> None:
    import json
    try:
        settings_file = paths.propertree / "settings.json"
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        json.dump(pt_instance.settings, settings_file.open("w"), indent=4)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def apply_all(paths: _paths.Paths | None = None) -> _paths.Paths:
    """Apply every runtime patch. Safe to call more than once."""
    global _applied
    paths = paths or _paths.get()
    if _applied:
        return paths

    # USBToolBox's terminal helper imports `ansiescapes`, whose PyPI release
    # reads TERM_PROGRAM at import time and raises without it.
    os.environ.setdefault("TERM_PROGRAM", "")

    patch_opcore_simplify(paths)
    patch_usbtoolbox(paths)
    patch_propertree(paths)
    _applied = True
    return paths


def bootstrap_sys_path() -> None:
    """Put the vendored trees and the compat shims on ``sys.path``."""
    bundle = _paths.bundle_dir()
    candidates = [
        bundle / "compat",
        bundle / "vendor" / "ocs",
        bundle / "vendor" / "utb",
        bundle / "vendor" / "ptree",
    ]
    for path in candidates:
        entry = str(path)
        if path.exists() and entry not in sys.path:
            sys.path.insert(0, entry)
