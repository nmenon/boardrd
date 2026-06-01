# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2026 Texas Instruments Incorporated - https://www.ti.com/
"""
Module resolver: map compatible strings and bare module names to .ko paths,
then recursively expand dependencies via modules.dep.

Inputs:
  - modules_dir: path to lib/modules/<KERNELRELEASE>/ (depmod output lives here)
  - compatible strings from DTB analysis
  - anchor module names from workflow YAMLs

Algorithm:
  1. Parse modules.alias → list of (pattern, module_name)
  2. Parse modules.dep  → dict of module_name → [dep_name, ...]
  3. For each compatible: fnmatch against of:N*T*C<compat>* alias patterns
  4. For each anchor name: locate matching .ko in modules_dir
  5. Recursively expand all dependencies
"""

import fnmatch
import logging
import os
log = logging.getLogger(__name__)


class ModResolver:
    def __init__(self, modules_dir):
        self._modules_dir = modules_dir
        self._alias_db = []     # list of (pattern, module_name)
        self._dep_db = {}       # module_name → list[dep_name]
        self._name_to_ko = {}   # bare_name → relative .ko path
        self._builtin_set = set()  # modules compiled into the kernel (=y)
        self._loaded = False

    def _ensure_loaded(self):
        if self._loaded:
            return
        self._load_alias_db()
        self._load_dep_db()
        self._load_builtin_set()
        self._build_name_index()
        self._loaded = True

    def is_builtin(self, name):
        """Return True if module is compiled into the kernel (no .ko needed)."""
        self._ensure_loaded()
        return _normalise_name(name) in {_normalise_name(b)
                                          for b in self._builtin_set}

    def _load_alias_db(self):
        alias_file = os.path.join(self._modules_dir, 'modules.alias')
        if not os.path.exists(alias_file):
            raise FileNotFoundError(
                f"modules.alias not found in {self._modules_dir}. "
                "Run 'make modules_install' first."
            )
        with open(alias_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split()
                if len(parts) == 3 and parts[0] == 'alias':
                    self._alias_db.append((parts[1], parts[2]))
        log.debug("Loaded %d alias entries from %s",
                  len(self._alias_db), alias_file)

    def _load_dep_db(self):
        dep_file = os.path.join(self._modules_dir, 'modules.dep')
        if not os.path.exists(dep_file):
            raise FileNotFoundError(
                f"modules.dep not found in {self._modules_dir}."
            )
        with open(dep_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                # Format: kernel/path/to/mod.ko: dep1.ko dep2.ko ...
                if ':' not in line:
                    continue
                left, _, right = line.partition(':')
                # Normalise: hyphens → underscores so alias names match .ko names
                mod_name = _normalise_name(_ko_to_name(left.strip()))
                deps = [_normalise_name(_ko_to_name(d))
                        for d in right.split() if d]
                self._dep_db[mod_name] = deps
        log.debug("Loaded %d module dep entries", len(self._dep_db))

    def _load_builtin_set(self):
        """Load modules.builtin — modules compiled into the kernel (=y)."""
        builtin_file = os.path.join(self._modules_dir, 'modules.builtin')
        if not os.path.exists(builtin_file):
            return
        with open(builtin_file) as f:
            for line in f:
                name = _ko_to_name(line.strip())
                if name:
                    self._builtin_set.add(name)
        log.debug("Loaded %d built-in module entries", len(self._builtin_set))

    def _build_name_index(self):
        """Build normalised_module_name → relative .ko path index."""
        kernel_dir = os.path.join(self._modules_dir, 'kernel')
        if not os.path.isdir(kernel_dir):
            log.warning("No kernel/ directory in %s", self._modules_dir)
            return
        for dirpath, _, filenames in os.walk(kernel_dir):
            for fname in filenames:
                if fname.endswith('.ko'):
                    rel = os.path.relpath(
                        os.path.join(dirpath, fname), self._modules_dir)
                    # Normalise: hyphens → underscores to match alias names
                    name = _normalise_name(_ko_to_name(fname))
                    self._name_to_ko[name] = rel
        log.debug("Indexed %d .ko files", len(self._name_to_ko))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def find_modules_for_compatible(self, compat):
        """
        Return list of module names matching a DT compatible string.

        For a compatible 'ti,am62-sdhci', generates the uevent string
        'of:NnodeTtypeCti,am62-sdhci' and fnmatches against alias patterns.
        """
        self._ensure_loaded()
        uevent = f"of:NnodeTtypeC{compat}"
        results = []
        for pattern, module in self._alias_db:
            if pattern.startswith('of:') and fnmatch.fnmatch(uevent, pattern):
                if module not in results:
                    results.append(module)
        return results

    def find_module_by_name(self, name):
        """
        Locate a module by bare name (e.g. 'mmc_block').
        Returns the normalised name if found in dep_db or name_to_ko index.
        Raises KeyError if not found.
        """
        self._ensure_loaded()
        normalised = _normalise_name(name)
        if normalised in self._dep_db:
            return normalised
        if normalised in self._name_to_ko:
            return normalised
        raise KeyError(f"Module not found: {name}")

    def resolve_deps(self, module_names):
        """
        Given an iterable of bare module names, return the full set of
        module names including all transitive dependencies.
        """
        self._ensure_loaded()
        result = set()
        queue = list(module_names)
        while queue:
            name = queue.pop()
            if name in result:
                continue
            result.add(name)
            for dep in self._dep_db.get(name, []):
                if dep not in result:
                    queue.append(dep)
        return result

    def topo_sort(self, module_names):
        """
        Return module_names sorted in dependency order: dependencies before
        the modules that use them (topological sort of the dep graph).

        This makes each modprobe call find its deps already loaded, avoids
        redundant dep resolution, and produces deterministic log output.
        Cycles are broken by skipping already-visited nodes.
        """
        self._ensure_loaded()
        in_set = set(module_names)
        visited = set()
        result = []

        def visit(mod):
            if mod in visited:
                return
            visited.add(mod)
            # Recurse into deps that are also in our module set
            for dep in self._dep_db.get(mod, []):
                if dep in in_set:
                    visit(dep)
            result.append(mod)

        for mod in sorted(in_set):  # sorted for deterministic output
            visit(mod)
        return result

    def resolve_compatibles(self, compatibles):
        """
        Given an iterable of DT compatible strings, return the full set
        of module names (including deps) needed.
        """
        self._ensure_loaded()
        initial = set()
        for compat in compatibles:
            matches = self.find_modules_for_compatible(compat)
            if matches:
                log.debug("  %-40s → %s", compat, ', '.join(matches))
            else:
                log.debug("  %-40s → (no match)", compat)
            initial.update(matches)
        return self.resolve_deps(initial)

    def resolve_anchors(self, anchor_names):
        """
        Given an iterable of bare module names (workflow anchors), return
        the full set of module names including deps.

        Built-in modules (compiled into the kernel) are skipped silently —
        they need no .ko file and will not be in modules.dep.
        A warning is only emitted for modules that are neither loadable
        nor built-in (genuinely missing from this kernel build).
        """
        self._ensure_loaded()
        initial = set()
        for name in anchor_names:
            try:
                resolved = self.find_module_by_name(name)
                initial.add(resolved)
            except KeyError:
                if self.is_builtin(name):
                    log.debug("Anchor '%s' is built-in (=y) — no .ko needed",
                              name)
                else:
                    log.warning(
                        "Workflow anchor '%s' not found as module or built-in "
                        "— check kernel config", name)
        return self.resolve_deps(initial)

    def ko_path(self, module_name):
        """
        Return the relative .ko path for a module name, or None if not
        found (e.g. built-in modules have no .ko file).
        """
        self._ensure_loaded()
        return self._name_to_ko.get(_normalise_name(module_name))

    def generate_workflow_suggestions(self, dt_module_names):
        """
        Given the set of DT-matched module names, return the subset of
        their transitive dependencies that are NOT themselves DT-matched.
        These are generic stack modules (e.g. mmc_core, mmc_block) that
        make good workflow anchor candidates.
        """
        self._ensure_loaded()
        all_deps = self.resolve_deps(dt_module_names)
        return sorted(all_deps - set(dt_module_names))


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _ko_to_name(path):
    """
    Convert a .ko path (possibly with kernel/ prefix) to a bare module name.
    e.g. 'kernel/drivers/mmc/host/sdhci_am654.ko' → 'sdhci_am654'
    """
    base = os.path.basename(path)
    if base.endswith('.ko'):
        base = base[:-3]
    return base


def _normalise_name(name):
    """Normalise module name: lowercase, hyphens → underscores."""
    return name.lower().replace('-', '_')
