# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for OpCoreForge.

Produces a single portable OpCoreForge.exe. Everything the three tools read at
runtime has to be listed here, because inside a one-file build the "source
directory" is a temporary extraction folder and anything not bundled simply is
not there:

  pt_scripts/snapshot.plist     the OC Snapshot schema - without it ProperTree
                                refuses to snapshot at all
  pt_scripts/menu.plist         right-click menu definitions
  pt_scripts/version.json       version shown in the editor
  ocs_scripts/datasets/*.icns   OpenCore picker background art
  resources/usbdump.exe         the helper USBToolBox shells out to for port
                                discovery (looked up under sys._MEIPASS)
  resources/Info.plist          template the generated USB map is built from
  seed/payload.zip              OpenCorePkg + kexts snapshot, unpacked to the
                                data folder on first run

Build with:  pyinstaller --clean --noconfirm OpCoreForge.spec
"""

import os
import sys
from pathlib import Path

SPEC_DIR = Path(os.path.abspath(SPECPATH))
SRC = SPEC_DIR / "src"
VENDOR = SRC / "vendor"

sys.path.insert(0, str(SRC))

# The three upstream projects each live in their own package namespace so they
# can coexist; every one of those roots must be importable during analysis.
PATHEX = [
    str(SRC),
    str(SRC / "compat"),
    str(VENDOR / "ocs"),
    str(VENDOR / "utb"),
    str(VENDOR / "ptree"),
]


def tree(source, target):
    """Collect a directory's non-Python files as data."""
    items = []
    source = Path(source)
    if not source.exists():
        return items
    for path in source.rglob("*"):
        if not path.is_file() or path.suffix in (".py", ".pyc"):
            continue
        if "__pycache__" in path.parts:
            continue
        items.append((str(path), str(Path(target) / path.relative_to(source).parent)))
    return items


datas = []
datas += tree(VENDOR / "ptree" / "pt_scripts", "pt_scripts")
datas += tree(VENDOR / "ocs" / "ocs_scripts" / "datasets", "ocs_scripts/datasets")
datas += tree(VENDOR / "utb" / "resources", "resources")

# utb_windows.py is read as *source*, not imported: its last line constructs a
# WindowsUSBMap and starts the tool, so that line is stripped before the rest
# is executed. Bytecode in the archive is no use for that, so the file itself
# has to be in the bundle -- without it, scanning USB ports fails with
# "utb_windows.py not found".
if (VENDOR / "utb" / "utb_windows.py").exists():
    datas.append((str(VENDOR / "utb" / "utb_windows.py"), "."))

# The application icon. The .ico is embedded in the executable below as well,
# but the window needs a file it can point Tk at, and on anything but Windows
# Tk cannot read an .ico at all -- hence the PNG beside it.
for _icon in (SPEC_DIR / "build" / "OpCoreForge.ico",
              SPEC_DIR / "assets" / "OpCoreForge-256.png"):
    if _icon.exists():
        datas.append((str(_icon), "."))

# Bundled OpenCore/kext payload (optional -- build/make_seed.py creates it).
seed = SRC / "seed"
if (seed / "payload.zip").exists():
    datas.append((str(seed / "payload.zip"), "seed"))
if (seed / "VERSION").exists():
    datas.append((str(seed / "VERSION"), "seed"))

hiddenimports = [
    # Imported lazily from inside functions, so analysis cannot see them.
    "ocs_scripts.wifi_profile_extractor",
    "ocs_scripts.datasets.codec_layouts",
    "ocs_scripts.datasets.pci_data",
    "ocs_scripts.datasets.mac_model_data",
    "ocs_scripts.datasets.acpi_patch_data",
    "ocs_scripts.datasets.chipset_data",
    "ocs_scripts.datasets.cpu_data",
    "ocs_scripts.datasets.gpu_data",
    "ocs_scripts.datasets.kext_data",
    "ocs_scripts.datasets.os_data",
    "utb_scripts.usbdump",
    "utb_scripts.iokit",
    "utb_base",
    "pt_scripts.config_tex_info",
    "pt_scripts.plist",
    "pt_scripts.plistwindow",
    "pt_scripts.downloader",
    "termcolor2",
    "ansiescapes",
    # OpCore-Simplify verifies downloads against this CA bundle when the
    # platform's own is not on disk -- which is the normal case on Windows.
    # Without it, it falls back to an *unverified* SSL context.
    "certifi",
    # Stage 10 writes a UEFI-bootable ISO with this; without it that one
    # button is unavailable and the stage says so.
    "pycdlib",
]

if sys.platform == "win32":
    # USBToolBox reads PnP device properties through WMI.
    hiddenimports += [
        "wmi", "win32com", "win32com.client", "pythoncom", "pywintypes",
        "win32api", "win32con",
    ]

excludes = [
    # Nothing here is used, and each pulls in a lot of weight.
    "numpy", "pandas", "matplotlib", "PIL", "scipy", "pytest",
    "setuptools", "pip", "test", "unittest", "pydoc_data",
]

block_cipher = None

# Read the single source of truth without importing the package.
_version_ns = {}
exec((SRC / "opcoreforge" / "version.py").read_text(encoding="utf-8"), _version_ns)
VERSION = _version_ns["__version__"]
VERSION_TUPLE = tuple(int(p) for p in VERSION.split(".")) + (0,)


def write_version_resource():
    """Stamp the version into the .exe so Explorer's Properties shows it.

    Without this the only way to tell two builds apart is to run one, which is
    exactly the confusion this is meant to remove.
    """
    if sys.platform != "win32":
        return None
    target = SPEC_DIR / "build" / "file_version_info.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "VSVersionInfo(\n"
        "  ffi=FixedFileInfo(filevers=%(t)r, prodvers=%(t)r, mask=0x3f,\n"
        "                    flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,\n"
        "                    date=(0, 0)),\n"
        "  kids=[\n"
        "    StringFileInfo([StringTable('040904B0', [\n"
        "      StringStruct('CompanyName', 'OpCoreForge'),\n"
        "      StringStruct('FileDescription',\n"
        "                   'OpCore-Simplify + USBToolBox + ProperTree'),\n"
        "      StringStruct('FileVersion', '%(v)s'),\n"
        "      StringStruct('InternalName', 'OpCoreForge'),\n"
        "      StringStruct('OriginalFilename', 'OpCoreForge.exe'),\n"
        "      StringStruct('ProductName', 'OpCoreForge'),\n"
        "      StringStruct('ProductVersion', '%(v)s')])]),\n"
        "    VarFileInfo([VarStruct('Translation', [1033, 1200])])\n"
        "  ]\n"
        ")\n" % {"t": VERSION_TUPLE, "v": VERSION},
        encoding="utf-8")
    return str(target)


version_resource = write_version_resource()

a = Analysis(
    [str(SRC / "OpCoreForge.py")],
    pathex=PATHEX,
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

icon = SPEC_DIR / "build" / "OpCoreForge.ico"

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="OpCoreForge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX mangles some pywin32 DLLs; the payload is already compressed.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon) if icon.exists() else None,
    version=version_resource,
)
