# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2026 Texas Instruments Incorporated - https://www.ti.com/
"""
Workflow loader: read boot-mode workflow YAML files.

Each workflow YAML defines:
  name:       boot mode name (mmc, nfs, ospi, usb, ufs)
  settle_ms:  milliseconds to wait after Pass 1 module loading before
              the Pass 2 runtime modalias scan (bus enumeration settle time)
  anchors:    bare module names that are entry points for this boot mode.
              Dependencies are resolved at build time via modules.dep.
              Do NOT list transitive deps here — they are found automatically.

The default workflow directory is <boardrd_root>/workflows/.
Override with --workflow-dir.
"""

import logging
import os

import yaml

from .paths import get_workflows_dir

log = logging.getLogger(__name__)

_DEFAULT_WORKFLOW_DIR = get_workflows_dir()


class Workflow:
    def __init__(self, name, anchors, settle_ms):
        self.name = name
        self.anchors = anchors        # list[str]
        self.settle_ms = settle_ms    # int


def load_workflow(name, workflow_dir=None):
    """
    Load a workflow YAML by name.

    Returns a Workflow object with .name, .anchors, .settle_ms.
    Raises FileNotFoundError if the workflow file does not exist.
    """
    workflow_dir = workflow_dir or _DEFAULT_WORKFLOW_DIR
    path = os.path.join(workflow_dir, f"{name}.yaml")

    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Workflow '{name}' not found at {path}. "
            f"Available: {_list_available(workflow_dir)}"
        )

    with open(path) as f:
        data = yaml.safe_load(f)

    anchors = data.get('anchors', [])
    settle_ms = int(data.get('settle_ms', 500))

    log.debug("Loaded workflow '%s': %d anchors, settle_ms=%d",
              name, len(anchors), settle_ms)
    return Workflow(name=name, anchors=anchors, settle_ms=settle_ms)


def generate_workflow_yaml(name, dt_modules, resolver, workflow_dir=None):
    """
    Auto-generate a workflow YAML by analyzing what modules appear in the
    dependency chains of DT-matched modules but are NOT themselves DT-matched.

    These non-DT-matched deps are the generic stack modules (e.g. mmc_core,
    mmc_block) that are good anchor candidates.

    Args:
        name:       workflow name to write (e.g. 'mmc')
        dt_modules: set of module names matched directly from DTB compatibles
        resolver:   ModResolver instance (already pointed at modules_dir)
        workflow_dir: output directory (default: workflows/)

    Writes <workflow_dir>/<name>.yaml and returns the suggested anchors list.
    """
    workflow_dir = workflow_dir or _DEFAULT_WORKFLOW_DIR
    os.makedirs(workflow_dir, exist_ok=True)

    suggestions = resolver.generate_workflow_suggestions(dt_modules)

    path = os.path.join(workflow_dir, f"{name}.yaml")

    lines = [
        "# SPDX-License-Identifier: GPL-2.0",
        "# Copyright (C) 2026 Texas Instruments Incorporated - https://www.ti.com/",
        "#",
        f"# Auto-generated workflow for: {name}",
        "# Review and trim before committing — remove modules that are not",
        "# needed for this boot mode. Transitive deps are resolved automatically.",
        "#",
        f"name: {name}",
        "",
        "# Milliseconds to wait after loading Pass 1 modules before the",
        "# runtime modalias scan (Pass 2). Tune for your hardware.",
        "settle_ms: 500",
        "",
        "# Entry-point modules for this boot mode.",
        "# Dependencies resolved automatically via modules.dep at build time.",
        "anchors:",
    ]
    for mod in suggestions:
        lines.append(f"  - {mod}")

    with open(path, 'w') as f:
        f.write('\n'.join(lines) + '\n')

    log.info("Generated workflow YAML: %s (%d suggested anchors)",
             path, len(suggestions))
    return suggestions


def _list_available(workflow_dir):
    if not os.path.isdir(workflow_dir):
        return '(directory not found)'
    names = [f[:-5] for f in os.listdir(workflow_dir) if f.endswith('.yaml')]
    return ', '.join(sorted(names)) or '(none)'
