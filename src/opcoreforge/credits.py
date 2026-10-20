"""
Who OpCoreForge is made by, and what it is made from.

Two lists. The projects it is built on are fixed facts and written down here,
matching THIRD-PARTY-NOTICES.md. The people who contributed to OpCoreForge
itself are read from GitHub when the credits are opened, so a new contributor
appears without a release; ``KNOWN_CONTRIBUTORS`` is what is shown when GitHub
cannot be reached.

Shared by OpCoreForge and the companion, so it imports nothing either of them
would not have.
"""

from __future__ import annotations

import json
import ssl
import urllib.request
from dataclasses import dataclass

from . import project


@dataclass
class Contributor:
    login: str
    url: str
    contributions: int = 0
    note: str = ""


@dataclass
class Upstream:
    name: str
    author: str
    url: str
    role: str
    licence: str = ""


KNOWN_CONTRIBUTORS = [
    Contributor("KurdtNervanna", "https://github.com/KurdtNervanna",
                note="creator and maintainer"),
]

#: what OpCoreForge is built on; bundled ones first, then what it fetches
UPSTREAM = [
    Upstream("OpCore-Simplify", "lzhoang2801",
             "https://github.com/lzhoang2801/OpCore-Simplify",
             "Hardware detection, compatibility, kexts, ACPI and config.plist "
             "generation (bundled)", "BSD 3-Clause"),
    Upstream("USBToolBox", "Dhinak G", "https://github.com/USBToolBox/tool",
             "USB port discovery and mapping (bundled)", "MIT"),
    Upstream("ProperTree", "CorpNewt", "https://github.com/corpnewt/ProperTree",
             "The config.plist editor and OC Snapshot (bundled)",
             "BSD 3-Clause"),
    Upstream("OpenCore", "Acidanthera",
             "https://github.com/acidanthera/OpenCorePkg",
             "The bootloader itself, and macrecovery.py for the installer "
             "(downloaded at run time)", "BSD 3-Clause"),
    Upstream("Hardware Sniffer", "lzhoang2801",
             "https://github.com/lzhoang2801/Hardware-Sniffer",
             "The hardware report stage 1 reads (downloaded at run time)"),
    Upstream("Dortania guides", "Dortania", "https://dortania.github.io",
             "The OpenCore Install Guide and the SMBIOS support tables"),
    Upstream("NVIDIA macOS driver", "NullMoth Systems",
             "https://github.com/nullmoth/nvidia-macos-driver",
             "Turing and newer NVIDIA cards on Sequoia (referenced, installed "
             "by its own installer)", "PolyForm Noncommercial"),
]


def _context():
    # A frozen macOS build has no system CA bundle of its own; certifi is
    # bundled for that reason. Elsewhere the platform store is fine.
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def parse_contributors(data) -> list:
    """GitHub's contributors list, without bots, most commits first."""
    people = []
    for entry in data if isinstance(data, list) else []:
        login = entry.get("login") or ""
        if not login or entry.get("type") == "Bot" or login.endswith("[bot]"):
            continue
        people.append(Contributor(
            login, entry.get("html_url") or "https://github.com/%s" % login,
            int(entry.get("contributions") or 0)))
    people.sort(key=lambda p: -p.contributions)
    notes = {c.login.lower(): c.note for c in KNOWN_CONTRIBUTORS}
    for person in people:
        person.note = notes.get(person.login.lower(), "")
    return people


def fetch_contributors(timeout=8, opener=None) -> list:
    """Contributors from GitHub, or ``KNOWN_CONTRIBUTORS`` if that fails."""
    url = "%s/contributors?per_page=100" % project.API_URL
    try:
        if opener:
            raw = opener(url)
        else:
            request = urllib.request.Request(url, headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "OpCoreForge"})
            with urllib.request.urlopen(request, timeout=timeout,
                                        context=_context()) as response:
                raw = response.read()
        people = parse_contributors(json.loads(raw))
    except Exception:
        people = []
    return people or list(KNOWN_CONTRIBUTORS)
