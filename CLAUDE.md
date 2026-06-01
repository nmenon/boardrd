# boardrd — Claude Code Context

## What this project does

boardrd builds a minimal initramfs (initrd) for embedded Linux boards.
Given a set of DTBs and boot mode workflows, it:
1. Analyzes DTBs to find required kernel modules (via pylibfdt + modules.alias)
2. Adds generic stack modules per workflow (mmc_block, nfs, usb_storage, etc.)
3. Resolves transitive dependencies via modules.dep
4. Builds busybox (static, minimal applets only) from the git submodule
5. Packs everything into a compressed cpio initramfs

The `/init` script does two-pass module loading at runtime:
- Pass 1: pre-built list from /etc/initrd-modules.conf
- Pass 2: modalias scan of /sys/bus/*/devices/*/modalias (catches overlay
  changes and enumerable bus devices missed by static DTB analysis)

## Key design decisions

- `modules.alias` (not `dt_to_config`) is used for module lookup — it reflects
  runtime autoloading, not just Kconfig symbols
- DTB analysis uses pylibfdt BFS following phandle references AND direct DT
  child nodes (needed for sub-buses like MDIO inside CPSW that aren't phandle
  targets)
- BFS depth is capped at 3 to prevent converging on shared infrastructure nodes
  (clock/power controllers) that would pull in the whole SoC
- PHY drivers (MDIO ID based, not OF compatible) are bundled as NFS workflow
  anchors since static DTB analysis cannot determine which one is needed
- Boot mode is inferred from `root=` cmdline; `bootmode=` overrides
- One initrd per invocation (the boards + workflows you pass in)
- Workflow YAMLs list anchors only; deps resolved by modules.dep at build time
- settle_ms per workflow controls Pass 1→Pass 2 delay for bus enumeration
- System.map must be passed to depmod so it knows which symbols are built-in;
  without it, modules with built-in kernel symbol dependencies fail to load

## Directory layout

```
boardrd.py          CLI entry point — run this
lib/
  dt_analyzer.py    DTB → compatible strings (pylibfdt)
  mod_resolver.py   modules.alias + modules.dep resolution
  workflow.py       workflow YAML loader + generator
  initrd_builder.py staging tree → cpio
  paths.py          data file path resolution (source + installed)
workflows/          Boot mode module recipes (anchors + settle_ms)
boards/             Board configs (arch/vendor/board.yaml)
templates/          init.sh.tmpl — the PID-1 init script template
busybox/            busybox source (git submodule, pinned tag)
busybox.config      Minimal busybox applet config
tests/              pytest test suite
```

## Board config format

Workflows are a list of dicts with `name:` and optional `boot_nodes:`:

```yaml
name: am62x-sk
dtbs:
  - arch/arm64/boot/dts/ti/k3-am625-sk.dtb
workflows:
  - name: mmc
    boot_nodes: [mmc0, mmc1]   # DT aliases from /aliases node
  - name: nfs
    boot_nodes: [ethernet0]    # ancestor walk finds the controller
```

`boot_nodes` accepts absolute DT paths or DT alias names (from /aliases in the
compiled DTB — NOT the same as DTS label names like `cpsw3g`).

## initrd layout

Busybox binary lives at `/bin/busybox`. Bin applets symlink to `busybox`
(same dir). Sbin applets (`modprobe`, `ip`, `switch_root`) symlink to
`../bin/busybox`. The udhcpc default script is at
`/usr/share/udhcpc/default.script`.

```
/bin/busybox        (binary)
/bin/<applet>  -> busybox
/sbin/<applet> -> ../bin/busybox
/usr/share/udhcpc/default.script  (applies DHCP lease: ip addr + ip route)
```

## busybox build

Uses `make oldconfig` starting from `busybox.config` (not `allnoconfig +
KCONFIG_ALLCONFIG` — that silently drops applets with unmet deps). The `yes ""`
pipe answers any new-option prompts with defaults.

`CONFIG_FEATURE_MOUNT_NFS` is disabled — it requires libtirpc headers not
present in cross-compile sysroots. The kernel NFS client handles NFS mounts
directly via `mount -t nfs`.

## Dependencies

- Python 3.8+
- `pylibfdt` — DTB parsing (`pip install pylibfdt`)
- `pyyaml` — YAML parsing (`pip install pyyaml`)
- `kmod` — `depmod` on PATH (standard on any Linux build host)
- `cpio` — archive packing
- Cross-compilation toolchain — for busybox (same prefix as kernel build)

## Common commands

```sh
# Build initrd — busybox already built, reuse it
boardrd --config boards/arm64/ti/am625-beagleplay.yaml \
        --kernel-dir /path/to/linux \
        --modules-dir /path/to/lib/modules/6.x.y \
        --system-map /path/to/linux/System.map \
        --busybox /tmp/busybox-build/busybox \
        --output beagleplay-nfs.cpio.gz

# Build initrd — build busybox from source (first time)
boardrd --config boards/arm64/ti/am625-beagleplay.yaml \
        --kernel-dir /path/to/linux \
        --modules-dir /path/to/lib/modules/6.x.y \
        --system-map /path/to/linux/System.map \
        --cross-compile aarch64-linux-gnu- --arch arm64 \
        --busybox-build-dir /tmp/busybox-build \
        --output beagleplay-nfs.cpio.gz

# Dry run — list modules without building
boardrd --config boards/arm64/ti/am62x-sk.yaml \
        --modules-dir /path/to/lib/modules/6.x.y \
        --list-modules

# Auto-generate workflow YAML suggestions for a kernel build
boardrd --generate-workflows \
        --dtb arch/arm64/boot/dts/ti/k3-am625-sk.dtb \
        --workflow mmc \
        --modules-dir /path/to/lib/modules/6.x.y
```

## Adding a new board

1. Create `boards/<arch>/<vendor>/<board>.yaml`
2. Set `dtbs:` to one or more DTB paths (relative to kernel tree root)
3. Add `workflows:` as list of dicts: `{name: nfs, boot_nodes: [ethernet0]}`
4. Omit `boot_nodes` to analyze all enabled DT nodes (safe, maximal)

## Adding a new workflow

1. Create `workflows/<name>.yaml` with `name:`, `settle_ms:`, `anchors:`
2. Anchors are bare module names (e.g. `mmc_block`) — no transitive deps
3. Or run `--generate-workflows` to get suggestions from a real kernel build

## Debugging init failures

- Uncomment `# set -x` near the top of `templates/init.sh.tmpl` and rebuild
  to get verbose execution tracing on the serial console
- `lsmod`, `dmesg`, `ip link show`, `ip addr show` are all available in the
  initrd shell (drop to shell via `die()` or after NFS mount failure)
- Check `/etc/initrd-modules.conf` in the initrd to see the module list

## Running tests

```sh
pytest tests/
```

Tests use pytest `tmp_path` fixture — no temp dirs leak after a run.

## Pyright warnings

The `reportMissingImports` warnings on `lib.*` imports in Pyright are expected
false positives — boardrd is a standalone script project, not an installed
package in Pyright's search path. Runtime imports work correctly.

## Commit format

```
Assisted-by: Claude:claude-sonnet-4-6
Signed-off-by: Nishanth Menon <nm@ti.com>
```
