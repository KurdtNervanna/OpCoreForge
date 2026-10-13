# NVIDIA Turing and newer on macOS Sequoia

To Apple, an NVIDIA GeForce card newer than Pascal has no macOS driver, so
OpCore-Simplify's compatibility checker refuses the machine outright:

> You cannot install macOS without a supported GPU.

The [NullMoth NVIDIA driver](https://github.com/nullmoth/nvidia-macos-driver)
changes that for one release: it is a Metal driver that runs **Turing, Ampere,
Ada and Blackwell** GeForce / Quadro / TITAN RTX cards on **macOS 15 Sequoia**
(tested on 15.7.x and 15.8.1), x86_64 only, on Intel Macs and PCs booting
through OpenCore. It is not an OpCoreForge project; OpCoreForge only knows how
to configure OpenCore for it.

## What OpCoreForge does

When a Turing-or-newer NVIDIA card is the **only** reason a machine would be
refused, the compatibility step offers the driver instead of stopping:

> No Apple driver exists for NVIDIA GeForce RTX 4070.
> The NullMoth driver can run it on macOS Sequoia 15:
>     https://github.com/nullmoth/nvidia-macos-driver
> Enable the NullMoth NVIDIA driver for this card? (yes/no):

Decline, and nothing changes — you get upstream's usual stop. Accept, and the
card is treated as Sequoia-capable, Sequoia becomes the build target, and when
the EFI is built OpCoreForge writes the driver's required OpenCore settings into
`config.plist`. If the machine already has a GPU macOS supports, no offer is
made and nothing is pinned to Sequoia.

## What gets written to config.plist

Exactly the values from the driver's README — no more:

| Key | Value | Why |
|---|---|---|
| `NVRAM → Add → 7C436110-… → csr-active-config` | `<430A0000>` | let macOS load unsigned kexts |
| `NVRAM → Add → 7C436110-… → boot-args` | `nvfb=1 nvaccel=1 nvfbheads=4 -nvkmsnosmooth amfi_get_out_of_my_way=0x1 amfi=0x80 ipc_control_port_options=0` | framebuffer + accelerator, 4 heads; the AMFI args let WindowServer load the bundle |
| `NVRAM → Delete → 7C436110-…` | `boot-args`, `csr-active-config` | rewrite both every boot |
| `Kernel → Block` | `com.apple.iokit.IONDRVSupport`, Strategy `Exclude` | otherwise the firmware framebuffer takes display 0 from NVRMFB |
| `Misc → Security → SecureBootModel` | `Disabled` | Apple Secure Boot refuses kexts Apple did not sign |

The existing `boot-args` are **merged**, not replaced: anything upstream chose
(verbosity, audio layout, …) is kept, and the card's incompatible flags
(`nv_disable=1`, `agdpmod=pikera`) are dropped. `Booter → Quirks →
ResizeAppleGpuBars` is left as upstream set it (the installer-safe small-BAR
value).

The kexts (`NVRM`, `NVAccel`, `NVRMFB`, `NVRMAGDC`) are **not** placed in
`EFI/OC/Kexts`. They install into `/Library/Extensions` and load from the
Auxiliary Kernel Collection — OpenCore only provides SIP and boot-args.

## What you still do by hand

1. **BIOS:** Above 4G Decoding **ON**, CSM **OFF**. Leave Resizable BAR as it
   is.
2. Install macOS Sequoia from the media OpCoreForge built.
3. On the running system, download the driver release and run its `install.sh`
   (or use the bundled **1401** app), then reboot:
   <https://github.com/nullmoth/nvidia-macos-driver>

## Licence

The driver's plugin, kexts, build and package code are **PolyForm
Noncommercial 1.0.0** — personal, research, educational and non-profit use
only. OpCoreForge neither bundles nor redistributes any of it; it writes
OpenCore settings and links to the upstream release. See
[`THIRD-PARTY-NOTICES.md`](../THIRD-PARTY-NOTICES.md).

## Where it lives in the code

- [`src/opcoreforge/nvidia.py`](../src/opcoreforge/nvidia.py) — the single
  source of truth: supported architectures, the Sequoia window, and the exact
  OpenCore values (`apply_to_config`).
- [`src/opcoreforge/patches.py`](../src/opcoreforge/patches.py) —
  `patch_compatibility_checker` makes the offer and reclassifies the card,
  without editing vendored upstream.
- [`src/opcoreforge/bridge/ocs.py`](../src/opcoreforge/bridge/ocs.py) —
  `_apply_nullmoth_driver` stamps the settings into the built `config.plist`.
- [`tools/test_nvidia_driver.py`](../tools/test_nvidia_driver.py) — the
  regression test for all of the above.
