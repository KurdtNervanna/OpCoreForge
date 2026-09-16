"""
Builds the payload snapshot that ships inside the executable.

OpCore-Simplify normally downloads OpenCorePkg, the kexts your hardware needs,
macserial and iasl on first use.  Running this script ahead of the PyInstaller
build captures that payload into ``src/seed/payload.zip``, which the app
unpacks beside itself the first time it runs.  After that, OpCore-Simplify's
own update logic takes over unchanged -- the seed only removes the cold start.

The download is performed by OpCore-Simplify itself (with every kext marked as
wanted), so the resulting cache is byte-identical to what the tool would have
fetched on its own, ``history.json`` included.

Networks that block ``github.com`` HTML -- CI sandboxes, corporate proxies --
cannot use the release-page scraping OpCore-Simplify falls back on for repos
that are not in Dortania's build repo.  Those kexts are reported and skipped
rather than failing the build; the app downloads them on demand later.
``OcBinaryData`` is fetched over git and mirrored locally when its archive URL
is blocked, because without it OpenCorePkg is never staged at all.

    python3 build/make_seed.py                # everything reachable
    python3 build/make_seed.py --core-only    # OpenCorePkg + essential kexts
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
SEED_DIR = SRC / "seed"

# Always seeded: the kexts essentially every configuration pulls in.
CORE_KEXTS = [
    "Lilu", "VirtualSMC", "SMCProcessor", "SMCSuperIO", "WhateverGreen",
    "AppleALC", "NVMeFix", "RestrictEvents", "USBToolBox", "UTBDefault",
    "IntelMausi", "RealtekRTL8111", "CpuTscSync", "ECEnabler", "CryptexFixup",
    "AirportItlwm", "IntelBluetoothFirmware", "BlueToolFixup",
    "VoodooPS2Controller", "VoodooI2C", "VoodooI2CHID", "AppleMCEReporterDisabler",
]


def log(message):
    print(message, file=sys.__stdout__, flush=True)


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def serve_directory(directory: Path):
    """Serve *directory* on localhost; returns (base_url, shutdown)."""
    handler = lambda *a, **kw: _QuietHandler(*a, directory=str(directory), **kw)  # noqa: E731
    server = socketserver.TCPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return "http://127.0.0.1:%d" % server.server_address[1], server.shutdown


def url_reachable(url: str) -> bool:
    try:
        request = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status < 400
    except Exception:
        return False


def mirror_ocbinarydata(workdir: Path):
    """Produce a local OcBinaryData archive when the upstream URL is blocked.

    OpenCorePkg is only staged into the cache once OcBinaryData has been
    fetched, so a blocked archive URL means no payload at all. The same content
    is available over git, which more restrictive networks usually still allow.
    """
    clone = workdir / "OcBinaryData"
    log("  mirroring OcBinaryData over git...")
    subprocess.run(
        ["git", "clone", "--depth", "1",
         "https://github.com/acidanthera/OcBinaryData.git", str(clone)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    shutil.rmtree(clone / ".git", ignore_errors=True)

    serve_root = workdir / "mirror"
    serve_root.mkdir(exist_ok=True)
    archive = serve_root / "master.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for item in clone.rglob("*"):
            if item.is_file():
                bundle.write(item, Path("OcBinaryData-master") /
                             item.relative_to(clone))
    base, shutdown = serve_directory(serve_root)
    return base + "/master.zip", shutdown


def collect(paths, core_only: bool):
    """Drive OpCore-Simplify's own downloader over the kext catalogue."""
    from ocs_scripts import gathering_files
    from ocs_scripts.datasets import kext_data

    gatherer = gathering_files.gatheringFiles()
    gatherer.ock_files_dir = str(paths.ock_files)
    gatherer.download_history_file = str(paths.ock_files / "history.json")

    workdir = Path(tempfile.mkdtemp(prefix="ocf_seed_"))
    shutdown = None
    if not url_reachable(gatherer.ocbinarydata_url):
        log("  OcBinaryData archive URL is blocked on this network")
        gatherer.ocbinarydata_url, shutdown = mirror_ocbinarydata(workdir)

    dortania = {}
    try:
        dortania = json.load(urllib.request.urlopen(
            gatherer.dortania_builds_url, timeout=30))
    except Exception as exc:
        log("  could not read the Dortania build index: %s" % exc)

    wanted, skipped = [], []
    for kext in kext_data.kexts:
        if core_only and kext.name not in CORE_KEXTS:
            kext.checked = False
            continue
        repo = (kext.github_repo or {}).get("repo")
        reachable = bool(kext.download_info) or not repo or repo in dortania
        if not reachable and not url_reachable("https://github.com/%s/%s/releases"
                                               % (kext.github_repo.get("owner"), repo)):
            kext.checked = False
            skipped.append(kext.name)
            continue
        kext.checked = True
        wanted.append(kext.name)

    log("  seeding %d kext(s)" % len(wanted))
    if skipped:
        log("  %d kext(s) have unreachable release pages on this network and "
            "will be fetched by the app on demand" % len(skipped))

    # A single unreachable asset aborts upstream's whole gather run, so drop
    # the offender and retry. Downloads already completed stay cached, so each
    # pass resumes rather than starting over.
    try:
        for _attempt in range(len(wanted) + 2):
            try:
                gatherer.gather_bootloader_kexts(kext_data.kexts, "24.99.99")
                break
            except Exception as exc:
                failed = _failed_product(str(exc))
                if not failed:
                    log("  gather failed: %s" % exc)
                    break
                dropped = _drop_product(kext_data.kexts, failed)
                if not dropped:
                    log("  gather failed on %s and it could not be dropped" % failed)
                    break
                log("  %s is not downloadable here; skipping (%s)"
                    % (failed, ", ".join(dropped)))
                for name in dropped:
                    if name in wanted:
                        wanted.remove(name)
                    skipped.append(name)
    finally:
        if shutdown:
            shutdown()
        shutil.rmtree(workdir, ignore_errors=True)

    return wanted, skipped


def _failed_product(message: str):
    marker = "Could not download "
    if marker in message:
        return message.split(marker, 1)[1].split(" at this time")[0].strip()
    return None


def _drop_product(kexts, product_name: str):
    """Uncheck every kext that maps onto *product_name*.

    gather_bootloader_kexts collapses several kexts onto one download (all the
    Brcm* kexts come from BrcmPatchRAM, for instance), so the failing product
    name may not match a kext name directly.
    """
    aliases = {
        "BrcmPatchRAM": lambda n: n.startswith("Brcm") or n == "BlueToolFixup",
        "USBToolBox": lambda n: n in ("USBToolBox", "UTBDefault"),
        "Ath3kBT": lambda n: n.startswith("Ath3kBT"),
        "IntelBluetoothFirmware": lambda n: n.startswith("IntelB"),
        "VoodooPS2": lambda n: "VoodooPS2" in n,
        "VoodooI2C": lambda n: n.startswith("VoodooI2C"),
    }
    predicate = aliases.get(product_name,
                            lambda n, p=product_name: n == p or n.startswith(p))
    dropped = []
    for kext in kexts:
        if kext.checked and predicate(kext.name):
            kext.checked = False
            dropped.append(kext.name)
    return dropped


def stage_binaries(paths):
    """Copy helper executables into the seed's bin directory."""
    staged = []
    utb_resources = SRC / "vendor" / "utb" / "resources"
    for name in ("usbdump.exe",):
        source = utb_resources / name
        if source.exists():
            shutil.copy2(source, paths.bin / name)
            staged.append(name)
    for name in ("iasl", "iasl.exe", "macserial", "macserial.exe",
                 "macserial.linux", "Hardware-Sniffer-CLI.exe"):
        if (paths.bin / name).exists():
            staged.append(name)
    return staged


def write_manifest(version, wanted, skipped):
    """Record what this seed actually contains.

    A seed built on a restricted network is incomplete, and the resulting
    executable would quietly download the rest on first use. Recording the gap
    lets BUILD_EXE.bat notice and rebuild the payload where the network allows
    it, rather than shipping a partial cache by accident.
    """
    SEED_DIR.mkdir(parents=True, exist_ok=True)
    (SEED_DIR / "MANIFEST.json").write_text(json.dumps({
        "version": version,
        "seeded": sorted(wanted),
        "skipped": sorted(set(skipped)),
        "complete": not skipped,
    }, indent=2), encoding="utf-8")


def build_archive(paths, version: str):
    SEED_DIR.mkdir(parents=True, exist_ok=True)
    archive = SEED_DIR / "payload.zip"
    if archive.exists():
        archive.unlink()

    roots = [("OCK_Files", paths.ock_files), ("bin", paths.bin)]
    count = 0
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=9) as bundle:
        for label, root in roots:
            if not root.exists():
                continue
            for item in sorted(root.rglob("*")):
                if not item.is_file():
                    continue
                bundle.write(item, Path(label) / item.relative_to(root))
                count += 1
    (SEED_DIR / "VERSION").write_text(version, encoding="utf-8")
    return archive, count


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--core-only", action="store_true",
                        help="seed only the kexts nearly every build uses")
    parser.add_argument("--data-dir", default=str(ROOT / "_seedwork"))
    args = parser.parse_args()

    os.environ.setdefault("TERM_PROGRAM", "")
    sys.path.insert(0, str(SRC))
    from opcoreforge import patches, paths as pm

    patches.bootstrap_sys_path()
    paths = pm.init(Path(args.data_dir))
    patches.apply_all(paths)

    # Route OpCore-Simplify's console output somewhere harmless but visible.
    from opcoreforge.bridge import console as console_bridge
    from ocs_scripts import utils as ocs_utils

    bridge = console_bridge.ConsoleBridge()
    bridge.on_prompt = lambda item: (setattr(item, "answer", ""),
                                     item.event.set())
    bridge.on_output = lambda chunk, tag: None
    console_bridge.install(bridge, ocs_utils)

    log("Building seed payload in %s" % paths.data)
    started = time.time()
    wanted, skipped = collect(paths, args.core_only)

    # iasl is fetched by constructing the ACPI helper.
    log("  fetching iasl...")
    try:
        from ocs_scripts import acpi_guru
        acpi_guru.ACPIGuru()
    except Exception as exc:
        log("  iasl could not be fetched: %s" % exc)

    staged = stage_binaries(paths)
    log("  staged helper binaries: %s" % (", ".join(staged) or "none"))

    version = time.strftime("%Y%m%d-%H%M%S")
    write_manifest(version, wanted, skipped)
    archive, count = build_archive(paths, version)
    size = archive.stat().st_size / (1024 * 1024)
    log("\nSeed written: %s" % archive)
    log("  %d files, %.1f MB compressed, version %s" % (count, size, version))
    log("  %d kext(s) seeded, %d skipped" % (len(wanted), len(skipped)))
    log("  took %.0fs" % (time.time() - started))
    return 0


if __name__ == "__main__":
    sys.exit(main())
