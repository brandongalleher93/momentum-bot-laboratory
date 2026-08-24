"""Local privacy helpers for runtime artifacts.

The application writes account diagnostics, market observations, and simulated
trade records.  These helpers make those local artifacts private to the current
OS user by default.
"""

from __future__ import annotations

import os
from pathlib import Path


PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


def enforce_private_umask() -> None:
    """Prevent newly created runtime files from being group/world readable."""

    os.umask(0o077)


def ensure_private_directory(path: Path) -> None:
    """Create a runtime directory and restrict it to the current OS user."""

    path.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIRECTORY_MODE)
    path.chmod(PRIVATE_DIRECTORY_MODE)


def ensure_private_file(path: Path) -> None:
    """Create a file if needed and restrict it to the current OS user."""

    ensure_private_directory(path.parent)
    path.touch(exist_ok=True, mode=PRIVATE_FILE_MODE)
    path.chmod(PRIVATE_FILE_MODE)
