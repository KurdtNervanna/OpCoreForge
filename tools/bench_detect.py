"""
Profiles what actually happens after "Detect this PC's hardware".

Two suspects:
  1. the log bridge - every byte OpCore-Simplify prints becomes a Tk callback
  2. read_acpi_tables - iasl disassembly plus pure-Python parsing of the result

Generates an ACPI set the size of a real desktop's (a large DSDT plus a stack
of SSDTs) and times each phase separately.
"""

import cProfile
import os
import pstats
import subprocess
import sys
import time
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("TERM_PROGRAM", "")

OUT = ROOT / "_bench"
ACPI = OUT / "ACPI"
STDERR = sys.stderr


def log(msg):
    STDERR.write(msg + "\n")
    STDERR.flush()


def find_iasl():
    for base in (ROOT / "_run", ROOT / "_seedwork", ROOT / "_bench"):
        for name in ("iasl", "iasl.exe"):
            candidate = base / "bin" / name
            if candidate.exists():
                return candidate
    return None


def big_dsdt(devices=220, methods_per_device=6):
    """An ASL source in the size class of a real desktop firmware's DSDT."""
    parts = ['DefinitionBlock ("", "DSDT", 2, "BENCH", "BENCH000", 0x00000001)', "{",
             "    Scope (\\_SB)", "    {", "        Device (PCI0)", "        {",
             '            Name (_HID, EisaId ("PNP0A08"))',
             '            Name (_CID, EisaId ("PNP0A03"))',
             "            Name (_UID, One)",
             "            Method (_STA, 0, NotSerialized) { Return (0x0F) }"]
    for i in range(devices):
        name = "D%03X" % i
        parts.append("            Device (%s)" % name)
        parts.append("            {")
        parts.append("                Name (_ADR, 0x%08X)" % (0x00010000 + i))
        parts.append("                Name (BUF%01X, Buffer (0x40) { %s })"
                     % (i % 10, ", ".join("0x%02X" % (j % 256) for j in range(64))))
        for m in range(methods_per_device):
            parts.append("                Method (M%02X%01X, 1, NotSerialized)" % (i % 256, m))
            parts.append("                {")
            parts.append("                    Store (Arg0, Local0)")
            parts.append("                    If (LEqual (Local0, Zero)) { Return (0x%02X) }" % m)
            parts.append("                    Add (Local0, 0x%02X, Local1)" % (m + 1))
            parts.append("                    Return (Local1)")
            parts.append("                }")
        parts.append("                Method (_PRW, 0, NotSerialized) { Return (Package (0x02) { 0x0D, 0x03 }) }")
        parts.append("            }")
    parts += [
        "            Device (LPCB)", "            {",
        "                Name (_ADR, 0x001F0000)",
        "                Device (RTC)", "                {",
        '                    Name (_HID, EisaId ("PNP0B00"))',
        "                    Name (_CRS, ResourceTemplate () {",
        "                        IO (Decode16, 0x0070, 0x0070, 0x01, 0x08)",
        "                        IRQNoFlags () {8} })", "                }",
        "                Device (HPET)", "                {",
        '                    Name (_HID, EisaId ("PNP0103"))',
        "                    Name (_CRS, ResourceTemplate () {",
        "                        IRQNoFlags () {0}",
        "                        Memory32Fixed (ReadWrite, 0xFED00000, 0x00000400) })",
        "                    Method (_STA, 0, NotSerialized) { Return (0x0F) }",
        "                }", "            }",
        "            Device (XHCI)", "            {",
        "                Name (_ADR, 0x00140000)",
        "                Method (_STA, 0, NotSerialized) { Return (0x0F) }",
        "            }",
        "            Device (HDEF)", "            {",
        "                Name (_ADR, 0x001F0003)",
        "                Method (_STA, 0, NotSerialized) { Return (0x0F) }",
        "            }",
        "            Device (SBUS) { Name (_ADR, 0x001F0004) }",
        "            Device (GFX0) { Name (_ADR, 0x00020000) }",
        "        }",
        "        Device (AWAC)", "        {",
        '            Name (_HID, "ACPI000E")',
        "            Method (_STA, 0, NotSerialized) {",
        "                If (LEqual (STAS, One)) { Return (0x0F) }",
        "                Return (Zero) }", "        }",
        "    }", "    Name (STAS, One)",
        "    Method (_PIC, 1, NotSerialized) { Store (Arg0, GPIC) }",
        "    Name (GPIC, Zero)",
        "    Scope (\\_PR)",
        "    {",
    ]
    for i in range(16):
        parts.append("        Processor (CP%02X, 0x%02X, 0x00000410, 0x06) {}" % (i, i + 1))
    parts += ["    }", "}"]
    return "\n".join(parts)


def small_ssdt(index):
    return "\n".join([
        'DefinitionBlock ("", "SSDT", 2, "BENCH", "SSDT%03d", 0x00000001)' % index,
        "{",
        "    External (\\_SB.PCI0, DeviceObj)",
        "    Scope (\\_SB.PCI0)",
        "    {",
        "        Device (S%03X)" % index,
        "        {",
        "            Name (_ADR, 0x%08X)" % (0x00030000 + index),
        "            Method (SM%02X, 0, NotSerialized) { Return (0x%02X) }" % (index % 256, index % 256),
        "        }",
        "    }",
        "}",
    ])


def build_tables(iasl, ssdt_count=28):
    ACPI.mkdir(parents=True, exist_ok=True)
    for old in ACPI.glob("*"):
        old.unlink()

    sources = [("DSDT", big_dsdt())]
    for i in range(ssdt_count):
        sources.append(("SSDT-%02d" % i, small_ssdt(i)))

    for name, text in sources:
        dsl = ACPI / (name + ".dsl")
        dsl.write_text(text)
        result = subprocess.run([str(iasl), "-p", str(ACPI / name), str(dsl)],
                                capture_output=True, text=True)
        if result.returncode != 0:
            log("iasl failed for %s:\n%s" % (name, result.stdout[-1500:]))
            return None
        dsl.unlink()

    total = sum(p.stat().st_size for p in ACPI.glob("*.aml"))
    log("  built %d tables, %.1f KB of AML" % (len(sources), total / 1024))
    return ACPI


# ---------------------------------------------------------------------------
# 1. log bridge throughput
# ---------------------------------------------------------------------------

def bench_log_bridge(chunks, chunk_size):
    """Writes on a worker thread, drained by the main thread, as the app does."""
    import threading
    import tkinter as tk

    from opcoreforge.bridge import console as console_bridge
    from opcoreforge.bridge.console import parse_ansi
    from opcoreforge.ui import theme
    from opcoreforge.ui.widgets import LogPane

    root = tk.Tk()
    theme.apply(root)
    root.geometry("900x400")
    pane = LogPane(root)
    pane.pack(fill="both", expand=True)
    root.update()

    bridge = console_bridge.ConsoleBridge()
    payload = "x" * chunk_size
    done = threading.Event()

    def worker():
        for _ in range(chunks):
            bridge.write(payload)
        done.set()

    def flush():
        text = bridge.drain_output()
        if text:
            for chunk, tag in parse_ansi(text):
                pane.append(chunk, tag)

    start = time.time()
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    done.wait()
    queued = time.time() - start
    while True:
        flush()
        root.update()
        if bridge._pending_len == 0:
            break
    drained = time.time() - start
    root.destroy()
    return queued, drained


# ---------------------------------------------------------------------------
# 2. read_acpi_tables
# ---------------------------------------------------------------------------

def bench_acpi(acpi_dir, profile=False):
    from opcoreforge import patches, paths as pm
    from opcoreforge.bridge import console as console_bridge

    patches.bootstrap_sys_path()
    P = pm.init(OUT)
    patches.apply_all(P)

    from ocs_scripts import acpi_guru, utils as ocs_utils

    bridge = console_bridge.ConsoleBridge()
    bridge.on_prompt = lambda item: (setattr(item, "answer", ""), item.event.set())
    console_bridge.install(bridge, ocs_utils)

    start = time.time()
    guru = acpi_guru.ACPIGuru()
    construct = time.time() - start

    if profile:
        profiler = cProfile.Profile()
        profiler.enable()
    start = time.time()
    guru.read_acpi_tables(str(acpi_dir))
    elapsed = time.time() - start
    if profile:
        profiler.disable()
        buf = StringIO()
        pstats.Stats(profiler, stream=buf).sort_stats("cumulative").print_stats(18)
        log(buf.getvalue())

    return construct, elapsed, bool(guru.ensure_dsdt())


def main():
    iasl = find_iasl()
    if not iasl:
        log("iasl not found - run build/make_seed.py or the app once first.")
        return 1
    log("using iasl at %s\n" % iasl)

    log("[1] Building a realistic ACPI set...")
    acpi_dir = build_tables(iasl)
    if acpi_dir is None:
        return 1

    log("\n[2] Log bridge throughput (what every printed byte costs)")
    for chunks, size, label in ((2000, 1, "2,000 single characters"),
                                (20000, 1, "20,000 single characters"),
                                (2000, 80, "2,000 x 80-char lines")):
        queued, drained = bench_log_bridge(chunks, size)
        log("    %-26s queued %6.2fs   drained %6.2fs" % (label, queued, drained))

    log("\n[3] read_acpi_tables")
    construct, elapsed, ok = bench_acpi(acpi_dir)
    log("    ACPIGuru() construction : %6.2fs" % construct)
    log("    read_acpi_tables()      : %6.2fs   (DSDT loaded: %s)" % (elapsed, ok))

    log("\n[4] Where read_acpi_tables spends its time")
    bench_acpi(acpi_dir, profile=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
