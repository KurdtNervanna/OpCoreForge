"""
Prepare the screenshots the README uses.

The capturing is already done by ``tools/test_gui.py``: it drives the real
window against the synthetic hardware report ``make_fixture.py`` produces -- a
Comet Lake i9-10900K that does not exist -- and photographs every stage into
``_shots/``. Nothing here re-runs that; this only picks the handful worth
publishing, crops them to the window and writes them to ``docs/screenshots/``.

Cropping is the only processing. Xvfb's screen is usually taller than the
window asks for, so the spare rows come out black; nothing else is touched, and
in particular nothing is retouched -- what is in the README is what the
application actually renders.

    xvfb-run -a python3 tools/test_gui.py    # capture into _shots/
    python3 tools/make_shots.py              # publish into docs/screenshots/
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "_shots"
PUBLISHED = ROOT / "docs" / "screenshots"

#: The window asks for this size; anything past it is Xvfb's empty screen.
WINDOW = (1280, 820)

#: Captured name -> published name. Ordered as the README uses them.
PUBLISH = [
    ("03-macos.png", "01-macos-version.png"),
    ("04-smbios.png", "02-smbios.png"),
    ("05-acpi.png", "03-acpi-patches.png"),
    ("06-kexts.png", "04-kexts.png"),
    ("08-build.png", "05-build-efi.png"),
    ("prompt-01.png", "06-inline-prompt.png"),
]


def main() -> int:
    if not SHOTS.is_dir():
        print("no _shots/ yet -- run: xvfb-run -a python3 tools/test_gui.py")
        return 1

    try:
        from PIL import Image
    except ImportError:
        Image = None
        print("Pillow is missing, so the shots are published uncropped")

    PUBLISHED.mkdir(parents=True, exist_ok=True)
    missing = []
    for source_name, published_name in PUBLISH:
        source = SHOTS / source_name
        target = PUBLISHED / published_name
        if not source.exists():
            missing.append(source_name)
            continue
        if Image is None:
            shutil.copy2(source, target)
        else:
            with Image.open(source) as image:
                image.crop((0, 0, min(WINDOW[0], image.width),
                            min(WINDOW[1], image.height))
                           ).save(target, optimize=True)
        print("  %-22s -> %-24s %d KB"
              % (source_name, published_name, target.stat().st_size // 1024))

    if missing:
        print("not captured yet: %s" % ", ".join(missing))
        print("(the GUI suite stops early without network access to the "
              "kext hosts, so the later stages may be absent)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
