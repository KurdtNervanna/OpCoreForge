#!/usr/bin/env python3
"""Entry point for both the source checkout and the frozen executable."""
import multiprocessing
import os
import sys

# USBToolBox's terminal helper imports `ansiescapes`, which reads TERM_PROGRAM
# at import time; without it the import raises before anything else can run.
os.environ.setdefault("TERM_PROGRAM", "")

if getattr(sys, "frozen", False):
    # Required or any accidental Process() spawn re-runs the whole executable.
    multiprocessing.freeze_support()
else:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from opcoreforge.ui.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
