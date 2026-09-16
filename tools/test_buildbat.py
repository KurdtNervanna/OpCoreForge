"""
Tests BUILD_EXE.bat's Python detection under a real cmd.exe (via Wine).

This exists because the detection shipped broken twice. Windows puts App
Execution Alias stubs for `python` and `py` on PATH; they are not interpreters,
they only print "Python was not found; run without arguments to install from
the Microsoft Store". The original check used `where`, which those stubs pass,
so the build then failed several steps later complaining about tkinter.

Batch has enough sharp edges (`if COND a & b` runs b unconditionally, `%~1`
strips quotes, `for /f` chokes on quoted paths) that reading the script is not
sufficient evidence it works. So the real block is lifted out of BUILD_EXE.bat
and exercised against fake interpreters.

The fakes are compiled to real .exe files rather than written as .bat scripts.
That is not fussiness: cmd transfers control permanently when a batch file is
invoked without `call`, so a .bat stand-in makes `call :try_python` never
return and every scenario fail for a reason that does not exist on Windows,
where python and py really are executables.

Needs a mingw cross-compiler and Wine; skips cleanly without them.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = Path("/tmp/battest")
CACHE = WORK / "exe-cache"
WINE = "/usr/lib/wine/wine64"
MINGW = "x86_64-w64-mingw32-gcc"
STDERR = sys.stderr
RESULTS = []

# A Store stub: on PATH, prints a sentence, is not an interpreter.
STUB_C = r"""
#include <stdio.h>
int main(void) {
    printf("Python was not found; run without arguments to install from the "
           "Microsoft Store, or disable this shortcut from Settings > Manage "
           "App Execution Aliases.\n");
    return 9009;
}
"""

# A stand-in that answers the probes the script issues. It scans the whole
# argument list rather than one position, because the code lands in a
# different place depending on the candidate: `python -c CODE` puts it in
# argv[2] but `py -3 -c CODE` puts it in argv[3].
REAL_C = r"""
#include <stdio.h>
#include <string.h>

static const char *find(int argc, char **argv, const char *needle) {
    int i;
    for (i = 1; i < argc; i++) {
        const char *hit = strstr(argv[i], needle);
        if (hit) return argv[i];
    }
    return 0;
}

int main(int argc, char **argv) {
    const char *arg = find(argc, argv, "ocf_python_probe");
    if (arg) {
        /* The script hands over a path inside r'...'; write to exactly that,
           so a path the script mangles shows up as a failure here. */
        const char *start = strstr(arg, "r'");
        char path[1024];
        const char *end;
        size_t len;
        FILE *f;
        if (!start) return 1;
        start += 2;
        end = strchr(start, '\'');
        if (!end) return 1;
        len = (size_t)(end - start);
        if (len >= sizeof path) return 1;
        memcpy(path, start, len);
        path[len] = 0;
        f = fopen(path, "w");
        if (!f) return 1;
        fputs("ok", f);
        fclose(f);
        return 0;
    }
    if (find(argc, argv, "sys.version.split")) { printf("%s\n", VER); return 0; }
    if (find(argc, argv, "sys.executable"))    { printf("%s\n", EXE); return 0; }
    if (find(argc, argv, "bump_version"))      { printf("%s\n", OCFVER); return 0; }
    if (find(argc, argv, "version_info"))      return OLD;
    if (find(argc, argv, "import tkinter"))    return TK;
    return 0;
}
"""


def compile_exe(source: str, defines: dict) -> Path:
    """Build (and cache) one fake interpreter."""
    key = hashlib.sha256(
        (source + repr(sorted(defines.items()))).encode()).hexdigest()[:16]
    out = CACHE / ("%s.exe" % key)
    if out.exists():
        return out
    CACHE.mkdir(parents=True, exist_ok=True)
    csrc = CACHE / ("%s.c" % key)
    csrc.write_text(source)
    cmd = [MINGW, "-O0", "-o", str(out)]
    for name, value in defines.items():
        cmd.append("-D%s=%s" % (name, value))
    cmd.append(str(csrc))
    subprocess.run(cmd, check=True, capture_output=True)
    return out


def stub() -> Path:
    return compile_exe(STUB_C, {})


def real(name: str, ver: str = "3.12.3", tk: int = 0, old: int = 0,
         ocfver: str = "9.9.9") -> Path:
    # These reach the compiler through execve, not a shell, so the quotes and
    # backslashes are exactly what C should see -- no shell escaping.
    return compile_exe(REAL_C, {
        "VER": '"%s"' % ver,
        "EXE": '"C:\\\\fake\\\\%s.exe"' % name,
        "OCFVER": '"%s"' % ocfver,
        "TK": str(tk),
        "OLD": str(old),
    })


def extract_block() -> str:
    """Lift the detection block out of the real script, verbatim."""
    text = (ROOT / "BUILD_EXE.bat").read_text()
    start = text.index("rem -- locate a Python that actually works")
    # The label definitions, not the `goto`/`call` that mention them first --
    # cutting at `goto :python_ok` silently drops the :try_python subroutine
    # and every scenario then fails with "Target to GOTO not found".
    end = re.search(r"(?m)^:python_ok\s*$", text)
    if end is None:
        raise SystemExit("BUILD_EXE.bat has no :python_ok label")
    block = text[start:end.end()]
    if "\n:try_python" not in block:
        raise SystemExit("extracted block is missing the :try_python subroutine")
    # `pause` waits for a keypress; harmless for a person, fatal for a test.
    block = re.sub(r"(?m)^(\s*)pause\s*$", r"\1rem pause", block)
    return ("@echo off\r\nsetlocal enabledelayedexpansion\r\n"
            + block.replace("\n", "\r\n")
            + "\r\necho RESULT_PY=[%PY%]\r\n"
              "echo RESULT_PYVER=[%PYVER%]\r\n"
              "echo RESULT_OCFVER=[%OCFVER%]\r\n")


def run(scenario: str, shims: dict) -> tuple[str, int]:
    case = WORK / scenario
    if case.exists():
        shutil.rmtree(case)
    binaries = case / "bin"
    binaries.mkdir(parents=True)
    for name, built in shims.items():
        shutil.copy(built, binaries / ("%s.exe" % name))
    (case / "test.bat").write_text(extract_block())
    # bump_version.py is consulted for OCFVER; give it something to find.
    (case / "build").mkdir()
    (case / "build" / "bump_version.py").write_text("print('9.9.9')\n")

    tmp = case / "tmp"
    tmp.mkdir(exist_ok=True)
    win = lambda path: "Z:" + str(path).replace("/", "\\\\")

    env = dict(os.environ)
    env.update({"WINEPREFIX": "/tmp/wp", "WINEDEBUG": "-all", "DISPLAY": ""})
    # The system directories stay on PATH: `for /f` runs its command through
    # CMD.EXE, so a PATH holding only the fakes leaves cmd unable to find
    # itself and every captured value comes back empty.
    path = "%s;C:\\windows\\system32;C:\\windows" % win(binaries)
    proc = subprocess.run(
        [WINE, "cmd", "/c",
         "set PATH=%s&& set TEMP=%s&& set TMP=%s&& cd /d %s&& test.bat"
         % (path, win(tmp), win(tmp), win(case))],
        capture_output=True, text=True, env=env, timeout=300)
    return proc.stdout.replace("\r", ""), proc.returncode


def check(label, got, expect):
    ok = expect(got) if callable(expect) else got == expect
    RESULTS.append(("PASS" if ok else "FAIL", label, got))


def field(output, name):
    match = re.search(r"RESULT_%s=\[(.*)\]" % name, output)
    return match.group(1) if match else None


def main():
    if not Path(WINE).exists() or not shutil.which(MINGW):
        STDERR.write("wine or mingw not available; skipping\n")
        return 0
    WORK.mkdir(parents=True, exist_ok=True)

    # 1. Only Store stubs on PATH -- the exact case that broke.
    out, _ = run("stub_only", {"python": stub(), "py": stub(),
                               "python3": stub()})
    check("stub-only: refuses to continue",
          "No working Python was found" in out, True)
    check("stub-only: names the Store placeholder",
          "Microsoft Store" in out and "App execution aliases" in out, True)
    check("stub-only: does not blame tkinter",
          "tkinter" in out, False)
    check("stub-only: does not pick a stub", field(out, "PY"), lambda v: not v)

    # 2. py is a stub, python is real -- must fall through, not give up.
    out, _ = run("stub_py_real_python",
                 {"py": stub(), "python": real("python")})
    check("falls through a stub to a real python", field(out, "PY"), "python")
    check("reads the version from the interpreter",
          field(out, "PYVER"), "3.12.3")
    check("reads the app version", field(out, "OCFVER"), "9.9.9")
    check("reports the working interpreter",
          "Using Python 3.12.3" in out, True)

    # 3. A working `py -3` is preferred and accepted.
    out, _ = run("real_py", {"py": real("py")})
    check("accepts a working py launcher", field(out, "PY"), "py -3")

    # 4. Real python, but no tkinter -- that message should appear only here.
    out, _ = run("no_tkinter", {"python": real("python", tk=1)})
    check("missing tkinter is reported accurately",
          "has no tkinter" in out, True)
    check("missing tkinter names the interpreter",
          "C:\\fake\\python.exe" in out, True)

    # 5. Too old.
    out, _ = run("too_old", {"python": real("python", ver="3.7.9", old=1)})
    check("rejects Python older than 3.9", "too old" in out, True)
    check("old python reports its version", "3.7.9" in out, True)

    # 6. Nothing at all on PATH.
    out, _ = run("nothing", {})
    check("no python at all: clear message",
          "No working Python was found" in out, True)
    # Which of the two "no Python" messages is shown depends on `where`, and
    # Wine's `where` returns 0 for everything, so that branch cannot be
    # distinguished here. It is correct on real Windows, where `where` returns
    # 1 when a command is not found.
    check("no python at all: still refuses to continue",
          field(out, "PY"), lambda v: not v)

    STDERR.write("\n")
    for status, label, value in RESULTS:
        STDERR.write("%s  %-46s %s\n" % (status, label, value))
    failed = [r for r in RESULTS if r[0] == "FAIL"]
    STDERR.write("\n%d checks, %d failed\n" % (len(RESULTS), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
