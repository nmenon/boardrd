# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2026 Texas Instruments Incorporated - https://www.ti.com/
"""Tests for lib/mod_resolver.py using mock modules.alias and modules.dep."""

import os
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from boardrd.lib.mod_resolver import ModResolver, _ko_to_name, _normalise_name


def make_modules_dir(tmp_path, alias_content, dep_content):
    """Create a temp modules dir with given alias and dep files."""
    kernel_dir = tmp_path / 'kernel' / 'drivers' / 'mmc' / 'host'
    kernel_dir.mkdir(parents=True)

    (tmp_path / 'modules.alias').write_text(alias_content)
    (tmp_path / 'modules.dep').write_text(dep_content)

    # Create stub .ko files matching dep entries
    for line in dep_content.splitlines():
        if ':' in line:
            ko_rel = line.split(':')[0].strip()
            ko_path = tmp_path / ko_rel
            ko_path.parent.mkdir(parents=True, exist_ok=True)
            ko_path.touch()

    return tmp_path


class TestKoToName:
    def test_strips_ko(self):
        assert _ko_to_name('sdhci_am654.ko') == 'sdhci_am654'

    def test_strips_path_and_ko(self):
        assert _ko_to_name('kernel/drivers/mmc/host/sdhci_am654.ko') == 'sdhci_am654'

    def test_no_extension(self):
        assert _ko_to_name('mymod') == 'mymod'


class TestNormaliseName:
    def test_hyphen_to_underscore(self):
        assert _normalise_name('sdhci-am654') == 'sdhci_am654'

    def test_lowercase(self):
        assert _normalise_name('MMC_BLOCK') == 'mmc_block'


class TestModResolver:
    ALIAS = textwrap.dedent("""\
        # aliases
        alias of:N*T*Cti,am625-sdhci* sdhci_am654
        alias platform:sdhci-am654 sdhci_am654
        alias of:N*T*Cti,am62-mmc* omap_hsmmc
    """)

    DEP = textwrap.dedent("""\
        kernel/drivers/mmc/host/sdhci_am654.ko: kernel/drivers/mmc/host/sdhci.ko
        kernel/drivers/mmc/host/sdhci.ko: kernel/drivers/mmc/core/mmc_core.ko
        kernel/drivers/mmc/core/mmc_core.ko:
        kernel/drivers/mmc/core/mmc_block.ko: kernel/drivers/mmc/core/mmc_core.ko
        kernel/drivers/mmc/host/omap_hsmmc.ko: kernel/drivers/mmc/core/mmc_core.ko
    """)

    @pytest.fixture
    def resolver(self, tmp_path):
        mdir = make_modules_dir(tmp_path, self.ALIAS, self.DEP)
        return ModResolver(str(mdir))

    def test_find_modules_for_compatible(self, resolver):
        matches = resolver.find_modules_for_compatible('ti,am625-sdhci')
        assert 'sdhci_am654' in matches

    def test_find_modules_no_match(self, resolver):
        matches = resolver.find_modules_for_compatible('nonexistent,device')
        assert matches == []

    def test_resolve_deps_recursive(self, resolver):
        result = resolver.resolve_deps({'sdhci_am654'})
        assert 'sdhci_am654' in result
        assert 'sdhci' in result
        assert 'mmc_core' in result

    def test_resolve_deps_already_included(self, resolver):
        result = resolver.resolve_deps({'mmc_core'})
        assert result == {'mmc_core'}

    def test_resolve_compatibles(self, resolver):
        result = resolver.resolve_compatibles(['ti,am625-sdhci'])
        assert 'sdhci_am654' in result
        assert 'mmc_core' in result

    def test_resolve_anchors_found(self, resolver):
        result = resolver.resolve_anchors(['mmc_block'])
        assert 'mmc_block' in result
        assert 'mmc_core' in result

    def test_resolve_anchors_not_found_warns(self, resolver, caplog):
        import logging
        with caplog.at_level(logging.WARNING):
            resolver.resolve_anchors(['nonexistent_module'])
        assert 'nonexistent_module' in caplog.text

    def test_generate_workflow_suggestions(self, resolver):
        dt_modules = {'sdhci_am654'}
        suggestions = resolver.generate_workflow_suggestions(dt_modules)
        assert 'sdhci' in suggestions
        assert 'mmc_core' in suggestions
        assert 'sdhci_am654' not in suggestions
