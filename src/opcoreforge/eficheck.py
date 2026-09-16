"""
Checking a built EFI against its own config.plist.

OpenCore does not tolerate a config that promises a file the ESP does not
have. If ``Kernel -> Add`` lists a kext and the folder is not there, the boot
stops dead with

    OC: Plist Kexts\\Something.kext\\Contents\\Info.plist is missing for
    injected kext Something.kext ()
    Halting on critical error

and that is the end of it -- no picker, no recovery, nothing to click. The
same is true of a missing ACPI table, driver or tool.

The way a config and a folder drift apart in this workflow is mundane. The
EFI is built with UTBDefault.kext (a generic USB map) and its entry is written
into config.plist at build time. Then the USB stage builds a real map and
swaps UTBMap.kext in for UTBDefault.kext *on disk*. Upstream's instructions
say to re-run OC Snapshot afterwards so config.plist catches up; if that never
happens, config still names a kext that has been deleted. The stick boots as
far as the picker and then halts on the kext it cannot find.

So there are two jobs here:

- ``replace_kext`` keeps config.plist in step at the moment the Kexts folder
  changes, which is the actual fix -- nothing should depend on the user
  remembering a manual step.
- ``audit`` and ``repair`` are the net underneath it. Every path the config
  names is checked against the disk before the EFI is staged onto media, and
  an entry pointing at nothing is dropped, which is exactly what a snapshot
  would have done. It is cheap, it is the last moment before a USB stick gets
  written, and it turns a dead boot into a line in the log.

Nothing here needs Tk, Windows or a network, so it is all testable.
"""

from __future__ import annotations

import plistlib
from pathlib import Path

#: config.plist sections that name a file, and where that file lives.
#: (section, subsection, key holding the relative path, folder under OC/)
FILE_SECTIONS = (
    ("ACPI", "Add", "Path", "ACPI"),
    ("UEFI", "Drivers", "Path", "Drivers"),
    ("Misc", "Tools", "Path", "Tools"),
)


class Problem:
    """One thing the config claims that the disk does not back up."""

    def __init__(self, section, entry, detail, fatal=True, kind="missing"):
        self.section = section
        self.entry = entry
        self.detail = detail
        self.fatal = bool(fatal)
        self.kind = kind

    def __str__(self):
        return "%s: %s - %s" % (self.section, self.entry, self.detail)

    def __repr__(self):
        return "<Problem %s %s fatal=%s>" % (self.section, self.entry, self.fatal)


def load_config(config_path: Path) -> dict:
    with Path(config_path).open("rb") as handle:
        return plistlib.load(handle)


def save_config(config_path: Path, config: dict) -> None:
    with Path(config_path).open("wb") as handle:
        plistlib.dump(config, handle, sort_keys=False)


# -- reading a kext -------------------------------------------------------

def _plist_path(bundle: Path) -> str:
    """The Info.plist inside *bundle*, relative to it, in OpenCore's spelling."""
    direct = bundle / "Contents" / "Info.plist"
    if direct.is_file():
        return "Contents/Info.plist"
    if (bundle / "Info.plist").is_file():
        return "Info.plist"
    for candidate in sorted(bundle.rglob("Info.plist")):
        return candidate.relative_to(bundle).as_posix()
    return ""


def kext_entry(kexts_dir: Path, name: str) -> dict:
    """A ``Kernel -> Add`` entry for the kext folder *name*.

    Built the way OpCore-Simplify builds one, so a config edited here and a
    config written by a fresh build look the same: the executable path is only
    filled in when the binary is really there (a codeless kext such as
    UTBMap.kext has none), and the kernel range is left open.
    """
    kexts_dir = Path(kexts_dir)
    bundle = kexts_dir / name
    if not bundle.is_dir():
        raise FileNotFoundError("%s is not in %s" % (name, kexts_dir))

    plist_rel = _plist_path(bundle)
    if not plist_rel:
        raise FileNotFoundError("%s has no Info.plist" % name)

    try:
        with (bundle / plist_rel).open("rb") as handle:
            info = plistlib.load(handle)
    except Exception:
        info = {}

    executable = ""
    binary = info.get("CFBundleExecutable")
    if binary and (bundle / "Contents" / "MacOS" / binary).is_file():
        executable = "Contents/MacOS/%s" % binary

    return {
        "Arch": "Any",
        "BundlePath": name,
        "Comment": "",
        "Enabled": True,
        "ExecutablePath": executable,
        "MaxKernel": "",
        "MinKernel": "",
        "PlistPath": plist_rel,
    }


# -- keeping config.plist in step -----------------------------------------

def replace_kext(config_path: Path, kexts_dir: Path, old_name: str,
                 new_name: str) -> list:
    """Swap *old_name* for *new_name* in ``Kernel -> Add``.

    The new entry takes the old one's place in the list, which matters: load
    order is dependency order, and the entry being replaced was already
    sequenced after whatever it needs. Its Arch, Enabled state and kernel
    range are carried over for the same reason; only the paths change.

    With no old entry to replace the new one is appended, which is still after
    every kext already listed, so a kext that depends on one of them is fine.
    Returns a list of what changed, empty when there was nothing to do.
    """
    config_path = Path(config_path)
    kexts_dir = Path(kexts_dir)
    if not config_path.is_file():
        return []

    config = load_config(config_path)
    entries = config.setdefault("Kernel", {}).setdefault("Add", [])
    actions = []

    fresh = None
    if (kexts_dir / new_name).is_dir():
        fresh = kext_entry(kexts_dir, new_name)

    index = None
    for position, entry in enumerate(entries):
        if entry.get("BundlePath") == old_name:
            index = position
            break

    already = [e for e in entries if e.get("BundlePath") == new_name]

    if fresh is None:
        # Nothing to install. Only tidy up: an entry for a kext that is gone
        # is the thing that halts the boot.
        if index is not None and not (kexts_dir / old_name).is_dir():
            entries.pop(index)
            actions.append("config.plist: removed %s (not in the Kexts folder)"
                           % old_name)
    elif index is not None:
        old = entries[index]
        for key in ("Arch", "Enabled", "MaxKernel", "MinKernel"):
            if key in old:
                fresh[key] = old[key]
        entries[index] = fresh
        actions.append("config.plist: %s replaced by %s in Kernel -> Add"
                       % (old_name, new_name))
        for duplicate in already:
            entries.remove(duplicate)
            actions.append("config.plist: removed a duplicate %s entry" % new_name)
    elif already:
        for entry in already:
            changed = [k for k in ("PlistPath", "ExecutablePath")
                       if entry.get(k) != fresh[k]]
            entry.update({k: fresh[k] for k in changed})
            if changed:
                actions.append("config.plist: corrected %s for %s"
                               % (", ".join(changed), new_name))
            if not entry.get("Enabled"):
                entry["Enabled"] = True
                actions.append("config.plist: enabled %s" % new_name)
    else:
        entries.append(fresh)
        actions.append("config.plist: added %s to Kernel -> Add" % new_name)

    if actions:
        save_config(config_path, config)
    return actions


def sync_usb_map(efi_dir: Path) -> list:
    """Make ``Kernel -> Add`` agree with whichever USB map is installed.

    Idempotent, and safe to call on an EFI that is already correct. It exists
    for the EFI that was built before this check did: UTBMap.kext copied into
    the Kexts folder, UTBDefault.kext deleted, and config.plist never told
    about either -- which halts the boot on the kext that is gone *and* leaves
    the port map doing nothing. Both halves are the same edit.

    Only this one pair is handled automatically. Adding an arbitrary kext to a
    config means deciding where it goes in the load order, and guessing that is
    worse than saying it is unlisted.
    """
    efi_dir = Path(efi_dir)
    kexts = efi_dir / "OC" / "Kexts"
    if not (kexts / "UTBMap.kext").is_dir():
        return []
    return replace_kext(efi_dir / "OC" / "config.plist", kexts,
                        "UTBDefault.kext", "UTBMap.kext")


# -- auditing --------------------------------------------------------------

def _kext_problems(config: dict, oc_dir: Path) -> list:
    problems = []
    kexts = oc_dir / "Kexts"
    for entry in config.get("Kernel", {}).get("Add", []) or []:
        bundle_name = entry.get("BundlePath") or ""
        if not bundle_name:
            continue
        enabled = bool(entry.get("Enabled", True))
        bundle = kexts / bundle_name
        where = "Kernel -> Add"
        if not bundle.is_dir():
            problems.append(Problem(
                where, bundle_name,
                "the kext is not in EFI/OC/Kexts", fatal=enabled))
            continue
        plist_rel = entry.get("PlistPath") or "Contents/Info.plist"
        if not (bundle / plist_rel).is_file():
            problems.append(Problem(
                where, bundle_name,
                "%s is missing inside the kext" % plist_rel, fatal=enabled))
            continue
        executable = entry.get("ExecutablePath") or ""
        if executable and not (bundle / executable).is_file():
            problems.append(Problem(
                where, bundle_name,
                "%s is missing inside the kext" % executable, fatal=enabled))
    return problems


def _listed_bundles(config: dict) -> set:
    return {e.get("BundlePath") for e in config.get("Kernel", {}).get("Add", []) or []
            if e.get("BundlePath")}


def _unlisted_kexts(config: dict, oc_dir: Path) -> list:
    """Kexts sitting in the folder that no entry loads.

    Not fatal -- OpenCore ignores them -- but it is how a USB port map ends up
    installed and doing nothing, so it is worth saying out loud.
    """
    kexts = oc_dir / "Kexts"
    if not kexts.is_dir():
        return []
    listed = _listed_bundles(config)
    problems = []
    for child in sorted(kexts.iterdir()):
        if child.is_dir() and child.name.endswith(".kext") and child.name not in listed:
            problems.append(Problem(
                "Kernel -> Add", child.name,
                "in the Kexts folder but not loaded by config.plist",
                fatal=False, kind="unlisted"))
    return problems


def _file_problems(config: dict, oc_dir: Path) -> list:
    problems = []
    for section, subsection, key, folder in FILE_SECTIONS:
        for entry in config.get(section, {}).get(subsection, []) or []:
            if isinstance(entry, str):          # older UEFI -> Drivers shape
                relative, enabled = entry, True
            else:
                relative = entry.get(key) or ""
                enabled = bool(entry.get("Enabled", True))
            if not relative:
                continue
            if not (oc_dir / folder / relative).is_file():
                problems.append(Problem(
                    "%s -> %s" % (section, subsection), relative,
                    "not in EFI/OC/%s" % folder, fatal=enabled))
    return problems


def audit(efi_dir: Path) -> list:
    """Every path config.plist names that the EFI folder does not have.

    Fatal problems are the ones that stop OpenCore; the rest are reported so
    they can be looked at, not acted on.
    """
    efi_dir = Path(efi_dir)
    oc_dir = efi_dir / "OC"
    config_path = oc_dir / "config.plist"
    if not config_path.is_file():
        return [Problem("config.plist", "config.plist",
                        "there is no config.plist in EFI/OC")]
    try:
        config = load_config(config_path)
    except Exception as error:
        return [Problem("config.plist", "config.plist",
                        "cannot be read: %s" % error)]

    problems = _kext_problems(config, oc_dir)
    problems.extend(_file_problems(config, oc_dir))
    problems.extend(_unlisted_kexts(config, oc_dir))
    return problems


def fatal(problems) -> list:
    return [problem for problem in problems if problem.fatal]


def repair(efi_dir: Path, report=None) -> list:
    """Drop entries that point at nothing. Returns what was changed.

    This is deliberately narrow. Removing an entry whose file is absent is
    what OC Snapshot does and it cannot make a working EFI worse, whereas
    *adding* an entry involves load order and dependencies -- that belongs to
    the code that put the file there, not to a repair pass.
    """
    efi_dir = Path(efi_dir)
    oc_dir = efi_dir / "OC"
    config_path = oc_dir / "config.plist"
    problems = [p for p in audit(efi_dir)
                if p.kind == "missing" and p.section != "config.plist"]
    if not problems or not config_path.is_file():
        return []

    try:
        config = load_config(config_path)
    except Exception:
        # A config that cannot be parsed is not something to rewrite. Say
        # nothing was fixed and leave the file exactly as it was.
        return []
    actions = []

    broken = {p.entry for p in problems if p.section == "Kernel -> Add"}
    if broken:
        keep = []
        for entry in config.get("Kernel", {}).get("Add", []) or []:
            name = entry.get("BundlePath")
            if name in broken:
                actions.append("Removed %s from Kernel -> Add (the kext is "
                               "not in the EFI)" % name)
            else:
                keep.append(entry)
        config["Kernel"]["Add"] = keep

    for section, subsection, key, folder in FILE_SECTIONS:
        names = {p.entry for p in problems
                 if p.section == "%s -> %s" % (section, subsection)}
        if not names:
            continue
        keep = []
        for entry in config.get(section, {}).get(subsection, []) or []:
            relative = entry if isinstance(entry, str) else (entry.get(key) or "")
            if relative in names:
                actions.append("Removed %s from %s -> %s (not in EFI/OC/%s)"
                               % (relative, section, subsection, folder))
            else:
                keep.append(entry)
        config[section][subsection] = keep

    if actions:
        save_config(config_path, config)
        if report:
            for action in actions:
                report(action)
    return actions


def summary(problems) -> str:
    """One line for the log, or an empty string when the EFI is consistent."""
    bad = fatal(problems)
    if bad:
        return ("config.plist names %d file(s) the EFI does not have: %s"
                % (len(bad), "; ".join(str(p) for p in bad)))
    other = [p for p in problems if not p.fatal]
    if other:
        return "%d note(s): %s" % (len(other), "; ".join(str(p) for p in other))
    return ""
