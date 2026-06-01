# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2026 Texas Instruments Incorporated - https://www.ti.com/
"""pytest configuration: clean up the pytest temp base dir after the session."""

import getpass
import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def cleanup_pytest_tmpdir():
    """Remove the pytest-of-<user> base dir in /tmp after the session ends."""
    yield
    base = Path(tempfile.gettempdir()) / f"pytest-of-{getpass.getuser()}"
    if base.exists():
        shutil.rmtree(base, ignore_errors=True)
