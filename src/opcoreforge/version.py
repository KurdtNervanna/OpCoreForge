"""
Single source of truth for the OpCoreForge version.

Deliberately a standalone module with no imports: the PyInstaller spec, the
build scripts and the bump tool all read it without importing the package
(which would drag in tkinter and the vendored trees).

Scheme is MAJOR.MINOR.PATCH and every delivered change bumps something, so a
build can always be identified from its title bar, ``--version`` or its
self-test output. Use build/bump_version.py rather than editing by hand -- it
keeps CHANGELOG.md in step.
"""

__version__ = "1.2.5"

# The day this version was cut. Used as a floor for the system clock: a PC
# cannot legitimately be running a build from the future, so a date earlier
# than this proves the clock is wrong -- which breaks every HTTPS download
# with a certificate error that says nothing about clocks. Kept in step by
# build/bump_version.py.
__released__ = "2026-09-16"
