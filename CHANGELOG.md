# Changelog

Every delivered build gets its own version, so you can always tell which one
you are running: it is in the title bar, in `OpCoreForge.exe --version`, at the
top of `--self-test`, and in the release zip's filename.

## 1.2.5 - 2026-09-16

- Application icon: assets/OpCoreForge.png, generated into build/OpCoreForge.ico at seven sizes by build/make_icon.py and embedded in the executable. The window uses the .ico on Windows and a 256px PNG elsewhere, since Tk cannot read an .ico off Windows
- Fixed the release archive silently dropping build/OpCoreForge.ico. The exclusion for PyInstaller's scratch directory was the prefix "build/OpCoreForge" with no trailing slash, so it matched the icon file as well as the folder - the icon was found by the first release that had one
- The archive now also carries LICENSE, THIRD-PARTY-NOTICES.md, .gitignore and assets/, which were not in its include list
- Licensing settled: BSD 3-Clause for the integration code, matching OpCore-Simplify and ProperTree, with USBToolBox's MIT terms compatible and adding nothing. THIRD-PARTY-NOTICES.md records what is redistributed, under which licence and from which upstream commit, and what is downloaded at run time rather than shipped

## 1.2.4 - 2026-09-16

- Building an ISO now asks where to save it. It used to write OpCoreForge-macOS.iso into the data folder without a dialog and without saying so, which from the outside is indistinguishable from the button doing nothing at all
- It also checks the free space first and refuses with a number rather than failing partway through: the FAT boot image and the ISO exist at the same moment, so it needs roughly twice the size of the media, and finding that out after twenty minutes of copying is the worst possible time
- Every long step now reports real progress. The FAT image writer, the ISO writer, staging the media folder and the USB copy all report bytes done against bytes total, and the status bar shows the percentage. The ISO's progress comes from the file object pycdlib writes to, since pycdlib offers no hook of its own
- The status bar shows how long the current step has been running once it passes two seconds, and finishes on "✓ Building the ISO - finished in 2:41" instead of "Ready", which looked identical whether a step had finished, been skipped, or never started
- Finished stages get a tick on their tab, so it is possible to see at a glance which steps are done
- Writing a USB stick now flushes Windows' write cache before reporting success, and says the drive is safe to remove; a completion dialog appears for the two steps long enough to walk away from
- New test: tools/test_progress.py (45 checks on progress reporting, the elapsed clock, tab marks and the throttle that keeps a 3 GB copy from flooding the main thread)

## 1.2.3 - 2026-09-08

- Fixed OpenCore halting on a critical error after the picker appeared: "Plist Kexts\UTBDefault.kext\Contents\Info.plist is missing for injected kext UTBDefault.kext". Installing the USB port map deletes UTBDefault.kext from the Kexts folder, and upstream's checklist says to re-run OC Snapshot afterwards so config.plist catches up. Nothing forced that, so Kernel -> Add kept naming a kext that had been deleted - which stops the boot dead - while UTBMap.kext sat in the folder unlisted and did nothing. install_usb_map now rewrites the entry in place, keeping load order and the kernel range, so the EFI boots whether or not a snapshot is ever run
- New eficheck module: config.plist is checked against the EFI folder before the media is staged and again at the finish stage. Every section that names a file is walked - Kernel -> Add, ACPI -> Add, UEFI -> Drivers, Misc -> Tools - and an entry pointing at nothing is dropped, which is what OC Snapshot would have done. Each repair is reported rather than done quietly, and a kext sitting in the folder that nothing loads is called out
- An EFI built by an earlier version is repaired in place: re-running stage 9 or 10 removes the dead UTBDefault.kext entry and adds the UTBMap.kext that was already installed, so the port map finally takes effect

## 1.2.2 - 2026-09-08

- The install media now shows macOS Recovery in the OpenCore picker. Recovery is an auxiliary entry and OpenCore's sample config sets HideAuxiliary, so the stick booted to a picker with nothing on it - the entry was there all along, behind the Space key. The copy of config.plist written to the media now shows auxiliary entries, waits 15 seconds instead of 5, and polls the Apple hotkeys; the EFI in Results keeps its own settings, because hiding auxiliary entries is the right default once macOS is installed
- ScanPolicy is forced to 0 on the media as a backstop - OpCore-Simplify already sets it, but anything else can hide a recovery on an external FAT volume
- Both banners now mention that Space reveals auxiliary entries, for media written by an older build

## 1.2.1 - 2026-09-08

- Fixed "The OpenCorePkg release does not list a RELEASE zip" on stage 10. OpCore-Simplify scrapes the release page rather than using the REST API, so its asset entries carry product_name and url, not name - the lookup was reading a key that is never there. It now uses Dortania's build index first, which is the same path stage 6 already downloads OpenCore through, and falls back to matching the asset URL
- The board ID is now a dropdown of Mac models instead of a text field. It lists only Macs whose board can be asked for the release chosen in stage 2, preselects the SMBIOS from stage 3 when that board reaches it, and says which Mac it fell back to when it does not - a MacBookPro10,1 board returns Catalina however clearly you asked for Sequoia. The model-to-board table is transcribed from Dortania's SMBIOS support page and joined with OpCore-Simplify's own support ranges
- Fixed the "where do I find this" link, which pointed at the wrong page and opened nothing. It used webbrowser, which in a windowed frozen build tries each registered browser in turn and spawns console windows that flash and vanish - the same symptom reported as a process looping. It now opens through the same call the rest of the app uses

## 1.2.0 - 2026-09-08

- New stage 10: macOS install media. Downloads a macOS recovery and puts it together with the EFI that was built, as a folder to copy by hand, a bootable USB stick, or a UEFI-bootable ISO for a virtual machine
- The recovery is fetched by OpenCore's own macrecovery.py, extracted from the OpenCorePkg release, and the board ID that decides which macOS Apple serves is read from what OpenCorePkg ships rather than guessed - if it cannot be parsed the stage asks instead of offering a default that might quietly fetch the wrong release
- The USB writer is the only part of OpCoreForge that can destroy data, so it refuses fixed disks, disk 0, anything holding the system or program drive, and anything too small; the diskpart script is a pure function that is logged before it runs and asserted on in tests with no disk present, and the drive has to be named back in the confirmation
- The ISO carries the media in a FAT32 image written from scratch - Windows has no mkfs.vfat - wrapped in ISO9660 with an El Torito UEFI entry. The tests walk that chain in the finished file and read the boot image back with mtools, so the writer is never checked only by its own reader
- New tests: tools/test_fatimage.py (27), tools/test_isoimage.py (18), tools/test_media.py (49); the GUI suite covers the new stage

## 1.1.8 - 2026-09-08

- On Windows the config.plist editor now opens in its own window by default. Embedding it in the tab demotes ProperTree's window with wm forget, and that faulted inside Tk itself three times on one machine - three different calls, every one while the first document was being opened, each after a fix that removed the previous one. An access violation is not a Python exception, so there is nothing to catch. A separate window is how ProperTree runs on its own, and everything else is identical: same document, same toolbar, OC Snapshot still pointed at the EFI stage 6 built
- The Own window checkbox on stage 8 switches between the two and is remembered, so embedding can still be chosen on Windows; it remains the default on every other platform and is still fully tested
- For anyone who does embed: the call that faulted is no longer made. Upstream's select() runs _tree.update() to force a layout pass before scrolling, which also dispatches focus and configure events to a window that no longer has a window manager; the embedded window uses update_idletasks() instead, which does the same layout pass and dispatches nothing. A plausible mitigation rather than a proven fix - it cannot be reproduced here

## 1.1.7 - 2026-09-08

- Fixed a second access violation while config.plist was opening, this time inside Tk itself with no Python frame on the stack. The editor's whole bring-up now runs with the main-thread pump paused - previously only its construction did, so a log flush could run inside the nested event loop ProperTree pumps while it populates the tree
- The config.plist editor can now open as its own window instead of inside the tab, which is how ProperTree runs normally and needs none of the wm forget demotion that has faulted twice on one machine. The document, the toolbar and OC Snapshot are identical either way
- Added an automatic fallback: a marker is written before the editor is built and removed once it is up, so a run that dies bringing it up is noticed at the next launch and the editor opens in its own window instead, with the reason shown. An Own window checkbox on stage 8 makes the choice permanent
- New test: tools/test_editor_window.py (17 checks on the standalone editor); test_diagnostics.py covers the crash marker and the remembered preference

## 1.1.6 - 2026-09-08

- Fixed the access violation that killed the application as config.plist opened. ProperTree keeps window titlebars in step with dark mode by passing each window's handle to DwmSetWindowAttribute, which is defined for top-level windows only; the embedded document is a child frame inside a tab, and passing its handle faults inside dwmapi - not a Python exception, so upstream's except: never saw it and the process just died. Only genuine top-level windows are passed now, so the converter and settings windows keep the feature and the embedded one, which has no titlebar, is skipped
- test_embed.py checks the window-manager distinction the fix relies on and that the embedded window never reaches the tinting call; its check helper can now assert values rather than only record them

## 1.1.5 - 2026-09-08

- Fixed the application dying silently right after the USB map was built. Switching to the config.plist tab happened inside ttk::notebook's own tab-change event, and the editor built there pumps the Tk event loop from its constructor - a nested loop inside the notebook's bookkeeping and inside the pump's own drain. Stage entry is now deferred to the idle queue, the pump holds exactly one timer however it is re-entered, and nothing of ours runs while a nested loop is in progress
- Added a native crash handler: a fault that kills the process leaves no Python traceback, so faulthandler now writes every thread's stack to OpCoreForge_Data\logs\crash.log. The editor also logs each step of its construction, so a report says which step died rather than which one finished
- Added an administrator check at startup. Stage 1 cannot dump ACPI tables and stage 7 silently misses USB ports without elevation, so OpCoreForge now says so and offers to restart itself as administrator
- Fixed stage buttons being clipped when the tool output pane is open. Footers are packed to the bottom before the tables that expand, and the output pane is capped at a third of the window
- New tests: tools/test_pump.py (21 checks on the pump under a nested event loop); the GUI suite checks every stage footer stays on screen with the output pane open

## 1.1.4 - 2026-09-08

- Fixed the USB stage failing in the packaged executable with 'The system cannot find the file specified: ...\utb_scripts'. utb_scripts.utils chdir's into its own module directory to look for a colours file; that directory does not exist inside a one-file build. The module is now pointed at a real directory, the same relocation the OpCore-Simplify helpers already use
- Fixed 'x_wmi_uninitialised_thread' when scanning USB ports. WMI is COM, and COM has to be initialised on the calling thread; every stage runs on a fresh one. Worker threads now join the process's multi-threaded apartment, which one sleeping thread holds open so the connection made in one stage still works in the next. A COM failure also gets one silent retry with a fresh connection
- Fixed the status bar - and with it Save diagnostics - disappearing on the USB map and config.plist tabs. It was packed after the notebook, so a tab taller than the window took the whole area and left it no height
- USB failures now explain themselves: COM not ready, a file missing from the build, or WMI not responding each get their own message and what to try, with the original error kept underneath
- test_gui.py checks the status bar survives the tallest tabs; test_diagnostics.py covers the frozen-path chdir, the COM apartment and the retry

## 1.1.3 - 2026-09-08

- Fixed the crash when picking a macOS version no GPU in the machine can drive. The list OpCore-Simplify offers is the union of every device's support range, so a Broadcom Wi-Fi card that OpenCore Legacy Patcher carries to Tahoe stretched the list past an Intel HD 4000 that stops at Sequoia; choosing from the far end disabled every GPU and the next step died on 'NoneType has no attribute items'. Those releases are now marked in the list and refused with the reason, naming the GPU and the newest macOS it can display
- Fixed 'utb_windows.py not found on sys.path' when scanning USB ports from the packaged executable. The file is read as source, so bytecode in the one-file archive was not enough; it is now shipped in the bundle, the bundle is searched, and --self-test checks for it
- Added Save diagnostics... and a Log to file switch to the status bar, plus OpCoreForge.exe --log. The session is recorded in memory from startup whether or not logging is on, so a report can be saved after a crash and still contain it - along with the build version, the PC's OS, Python, clock and elevation, and the detected hardware
- Errors that have a diagnosis attached now open a readable dialog naming the problem instead of a traceback, and the generic error dialog points at Save diagnostics
- New tests: tools/test_gpu_ceiling.py (24 checks) and tools/test_diagnostics.py (30 checks); test_gui.py covers the diagnostics controls

## 1.1.2 - 2026-09-08

- BUILD_EXE.bat: make each Python candidate prove it can run before using it, so a Microsoft Store alias stub no longer wins the search and then fails later blaming tkinter
- BUILD_EXE.bat: fall back through py -3, python, python3 and the usual install folders; report the Python version, path and the OpCoreForge version being built
- Diagnose why a download failed instead of reporting a traceback: a wrong system clock, which invalidates every HTTPS certificate, is now named as the cause with the true time and how to fix it
- Warn at startup when the system date is earlier than this build's release date - proof the clock is wrong before anything is downloaded
- New tests: tools/test_buildbat.py (Python detection under a real cmd.exe) and tools/test_netdiag.py (network failure diagnosis)

## 1.1.1 - 2026-09-08

- Fixed the app appearing to wait for input it never asked for. When OpCore-Simplify refuses to continue (unsupported GPU, no GPU found, and four similar checks) it prints the reason, waits for Enter, then exits. OpCoreForge auto-answers that pause, so the exit was unwinding silently: the status bar said "Cancelled" and the explanation stayed in a log pane that is hidden by default. The reason now travels with the abort and is shown in a dialog, on a red banner, and in the compatibility panel.
- Trailing "Press Enter to continue..." is stripped from messages shown in the GUI, where there is nothing to press.

## 1.1.0 - 2026-09-08

- Fixed detection being extremely slow after "Detect this PC's hardware".
  Background threads were reaching into Tk through `root.after()` to report
  progress, which serialises every call against the UI thread at roughly 40 ms
  each. OpCore-Simplify's download progress bar prints four times per chunk, so
  this ran into the tens of thousands of calls and took minutes. Worker threads
  no longer touch Tk at all: they queue work and the UI thread drains it every
  30 ms. Pushing 20,000 writes through the log went from never finishing to
  0.03 s.
- The tool output pane now honours carriage returns, so a download progress bar
  is one line updating in place rather than thousands of near-identical lines.
- Hardware detection reports what it is doing ("Scanning hardware and dumping
  ACPI tables", "Disassembling ACPI tables with iasl") instead of sitting on a
  single unchanging message during the parts that genuinely take a while.
- Fixed a re-entrancy bug that could build a second config.plist editor:
  ProperTree pumps the Tk event loop while laying out its first document, so a
  pending callback could re-enter the code constructing it.
- Version is now shown in the title bar, `--version`, `--self-test` and the
  release filename, from one source of truth.

## 1.0.0 - 2026-09-07

- First build: OpCore-Simplify, USBToolBox and ProperTree combined into one
  portable Windows executable across nine ordered stages.
- The three upstream projects, which each ship a top-level package called
  `Scripts`, are mechanically renamed at vendoring time so they can coexist in
  one process.
- Working files (OpenCore cache, built EFI, USB port map, editor settings) live
  beside the executable, with a fallback to `%LOCALAPPDATA%` when that folder is
  read-only.
- Questions asked from inside OpCore-Simplify's analysis surface as dialogs
  showing the text upstream printed, with its options as buttons.
- ProperTree is embedded in a tab with OC Snapshot, undo/redo, find & replace
  and the Configuration.tex tooltips intact.
- USBToolBox's `UTBMap.kext` is installed into the built EFI and the placeholder
  `UTBDefault.kext` removed, then picked up by OC Snapshot.
- OC Snapshot targets the EFI that was just built instead of asking for the
  folder; a single unreachable kext download no longer aborts the whole build.
- `--self-test` verifies a packaged build is complete.
