"""
Copying the companion off the install stick and into /Applications.

The companion starts life inside a disk image on the USB stick. Once OpenCore
is on the internal disk the stick is meant to go back in a drawer, so the app
offers to keep a copy of itself on the Mac. ``ditto`` is used because it keeps
the bundle's symlinks and code signature intact, which a plain copy does not.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

from . import installer

APPLICATIONS = Path("/Applications")
DITTO = "/usr/bin/ditto"


def bundle_path(executable: str = None):
    """The ``.app`` this process runs from, or None when not in a bundle."""
    path = Path(executable or sys.executable).resolve()
    for parent in path.parents:
        if parent.suffix == ".app":
            return parent
    return None


def is_installed(bundle, applications: Path = APPLICATIONS) -> bool:
    return bundle is not None and Path(bundle).parent == Path(applications)


def copy_script(bundle: Path, applications: Path = APPLICATIONS,
                ditto: str = DITTO) -> str:
    """Shell commands that replace the copy in /Applications. Runs nothing."""
    q = shlex.quote
    target = Path(applications) / Path(bundle).name
    return "\n".join([
        "set -e",
        "rm -rf %s" % q(str(target)),
        "%s %s %s" % (q(ditto), q(str(bundle)), q(str(target))),
        "",
    ])


def copy_to_applications(bundle: Path, applications: Path = APPLICATIONS,
                         runner=None) -> Path:
    """Copy *bundle* into *applications*, asking for a password if needed.

    An administrator can normally write to /Applications without one, so the
    copy is tried as the user first and only escalated if that is refused.
    """
    target = Path(applications) / Path(bundle).name
    script = copy_script(bundle, applications)
    done = subprocess.run(["/bin/sh", "-c", script], capture_output=True)
    if done.returncode != 0:
        installer.run_privileged(script, runner)
    if not target.is_dir():
        raise installer.InstallError(
            "The copy in %s did not appear." % applications,
            "Drag the app from the disk image into Applications instead.")
    return target
