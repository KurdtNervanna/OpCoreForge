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

    patch_compatibility_checker()


def patch_compatibility_checker() -> None:
    """Offer the NullMoth NVIDIA driver instead of dead-ending on Turing+ cards.

    OpCore-Simplify's ``check_gpu_compatibility`` prints "You cannot install
    macOS without a supported GPU" and exits when every GPU is unsupported --
    which, to Apple, a Turing or newer GeForce card is. The NullMoth driver
    (see ``opcoreforge.nvidia``) runs those cards on macOS Sequoia, so when one
    is the *only* reason for the dead-end we ask the user whether to enable it,
    and on "yes" reclassify the card as Sequoia-capable before the flow gives
    up.

    Declining -- or a machine that already has a supported GPU, so upstream
    never stops -- preserves upstream's behaviour exactly, including the visible
    stop its own regression test checks for.

    Done here rather than in ``src/vendor`` so re-vendoring a newer upstream
    cannot silently drop it (see the module docstring).
    """
    from ocs_scripts import compatibility_checker

    from . import nvidia

    checker_cls = compatibility_checker.CompatibilityChecker
    original = checker_cls.check_gpu_compatibility
    if getattr(original, "_nullmoth_wrapped", False):
        return

    class _ResumeAfterStop(Exception):
        """Raised in place of upstream's exit so we regain control."""

    def _offer(self, candidates) -> bool:
        names = ", ".join(name for name, _ in candidates)
        plural = len(candidates) != 1
        print("")
        print("No Apple driver exists for {}.".format(names))
        print("The NullMoth driver can run {} on {}:".format(
            "them" if plural else "it", nvidia.TARGET_NAME))
        print("    {}".format(nvidia.REPO_URL))
        print("OpCoreForge writes the OpenCore settings for it; the kexts are "
              "installed by hand after macOS is up.")
        answer = self.utils.request_input(
            "Enable the NullMoth NVIDIA driver for this card? (yes/no): ")
        return str(answer).strip().lower().startswith("y")

    def check_gpu_compatibility(self):
        nvidia.reset()
        if not nvidia.find_candidates(self.hardware_report):
            return original(self)

        # Run upstream, but trap its dead-end exit so we can offer the driver
        # rather than let the program stop. Everything upstream prints and every
        # GPU's Compatibility is in place by the time it reaches the exit.
        saved_exit = self.utils.exit_program
        stopped = {"hit": False}

        def _trap_exit(*_a, **_k):
            stopped["hit"] = True
            raise _ResumeAfterStop()

        self.utils.exit_program = _trap_exit
        try:
            original(self)
        except _ResumeAfterStop:
            pass
        finally:
            self.utils.exit_program = saved_exit

        if not stopped["hit"]:
            # Another GPU is supported; upstream did not stop. Leave its result
            # alone rather than pinning the whole machine to Sequoia.
            return None

        candidates = nvidia.find_candidates(self.hardware_report)
        if not candidates or not _offer(self, candidates):
            # Reproduce upstream's visible stop exactly.
            self.utils.request_input()
            self.utils.exit_program()
            return None

        nvidia.set_engaged(True)
        for _name, props in candidates:
            props["Compatibility"] = nvidia.COMPATIBILITY
            props.pop("OCLP Compatibility", None)
        self._restrict_native_compatibility(nvidia.COMPATIBILITY)
        print("")
        print("NullMoth NVIDIA driver enabled -- target: {}.".format(
            nvidia.TARGET_NAME))
        return None

    check_gpu_compatibility._nullmoth_wrapped = True
    checker_cls.check_gpu_compatibility = check_gpu_compatibility


def patch_iasl_resolver() -> None:
    """Resolve the Windows iasl from the newest acpica release that ships it.

    OpCore-Simplify's ``DSDT.get_latest_iasl`` inspects only the *latest*
    acpica release, but acpica cuts source-only releases with no Windows
    binary (e.g. 20260930 carries just the Unix ``iasl``). Upstream then dies
    with "Could not get latest iasl for Windows" -- both when make_seed.py
    builds the payload and on the executable's first run, which is a hard
    startup failure because every flow needs the ACPI compiler.

    This walks the releases page newest-to-oldest and returns the first
    ``iasl*win*.zip`` it finds -- what upstream meant to do. It falls back to
    upstream's own method if anything about the page is not as expected, and is
    done here so re-vendoring cannot drop it (see the module docstring).
    """
    from ocs_scripts import dsdt as _dsdt

    dsdt_cls = _dsdt.DSDT
    original = dsdt_cls.get_latest_iasl
    if getattr(original, "_nullmoth_wrapped", False):
        return

    def get_latest_iasl(self):
        try:
            github = self.github
            releases_url = "https://github.com/acpica/acpica/releases"
            page = github.fetcher.fetch_and_parse_content(releases_url)
            if page:
                tags = []
                for line in page.splitlines():
                    if "<a" in line and 'href="' in line and "/releases/tag/" in line:
                        tag = line.split("/releases/tag/")[1].split('"')[0]
                        if tag and tag not in tags:
                            tags.append(tag)
                for tag in tags:
                    assets_url = (
                        "https://github.com/acpica/acpica/releases/"
                        "expanded_assets/{}".format(tag))
                    assets_page = github.fetcher.fetch_and_parse_content(assets_url)
                    if not assets_page:
                        continue
                    for asset in github._extract_assets(assets_page):
                        url = (asset.get("url") or "").lower()
                        if "iasl" in url and "win" in url and url.endswith(".zip"):
                            return asset.get("url")
        except Exception:
            pass
        # Anything unexpected: defer to upstream's latest-release-only logic.
        return original(self)

    get_latest_iasl._nullmoth_wrapped = True
    dsdt_cls.get_latest_iasl = get_latest_iasl


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
    patch_iasl_resolver()
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
