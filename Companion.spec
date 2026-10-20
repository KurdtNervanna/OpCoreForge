# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for OpCoreForge Companion, the macOS app.

Produces ``dist/OpCoreForge Companion.app``. It has to be built on a Mac --
PyInstaller cannot cross-compile -- which is what
.github/workflows/companion.yml does, on an Intel runner because every machine
OpCoreForge builds an EFI for is an Intel or AMD PC.

The companion needs none of the three bundled upstream projects, only
OpCoreForge's own EFI checks, theme and widgets, so ``src/vendor`` is not on
the path and the app stays small.

Build with:  pyinstaller --clean --noconfirm Companion.spec
"""

import os
import sys
from pathlib import Path

SPEC_DIR = Path(os.path.abspath(SPECPATH))
SRC = SPEC_DIR / "src"
sys.path.insert(0, str(SRC))

from opcoreforge.version import __version__  # noqa: E402

NAME = "OpCoreForge Companion"
ICON = SPEC_DIR / "assets" / "OpCoreForge.png"

a = Analysis(
    [str(SRC / "Companion.py")],
    pathex=[str(SRC)],
    hiddenimports=["certifi"],
    excludes=["ocs_scripts", "pt_scripts", "utb_scripts"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    console=False,
    argv_emulation=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME)
app = BUNDLE(
    coll,
    name="%s.app" % NAME,
    # PyInstaller converts the PNG to .icns when Pillow is installed.
    icon=str(ICON) if ICON.exists() else None,
    bundle_identifier="io.github.kurdtnervanna.opcoreforge.companion",
    version=__version__,
    info_plist={
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "LSMinimumSystemVersion": "10.13",
        "NSHighResolutionCapable": True,
        "NSRemovableVolumesUsageDescription":
            "The companion reads the EFI folder from the OpCoreForge USB "
            "stick to install it on this Mac's disk.",
    },
)
