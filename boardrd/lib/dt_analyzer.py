# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2026 Texas Instruments Incorporated - https://www.ti.com/
"""
DTB analyzer: extract compatible strings from a device tree blob.

Two modes:
  - All enabled nodes (boot_nodes omitted): collect compatibles from every
    node whose status is not "disabled" or "reserved". Safe, maximal.
  - Explicit boot_nodes: BFS from named nodes following all phandle
    references in all properties. No property whitelist — any referenced
    node may carry a loadable module.
"""

import struct
import logging

log = logging.getLogger(__name__)


def _fdt32(data, offset):
    """Read a big-endian u32 from bytes."""
    return struct.unpack_from('>I', data, offset)[0]


class DtbAnalyzer:
    def __init__(self, dtb_path):
        try:
            import libfdt
            self._libfdt = libfdt
        except ImportError:
            raise ImportError(
                "pylibfdt is required: install via 'pip install pylibfdt' "
                "or ensure dtc is built with Python bindings"
            )

        with open(dtb_path, 'rb') as f:
            self._data = f.read()
        self._fdt = self._libfdt.Fdt(self._data)
        self._dtb_path = dtb_path

    def _get_strings(self, node_offset, prop_name):
        """Return list of null-separated strings from a property, or []."""
        prop = self._fdt.getprop(node_offset, prop_name,
                                 self._libfdt.QUIET_NOTFOUND)
        if prop is None:
            return []
        try:
            raw = bytes(prop)
        except (ValueError, TypeError):
            # Zero-length or boolean property (e.g. 'no-map;')
            return []
        raw = raw.rstrip(b'\x00')
        if not raw:
            return []
        return [s for s in raw.decode('utf-8', errors='replace').split('\x00')
                if s]

    def _is_enabled(self, node_offset):
        """Return True if node has no status or status is 'okay'/'ok'."""
        strings = self._get_strings(node_offset, 'status')
        if not strings:
            return True
        return strings[0] in ('okay', 'ok')

    def _node_compatibles(self, node_offset):
        """Return set of compatible strings for a node."""
        return set(self._get_strings(node_offset, 'compatible'))

    def _iter_all_nodes(self, root_offset):
        """Yield all descendant node offsets via depth-first walk."""
        libfdt = self._libfdt
        child = self._fdt.first_subnode(root_offset, libfdt.QUIET_NOTFOUND)
        while child >= 0:
            yield child
            yield from self._iter_all_nodes(child)
            child = self._fdt.next_subnode(child, libfdt.QUIET_NOTFOUND)

    def _phandle_targets(self, node_offset):
        """
        Yield node offsets referenced by any phandle-valued property.

        Heuristic: treat every u32 word in every property as a potential
        phandle. Words that resolve to a valid node are yielded. Accepts
        a small false-positive rate (extra modules) in exchange for
        completeness.

        Skip known non-phandle properties to reduce false positives:
        reg, #address-cells, #size-cells, interrupts, ranges, bus-range.
        """
        libfdt = self._libfdt
        SKIP_PROPS = {
            'reg', '#address-cells', '#size-cells', 'interrupts',
            'ranges', 'bus-range', 'interrupt-map', 'interrupt-map-mask',
            'dma-ranges', 'phandle', 'linux,phandle',
        }

        prop_off = self._fdt.first_property_offset(
            node_offset, libfdt.QUIET_NOTFOUND)
        while prop_off >= 0:
            prop = self._fdt.get_property_by_offset(prop_off)
            if prop.name not in SKIP_PROPS:
                data = bytes(prop)
                for i in range(0, len(data) - 3, 4):
                    word = _fdt32(data, i)
                    if word == 0:
                        continue
                    try:
                        target = self._fdt.node_offset_by_phandle(
                            word, self._libfdt.QUIET_NOTFOUND)
                    except self._libfdt.FdtException:
                        continue
                    if target >= 0:
                        yield target
            prop_off = self._fdt.next_property_offset(
                prop_off, libfdt.QUIET_NOTFOUND)

    def get_compatibles(self, boot_nodes=None):
        """
        Return sorted list of unique compatible strings.

        Args:
            boot_nodes: list of absolute DT node paths, or None.
                        None → scan all enabled nodes (safe, maximal).
                        List → BFS from named nodes via all phandle refs.
        """
        if boot_nodes is None:
            return self._scan_all_enabled()
        return self._scan_with_phandle_bfs(boot_nodes)

    def _scan_all_enabled(self):
        """Collect compatibles from all enabled nodes."""
        result = set()
        root = self._fdt.path_offset('/')
        for offset in self._iter_all_nodes(root):
            if self._is_enabled(offset):
                result.update(self._node_compatibles(offset))
        log.debug("%s: all-nodes scan → %d compatibles",
                  self._dtb_path, len(result))
        return sorted(result)

    def _resolve_node_path(self, name):
        """
        Resolve a node name to an absolute DT path.

        Accepts:
          - Absolute paths:    /bus@f0000/ethernet@8000000
          - DT alias names:    ethernet0, mmc0, serial0, ...
            Entries in /aliases node of the compiled DTB.
          - DTS label names:   mcu_cpsw, cpsw3g, sdhci0, ...
            Entries in /__symbols__ node (present in DTBs compiled with
            CONFIG_OF_ALL_DTBS or -@ dtc flag). Maps DTS label names to
            full node paths — the canonical way to reference nodes when
            no /aliases entry exists.

        Returns (offset, is_alias) where is_alias=True when the name was
        resolved via /aliases or /__symbols__ (not an absolute path).
        Raises FdtException if not found.
        """
        libfdt = self._libfdt
        # Try as absolute path first
        if name.startswith('/'):
            return self._fdt.path_offset(name), False

        def _lookup_in(node_path):
            try:
                node_off = self._fdt.path_offset(node_path)
                prop = self._fdt.getprop(node_off, name,
                                         libfdt.QUIET_NOTFOUND)
                if prop is not None and not isinstance(prop, int) and len(prop) > 1:
                    path = bytes(prop).rstrip(b'\x00').decode('ascii', errors='ignore')
                    if path.startswith('/'):
                        return self._fdt.path_offset(path), True
            except (libfdt.FdtException, ValueError, UnicodeDecodeError):
                pass
            return None

        # 1. Try /aliases  (standard DT alias: ethernet0, mmc0 ...)
        result = _lookup_in('/aliases')
        if result:
            return result

        # 2. Try /__symbols__  (DTS label names: mcu_cpsw, cpsw3g ...)
        result = _lookup_in('/__symbols__')
        if result:
            log.debug("Resolved '%s' via /__symbols__", name)
            return result

        raise libfdt.FdtException(-libfdt.FDT_ERR_NOTFOUND)

    # Maximum phandle-follow depth from each boot node.
    # Depth 0 = the boot node itself.
    # Depth 1 = nodes directly referenced by the boot node (PHY, MDIO).
    # Depth 2 = nodes referenced by those (PHY sub-nodes, etc.).
    # Deeper traversal risks pulling in shared infrastructure nodes
    # (clock/power/interrupt controllers) that are reachable from every
    # peripheral and would make all workflows converge to the same set.
    _PHANDLE_MAX_DEPTH = 3

    def _scan_with_phandle_bfs(self, boot_nodes):
        """BFS from named nodes following all phandle references."""
        libfdt = self._libfdt
        visited = set()
        # Queue entries are (offset, depth)
        queue = []
        result = set()

        for path in boot_nodes:
            try:
                offset, is_alias = self._resolve_node_path(path)
                queue.append((offset, 0))
                # For DT aliases that resolve to a child node with NO
                # compatible of its own (e.g. ethernet0 → ethernet-ports/port@1
                # which has no compatible), walk up to find the first ancestor
                # WITH a compatible — that's the controller node.
                # Stop immediately after finding one; do NOT continue to root
                # as that would enqueue bus nodes and pull in the whole SoC.
                # Skip ancestor walk when the resolved node already has a
                # compatible (e.g. mmc0 → mmc@fa10000 has ti,am62-sdhci).
                if is_alias and not self._node_compatibles(offset):
                    ancestor = self._fdt.parent_offset(
                        offset, libfdt.QUIET_NOTFOUND)
                    while ancestor > 0:
                        if self._node_compatibles(ancestor):
                            if ancestor not in visited:
                                queue.append((ancestor, 0))
                            break   # stop at first ancestor with compatible
                        ancestor = self._fdt.parent_offset(
                            ancestor, libfdt.QUIET_NOTFOUND)
            except libfdt.FdtException:
                log.warning("Node not found in %s: %s", self._dtb_path, path)

        while queue:
            offset, depth = queue.pop(0)
            if offset in visited:
                continue
            visited.add(offset)

            result.update(self._node_compatibles(offset))

            if depth < self._PHANDLE_MAX_DEPTH:
                for target in self._phandle_targets(offset):
                    if target not in visited:
                        queue.append((target, depth + 1))

                # Also enqueue direct DT children (depth + 1).
                # This catches sub-buses (e.g. MDIO inside CPSW) whose
                # drivers are NOT reached via phandle references.
                child = self._fdt.first_subnode(offset, self._libfdt.QUIET_NOTFOUND)
                while child >= 0:
                    if child not in visited:
                        queue.append((child, depth + 1))
                    child = self._fdt.next_subnode(child, self._libfdt.QUIET_NOTFOUND)

        log.debug("%s: BFS from %d nodes → %d compatibles, %d nodes visited",
                  self._dtb_path, len(boot_nodes), len(result), len(visited))
        return sorted(result)


def get_compatibles(dtb_path, boot_nodes=None):
    """Convenience wrapper around DtbAnalyzer."""
    return DtbAnalyzer(dtb_path).get_compatibles(boot_nodes)
