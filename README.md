# OpCoreForge

OpCore-Simplify, USBToolBox and ProperTree combined into one portable Windows
executable, driven by a GUI that runs them in the order they actually have to
run in.

Each of the three does one part of building a working OpenCore EFI, and
OpCore-Simplify ends by telling you to go and do the other two by hand:

> \* USB Mapping:
>     - Use USBToolBox tool to map USB ports.
>     - Add created UTBMap.kext into the EFI\OC\Kexts folder.
>     - Remove UTBDefault.kext in the EFI\OC\Kexts folder.
>     - Edit config.plist:
>         - Use ProperTree to open your config.plist.
>         - Run OC Snapshot by pressing Command/Ctrl + R.

OpCoreForge turns that closing instruction into stages 7 and 8 of the same
program, and does the file shuffling itself.

---

## Building the executable

On the Windows machine, with [Python 3.9+](https://www.python.org/downloads/)
installed ("Add python.exe to PATH" ticked):

```
BUILD_EXE.bat
```

That is the whole procedure. It creates a local virtual environment, installs
PyInstaller and pywin32 into it, downloads the OpenCore payload to bundle, and
writes **`dist\OpCoreForge.exe`** — one file, roughly 70 MB, that you can copy
anywhere and double-click.

To confirm a build is complete:

```
OpCoreForge.exe --self-test
```

It checks that all three projects load side by side, that every data file each
of them reads at runtime is in the bundle, that the data folder is writable and
that the config.plist editor still embeds. Anything missing is named.

The icon is `assets/OpCoreForge.png`; `build/OpCoreForge.ico` is generated from
it by `python build/make_icon.py` at seven sizes and is committed, because the
Windows machine that runs `BUILD_EXE.bat` has no Pillow and an executable
missing its icon over a build dependency is a silly way to lose it. The window
uses the `.ico` on Windows and the PNG everywhere else, since Tk cannot read an
`.ico` off Windows.

---

## Using it

**Run it as administrator.** Right-click › Run as administrator. Two stages
need it: stage 1 dumps this PC's ACPI tables, which Windows only hands to an
elevated process, and stage 7 reads USB port properties and silently reports
fewer ports without it. OpCoreForge checks at startup and offers to restart
itself elevated if it is not.

Ten tabs, unlocked as their inputs appear.

| Stage | Tool | What happens |
|---|---|---|
| 1 Hardware | OpCore-Simplify | Run Hardware Sniffer, or import a `Report.json` + ACPI folder. Shows the compatibility report. |
| 2 macOS | OpCore-Simplify | Every release from High Sierra to Tahoe, flagged where OpenCore Legacy Patcher would be required. |
| 3 SMBIOS | OpCore-Simplify | The recommended Mac model, with the full 77-model catalogue behind a toggle. |
| 4 ACPI | OpCore-Simplify | All 26 patches with their descriptions; the recommended set is pre-selected. |
| 5 Kexts | OpCore-Simplify | All 88 kexts by category, with upstream's `R / x / ! / ? / -` status markers. |
| 6 Build EFI | OpCore-Simplify | Downloads OpenCore + kexts, applies patches, generates config.plist, writes the EFI. |
| 7 USB map | USBToolBox | Live port discovery, connector types, comments; builds `UTBMap.kext` **and installs it into the EFI, removing `UTBDefault.kext` and rewriting `Kernel -> Add` to match**. |
| 8 config.plist | ProperTree | The full editor. On Windows it opens in its own window (see below); OC Snapshot already knows which OC folder to scan. |
| 9 Finish | — | BIOS settings to change, how to deploy, what is still outstanding; checks the EFI against its own `config.plist`. |
| 10 Install media | — | Downloads a macOS recovery and puts it with the EFI: a folder, a bootable USB, or an ISO for a VM. |

Everything lives beside the executable:

```
OpCoreForge.exe
OpCoreForge_Data/
    Results/        the EFI folder that was built
    OCK_Files/      OpenCorePkg + kext cache
    SysReport/      Hardware Sniffer output
    USBMap/         usb.json, settings.json, UTBMap.kext
    ProperTree/     editor settings, Configuration.tex
    bin/            iasl.exe, macserial.exe, Hardware-Sniffer-CLI.exe
    logs/
```

If the folder holding the .exe is not writable, it falls back to
`%LOCALAPPDATA%\OpCoreForge` and says so in the title bar area.

---

## The install media

Stage 10 makes a macOS **Recovery** installer: the machine boots into Recovery
through OpenCore and downloads macOS from Apple during setup. A full offline
installer cannot be built on Windows at all — Apple's `createinstallmedia` is a
macOS binary writing an APFS volume — which is why OpenCore Legacy Patcher can
offer one and this cannot. The macOS you end up with is the same.

The recovery itself is downloaded by **OpenCore's own `macrecovery.py`**,
extracted from the OpenCorePkg release. Apple's recovery protocol is
undocumented and moves; reimplementing it would be a quiet way to fetch the
wrong thing.

The board ID is what tells Apple which macOS to send, and it follows **the
release chosen in stage 2, not the SMBIOS the EFI presents** — those are
routinely different. A MacBookPro10,1 is a sensible SMBIOS for an Ivy Bridge
laptop and a useless board for anything past Catalina, so asking for Sequoia
with it returns Catalina, silently. Stage 10 therefore lists only Macs whose
board reaches the chosen release, preferring the stage 3 SMBIOS when it
qualifies and saying so when it does not. The model-to-board table in
`boards.py` is transcribed from [Dortania's SMBIOS support
page](https://dortania.github.io/OpenCore-Install-Guide/extras/smbios-support.html);
which releases each board covers comes from OpCore-Simplify's own model data.
Neither half is written from memory.

The `config.plist` **on the media** is adjusted for installing: auxiliary
entries shown, a longer picker timeout, and everything scanned. macOS Recovery
is an auxiliary entry, and OpenCore's sample config hides those — sensible on
an installed machine, and on an installer it hides the only thing you booted
the stick for, leaving a picker that looks empty. The EFI in `Results` is left
alone, because that one is for the machine once macOS is on it.

Before any of that is copied, the EFI is checked against its own
`config.plist`. OpenCore halts on a critical error if `Kernel -> Add` names a
kext the folder does not have — or `ACPI -> Add` a table, or `UEFI -> Drivers`
a driver — and it halts *after* the picker has drawn, which reads as "macOS
will not boot" rather than "a file is missing". `eficheck.audit` walks every
section that names a file and `eficheck.repair` drops the entries that point at
nothing, which is what OC Snapshot would have done. It is the last moment
before a USB stick is written, and each repair is reported rather than done
quietly.

Three outputs, all from one folder so the safe path always exists:

- **The folder.** `OpCoreForge_Data\InstallMedia` holds `EFI\` and
  `com.apple.recovery.boot\`. Format a USB stick as FAT32 and copy both
  across; that is the whole procedure.
- **The USB.** The only thing here that can destroy data, so: removable drives
  only, never disk 0, never a disk holding the system drive, and the drive has
  to be named back in the confirmation before anything runs. The `diskpart`
  script is built by a pure function, logged before it executes, and asserted
  on in tests without a disk present.
- **The ISO.** For VMs. A UEFI machine boots an ISO through an El Torito
  catalog entry pointing at a FAT image the firmware mounts as an EFI System
  Partition, so the media goes into a FAT32 image — written from scratch,
  because Windows ships nothing that makes one — which is then wrapped in
  ISO9660. The test suite walks that chain in the finished file and reads the
  boot image back with mtools. You choose where it is saved, and the free
  space is checked first: the boot image and the ISO exist at the same moment,
  so it wants roughly twice the size of the media.

### Knowing that something is happening

Three of these steps copy gigabytes and two of them do it twice. A window that
says nothing for four minutes is indistinguishable from one that has died, so:

- Every long operation takes a `progress(done, total)` callback — the FAT
  image writer, the ISO writer (through the file object pycdlib writes to,
  since it offers no hook of its own), staging the folder, and the USB copy.
  The status bar shows a real percentage, throttled to ten updates a second so
  a 3 GB copy does not hand three thousand callbacks to the main thread.
- The status line carries a clock once a step passes two seconds, and ends on
  `✓ <what it was> — finished in 2:41` rather than "Ready", which looked the
  same whether something had finished, been skipped, or never run.
- A finished stage gets a tick on its tab. Stages unlock only when the one
  before them is done, so that is where the mark comes from.
- Writing a USB flushes Windows' write cache before saying it is finished,
  because "copied" and "safe to unplug" are not the same moment.

---

## How the three are integrated

The design rule throughout: **OpCoreForge never re-implements a decision.**
Every compatibility check, kext dependency resolution, SMBIOS suggestion, ACPI
patch, config.plist rule, USB matching key and plist edit is executed by the
original upstream code. Only the terminal menus on top of them were replaced.

**Nothing in `src/vendor/` is hand-edited.** `build/vendor.py` performs one
mechanical transformation and asserts every rule it applies; behavioural changes
all live in `src/opcoreforge/patches.py`, so pulling a newer upstream release is
one command and cannot silently drop a fix.

### 1. Three packages called `Scripts`

All three projects ship a top-level package literally named `Scripts`. Only one
can win `sys.modules["Scripts"]`, and the other two then silently import the
wrong files. `build/vendor.py` renames them to `ocs_scripts`, `utb_scripts` and
`pt_scripts` and rewrites the imports, failing loudly if upstream's layout has
moved.

### 2. Paths that assume a source directory

Each tool resolves its working files relative to its own source folder —
`OCK_Files`, `Results`, `usb.json`, `UTBMap.kext`, `iasl.exe`, ProperTree's
`settings.json`. Inside a one-file build that folder is a temp directory deleted
on exit, so a naive bundle re-downloads OpenCore on every launch and loses your
port map.

All of those paths derive from the module's own `__file__`, so `patches.py`
reassigns `__file__` on the imported module. One line relocates every path that
module computes, with no forked code to keep in sync:

```
<data>/bin/kext_maestro.py   ->  dirname(dirname(f))  ->  <data>/OCK_Files
<data>/bin/smbios.py         ->  dirname(f)           ->  <data>/bin/macserial.exe
<data>/ocs_main.py           ->  dirname(f)           ->  <data>/Results
```

### 3. Questions asked from inside the logic

OpCore-Simplify's analysis and its terminal UI are interleaved:
`select_required_kexts` stops mid-analysis to ask which Intel Wi-Fi kext to use;
`hardware_customization` asks which GPU combination to enable; there are a
couple of dozen such prompts across ~15k lines.

Rather than fork that logic, `bridge/console.py` replaces the primitives every
prompt goes through — `Utils.head`, `print`, `Utils.request_input`. Any prompt
anywhere, **including ones added by a future upstream release**, surfaces as a
dialog showing the exact text upstream intended, with its numbered options
turned into buttons and a free-text field as the fallback. Nothing is answered
on your behalf; only "press Enter to continue" is acknowledged automatically,
and that text still appears in the tool output pane.

The same trick handles USBToolBox: `TUIMenu` and `TUIOnlyPrint` are swapped for
scriptable stand-ins, so upstream's `build_kext` — the part that must be exactly
right — runs unmodified with its three terminal prompts pre-answered.

### 4. ProperTree inside a tab

ProperTree is a whole Tk application: it owns a root, builds a window per
document, installs app-wide keybindings and runs its own mainloop. Four things
let it live in a notebook tab with everything intact:

- **A stand-in root.** ProperTree packs its hex/Base64 converter widgets
  directly into its Tk root, so handing it the real application window would
  scatter them across the UI. `tkinter.Tk` is briefly redirected to return a
  dedicated hidden `Toplevel` instead — which is also exactly what Ctrl+T then
  shows. Its `mainloop()` call is suppressed so control returns.
- **`wm forget`.** The document window is created as a child of the tab frame
  and then demoted from top-level to an ordinary managed widget. It keeps its
  widgets, bindings and state; only the window manager stops handling it. Every
  `wm` method then raises, so the ones ProperTree calls are shimmed.
- **A redirected `stackorder`.** Every ProperTree menu command resolves its
  target with `stackorder(...)[-1]`, and `wm stackorder` cannot see a forgotten
  window — so without this override, Ctrl+R and friends would silently do
  nothing.
- **No titlebar tinting on the embedded window.** ProperTree keeps its window
  titlebars in step with the system dark mode by handing each window's handle
  to `DwmSetWindowAttribute`. That API is defined for top-level windows only;
  the embedded document is a child frame, and passing its handle faults inside
  `dwmapi` — an access violation, so upstream's `except:` never sees it and the
  process simply dies. `winfo_manager()` distinguishes the two (`"wm"` versus
  the geometry manager holding it), so the real top-levels keep the feature.

**And, on Windows, a decision not to.** Embedding a whole second Tk
application is the one part of this that reaches past what Tk supports, and on
the machine this was reported from it faulted inside Tk's own code three times
— in three different calls, every one while the first document was being
opened, each after a fix that removed the previous one. An access violation is
not a Python exception: there is nothing to catch and the process simply
vanishes.

So on Windows the editor opens in its own window by default. That is how
ProperTree runs on its own, which is the configuration with millions of hours
behind it. The tab is a nicety; the editor working is not.

Everything that makes it part of OpCoreForge is unchanged — the same
document, the same toolbar, OC Snapshot still pointed at the EFI stage 6 built
— only the tab is replaced by a panel explaining where it went. The **Own
window** checkbox on stage 8 switches between the two and is remembered.

Embedding is still there and still tested, and it is the default everywhere
else. If it is turned on and does not survive, that is caught too: a marker
file is written just before the editor is built and removed once it is up, so
a run that dies is noticed on the next launch and the window is used instead,
with the reason on screen.

Its self-updater is removed: it spawns `sys.executable Scripts/update_check.py`
through `multiprocessing`, and inside a one-file build `sys.executable` is
OpCoreForge itself, so that would relaunch the whole application. (It is reached
synchronously from `__init__`, so the class is patched before construction.)

### 5. One pump, and no worker thread ever touches Tk

Tkinter is not thread-safe. The usual shortcut — calling `root.after()` from a
background thread — appears to work, but every call has to be serialised
against the main thread. Measured on the log path it cost about **40 ms per
call**, and OpCore-Simplify's download progress bar prints four times per
chunk: detection took *minutes* and the window looked hung.

So background threads never touch Tk. They push callables onto a plain
`queue.Queue` and the Tk thread drains it every 30 ms (`MainThreadPump`).
Console output is not even posted per write — the worker appends to a buffer
and the pump flushes it in one widget update per tick:

| pushed through the log path | before | after |
|---|---|---|
| 2,000 writes | 83.6 s | 0.00 s |
| 20,000 writes | did not finish | 0.03 s |

The log pane also emulates carriage returns, so a progress bar renders as one
line that updates in place instead of thousands of near-identical lines.

A related trap: `ProperTree.__init__` calls `update()` while laying out its
first document, so any pending callback can re-enter the code that is building
it. Both the config stage and the editor wrapper guard against being asked to
start a second editor mid-construction.

### 6. Two improvements over running the tools separately

- **OC Snapshot knows where to look.** Standalone ProperTree asks you to find
  the OC folder. Here it is the EFI stage 6 just built, so the picker is
  answered automatically.
- **A dead download no longer costs the whole build.** Upstream aborts the
  entire gather run if one asset is unreachable. OpCoreForge names the kext and
  offers to continue without it, so a single kext host being down does not mean
  "no EFI at all".

---

## Versions

Every delivered build gets its own version, so two builds are never
confusable. It appears in four places:

```
title bar                     OpCoreForge 1.1.0
OpCoreForge.exe --version     OpCoreForge 1.1.0
OpCoreForge.exe --self-test   OpCoreForge 1.1.0 self test
Explorer > Properties         File version 1.1.0
```

`CHANGELOG.md` records what changed in each one. The version lives in exactly
one place, `src/opcoreforge/version.py`, and is bumped with:

```
python build/bump_version.py patch -m "What changed"     # a fix
python build/bump_version.py minor -m "What changed"     # new behaviour
python build/bump_version.py major -m "What changed"     # a rework
python build/bump_version.py --show                      # current version
```

That writes the new number and adds the changelog entry in one step, so the two
cannot drift apart. The .exe keeps its stable name — shortcuts and the data
folder stay put — and the release zip carries the version instead.

## Keeping up with upstream

```
python build/vendor.py          # re-clone and re-apply the renames
python build/make_seed.py       # refresh the bundled payload
BUILD_EXE.bat --skip-seed       # rebuild
```

`vendor.py` asserts every rewrite it makes. If upstream restructures something,
vendoring fails with the rule that stopped matching rather than producing a
build that is broken in a subtle way. `src/vendor/*/UPSTREAM.txt` records the
commit each tree came from.

Verified against:

| Project | Commit |
|---|---|
| lzhoang2801/OpCore-Simplify | `e5d8a9f551b1` |
| USBToolBox/tool | `0c6823a2c643` |
| corpnewt/ProperTree | `51ed53dbe3c9` |

---

## Testing

```
python tools/make_fixture.py      # synthetic Comet Lake report + compiled DSDT
python tools/test_workflow.py     # 49 checks: report -> EFI -> USB map -> snapshot
python tools/test_gui.py          # 66 checks driving the real window, with screenshots
python tools/test_embed.py        # 33 checks on the ProperTree embedding
python tools/test_editor_window.py # 18 checks on the editor as its own window
python tools/test_unsupported.py  # 13 checks: a refusal must be visible, not silent
python tools/test_netdiag.py      # 31 checks: name the real cause of a failed download
python tools/test_gpu_ceiling.py  # 24 checks: refuse macOS no GPU here can drive
python tools/test_diagnostics.py  # 76 checks: session log, frozen paths, COM, elevation
python tools/test_pump.py         # 21 checks: the pump under a nested event loop
python tools/test_fatimage.py     # 27 checks: the FAT32 writer, read back by mtools
python tools/test_isoimage.py     # 18 checks: the ISO's UEFI boot chain, byte by byte
python tools/test_media.py        # 78 checks: boards, the recovery pipeline, every USB refusal
python tools/test_eficheck.py     # 53 checks: config.plist must not name a file the EFI lacks
python tools/test_progress.py     # 45 checks: progress, elapsed time, completion marks
python tools/test_buildbat.py     # 15 checks on BUILD_EXE.bat, under a real cmd.exe
python tools/bench_detect.py      # times the log path and ACPI parsing
python tools/bench_logpane.py     # isolates the per-write cost of the log pane
```

`test_workflow.py` builds a genuine EFI end to end — real SSDTs compiled by
iasl, a real config.plist with a generated serial, a real `UTBMap.kext` — then
loads it into the embedded editor and verifies OC Snapshot picks up the USB map
and drops the placeholder. On Linux, run them under `xvfb-run`.

---

## Reporting a bug

The status bar has **Save diagnostics...**. It writes one file containing the
build version, this PC's details (OS, Python, whether it is running elevated,
the clock), the hardware it detected, and everything the three tools printed
this session. Send that file.

It works after the fact: the session is recorded in memory from the moment the
window opens whether or not anything is switched on, so the crash that just
happened is already in it. A crash that kills the process outright leaves no
Python traceback at all, so `faulthandler` writes the native stack of every
thread to `OpCoreForge_Data\logs\crash.log` — send that too if the window
disappears. **Log to file** next to it mirrors the same content
to `OpCoreForge_Data\logs\` as it happens, which is the one to use for a
failure that takes the application down before a dialog can be read.
`OpCoreForge.exe --log` starts with it on.

---

## Notes and limits

- **The macOS list is wider than what will actually work.** OpCore-Simplify
  offers the union of every device's support range, so one component with a
  long life — a Broadcom Wi-Fi card that OpenCore Legacy Patcher carries to the
  newest release — stretches the list past what the graphics can drive.
  OpCoreForge marks those rows and refuses them, naming the GPU and the newest
  release it can display; chosen anyway, upstream disables every GPU and the
  next step dies reading an empty list.
- **Stages 1 and 7 need Windows.** Hardware Sniffer is a Windows binary, and USB
  port discovery reads live PnP data through WMI. The rest is cross-platform,
  which is what makes the test suite possible. Off Windows, stage 1 accepts an
  imported `Report.json` and stage 7 accepts a `usb.json` captured on the target
  machine.
- **The bundled payload is a starting point, not a pin.** After unpacking it
  once, OpCore-Simplify's own update logic takes over unchanged — release ids
  compared against `history.json`, SHA-256 verified, anything stale
  re-downloaded.
- **`certifi` is bundled deliberately.** OpCore-Simplify looks for the
  platform's CA bundle on disk and, not finding one (the normal case on
  Windows), falls back to certifi — and if that is absent too, to an
  *unverified* SSL context. Bundling it keeps kext downloads verified.
- This does not make a Hackintosh guaranteed to boot. It removes the file
  shuffling and the ordering mistakes; the
  [Dortania guide](https://dortania.github.io/OpenCore-Install-Guide/) is still
  the thing to read when something does not work.

## Credits and licences

All the real work belongs to the upstream authors. OpCoreForge is integration
code around them, and each project keeps its own licence in
`src/vendor/*/LICENSE`.

- [OpCore-Simplify](https://github.com/lzhoang2801/OpCore-Simplify) — lzhoang2801 — BSD 3-Clause
- [USBToolBox/tool](https://github.com/USBToolBox/tool) — dhinakg — MIT
- [ProperTree](https://github.com/corpnewt/ProperTree) — CorpNewt — BSD 3-Clause

OpCoreForge's own code — everything outside `src/vendor/` — is BSD 3-Clause;
see `LICENSE`. The same licence as two of the three upstreams, so the tree
reads under one set of conditions rather than a patchwork, and the MIT tree is
compatible with it. `THIRD-PARTY-NOTICES.md` has the whole picture: what is
redistributed, under which licence, from which commit, and what is downloaded
at run time instead of shipped.
