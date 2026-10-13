"""
NullMoth NVIDIA driver awareness.

NVIDIA's Turing and newer GeForce / Quadro / TITAN RTX cards have no Apple
driver, so OpCore-Simplify's compatibility checker dead-ends on them -- its own
regression fixture is a GTX 1660 Ti (Turing) that "has no macOS driver and
never will".

That last clause is no longer true. The NullMoth driver
(https://github.com/nullmoth/nvidia-macos-driver) is a Metal driver that runs
Turing+ GeForce cards on macOS 15 Sequoia, delivered entirely through OpenCore
configuration -- an NVRAM boot-args line, a relaxed SIP value, a block of the
firmware framebuffer, and Secure Boot turned off -- plus kexts the user
installs into ``/Library/Extensions`` after macOS is up (NOT ``EFI/OC/Kexts``).

This module holds everything OpCoreForge needs to know about that driver in one
place: who it applies to, what macOS it reaches, and the exact OpenCore values
to write.

- ``patches.py`` calls :func:`find_candidates` / :func:`set_engaged` to offer
  the driver at the compatibility step instead of letting upstream dead-end.
- ``bridge/ocs.py`` calls :func:`apply_to_config` to stamp the values into the
  config.plist it just built, and :func:`post_install_notes` for the by-hand
  steps that remain.

Every value here is taken verbatim from the driver's own README (NVRAM GUID
``7C436110-...``, ``csr-active-config`` ``430A0000``, the boot-args line, the
``IONDRVSupport`` block, ``SecureBootModel`` Disabled). Nothing is invented;
change it only to track that README.
"""

from __future__ import annotations

REPO_URL = "https://github.com/nullmoth/nvidia-macos-driver"

# macOS the driver targets, as Darwin versions. Sequoia (15) == Darwin 24; the
# README's tested range is 15.7.x - 15.8.1, so the window is Sequoia only.
TARGET_DARWIN_MAJOR = 24
# (max, min) -- the shape OpCore-Simplify's compatibility checker uses.
COMPATIBILITY = ("24.99.99", "24.0.0")
TARGET_NAME = "macOS Sequoia 15"

# GPU architectures the driver supports, matched case-insensitively against
# OpCore-Simplify's GPU "Codename". All are newer than the Pascal / Maxwell
# families the checker already treats as (web-driver) supported.
SUPPORTED_CODENAMES = ("turing", "ampere", "ada", "blackwell")

# --- exact OpenCore values (driver README) ---------------------------------
NVRAM_GUID = "7C436110-AB2A-4BBB-A880-FE41995C9F82"
# <430A0000>: unsigned kexts plus the bits root patches need.
CSR_ACTIVE_CONFIG = b"\x43\x0a\x00\x00"
DRIVER_BOOT_ARGS = (
    "nvfb=1",
    "nvaccel=1",
    "nvfbheads=4",
    "-nvkmsnosmooth",
    "amfi_get_out_of_my_way=0x1",
    "amfi=0x80",
    "ipc_control_port_options=0",
)
# boot-args this card must NOT carry (README: remove these for the NVIDIA card).
CONFLICTING_BOOT_ARGS = ("nv_disable=1", "agdpmod=pikera")
# Blocking the firmware framebuffer stops it taking display 0 from NVRMFB.
BLOCKED_KEXT_ID = "com.apple.iokit.IONDRVSupport"


def is_supported_codename(codename) -> bool:
    text = (codename or "").lower()
    return any(name in text for name in SUPPORTED_CODENAMES)


def nvidia_cards(hardware_report):
    """Every NVIDIA GPU in the report the NullMoth driver can drive.

    Returns a list of ``(name, props)`` regardless of how the compatibility
    checker rated it -- used to name the cards once the driver is enabled.
    """
    cards = []
    for name, props in (hardware_report or {}).get("GPU", {}).items():
        if props.get("Manufacturer") != "NVIDIA":
            continue
        if is_supported_codename(props.get("Codename")):
            cards.append((name, props))
    return cards


def find_candidates(hardware_report):
    """NullMoth-capable cards that Apple left unsupported.

    A candidate is a card from :func:`nvidia_cards` whose ``Compatibility`` the
    checker set to ``(None, None)`` (or has not set yet). These are the cards an
    offer is worth making for.
    """
    candidates = []
    for name, props in nvidia_cards(hardware_report):
        if props.get("Compatibility") in (None, (None, None)):
            candidates.append((name, props))
    return candidates


def targets_sequoia(macos_version) -> bool:
    """True when a Darwin version string is macOS Sequoia (15 / Darwin 24)."""
    try:
        return int(str(macos_version).split(".")[0]) == TARGET_DARWIN_MAJOR
    except (TypeError, ValueError):
        return False


# --- per-run engagement -----------------------------------------------------
# The user opts in once per hardware report, through a yes/no dialog at the
# compatibility step. The decision is read again when the EFI is built.
_engaged = False


def reset() -> None:
    global _engaged
    _engaged = False


def set_engaged(value: bool) -> None:
    global _engaged
    _engaged = bool(value)


def engaged() -> bool:
    return _engaged


def apply_to_config(config, macos_version):
    """Write the driver's required OpenCore settings into a config.plist dict.

    Returns a list of human-readable descriptions of what changed. Idempotent:
    a second run yields the same config and reports nothing new. Only Sequoia
    builds are touched; any other target returns an empty list unchanged.
    """
    if not targets_sequoia(macos_version):
        return []

    changes = []

    nvram = config.setdefault("NVRAM", {})
    add = nvram.setdefault("Add", {})
    section = add.setdefault(NVRAM_GUID, {})

    # boot-args: merge -- drop the card's known-bad flags, keep everything else
    # upstream chose (verbosity, audio layout, ...), add the driver's own.
    existing = section.get("boot-args", "") or ""
    present = existing.split()
    removed = [token for token in present if token in CONFLICTING_BOOT_ARGS]
    tokens = [token for token in present if token not in CONFLICTING_BOOT_ARGS]
    added = []
    for token in DRIVER_BOOT_ARGS:
        if token not in tokens:
            tokens.append(token)
            added.append(token)
    new_boot_args = " ".join(tokens).strip()
    if new_boot_args != existing:
        section["boot-args"] = new_boot_args
        if added:
            changes.append("boot-args += " + " ".join(added))
        if removed:
            changes.append("boot-args -= " + " ".join(removed))

    if section.get("csr-active-config") != CSR_ACTIVE_CONFIG:
        section["csr-active-config"] = CSR_ACTIVE_CONFIG
        changes.append("csr-active-config = 430A0000 (allow unsigned kexts)")

    # NVRAM -> Delete: rewrite both keys every boot so a stale value can't stick.
    delete = nvram.setdefault("Delete", {})
    delete_section = delete.setdefault(NVRAM_GUID, [])
    for key in ("boot-args", "csr-active-config"):
        if key not in delete_section:
            delete_section.append(key)
            changes.append("NVRAM Delete += " + key)

    # Kernel -> Block the firmware framebuffer so NVRMFB can own display 0.
    kernel = config.setdefault("Kernel", {})
    block = kernel.setdefault("Block", [])
    already = any(
        entry.get("Identifier") == BLOCKED_KEXT_ID
        and entry.get("Strategy") == "Exclude"
        and entry.get("Enabled")
        for entry in block
    )
    if not already:
        block.append({
            "Arch": "Any",
            "Comment": "NullMoth NVIDIA driver: free display 0 for NVRMFB",
            "Enabled": True,
            "Identifier": BLOCKED_KEXT_ID,
            "MaxKernel": "",
            "MinKernel": "",
            "Strategy": "Exclude",
        })
        changes.append("Kernel Block += " + BLOCKED_KEXT_ID + " (Exclude)")

    # Secure Boot refuses kexts Apple did not sign.
    security = config.setdefault("Misc", {}).setdefault("Security", {})
    if security.get("SecureBootModel") != "Disabled":
        security["SecureBootModel"] = "Disabled"
        changes.append("SecureBootModel = Disabled")

    return changes


def post_install_notes(card_names=None):
    """The steps that remain by hand, once macOS is installed."""
    cards = ", ".join(card_names) if card_names else "your NVIDIA card"
    return [
        "OpCoreForge set OpenCore up for the NullMoth NVIDIA driver so "
        "{} can run on {}.".format(cards, TARGET_NAME),
        "The kexts are NOT in the EFI. After macOS is installed, get the "
        "driver release and run its install.sh (or the 1401 app):",
        "    " + REPO_URL,
        "They install into /Library/Extensions and load from the Auxiliary "
        "Kernel Collection; a reboot follows.",
        "BIOS: Above 4G Decoding ON, CSM OFF. Leave Resizable BAR as it is.",
        "The driver is PolyForm Noncommercial 1.0.0 -- personal, research, "
        "educational and non-profit use only.",
    ]
