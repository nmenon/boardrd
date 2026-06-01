# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2026 Texas Instruments Incorporated - https://www.ti.com/
"""
Path resolution for boardrd data files (workflows, templates, boards).

Works correctly in three scenarios:
  - Direct execution:   python -m boardrd
  - Editable install:   pip install -e .
  - Regular install:    pip install .

All data lives inside the boardrd/ package so it is always co-installed.
"""

import os

# boardrd/lib/paths.py → boardrd/ package root is one level up
_BOARDRD_PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_workflows_dir():
    return os.path.join(_BOARDRD_PKG, 'workflows')


def get_templates_dir():
    return os.path.join(_BOARDRD_PKG, 'templates')


def get_boards_dir():
    return os.path.join(_BOARDRD_PKG, 'boards')


def get_busybox_config():
    # busybox.config lives alongside paths.py in boardrd/lib/
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'busybox.config')


def get_busybox_src():
    # The busybox git submodule is at the project root, one level above boardrd/.
    # Works for source tree and editable installs.
    # For a regular pip install there is no submodule; pass --busybox-src or --busybox.
    candidate = os.path.join(os.path.dirname(_BOARDRD_PKG), 'busybox')
    if os.path.isdir(candidate):
        return candidate
    return None
