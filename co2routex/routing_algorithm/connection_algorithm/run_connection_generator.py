"""Convenience entry point for running from an IDE or terminal."""

from __future__ import annotations

import sys
from pathlib import Path


PACKAGE_PARENT = Path(__file__).resolve().parent.parent

if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))

from connection_algorithm.cli import main  # noqa: E402


if __name__ == "__main__":
    main()
