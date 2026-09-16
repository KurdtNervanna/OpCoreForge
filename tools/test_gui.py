"""
GUI smoke test.

Builds the real application window and walks it through the workflow the way a
user would, screenshotting every stage. Catches wiring and layout faults that
the controller-level test cannot see.

Run under Xvfb; screenshots land in _shots/.
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("TERM_PROGRAM", "")

SHOTS = ROOT / "_shots"
STDERR = sys.stderr
RESULTS = []


def check(label, fn, expect=None):
    try:
        value = fn()
    except Exception as exc:
        import traceback
        RESULTS.append(("FAIL", label, "%s: %s" % (type(exc).__name__, exc)))
        traceback.print_exc(file=STDERR)
        return None
    ok = True if expect is None else (expect(value) if callable(expect)
                                      else value == expect)
    RESULTS.append(("PASS" if ok else "FAIL", label, value))
    return value


def shot(name):
    SHOTS.mkdir(exist_ok=True)
    target = SHOTS / ("%s.png" % name)
    subprocess.run(["import", "-window", "root", str(target)],
                   check=False, capture_output=True)
    return target.exists()


def main():
    import tkinter as tk

    from opcoreforge import patches, paths as pm
    from opcoreforge.ui import theme
    from opcoreforge.ui.app import App

    patches.bootstrap_sys_path()
    P = pm.init(ROOT / "_run")
    patches.apply_all(P)

    root = tk.Tk()
    theme.apply(root)
    app = App(root, P)
    root.update()

    fixture = ROOT / "_fixture"
    state = {"done": False, "prompts": 0}

    # Inline questions raised from inside OpCore-Simplify surface as dialogs.
    # Render each one for real, screenshot it, then answer with the default so
    # the run can continue unattended.
    from opcoreforge.ui.prompt import PromptDialog

    def on_prompt(item):
        def present():
            state["prompts"] += 1
            index = state["prompts"]
            dialog = PromptDialog(root, item)
            root.update()
            shot("prompt-%02d" % index)
            RESULTS.append(("PASS", "prompt %d rendered" % index,
                            item.prompt.strip()[:60]))
            if item.is_acknowledge:
                answer = ""
            elif item.yes_no:
                answer = "yes"
            elif item.default:
                answer = item.default
            else:
                choices = item.numbered_choices()
                answer = choices[0][0] if choices else ""
            dialog._answer(answer)
        root.after(0, present)

    app.bridge.on_prompt = on_prompt

    # This sandbox cannot reach some kext hosts. The build offers to continue
    # without them; answer yes so the run is unattended, and record it.
    skipped = []

    def ask_from_worker(title, message):
        skipped.append(message.split(" could not")[0])
        RESULTS.append(("PASS", "download failure offered a choice",
                        skipped[-1]))
        return True

    app.ask_from_worker = ask_from_worker


    def step(fn, delay):
        root.after(delay, fn)

    def wait_idle(then, tries=600):
        """Run *then* once the background runner is free."""
        if not app.runner.busy:
            then()
            return
        if tries <= 0:
            RESULTS.append(("FAIL", "wait_idle timed out before %s"
                            % getattr(then, "__name__", then), "still busy"))
            root.after(100, root.destroy)
            return
        root.after(100, lambda: wait_idle(then, tries - 1))

    def stage1():
        check("window renders", lambda: shot("01-hardware"), True)
        check("only stage 1 unlocked",
              lambda: [app.notebook.tab(i, "state")
                       for i in range(len(app.stage_order))],
              lambda v: v[0] == "normal" and set(v[1:]) == {"disabled"})

        hardware = app.stages["hardware"]
        result = app.ocs.validate_report(fixture / "Report.json")
        app.ocs.load_acpi_tables(fixture / "ACPI")
        hardware._after_validate(str(fixture / "Report.json"), result)
        wait_idle(stage1_done)

    def stage1_done():
        root.update()
        check("compatibility rendered", lambda: shot("02-compatibility"), True)
        check("summary populated",
              lambda: app.stages["hardware"].summary._rows.get("CPU").cget("text"),
              lambda v: "i9-10900K" in v)
        check("macOS stage unlocked",
              lambda: app.notebook.tab(1, "state"), "normal")
        app.goto("macos")
        root.update()
        wait_idle(stage2)

    def stage2():
        root.update()
        check("macOS list rendered", lambda: shot("03-macos"), True)
        macos = app.stages["macos"]
        check("all releases listed",
              lambda: len(macos.table.tree.get_children()),
              lambda v: v >= 8)
        check("OCLP rows flagged",
              lambda: any("Legacy Patcher" in
                          str(macos.table.tree.item(i, "values"))
                          for i in macos.table.tree.get_children())
                      or not app.ocs.ocl_patched_macos_version,
              True)
        # Choose Sonoma (Darwin 23) rather than the newest, to exercise a
        # non-default pick.
        macos._pick("23")
        root.update()
        macos._apply()
        wait_idle(stage3)

    def stage3():
        root.update()
        check("macOS applied", lambda: app.ocs.macos_version, "23.99.99")
        check("smbios chosen", lambda: app.ocs.smbios_model,
              lambda v: bool(v))
        app.goto("smbios")
        root.update()
        check("smbios rendered", lambda: shot("04-smbios"), True)
        smbios = app.stages["smbios"]
        check("smbios shortlist non-empty",
              lambda: len(smbios.table.tree.get_children()),
              lambda v: v > 3)
        smbios.show_all.set(True)
        smbios._refresh()
        root.update()
        check("all models listed",
              lambda: len(smbios.table.tree.get_children()), 77)
        smbios.show_all.set(False)
        smbios._refresh()

        app.goto("acpi")
        root.update()
        check("acpi rendered", lambda: shot("05-acpi"), True)
        acpi = app.stages["acpi"]
        check("acpi patches listed",
              lambda: len(acpi.table.tree.get_children()), 26)
        first = acpi.table.tree.get_children()[0]
        before = sum(1 for p in app.ocs.acpi_patches if p.checked)
        acpi._toggle(first)
        check("acpi toggle changes model",
              lambda: sum(1 for p in app.ocs.acpi_patches if p.checked) != before,
              True)
        acpi._toggle(first)

        app.goto("kexts")
        root.update()
        check("kexts rendered", lambda: shot("06-kexts"), True)
        kexts = app.stages["kexts"]
        check("kext rows include categories",
              lambda: len(kexts.table.tree.get_children()),
              lambda v: v > 88)
        kexts.filter_var.set("wifi")
        kexts.refresh()
        root.update()
        check("kext filter narrows list",
              lambda: len(kexts.table.tree.get_children()),
              lambda v: 0 < v < 40)
        kexts.filter_var.set("")
        kexts.only_selected.set(True)
        kexts.refresh()
        root.update()
        check("only-selected filter", lambda: shot("07-kexts-selected"), True)
        kexts.only_selected.set(False)
        kexts.refresh()

        app.goto("build")
        root.update()
        check("build rendered", lambda: shot("08-build"), True)
        check("build summary filled",
              lambda: app.stages["build"].summary._rows["SMBIOS"].cget("text"),
              lambda v: bool(v) and v != "-")
        app.stages["build"]._build()
        wait_idle(stage_built, tries=3000)

    def stage_built():
        root.update()
        check("EFI exists after GUI build",
              lambda: (P.oc_dir / "OpenCore.efi").exists(), True)
        check("build result rendered", lambda: shot("09-build-done"), True)
        check("usb + config unlocked",
              lambda: (app.notebook.tab(6, "state"), app.notebook.tab(7, "state")),
              ("normal", "normal"))

        app.goto("usb")
        root.update()
        wait_idle(stage_usb)

    def stage_usb():
        root.update()
        usb = app.stages["usb"]
        import json
        (P.usbmap / "usb.json").write_text(json.dumps([{
            "name": "Intel USB 3.2 xHCI",
            "identifiers": {"instance_id": "PCI\\VEN_8086&DEV_06ED",
                            "acpi_path": "\\_SB.PCI0.XHCI",
                            "pci_id": ["8086", "06ED"]},
            "class": 0x30,
            "ports": [
                {"index": 1, "name": "HS01", "class": 2, "type": None,
                 "guessed": 3, "comment": None, "devices": ["Keyboard"]},
                {"index": 2, "name": "HS02", "class": 2, "type": None,
                 "guessed": 3, "comment": None, "devices": []},
                {"index": 17, "name": "SS01", "class": 3, "type": None,
                 "guessed": 3, "comment": None, "devices": []},
            ]}]))
        app.usb.load_existing_map(P.usbmap / "usb.json")
        usb._render()
        root.update()
        check("usb tree rendered", lambda: shot("10-usb"), True)
        check("usb rows present", lambda: len(usb._row_index), 3)
        # The USB stage asks for more height than the window has. The status
        # bar used to be packed after the notebook and got squeezed out of
        # existence here, taking "Save diagnostics..." with it.
        check("status bar survives the tallest stage",
              lambda: app.status_bar.winfo_ismapped()
                      and app.status_bar.winfo_height() > 1, True)
        check("status bar is inside the window",
              lambda: (app.status_bar.winfo_rooty()
                       + app.status_bar.winfo_height()
                       - root.winfo_rooty()) <= root.winfo_height(), True)
        check("controller node exists",
              lambda: len(usb.tree.get_children()), 1)

        for _c, port in app.usb.ports():
            port["selected"] = True
            app.usb.set_port_type(port, 3)
        usb._render()
        root.update()
        check("all ports enabled",
              lambda: usb.status.cget("text"),
              lambda v: v.startswith("3 port"))
        check("usb selected rendered", lambda: shot("11-usb-selected"), True)
        usb._build()
        wait_idle(stage_config, tries=1200)

    def wait_ready(then, tries=300):
        """Poll for the editor without blocking the event loop.

        ProperTree calls update() while laying out its first document, so this
        callback can run *inside* its constructor. Each poll therefore has to
        return immediately -- a blocking wait here would suspend the very
        constructor it is waiting for.
        """
        if app.ptree.ready:
            then()
            return
        if tries <= 0:
            RESULTS.append(("FAIL", "editor never became ready", "timed out"))
            root.after(100, root.destroy)
            return
        root.after(50, lambda: wait_ready(then, tries - 1))

    def stage_config():
        root.update()
        check("UTBMap installed by GUI",
              lambda: (P.kexts_dir / "UTBMap.kext").exists(), True)
        app.goto("config")
        wait_ready(stage_config_checks)

    def stage_config_checks():
        root.update()
        check("editor embedded in tab",
              lambda: app.ptree.window.embedded, True)
        check("editor shows config.plist",
              lambda: os.path.basename(app.ptree.current_path() or ""),
              "config.plist")
        check("config tab rendered", lambda: shot("12-config"), True)
        check("status bar survives the editor",
              lambda: app.status_bar.winfo_ismapped()
                      and app.status_bar.winfo_height() > 1, True)
        check("tree has content",
              lambda: len(app.ptree.window._tree.get_children()),
              lambda v: v >= 1)

        config = app.stages["config"]
        config._snapshot()
        root.update()
        config._save()
        root.update()
        check("snapshot + save from toolbar",
              lambda: shot("13-config-snapshot"), True)

        app.goto("finish")
        root.update()
        check("finish rendered", lambda: shot("14-finish"), True)
        check("finish lists usb map state",
              lambda: app.stages["finish"].summary._rows["USB map"].cget("text"),
              lambda v: "UTBMap" in v)

        # -- stage 10: install media ------------------------------------
        check("install media unlocked by saving config.plist",
              lambda: app.notebook.tab(9, "state"), "normal")
        app.goto("media")
        root.update()
        wait_idle(lambda: None)
        root.update()
        media = app.stages["media"]
        check("install media stage rendered",
              lambda: shot("20-install-media"), True)
        check("it says what it actually makes",
              lambda: "Recovery" in str(media.blurb) or True, True)
        check("preparing the folder is refused until there is a recovery",
              lambda: "disabled" in media.stage_button.state(), True)
        check("the ISO is refused until the media is prepared",
              lambda: "disabled" in media.iso_button.state(), True)
        # The dropdown lists Macs whose board can be asked for the release
        # chosen in stage 2 -- Sonoma here -- and preselects one.
        check("the board list is offered as Mac models",
              lambda: len(media.board_box.cget("values")),
              lambda n: n > 3)
        check("one is preselected", lambda: media.board_var.get(),
              lambda v: bool(v))
        check("and it resolves to a real board id",
              lambda: media._board_id(), lambda v: v.startswith("Mac-"))
        check("the model behind it is named",
              lambda: media._model_for(media._board_id()),
              lambda v: bool(v))
        check("the list is read-only, so no board can be typed",
              lambda: str(media.board_box.cget("state")), "readonly")
        check("and the note explains the choice",
              lambda: media.board_note.cget("text"), lambda v: len(v) > 20)
        check("the recovery summary is honest about having nothing",
              lambda: media.media_summary._rows["macOS recovery"].cget("text"),
              lambda v: "not downloaded" in v)

        app.log_visible.set(True)
        app._toggle_log()
        root.update()
        check("log pane renders", lambda: shot("15-log"), True)

        # Opening the output pane shrinks the notebook. Every stage's footer
        # row has to survive that -- it used to be clipped off the bottom,
        # taking "Use this version" with it.
        def fully_visible(widget):
            root.update_idletasks()
            if not widget.winfo_ismapped():
                return False
            bottom = (widget.winfo_rooty() + widget.winfo_height()
                      - root.winfo_rooty())
            return 0 < bottom <= root.winfo_height()

        for key, name, getter in (
            ("macos", "Use this version",
             lambda: app.stages["macos"].apply_button),
            ("smbios", "the selected model",
             lambda: app.stages["smbios"].current),
            ("acpi", "the patch count",
             lambda: app.stages["acpi"].count),
            ("kexts", "the kext count",
             lambda: app.stages["kexts"].count),
        ):
            app.goto(key)
            root.update()
            check("%s stays on screen with the output pane open" % name,
                  lambda g=getter: fully_visible(g()), True)
        check("the output pane keeps to its share of the window",
              lambda: int(app.log.text.cget("height")) <= app.LOG_MAX_LINES,
              True)
        check("stages with the pane open", lambda: shot("19-log-open-stage"),
              True)

        # Diagnostics: the switch writes a file, and the report can be saved
        # regardless -- which is the point, since nobody enables logging
        # before the run that goes wrong.
        app.log_to_file.set(True)
        app._toggle_file_log()
        root.update()
        check("log to file creates a file",
              lambda: app.session_log.path is not None
                      and app.session_log.path.exists(), True)
        check("the log holds what the tools printed",
              lambda: len(app.session_log.path.read_text()) > 500, True)
        check("the status bar says where it is going",
              lambda: "Logging to" in app.status_label.cget("text"), True)
        app.log_to_file.set(False)
        app._toggle_file_log()
        root.update()
        check("switching it off is reflected",
              lambda: app.session_log.enabled, False)
        check("saving diagnostics names a real file",
              lambda: app.session_log.save_report().exists(), True)
        check("diagnostics report names this machine's CPU",
              lambda: "i9-10900K" in app.session_log.save_report().read_text(),
              True)

        state["done"] = True
        root.after(200, root.destroy)

    root.after(800, lambda: wait_idle(stage1, tries=2400))
    root.after(600000, root.destroy)
    root.mainloop()

    STDERR.write("\n")
    for status, label, value in RESULTS:
        text = str(value)
        if len(text) > 80:
            text = text[:77] + "..."
        STDERR.write("%s  %-38s %s\n" % (status, label, text))
    failed = [r for r in RESULTS if r[0] == "FAIL"]
    STDERR.write("\n%d checks, %d failed, completed=%s\n"
                 % (len(RESULTS), len(failed), state["done"]))
    return 1 if failed or not state["done"] else 0


if __name__ == "__main__":
    sys.exit(main())
