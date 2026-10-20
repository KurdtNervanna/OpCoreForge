#!/usr/bin/env python3
"""Entry point for OpCoreForge Companion, from source or the macOS app."""
import os
import sys

if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from opcoreforge.companion.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
