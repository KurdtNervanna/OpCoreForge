@echo off
setlocal enabledelayedexpansion
title Build OpCoreForge.exe

rem ---------------------------------------------------------------------------
rem  Builds OpCoreForge.exe - a single portable executable containing
rem  OpCore-Simplify, USBToolBox and ProperTree.
rem
rem  Requirements: Windows 10/11 (64-bit) and Python 3.9+ from python.org with
rem  "Add python.exe to PATH" ticked. Nothing else - this script installs what
rem  it needs into a local virtual environment and never touches your system
rem  Python packages.
rem
rem  Run it by double-clicking, or from a command prompt in this folder.
rem ---------------------------------------------------------------------------

cd /d "%~dp0"

echo.
echo  ==========================================================
echo   OpCoreForge - building the portable executable
echo  ==========================================================
echo.

rem -- locate a Python that actually works -------------------------------------
rem  It is not enough that a command exists. Windows ships App Execution Alias
rem  stubs for "python" and "py" that are on PATH but only print
rem  "Python was not found; run without arguments to install from the Microsoft
rem  Store". A `where` test passes on those and every later step then fails for
rem  a misleading reason, so each candidate is made to prove itself by running.
set "PY="
set "PYSAW=0"
set "OCFPROBE=%TEMP%\ocf_python_probe.tmp"
where python >nul 2>&1 && set "PYSAW=1"
where py >nul 2>&1 && set "PYSAW=1"

rem Candidates are passed through CAND rather than as an argument, because
rem %~1 would strip the quotes a path like "C:\Program Files\Python312" needs.
rem  (`if COND a & b` runs b unconditionally, hence the parentheses.)
set "CAND=py -3"
call :try_python
if not defined PY (
    set "CAND=python"
    call :try_python
)
if not defined PY (
    set "CAND=python3"
    call :try_python
)

rem Fall back to the usual install locations for a Python that is not on PATH.
rem (%ProgramFiles(x86)% is deliberately not searched: the parentheses in its
rem name break batch parsing inside a block, and a 32-bit build is not wanted.)
if not defined PY (
    for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
        if not defined PY if exist "%%~D\python.exe" (
            set "CAND="%%~D\python.exe""
            call :try_python
        )
    )
)
if not defined PY (
    for /d %%D in ("%ProgramFiles%\Python3*") do (
        if not defined PY if exist "%%~D\python.exe" (
            set "CAND="%%~D\python.exe""
            call :try_python
        )
    )
)
if not defined PY (
    for /d %%D in ("C:\Python3*") do (
        if not defined PY if exist "%%~D\python.exe" (
            set "CAND="%%~D\python.exe""
            call :try_python
        )
    )
)

if not defined PY (
    echo  [X] No working Python was found.
    echo.
    if "%PYSAW%"=="1" (
        echo      A "python" command exists but it is the Microsoft Store
        echo      placeholder, not a real interpreter.
        echo.
        echo      Fix it either way:
        echo        - install Python 3.9+ from https://www.python.org/downloads/
        echo          with "Add python.exe to PATH" ticked, and/or
        echo        - turn off the stubs in Settings ^> Apps ^>
        echo          Advanced app settings ^> App execution aliases
        echo          ^(switch off "python.exe" and "python3.exe"^)
    ) else (
        echo      Install Python 3.9 or newer from https://www.python.org/downloads/
        echo      and make sure "Add python.exe to PATH" is ticked during setup.
    )
    echo.
    pause
    exit /b 1
)

rem Ask the interpreter itself rather than parsing its banner.
for /f "usebackq delims=" %%v in (`%PY% -c "import sys;print(sys.version.split()[0])" 2^>nul`) do set "PYVER=%%v"
for /f "usebackq delims=" %%v in (`%PY% -c "import sys;print(sys.executable)" 2^>nul`) do set "PYEXE=%%v"

%PY% -c "import sys;raise SystemExit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
if errorlevel 1 (
    echo  [X] Python %PYVER% is too old. OpCoreForge needs 3.9 or newer.
    echo      Found at: %PYEXE%
    echo      Get a current version from https://www.python.org/downloads/
    pause
    exit /b 1
)

set "OCFVER=unknown"
for /f "usebackq delims=" %%v in (`%PY% build\bump_version.py --show 2^>nul`) do set "OCFVER=%%v"

echo  [1/6] Using Python %PYVER%  -  building OpCoreForge %OCFVER%

rem Tkinter ships with the python.org installer but is missing from some
rem builds; catching that here is far clearer than a failure 10 minutes in.
%PY% -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo  [X] This Python has no tkinter, which OpCoreForge's interface needs.
    echo      Found at: %PYEXE%
    echo      Reinstall from python.org and keep the "tcl/tk and IDLE" option
    echo      enabled, then run this again.
    pause
    exit /b 1
)
goto :python_ok

:try_python
rem Makes the candidate prove it is a real interpreter by having it write a
rem file. Exit codes alone are not conclusive and a text search would depend on
rem findstr; only something that can actually execute Python creates this.
del /q "%OCFPROBE%" >nul 2>&1
%CAND% -c "open(r'%OCFPROBE%','w').write('ok')" >nul 2>&1
if exist "%OCFPROBE%" set "PY=%CAND%"
del /q "%OCFPROBE%" >nul 2>&1
goto :eof

:python_ok

rem -- virtual environment ----------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo  [2/6] Creating build environment...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo  [X] Could not create the virtual environment.
        pause
        exit /b 1
    )
) else (
    echo  [2/6] Reusing existing build environment
)
set "VPY=.venv\Scripts\python.exe"

echo  [3/6] Installing build dependencies...
"%VPY%" -m pip install --upgrade pip --quiet
"%VPY%" -m pip install --quiet pyinstaller termcolor2 certifi wmi pywin32 pycdlib
if errorlevel 1 (
    echo  [X] Dependency installation failed. Check your internet connection.
    pause
    exit /b 1
)

rem -- vendored sources -------------------------------------------------------
if not exist "src\vendor\ocs\ocs_main.py" (
    echo  [4/6] Fetching the three upstream projects...
    where git >nul 2>&1
    if errorlevel 1 (
        echo  [X] The vendored sources are missing and git is not installed.
        echo      Install Git for Windows, or use a release archive that
        echo      already contains src\vendor.
        pause
        exit /b 1
    )
    "%VPY%" build\vendor.py
    if errorlevel 1 (
        echo  [X] Vendoring failed - see the message above.
        pause
        exit /b 1
    )
) else (
    echo  [4/6] Vendored sources present
)

rem -- offline payload --------------------------------------------------------
if "%~1"=="--skip-seed" goto :skipseed

set "SEEDSTATE=missing"
if exist "src\seed\payload.zip" set "SEEDSTATE=partial"
if exist "src\seed\MANIFEST.json" (
    for /f "usebackq delims=" %%c in (`"%VPY%" -c "import json;print(json.load(open(r'src\seed\MANIFEST.json'))['complete'])" 2^>nul`) do (
        if /i "%%c"=="True" set "SEEDSTATE=complete"
    )
)

if "%SEEDSTATE%"=="complete" (
    echo  [5/6] Bundled payload is complete ^(delete src\seed to rebuild^)
    goto :skipseed
)
if "%SEEDSTATE%"=="partial" (
    echo  [5/6] Bundled payload is incomplete - rebuilding it here, where the
    echo        network can reach every kext repository...
) else (
    echo  [5/6] Downloading OpenCore + kexts to bundle offline...
)
echo        This takes a few minutes and needs internet access.
"%VPY%" build\make_seed.py
if errorlevel 1 (
    echo  [!] The payload could not be built. Continuing anyway - the
    echo      executable will download what it needs on first use.
)
:skipseed

rem -- build ------------------------------------------------------------------
echo  [6/6] Building OpCoreForge.exe...
"%VPY%" -m PyInstaller --clean --noconfirm OpCoreForge.spec
if errorlevel 1 (
    echo.
    echo  [X] The build failed - see the output above.
    pause
    exit /b 1
)

if not exist "dist\OpCoreForge.exe" (
    echo  [X] The build reported success but dist\OpCoreForge.exe is missing.
    pause
    exit /b 1
)

for %%A in ("dist\OpCoreForge.exe") do set "SIZE=%%~zA"
set /a SIZEMB=!SIZE! / 1048576

echo.
echo  ==========================================================
echo   Done.  dist\OpCoreForge.exe   %OCFVER%   (!SIZEMB! MB)
echo  ==========================================================
echo.
echo   Copy that one file anywhere and run it. It creates an
echo   OpCoreForge_Data folder beside itself for the EFI it builds,
echo   the OpenCore/kext cache and your USB port map.
echo.
pause
