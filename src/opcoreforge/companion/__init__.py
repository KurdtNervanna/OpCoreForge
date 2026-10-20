"""
OpCoreForge Companion: the macOS half of OpCoreForge.

OpCoreForge runs on Windows and ends with a USB stick. The companion runs on
the machine once macOS is installed and does what can only be done from there:
putting OpenCore on the internal disk so the stick is no longer needed to boot.

It ships as a disk image on that same stick (``OpCoreForge/`` on the media), so
nothing has to be downloaded on a machine that may not have working networking
yet. Everything macOS-specific is in ``system``; ``disks`` and ``installer``
take its output as plain data, which is what lets them be tested on any OS.
"""
