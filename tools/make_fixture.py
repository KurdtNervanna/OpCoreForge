"""
Builds a synthetic but structurally realistic hardware report + ACPI dump,
so the whole workflow can be exercised without a physical machine.

Modelled on a Comet Lake desktop: Z490 board, i9-10900K, RX 580, I219-V
ethernet, ALC1220 audio, Intel Bluetooth, NVMe storage.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "_fixture"
ACPI = OUT / "ACPI"

REPORT = {
    "Motherboard": {
        "Name": "ASUS ROG STRIX Z490-E GAMING",
        "Chipset": "Z490",
        "Platform": "Desktop",
    },
    "BIOS": {
        "Version": "2701",
        "Release Date": "03/14/2024",
        "System Type": "x64",
        "Firmware Type": "UEFI",
        "Secure Boot": "Disabled",
    },
    "CPU": {
        "Manufacturer": "Intel",
        "Processor Name": "Intel(R) Core(TM) i9-10900K CPU @ 3.70GHz",
        "Codename": "Comet Lake",
        "Core Count": "10",
        "CPU Count": "1",
        "SIMD Features": "SSE, SSE2, SSE3, SSSE3, SSE4.1, SSE4.2, AVX, AVX2",
    },
    "GPU": {
        "AMD Radeon RX 580": {
            "Manufacturer": "AMD",
            "Codename": "Polaris 20",
            "Device ID": "1002-67DF",
            "Device Type": "Discrete GPU",
            "Subsystem ID": "0xE3871DA2",
            "PCI Path": "PciRoot(0x0)/Pci(0x1,0x0)/Pci(0x0,0x0)",
            "Resizable BAR": "Disabled",
        },
        "Intel UHD Graphics 630": {
            "Manufacturer": "Intel",
            "Codename": "Comet Lake",
            "Device ID": "8086-9BC5",
            "Device Type": "Integrated GPU",
            "Subsystem ID": "0x87721043",
            "PCI Path": "PciRoot(0x0)/Pci(0x2,0x0)",
            "ACPI Path": "\\_SB.PCI0.GFX0",
        },
    },
    "Monitor": {
        "DELL U2718Q": {
            "Connector Type": "DP",
            "Resolution": "3840x2160",
            "Connected GPU": "AMD Radeon RX 580",
        }
    },
    "Network": {
        "Intel(R) Ethernet Connection (11) I219-V": {
            "Bus Type": "PCI",
            "Device ID": "8086-0D4F",
            "Subsystem ID": "0x86721043",
            "PCI Path": "PciRoot(0x0)/Pci(0x1F,0x6)",
            "ACPI Path": "\\_SB.PCI0.GLAN",
        }
    },
    "Sound": {
        "Realtek ALC1220": {
            "Bus Type": "PCI",
            "Device ID": "10EC-1220",
            "Subsystem ID": "0x86721043",
            "Audio Endpoints": ["Speakers", "Line Out", "Microphone"],
            "Controller Device ID": "8086-06C8",
        }
    },
    "USB Controllers": {
        "Intel(R) USB 3.2 eXtensible Host Controller": {
            "Bus Type": "PCI",
            "Device ID": "8086-06ED",
            "Subsystem ID": "0x86721043",
            "PCI Path": "PciRoot(0x0)/Pci(0x14,0x0)",
            "ACPI Path": "\\_SB.PCI0.XHCI",
        }
    },
    "Input": {
        "HID Keyboard Device": {
            "Bus Type": "USB",
            "Device": "HID_DEVICE_SYSTEM_KEYBOARD",
            "Device Type": "USB",
        }
    },
    "Storage Controllers": {
        "Samsung NVMe SSD Controller": {
            "Bus Type": "PCI",
            "Device ID": "144D-A808",
            "PCI Path": "PciRoot(0x0)/Pci(0x1B,0x0)/Pci(0x0,0x0)",
            "Disk Drives": ["Samsung SSD 970 EVO Plus 1TB"],
        },
        "Intel(R) SATA AHCI Controller": {
            "Bus Type": "PCI",
            "Device ID": "8086-06D2",
            "PCI Path": "PciRoot(0x0)/Pci(0x17,0x0)",
            "Disk Drives": ["WDC WD40EZRZ"],
        },
    },
    "Bluetooth": {
        "Intel(R) Wireless Bluetooth(R)": {
            "Bus Type": "USB",
            "Device ID": "8087-0026",
        }
    },
    "System Devices": {
        "PCI Express Root Complex": {
            "Bus Type": "PCI",
            "Device ID": "8086-9B33",
            "PCI Path": "PciRoot(0x0)/Pci(0x0,0x0)",
        },
        "Intel(R) SMBus": {
            "Bus Type": "PCI",
            "Device ID": "8086-06A3",
            "PCI Path": "PciRoot(0x0)/Pci(0x1F,0x4)",
        },
    },
}

DSDT = r"""
DefinitionBlock ("", "DSDT", 2, "OCFTST", "OCFTEST", 0x00000001)
{
    Scope (\_SB)
    {
        Device (PCI0)
        {
            Name (_HID, EisaId ("PNP0A08"))
            Name (_CID, EisaId ("PNP0A03"))
            Name (_UID, One)
            Method (_STA, 0, NotSerialized) { Return (0x0F) }

            Device (GFX0)
            {
                Name (_ADR, 0x00020000)
                Method (_STA, 0, NotSerialized) { Return (0x0F) }
            }

            Device (PEG0)
            {
                Name (_ADR, 0x00010000)
                Device (PEGP)
                {
                    Name (_ADR, Zero)
                }
            }

            Device (XHCI)
            {
                Name (_ADR, 0x00140000)
                Method (_STA, 0, NotSerialized) { Return (0x0F) }
                Device (RHUB)
                {
                    Name (_ADR, Zero)
                    Device (HS01) { Name (_ADR, One) }
                    Device (HS02) { Name (_ADR, 0x02) }
                    Device (SS01) { Name (_ADR, 0x11) }
                }
            }

            Device (SAT0)
            {
                Name (_ADR, 0x00170000)
            }

            Device (GLAN)
            {
                Name (_ADR, 0x001F0006)
            }

            Device (HDEF)
            {
                Name (_ADR, 0x001F0003)
                Method (_STA, 0, NotSerialized) { Return (0x0F) }
            }

            Device (SBUS)
            {
                Name (_ADR, 0x001F0004)
            }

            Device (LPCB)
            {
                Name (_ADR, 0x001F0000)

                Device (RTC)
                {
                    Name (_HID, EisaId ("PNP0B00"))
                    Name (_CRS, ResourceTemplate ()
                    {
                        IO (Decode16, 0x0070, 0x0070, 0x01, 0x08)
                        IRQNoFlags () {8}
                    })
                }

                Device (HPET)
                {
                    Name (_HID, EisaId ("PNP0103"))
                    Name (_CRS, ResourceTemplate ()
                    {
                        IRQNoFlags () {0}
                        IRQNoFlags () {8}
                        Memory32Fixed (ReadWrite, 0xFED00000, 0x00000400)
                    })
                    Method (_STA, 0, NotSerialized) { Return (0x0F) }
                }

                Device (TIMR)
                {
                    Name (_HID, EisaId ("PNP0100"))
                    Name (_CRS, ResourceTemplate ()
                    {
                        IO (Decode16, 0x0040, 0x0040, 0x01, 0x04)
                        IRQNoFlags () {0}
                    })
                }

                Device (PIC)
                {
                    Name (_HID, EisaId ("PNP0000"))
                    Name (_CRS, ResourceTemplate ()
                    {
                        IO (Decode16, 0x0020, 0x0020, 0x01, 0x02)
                        IRQNoFlags () {2}
                    })
                }
            }
        }

        Device (AWAC)
        {
            Name (_HID, "ACPI000E")
            Method (_STA, 0, NotSerialized)
            {
                If (LEqual (STAS, One)) { Return (0x0F) }
                Return (Zero)
            }
        }
    }

    Name (STAS, One)

    Method (_PIC, 1, NotSerialized)
    {
        Store (Arg0, GPIC)
    }

    Name (GPIC, Zero)

    Scope (\_PR)
    {
        Processor (CP00, 0x01, 0x00000410, 0x06) {}
        Processor (CP01, 0x02, 0x00000410, 0x06) {}
    }

    Method (OSDW, 0, NotSerialized)
    {
        Return (Zero)
    }
}
"""


def main():
    iasl = None
    search = []
    for base in (ROOT / "_run", ROOT / "_seedwork",
                 ROOT / "OpCoreForge_Data", ROOT / "dist" / "OpCoreForge_Data"):
        search += [base / "bin" / "iasl", base / "bin" / "iasl.exe"]
    search.append(shutil.which("iasl"))
    for candidate in search:
        if candidate and Path(candidate).exists():
            iasl = Path(candidate)
            break
    if iasl is None:
        print("iasl not found. Run the app or build/make_seed.py once so it is\n"
              "downloaded, then retry. Looked in:")
        for candidate in search:
            if candidate:
                print("   ", candidate)
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    ACPI.mkdir(parents=True, exist_ok=True)

    (OUT / "Report.json").write_text(json.dumps(REPORT, indent=4))

    source = ACPI / "DSDT.dsl"
    source.write_text(DSDT)
    result = subprocess.run([str(iasl), "-p", str(ACPI / "DSDT"), str(source)],
                            capture_output=True, text=True)
    print(result.stdout[-2500:])
    if result.returncode != 0:
        print(result.stderr[-2500:])
        return 1
    source.unlink(missing_ok=True)

    print("\nfixture written to", OUT)
    for path in sorted(OUT.rglob("*")):
        print("  ", path.relative_to(OUT), path.stat().st_size if path.is_file() else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
