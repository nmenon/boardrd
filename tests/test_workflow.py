# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2026 Texas Instruments Incorporated - https://www.ti.com/
"""Tests for lib/workflow.py."""

import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from boardrd.lib.workflow import load_workflow, Workflow


def make_workflow_dir(tmp_path, workflows):
    """Create a temp workflow dir with given YAML files. workflows is dict name→content."""
    for name, content in workflows.items():
        (tmp_path / f'{name}.yaml').write_text(yaml.dump(content))
    return tmp_path


class TestLoadWorkflow:
    def test_loads_anchors_and_settle(self, tmp_path):
        wdir = make_workflow_dir(tmp_path, {
            'mmc': {'name': 'mmc', 'settle_ms': 200,
                    'anchors': ['mmc_block', 'vfat', 'ext4']}
        })
        wf = load_workflow('mmc', str(wdir))
        assert isinstance(wf, Workflow)
        assert wf.name == 'mmc'
        assert wf.settle_ms == 200
        assert 'mmc_block' in wf.anchors
        assert 'vfat' in wf.anchors

    def test_default_settle_ms(self, tmp_path):
        wdir = make_workflow_dir(tmp_path, {
            'custom': {'name': 'custom', 'anchors': ['some_mod']}
        })
        wf = load_workflow('custom', str(wdir))
        assert wf.settle_ms == 500

    def test_missing_workflow_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="not found"):
            load_workflow('nonexistent', str(tmp_path))

    def test_empty_anchors(self, tmp_path):
        wdir = make_workflow_dir(tmp_path, {
            'empty': {'name': 'empty', 'settle_ms': 100, 'anchors': []}
        })
        wf = load_workflow('empty', str(wdir))
        assert wf.anchors == []
