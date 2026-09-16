"""
Turn assets/OpCoreForge.png into the Windows .ico the build embeds.

Run this only when the artwork changes. The generated .ico is committed
alongside the source image on purpose: BUILD_EXE.bat runs on a Windows machine
that has no Pillow, and an executable without its icon because a build
dependency was missing is a silly way to lose a detail everyone sees.

    python build/make_icon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "assets" / "OpCoreForge.png"
ICO = ROOT / "build" / "OpCoreForge.ico"
WINDOW_PNG = ROOT / "assets" / "OpCoreForge-256.png"

#: Windows picks the nearest size: 16 in the title bar, 32 in the taskbar and
#: alt-tab, 48 in Explorer's medium view, 256 for the large ones.
SIZES = (16, 24, 32, 48, 64, 128, 256)


def main() -> int:
    try:
        from PIL import Image
    except ImportError:
        print("Pillow is needed to regenerate the icon: pip install Pillow")
        return 1

    if not SOURCE.exists():
        print("no artwork at %s" % SOURCE)
        return 1

    image = Image.open(SOURCE).convert("RGBA")
    if image.width != image.height:
        print("the artwork must be square, not %dx%d" % image.size)
        return 1

    ICO.parent.mkdir(parents=True, exist_ok=True)
    image.save(ICO, format="ICO",
               sizes=[(size, size) for size in SIZES])

    # Tk cannot read an .ico on anything but Windows, and reads PNG natively,
    # so the window icon is a plain 256px PNG.
    image.resize((256, 256), Image.LANCZOS).save(WINDOW_PNG, format="PNG")

    print("wrote %s (%s) and %s"
          % (ICO.relative_to(ROOT),
             ", ".join("%d" % s for s in SIZES),
             WINDOW_PNG.relative_to(ROOT)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
