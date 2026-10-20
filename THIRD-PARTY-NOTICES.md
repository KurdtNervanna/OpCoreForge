# Third-party notices

OpCoreForge redistributes three upstream projects in source form under
`src/vendor/`. All the real work of building an OpenCore EFI belongs to their
authors; OpCoreForge is integration code around them.

Each tree is a **mechanically rewritten copy** produced by `build/vendor.py`,
never edited by hand. The only changes are package and module renames, needed
because all three ship a top-level package called `Scripts` and Python cannot
import three of those at once. `src/vendor/*/UPSTREAM.txt` records the exact
transformation and the commit it was applied to; the licence text each author
shipped is kept verbatim beside it.

| Project | Author | Licence | Vendored commit | Path |
|---|---|---|---|---|
| [OpCore-Simplify](https://github.com/lzhoang2801/OpCore-Simplify) | lzhoang2801 | BSD 3-Clause | `e5d8a9f551b1e2a96e2f968696b460b65a7dad2e` | `src/vendor/ocs/` |
| [ProperTree](https://github.com/corpnewt/ProperTree) | CorpNewt | BSD 3-Clause | `51ed53dbe3c96a81686ae1fc47f6d2a92f668159` | `src/vendor/ptree/` |
| [USBToolBox/tool](https://github.com/USBToolBox/tool) | Dhinak G | MIT | `0c6823a2c643a4488888938451194723dc9ae29a` | `src/vendor/utb/` |

Full licence texts:

- `src/vendor/ocs/LICENSE` — BSD 3-Clause, Copyright (c) 2024 lzhoang2601
- `src/vendor/ptree/LICENSE` — BSD 3-Clause, Copyright (c) 2019 CorpNewt
- `src/vendor/utb/LICENSE` — MIT, Copyright (c) 2021-2022 Dhinak G

All three permit redistribution in source and binary form provided the
copyright notice, the conditions and the disclaimer travel with the code. They
do, in both forms: the files above are in the repository, and
`OpCoreForge.spec` ships `src/vendor/` into the executable, so a built
`OpCoreForge.exe` carries them too. The BSD-3 non-endorsement clause is why
neither the upstream authors' names nor the projects' names are used to
promote OpCoreForge — they appear here and in the README as attribution, which
is what clause 3 requires rather than forbids.

## Things downloaded at run time, not redistributed

The executable ships no OpenCore build, kext or ACPI compiler. Those are
fetched when a build is run, by upstream's own downloader, from the projects
that publish them — OpenCorePkg (Acidanthera), the individual kext releases,
and Apple's recovery servers for the macOS installer image in stage 10. They
keep their own licences and none of them are in this repository.

## Referenced, not redistributed: the NullMoth NVIDIA driver

OpCoreForge can configure OpenCore for the
[NullMoth NVIDIA driver](https://github.com/nullmoth/nvidia-macos-driver),
which runs Turing and newer GeForce cards on macOS Sequoia (see
`docs/NVIDIA-DRIVER.md`). **None of that project is in this repository or in a
built `OpCoreForge.exe`.** OpCoreForge writes the OpenCore settings the driver's
README specifies and links to the upstream release; the user downloads and
installs the driver themselves, after macOS is up.

The driver's plugin, kexts, build and package code are **PolyForm Noncommercial
1.0.0**, © NullMoth Systems — personal, research, educational and non-profit use
only. Its shader translator is LGPL-3.0-or-later and its Mesa/NVK changes are
MIT. Because nothing from it is redistributed here, those terms bind the driver
you download, not OpCoreForge. The constants OpCoreForge writes into a
`config.plist` (an NVRAM GUID, a `csr-active-config` value, a boot-args line, a
kext identifier to block) are configuration facts, not the driver's code.

## The companion app

`OpCoreForge-Companion.dmg` contains none of the three projects above. It is
OpCoreForge's own code frozen by PyInstaller with the Python runtime and Tcl/Tk
it was built with (PSF License; Tcl/Tk's BSD-style licence) and `certifi`
(MPL-2.0, unmodified; source at https://github.com/certifi/python-certifi),
each under its own licence.

## OpCoreForge itself

Everything outside `src/vendor/` — `src/opcoreforge/`, `src/compat/`,
`build/`, `tools/`, and the top-level scripts and documentation — is BSD
3-Clause, Copyright (c) 2026 Drew Nucci. See `LICENSE`.

BSD 3-Clause was chosen to match the two upstream projects that use it, so the
whole tree reads under one set of conditions; the MIT-licensed tree is
compatible with that and imposes nothing further.
