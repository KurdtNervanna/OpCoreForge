"""
Minimal stand-in for the `ansiescapes` package.

USBToolBox's terminal UI imports `ansiescapes` -- specifically the
`embedded-dev` fork, since the release on PyPI is Python 2 only and raises on
import under Python 3.  OpCoreForge drives USBToolBox through its data model
rather than through its terminal UI, so the only reason this module needs to
exist at all is so `utb_scripts.utils` imports cleanly.  Providing the handful
of cursor escapes it references removes a git-sourced dependency from the
frozen build.
"""

ESC = "\x1b["

cursorSavePosition = "\x1b7"
cursorRestorePosition = "\x1b8"
cursorPrevLine = ESC + "F"
cursorNextLine = ESC + "E"
cursorLeft = ESC + "G"
cursorHide = ESC + "?25l"
cursorShow = ESC + "?25h"
eraseDown = ESC + "J"
eraseUp = ESC + "1J"
eraseLine = ESC + "2K"
eraseScreen = ESC + "2J"
clearScreen = "\x1bc"


def cursorTo(x=0, y=None):
    if y is None:
        return ESC + "%dG" % (x + 1)
    return ESC + "%d;%dH" % (y + 1, x + 1)


def cursorMove(x=0, y=0):
    out = ""
    if y:
        out += ESC + "%d%s" % (abs(y), "B" if y > 0 else "A")
    if x:
        out += ESC + "%d%s" % (abs(x), "C" if x > 0 else "D")
    return out


def cursorUp(n=1):
    return ESC + "%dA" % n


def cursorDown(n=1):
    return ESC + "%dB" % n


def eraseLines(n=1):
    return (eraseLine + cursorUp()) * max(n - 1, 0) + eraseLine + cursorLeft
