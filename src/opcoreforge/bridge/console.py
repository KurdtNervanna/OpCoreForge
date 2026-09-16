"""
Console interaction bridge.

OpCore-Simplify is a terminal program.  Its decision logic and its terminal UI
are interleaved: functions like ``select_required_kexts`` will, in the middle of
otherwise pure hardware analysis, stop and ask the operator which Wi-Fi kext to
use, whether to force-load an incompatible kext on an unsupported macOS
release, or which of two AMD Navi drivers to try.  Those questions carry real
information and there are a couple of dozen of them scattered across ~15k lines.

OpCoreForge gives the big menus purpose-built GUI screens, but it must not lose
the inline questions -- and it must not break if a future upstream release adds
new ones.  So instead of forking the logic, this module replaces the *output and
input primitives* that every one of those prompts goes through:

    Utils.head()             -> starts a new logical screen
    print()                  -> accumulates into that screen's text
    Utils.request_input()    -> hands the screen to the GUI and blocks
    Utils.progress_bar()     -> reports step progress
    Utils.exit_program()     -> unwinds the worker instead of killing the app

The result is that *any* prompt anywhere in OpCore-Simplify -- including ones
that do not exist yet -- automatically surfaces as a dialog showing exactly the
text upstream intended, with its choices turned into buttons where they can be
parsed and a free-text field otherwise.  No upstream function is bypassed,
short-circuited, or answered on the user's behalf.

The OpCore-Simplify workflow runs on a worker thread; Tk lives on the main
thread.  Requests cross that boundary through a queue plus an Event the worker
waits on.
"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading

ANSI_RE = re.compile(r"\x1b\[([0-9;]*)m")
ANSI_ANY_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# Foreground colours OpCore-Simplify actually uses, mapped to tag names the
# log/dialog widgets define.
_SGR_TAGS = {
    "1": "bold",
    "90": "dim",
    "91": "red",
    "92": "green",
    "93": "yellow",
    "96": "cyan",
    "32": "green",
    "36": "cyan",
    "31": "red",
    "0": None,
}


class WorkflowAborted(Exception):
    """Raised inside the worker thread to unwind an in-flight operation.

    Used when the user cancels a prompt or closes the application while
    OpCore-Simplify is blocked waiting for input.  Upstream's ``exit_program``
    (which calls ``sys.exit``) is redirected here so quitting a sub-menu never
    takes the whole GUI down.

    ``screen`` carries whatever was printed just before the stop. Upstream's
    fatal paths -- "You cannot install macOS without a supported GPU", "No GPU
    found", and four more like them -- print the reason, wait for Enter, then
    exit. The waiting-for-Enter step is what a terminal user reads the message
    during; in a GUI there is nothing to read unless the reason travels with
    the exception, so it does.
    """

    def __init__(self, message="", screen="", fatal=False):
        super().__init__(message)
        self.reason = message
        self.screen = screen
        #: True when OpCore-Simplify stopped itself, rather than the user
        #: cancelling a prompt or the app shutting down.
        self.fatal = fatal


def strip_ansi(text: str) -> str:
    return ANSI_ANY_RE.sub("", text)


_TRAILING_PROMPT_RE = re.compile(
    r"(?:\n\s*)*(?:press\s+enter[^\n]*|[^\n]*press\s+\[?enter\]?[^\n]*)\s*$",
    re.I)


def strip_trailing_prompt(text: str) -> str:
    """Remove a trailing "Press Enter to continue..." from captured output.

    In a terminal that line is the pause that lets you read the message. In a
    dialog there is nothing to press, so leaving it in just invites the reader
    to hunt for an input box that does not exist.
    """
    return _TRAILING_PROMPT_RE.sub("", text or "").rstrip()


def parse_ansi(text: str):
    """Split text into ``(chunk, tag)`` pairs, tag being a style name or None."""
    out = []
    pos = 0
    tag = None
    for match in ANSI_RE.finditer(text):
        if match.start() > pos:
            out.append((text[pos:match.start()], tag))
        codes = [c for c in match.group(1).split(";") if c]
        if not codes or codes == ["0"]:
            tag = None
        else:
            tag = None
            for code in codes:
                mapped = _SGR_TAGS.get(code)
                if mapped:
                    tag = mapped
        pos = match.end()
    if pos < len(text):
        out.append((text[pos:], tag))
    return [(chunk, t) for chunk, t in out if chunk]


class Prompt:
    """One question from OpCore-Simplify, ready for the GUI to render."""

    #: prompts that are purely "press enter to carry on"
    ACK_RE = re.compile(r"press enter|continue\.\.\.$|^$", re.I)

    def __init__(self, title: str, screen: str, prompt: str):
        self.title = title or "OpCore Simplify"
        self.screen = screen
        self.prompt = prompt
        self.answer: str | None = None
        self.event = threading.Event()
        self.aborted = False

    # -- classification --------------------------------------------------

    @property
    def is_acknowledge(self) -> bool:
        text = self.prompt.strip()
        return not text or bool(self.ACK_RE.search(text))

    @property
    def yes_no(self) -> tuple[str, str] | None:
        """Return the (yes, no) literals when the prompt is a yes/no question."""
        match = re.search(r"\((yes|y)\s*/\s*(no|n)\)", self.prompt, re.I)
        if match:
            return "yes", "no"
        match = re.search(r"\((no|n)\s*/\s*(yes|y)\)", self.prompt, re.I)
        if match:
            return "yes", "no"
        return None

    @property
    def default(self) -> str | None:
        match = re.search(r"\(default:\s*([^)]+)\)", self.prompt, re.I)
        return match.group(1).strip() if match else None

    def numbered_choices(self) -> list[tuple[str, str]]:
        """Parse ``1. Something`` style options out of the captured screen.

        Returns a list of (value, label).  Only used to offer shortcut buttons;
        the free-text entry is always available so nothing is ever unreachable.
        """
        choices: list[tuple[str, str]] = []
        for line in strip_ansi(self.screen).splitlines():
            match = re.match(r"^\s{0,4}(\d{1,2})\.\s+(\S.*?)\s*$", line)
            if match:
                value, label = match.group(1), match.group(2)
                if not any(value == v for v, _ in choices):
                    choices.append((value, label))
        # A menu of 1..N with no gaps is a real menu; anything else is probably
        # numbered prose, so don't offer buttons for it.
        if len(choices) < 2 or len(choices) > 30:
            return []
        return choices

    def letter_choices(self) -> list[tuple[str, str]]:
        choices: list[tuple[str, str]] = []
        for line in strip_ansi(self.screen).splitlines():
            match = re.match(r"^\s{0,4}([A-Za-z])\.\s+(\S.*?)\s*$", line)
            if match:
                value, label = match.group(1), match.group(2)
                if not any(value.lower() == v.lower() for v, _ in choices):
                    choices.append((value, label))
        return choices


class ConsoleBridge:
    """Routes OpCore-Simplify's terminal I/O to the GUI.

    Everything here runs on the OpCore-Simplify worker thread, so nothing in
    this class may touch Tk. Output is buffered for the Tk thread to collect;
    prompts and progress are handed over through a pump the UI supplies.
    """

    #: cap on unread console output held in memory
    MAX_PENDING = 512 * 1024
    #: cap on chunks retained for the "what was on screen" prompt context
    MAX_SCREEN_CHUNKS = 4000

    def __init__(self):
        self.requests: "queue.Queue[Prompt]" = queue.Queue()
        self._screen: list[str] = []
        self._pending: list[str] = []
        self._pending_len = 0
        self._dropped = 0
        self._title = "OpCore Simplify"
        self._lock = threading.RLock()
        self._aborting = False
        self._auto_answers: dict[re.Pattern, str] = {}

        #: set by the UI to a MainThreadPump; all UI callbacks go through it
        self.pump = None
        #: called on the Tk thread with a Prompt; must eventually set answer
        #: and fire ``prompt.event``.
        self.on_prompt = None
        #: called with (title, steps, index, done)
        self.on_progress = None
        #: called with a new screen title
        self.on_screen = None

        self._stdout = sys.stdout

    def _to_ui(self, callback, *args):
        """Hand a callback to the Tk thread, whichever thread we are on."""
        if callback is None:
            return
        if self.pump is not None:
            self.pump.post(callback, *args)
        else:
            callback(*args)

    # -- lifecycle -------------------------------------------------------

    def abort(self):
        """Unblock any waiting worker and make further prompts raise."""
        self._aborting = True
        while True:
            try:
                pending = self.requests.get_nowait()
            except queue.Empty:
                break
            pending.aborted = True
            pending.event.set()

    def reset(self):
        self._aborting = False
        with self._lock:
            self._screen = []

    def auto_answer(self, pattern: str, response: str):
        """Pre-answer a prompt matching *pattern* without showing a dialog.

        Used only where the GUI has already collected the same decision through
        a dedicated screen, so the user is never asked the same thing twice.
        """
        self._auto_answers[re.compile(pattern, re.I)] = response

    def clear_auto_answers(self):
        self._auto_answers.clear()

    # -- captured console primitives -------------------------------------

    def head(self, text=None, width=68, resize=True):
        with self._lock:
            self._screen = []
            self._title = text or "OpCore Simplify"
        self._to_ui(self.on_screen, self._title)

    def write(self, text: str):
        """Stand-in for ``sys.stdout.write`` while the worker is running.

        Called from the worker thread, once per ``print`` -- and a download
        progress bar prints four times per chunk, so this can run tens of
        thousands of times a minute. It therefore does no UI work at all: the
        text is buffered here and the Tk thread drains it on a timer via
        ``drain_output``.
        """
        if not text:
            return
        with self._lock:
            self._screen.append(text)
            self._pending.append(text)
            self._pending_len += len(text)
            # Never let an unread buffer grow without bound (a runaway
            # subprocess, or the log pane hidden and nobody draining).
            if self._pending_len > self.MAX_PENDING:
                dropped = 0
                while self._pending_len > self.MAX_PENDING // 2 and self._pending:
                    dropped += len(self._pending.pop(0))
                self._pending_len -= dropped
                self._dropped += dropped
            # The captured screen is only ever used for the next prompt, so it
            # does not need to hold a whole build's worth of output either.
            if len(self._screen) > self.MAX_SCREEN_CHUNKS:
                del self._screen[:-self.MAX_SCREEN_CHUNKS // 2]

    def drain_output(self):
        """Return everything printed since the last call. Tk thread only."""
        with self._lock:
            if not self._pending:
                return ""
            text = "".join(self._pending)
            self._pending = []
            self._pending_len = 0
            if self._dropped:
                text = ("... [%d characters of output skipped] ...\n" % self._dropped) + text
                self._dropped = 0
        return text

    def flush(self):
        pass

    def isatty(self):
        return False

    def current_screen(self) -> str:
        with self._lock:
            return "".join(self._screen)

    def adjust_window_size(self, content=""):
        pass

    def progress_bar(self, title, steps, current_step_index, done=False):
        self._to_ui(self.on_progress, title, list(steps),
                    current_step_index, done)

    def open_folder(self, folder_path):
        """Reveal a folder in the platform file manager."""
        try:
            if os.name == "nt":
                os.startfile(str(folder_path))  # noqa: S606  (Windows only)
            elif sys.platform == "darwin":
                subprocess.run(["open", str(folder_path)], check=False)
            else:
                subprocess.run(["xdg-open", str(folder_path)], check=False)
        except Exception:
            pass

    def exit_program(self):
        """Upstream calls ``sys.exit(0)`` here; unwind the worker instead.

        The screen goes with it so the UI can show *why* it stopped -- the
        preceding lines are the whole explanation.
        """
        raise WorkflowAborted("OpCore-Simplify stopped",
                              screen=self.current_screen(), fatal=True)

    def request_input(self, prompt: str = "Press Enter to continue...") -> str:
        if self._aborting:
            raise WorkflowAborted("cancelled")

        for pattern, response in self._auto_answers.items():
            if pattern.search(prompt):
                self.write(prompt + response + "\n")
                return response

        item = Prompt(self._title, self.current_screen(), prompt)
        self.requests.put(item)
        self._to_ui(self.on_prompt, item)
        item.event.wait()

        if item.aborted or self._aborting:
            raise WorkflowAborted("cancelled")

        answer = item.answer if item.answer is not None else ""
        self.write("%s%s\n" % (prompt, answer))
        # A fresh screen begins after every answered prompt in upstream's flow.
        return answer


class _StdoutProxy:
    """Redirects ``print`` into the bridge without losing the real stream."""

    def __init__(self, bridge: ConsoleBridge, real):
        self._bridge = bridge
        self._real = real

    def write(self, text):
        self._bridge.write(text)
        return len(text)

    def flush(self):
        pass

    def isatty(self):
        return False

    def __getattr__(self, name):
        return getattr(self._real, name)


def install(bridge: ConsoleBridge, utils_module) -> None:
    """Point ``utils.Utils`` and stdout at *bridge*.

    ``utils_module`` is ``ocs_scripts.utils``.  Every OpCore-Simplify component
    builds its own ``Utils()`` instance, so patching the class covers all of
    them, including instances created later.
    """
    cls = utils_module.Utils
    cls.head = lambda self, text=None, width=68, resize=True: bridge.head(text, width, resize)
    cls.request_input = lambda self, prompt="Press Enter to continue...": bridge.request_input(prompt)
    cls.progress_bar = lambda self, title, steps, i, done=False: bridge.progress_bar(title, steps, i, done)
    cls.adjust_window_size = lambda self, content="": bridge.adjust_window_size(content)
    cls.exit_program = lambda self: bridge.exit_program()
    cls.open_folder = lambda self, folder_path: bridge.open_folder(folder_path)

    sys.stdout = _StdoutProxy(bridge, bridge._stdout)

    # Upstream's fetcher prints the real reason a request failed and then
    # returns None, so the printed line is the only surviving evidence. Let
    # the diagnostics read it back off this screen.
    from .. import netdiag
    netdiag.set_console_reader(bridge.current_screen)
