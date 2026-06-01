# boardrd

> **Work in progress.** Core NFS boot functionality works and has been validated
> on AM62x (BeaglePlay, AM625-SK) and J721E (BeagleBone AI-64). Board configs
> cover 25+ TI K3 boards. APIs and config formats are still evolving.
> Not yet recommended for production use.

Board-specific initrd builder for embedded Linux systems.

Given a set of device tree blobs (DTBs) and boot mode workflows, boardrd
builds a minimal initramfs containing exactly the kernel modules needed to
boot those boards — no more, no less.

## Why

Embedded Linux kernels grow over time. When the kernel image becomes too large
for the boot flow, drivers are moved to modules. The result: the kernel can no
longer mount root on its own, because the MMC, NFS, OSPI, or UFS driver it
needs is now a `.ko` file sitting on the very filesystem it cannot yet access.

An initrd breaks the cycle. boardrd automates building that initrd — one that
is tailored to your specific boards and boot modes rather than a generic
distribution initrd carrying megabytes of drivers you will never use.

## How it works

```
DTB files                    modules.alias + modules.dep
    │                                    │
    ▼                                    ▼
DT analysis              ┌──────── Module resolver ────────┐
(pylibfdt BFS +          │  match compatibles via fnmatch  │
 child node scan)        │  recursive dep expansion        │
    │                    └─────────────────────────────────┘
    └────────────────────────────────────┐
                                         ▼
workflow YAMLs ──── anchor modules ──── unified module set
(mmc/nfs/ospi/usb/ufs)                          │
                                                ▼
                                     initrd builder
                                busybox + modules + init
                                                │
                                                ▼
                                     initrd.cpio.gz
```

The `/init` script uses two-pass module loading at runtime:

- **Pass 1** — pre-built list from `/etc/initrd-modules.conf`: loads bus
  controllers and known stack modules.
- **Pass 2** — runtime modalias scan of `/sys/bus/*/devices/*/modalias`:
  catches devices enabled by bootloader DT fixups/overlays and devices
  behind enumerable buses (PCIe, USB) that are invisible to static DTB
  analysis.

Boot mode is inferred from the `root=` kernel parameter at runtime:

| `root=` value | Detected mode |
|---|---|
| `/dev/nfs` | NFS |
| `/dev/mmcblk*` | MMC |
| `ubi*`, `/dev/mtdblock*` | OSPI |
| `PARTUUID=`, `UUID=`, `LABEL=`, `/dev/sd*` | unknown → load all |

The explicit `bootmode=mmc|nfs|ospi|usb|ufs` parameter overrides inference.

## Requirements

### Python packages (`pip install -r requirements.txt`)

- Python 3.8+
- [`pylibfdt`](https://pypi.org/project/pylibfdt/) — DTB parsing
- [`pyyaml`](https://pypi.org/project/PyYAML/) — YAML config

### System packages (`requirements-system.txt`)

boardrd validates all required tools at startup and reports every
missing one before attempting the build.

**Always required:**
```sh
# Debian/Ubuntu
apt-get install cpio kmod make gzip

# Fedora/RHEL
dnf install cpio kmod make gzip
```

**LLVM builds** (`--llvm` or `--musl-root`):
```sh
apt-get install clang lld llvm
```

**GCC cross-compilation** (`--cross-compile aarch64-linux-gnu-`):
```sh
apt-get install gcc-aarch64-linux-gnu
```

**musl toolchain** (`--musl-root`, self-contained, download separately):
```sh
wget https://musl.cc/aarch64-linux-musl-cross.tgz
tar -xf aarch64-linux-musl-cross.tgz
```

See `requirements-system.txt` for the full annotated list with RPM equivalents.

## Installation

**Directly from GitHub (no clone needed):**

```sh
pip install "git+https://github.com/nmenon/boardrd.git@main"
```

**From a local clone:**

```sh
git clone https://github.com/nmenon/boardrd.git
cd boardrd
pip install .
```

**Editable install for development** (keeps `boardrd/` data files editable
in-place):

```sh
git clone https://github.com/nmenon/boardrd.git
cd boardrd
pip install -e .
```

## Quick start

```sh
# Dry run — list modules that would be bundled, no build required
boardrd --dtb arch/arm64/boot/dts/ti/k3-am625-beagleplay.dtb \
        --boot-nodes ethernet0 \
        --modules-dir /path/to/lib/modules/6.x.y \
        --workflow nfs \
        --list-modules
```

## BeaglePlay — NFS boot example

Full end-to-end example: build a kernel, install modules, build the initrd,
and boot the BeaglePlay over NFS from a Linux host at `192.168.0.1`.

### 1. Build the kernel and install modules

```sh
export ARCH=arm64
export CROSS_COMPILE=aarch64-none-linux-gnu-
export KDIR=$HOME/linux           # kernel source tree
export MDIR=/tmp/kern_modules     # modules staging area

make -C $KDIR defconfig
make -C $KDIR -j$(nproc)
make -C $KDIR modules_install INSTALL_MOD_PATH=$MDIR

KREL=$(ls $MDIR/lib/modules/)
```

### 2. Get busybox source (first time only)

```sh
# Official mirror — see https://busybox.net/source.html
git clone https://github.com/vda-linux/busybox_mirror.git /opt/busybox-src
```

### 3. Build the initrd

Three toolchain options — pick one:

**Option A: GCC cross-compiler** (standard, most common)
```sh
# First time: build busybox from source
boardrd --config boardrd/boards/arm64/ti/k3-am625-beagleplay.yaml \
        --kernel-dir $KDIR \
        --modules-dir $MDIR/lib/modules/$KREL \
        --system-map $KDIR/System.map \
        --cross-compile aarch64-none-linux-gnu- --arch arm64 \
        --busybox-src /opt/busybox-src \
        --busybox /tmp/boardrd-busybox \
        --output /tmp/beagleplay-nfs.cpio.gz
```

**Option B: LLVM/clang + musl libc** (fully self-contained, no GCC needed)
```sh
# Get the musl cross-compilation toolchain (one-time)
wget https://musl.cc/aarch64-linux-musl-cross.tgz
tar -xf aarch64-linux-musl-cross.tgz   # produces aarch64-linux-musl-cross/

# Build — --musl-root implies --llvm
boardrd --config boardrd/boards/arm64/ti/k3-am625-beagleplay.yaml \
        --kernel-dir $KDIR \
        --modules-dir $MDIR/lib/modules/$KREL \
        --system-map $KDIR/System.map \
        --busybox-src /opt/busybox-src \
        --musl-root aarch64-linux-musl-cross \
        --busybox /tmp/boardrd-busybox-musl \
        --output /tmp/beagleplay-nfs.cpio.gz
```

**Option C: Pre-built busybox** (fastest, skip the build entirely)
```sh
boardrd --config boardrd/boards/arm64/ti/k3-am625-beagleplay.yaml \
        --modules-dir $MDIR/lib/modules/$KREL \
        --system-map $KDIR/System.map \
        --busybox /path/to/prebuilt/aarch64-busybox \
        --output /tmp/beagleplay-nfs.cpio.gz
```

`--busybox PATH` is smart: if `PATH` is a directory, boardrd looks for
`PATH/busybox` and builds there if not found; if `PATH` is a file it is
used directly as the binary.

### 4. Copy files to TFTP server

```sh
TFTPROOT=/images

cp $KDIR/arch/arm64/boot/Image                              $TFTPROOT/Image
cp $KDIR/arch/arm64/boot/dts/ti/k3-am625-beagleplay.dtb    $TFTPROOT/k3-am625-beagleplay.dtb
cp /tmp/beagleplay-nfs.cpio.gz                              $TFTPROOT/beagleplay-nfs.cpio.gz
```

### 5. Configure NFS export on the host

```sh
# /etc/exports
# /OE/rootfs/beagleplay  192.168.0.0/24(rw,no_root_squash,no_subtree_check)
sudo exportfs -ra
```

### 6. Boot from U-Boot

```
setenv serverip 192.168.0.1
setenv autoload no
setenv bootargs 'console=ttyS2,115200n8 earlycon=ns16550a,mmio32,0x02800000 rootwait fsck.mode=skip ip=:::::eth0:dhcp root=/dev/nfs rw nfsroot=192.168.0.1:/OE/rootfs/beagleplay,nolock,v3,tcp,rsize=4096,wsize=4096'

dhcp
tftp ${loadaddr}   192.168.0.1:Image
tftp ${fdt_addr_r} 192.168.0.1:k3-am625-beagleplay.dtb
tftp ${rdaddr}     192.168.0.1:beagleplay-nfs.cpio.gz
setenv _initramfs ${rdaddr}:${filesize}

booti ${loadaddr} ${_initramfs} ${fdt_addr_r}
```

The boardrd init script will:
1. Load ethernet modules (`ti_am65_cpsw_nuss`, `dp83869`, etc.)
2. Wait for carrier (~4 s on BeaglePlay)
3. Run DHCP and obtain an IP address
4. Mount the NFS rootfs
5. `switch_root` to `/sbin/init`

## Boot kernel parameters

The init script reads these parameters from `/proc/cmdline`:

| Parameter | Description |
|---|---|
| `root=<dev>` | Root device. Drives boot mode detection. |
| `bootmode=<mode>` | Explicit boot mode override: `mmc`, `nfs`, `ospi`, `usb`, `ufs` |
| `nfsroot=<server>:<path>[,<opts>]` | NFS server and export path with mount options |
| `ip=<value>` | Network config. Short form (`dhcp`, `off`) or full `client:server:gw:mask:hostname:dev:autoconf` |
| `rootfstype=<fs>` | Filesystem type hint for mount |
| `rootwait` | Wait indefinitely for root device |
| `rootdelay=<sec>` | Timeout for root device (default 60s) |
| `rw` / `ro` | Mount root read-write or read-only |
| `init=<path>` | Real init on root filesystem (default `/sbin/init`) |

### Real-world examples

**MMC / eMMC (PARTUUID-based root):**
```
root=PARTUUID=ba1bd45f-7a63-4adf-aaec-9ebe621377a4 rootwait rootfstype=ext4
```

**NFS root with DHCP:**
```
root=/dev/nfs rw nfsroot=192.168.0.1:/exports/rootfs,nolock,v3,tcp,rsize=4096,wsize=4096 ip=:::::eth0:dhcp
```

**NFS root with static IP:**
```
root=/dev/nfs rw nfsroot=192.168.0.1:/exports/rootfs,nolock,v3,tcp ip=192.168.0.100::192.168.0.1:255.255.255.0::eth0:off
```

**UFS (PARTUUID-based root):**
```
root=PARTUUID=a6426803-02 rw rootfstype=ext4 rootwait
```

## Board configuration

Board configs live under `boardrd/boards/<arch>/<vendor>/<board>.yaml`.

Each workflow entry can specify `boot_nodes` to restrict DTB analysis to
nodes reachable from the boot device. Omit to scan all enabled nodes.

```yaml
name: k3-am625-sk
description: Texas Instruments AM62x Starter Kit

dtbs:
  - arch/arm64/boot/dts/ti/k3-am625-sk.dtb

workflows:
  - name: mmc
    boot_nodes:
      - mmc0    # DT alias → MMC controller
      - mmc1
  - name: nfs
    boot_nodes:
      - ethernet0   # DT alias → ethernet controller
```

`boot_nodes` accepts — resolved in this order:
- **Absolute DT paths** — `/bus@f0000/ethernet@8000000`
- **DT aliases** — `ethernet0`, `mmc0`, `serial0`
  (from the `/aliases` node in the compiled DTB)
- **DTS label names** — `mcu_cpsw`, `cpsw3g`, `sdhci0`
  (from the `/__symbols__` node, present in DTBs compiled with the
  dtc `-@` flag or `CONFIG_OF_ALL_DTBS`; the right way to reference
  nodes on SoCs that have no `/aliases` entry for a given device)

If a name is not found, a warning is logged and it is skipped — useful
for listing a generic alias (`ethernet0`) and a label fallback
(`mcu_cpsw`) together so the same config works across board variants.

When `boot_nodes` is omitted, all enabled nodes are analyzed.

**DTB paths** are relative to the kernel tree root (`--kernel-dir`).

### Pre-defined boards

All board configs live under `boardrd/boards/arm64/ti/`.

| Board | Config file | Workflows |
|---|---|---|
| BeaglePlay (AM625) | `am625-beagleplay` | nfs |
| PocketBeagle 2 (AM625) | `am62-pocketbeagle2` | nfs |
| BeagleBone AI-64 (J721E) | `j721e-beagleboneai64` | nfs |
| BeagleY-AI (AM67A) | `am67a-beagley-ai` | nfs |
| AM625 Starter Kit | `am625-sk` / `am6254atl-sk` | mmc, nfs |
| AM62A Starter Kit | `am62a-sk` | mmc, nfs |
| AM62-LP Starter Kit | `am62-lp-sk` | mmc, nfs |
| AM62P5 Starter Kit | `am62p5-sk` | mmc, nfs |
| AM62D2 EVM | `am62d2-evm` | mmc, nfs |
| AM642 Starter Kit | `am642-sk` | mmc, nfs |
| AM642 EVM / EVM-NAND | `am642-evm` | mmc, nfs |
| AM654 base-board / EVM / GP-EVM / IDK | `am654-*` | mmc, nfs |
| AM68 Starter Kit | `am68-sk` | mmc, nfs |
| AM69 Starter Kit | `am69-sk` | mmc, nfs |
| J721E EVM / SK / CPB | `j721e-*` | mmc, nfs |
| J721S2 EVM / CPB | `j721s2-*` | mmc, nfs |
| J722S EVM | `j722s-evm` | mmc, nfs |
| J7200 EVM / CPB | `j7200-*` | mmc, nfs, ospi |
| J742S2 EVM | `j742s2-evm` | mmc, nfs |
| J784S4 EVM | `j784s4-evm` | ufs, mmc, nfs |

#### NFS `boot_nodes` patterns

All NFS configs use a combination of these entries (missing ones warn and skip):

| SoC family | `boot_nodes` |
|---|---|
| AM62x, AM64x, AM67A, J722S | `ethernet0`, `cpsw_port1` |
| AM65x, J721E, J721S2, J7200 | `ethernet0`, `mcu_cpsw` |
| AM68x, AM69x, J742S2, J784S4 | `ethernet0`, `mcu_cpsw` |

### Adding a new board

1. Create `boardrd/boards/<arch>/<vendor>/<board>.yaml`
2. Set `dtbs:` to one or more DTB paths (relative to kernel tree root)
3. Add `workflows:` as a list of dicts with `name:` and optional `boot_nodes:`

## Workflow configuration

Workflows live under `boardrd/workflows/<name>.yaml`.

```yaml
name: mmc
settle_ms: 200
anchors:
  - mmc_block
  - vfat
  - ext4
```

**`settle_ms`** is the delay between Pass 1 and Pass 2 to allow bus
enumeration.

| Workflow | Default `settle_ms` | Rationale |
|---|---|---|
| mmc | 200 | MMC controllers probe quickly |
| nfs | 500 | PHY/MAC init; DHCP handled separately |
| ospi | 100 | SPI-NOR probe is synchronous |
| usb | 2000 | USB enumeration takes 1–2 s |
| ufs | 1000 | UFS link training ~500 ms |

### NFS workflow — PHY drivers

Ethernet PHY chips are identified at runtime via MDIO device ID, not DT
compatible strings. boardrd bundles all available PHY `.ko` files in the NFS
workflow so any board's PHY is covered without board-specific tuning.

### Pre-defined workflows

| Workflow | Boot device | Notes |
|---|---|---|
| mmc | eMMC / SD card | anchors: mmc_block, vfat, ext4 |
| nfs | NFS root filesystem | anchors: nfs, nfsv3 + all PHY drivers |
| ospi | OSPI/QSPI SPI-NOR flash | anchors: spi_nor, mtdblock, jffs2 |
| usb | USB mass storage | anchors: usb_storage, vfat, ext4 |
| ufs | UFS storage | anchors: scsi_mod, sd_mod, vfat, ext4 |

### Auto-generating workflow anchors

```sh
boardrd --generate-workflows \
        --dtb arch/arm64/boot/dts/ti/k3-am625-sk.dtb \
        --workflow mmc \
        --modules-dir /path/to/lib/modules/6.x.y
```

## CLI reference

```
boardrd [OPTIONS]

Options:
  --config FILE           YAML config file (CLI args override YAML values)
  --modules-dir DIR       Path to lib/modules/<KERNELRELEASE>/
                          Parent dir accepted; auto-detects single release.
  --kernel-dir DIR        Kernel source/build tree root; DTB paths resolved
                          relative to this directory
  --system-map FILE       System.map from kernel build — passed to depmod
                          to resolve built-in kernel symbol dependencies
  --dtb FILE              DTB file (repeatable; replaces YAML boards list)
  --boot-nodes PATH...    DT node path(s) for boot device (space-separated);
                          matches boot_nodes: in board YAML
  --workflow NAME         Boot workflow (repeatable; replaces YAML list)
  --workflow-dir DIR      Workflow YAML directory
  --output FILE           Output initrd filename (default: initrd.cpio.gz)
  --compression TYPE      gzip | xz | zstd | none  (default: gzip)
  --busybox PATH          Pre-built binary (FILE) OR build cache directory
                          (DIR). Looks for DIR/busybox; builds there if
                          not found (requires --busybox-src first time).
  --busybox-src DIR       busybox source tree
                          (see https://busybox.net/source.html)
  --musl-root DIR         musl cross-compilation toolchain root
                          (e.g. aarch64-linux-musl-cross/). Implies --llvm.
                          Self-contained: no GCC cross-compiler required.
                          Download: wget https://musl.cc/aarch64-linux-musl-cross.tgz
  --cross-compile PREFIX  GCC tool prefix (e.g. aarch64-linux-gnu-)
  --arch ARCH             Target architecture (e.g. arm64)
  --llvm                  Use clang + llvm-* tools (sysroot auto-detected
                          from GCC cross-compiler; use --musl-root instead
                          for a self-contained LLVM build)
  --generate-workflows    Generate workflow YAML suggestions from DTB analysis
  --list-modules          Print module list and exit (dry run, no build)
  -v, --verbose           Enable verbose logging
```

**CLI vs YAML priority:** CLI arguments override YAML config values.
`--dtb` and `--workflow` on the CLI *replace* (not append to) the YAML lists.

## Relation to Dracut

[Dracut](https://github.com/dracut-ng/dracut/) is the established general-purpose
initramfs framework used by Fedora, RHEL, openSUSE, Arch and others
(see [adoption](https://en.wikipedia.org/wiki/Dracut_(software)#Adoption)).
boardrd solves a related but narrower problem for embedded cross-compiled targets.

### Key differences (as of 2026-06)

| Aspect | Dracut | boardrd |
|---|---|---|
| **Target** | Distribution Linux (x86, ARM, …) | Embedded boards with specific boot modes |
| **Build host** | Same machine as target (self-hosted) | Cross-compiled on x86 for ARM target |
| **Module discovery** | Runtime `udev` hardware detection | Static DTB analysis + workflow anchors |
| **Init framework** | Full dracut module system (bash hooks) | Minimal single-purpose `/init` script |
| **Network stack** | NetworkManager / systemd-networkd | Minimal udhcpc + busybox `ip` |
| **Userspace** | glibc-linked tools from host distro | Single statically-linked busybox |
| **Size** | Tens of MB (general-purpose) | 2–5 MB (board + workflow specific) |
| **Boot modes** | Flexible (disk, NFS, iSCSI, …) | Explicit per-board workflow YAMLs |

### Why not just use Dracut?

- Dracut builds for the **running host** — no out-of-the-box cross-compilation support
- Dracut's hardware discovery requires the target hardware to be present at build time
- DTB-based module selection is specific to embedded SoC workflows not currently in Dracut
- The static busybox binary avoids shared library dependency issues in initrd on constrained targets

### Potential future integration with Dracut

boardrd is intentionally minimal. A natural evolution path is as a **standalone
Dracut module** rather than a fork or replacement.

Dracut already has
[`70devicetree-firmware`](https://github.com/dracut-ng/dracut/blob/main/modules.d/70devicetree-firmware/module-setup.sh)
which reads DT `firmware-name` properties to pull firmware files into the initrd.
The boardrd DTB analysis fits naturally alongside it as a complementary module —
e.g. `70devicetree-modules` or `90boardrd` — that reads `compatible` strings via
pylibfdt BFS and calls `instmods` to include the right `.ko` files.

The module would hook into Dracut's standard `install()` function:

```bash
# modules.d/70devicetree-modules/module-setup.sh (sketch)
install() {
    # Run boardrd DTB analysis and add discovered modules via instmods
    local mods
    mods=$(boardrd --dtb "$dtb" --list-modules --no-build)
    instmods $mods
}
```

Other future directions:

- **Cross-compilation** — boardrd's `--cross-compile` / `--arch` could inform a
  Dracut `--sysroot` cross-build mode for building initramfs on x86 for ARM targets
- **Workflow → Dracut config** — the `boot_nodes` + workflow YAML concept maps onto
  Dracut's `--add-drivers` and `--filesystems` flags, enabling per-boot-mode configs
- **Replace custom `/init`** — once drivers load correctly, Dracut's own network and
  NFS support (dracut-network module) could replace boardrd's minimal busybox init

## busybox

boardrd builds a minimal static busybox. The source is **not bundled** —
clone it once from the official mirror (see https://busybox.net/source.html):

```sh
git clone https://github.com/vda-linux/busybox_mirror.git /opt/busybox-src
```

The build uses `make oldconfig` from `boardrd/lib/busybox.config` to ensure
all Kconfig dependencies are satisfied. The compiled binary is cached in
the directory passed to `--busybox` for incremental rebuilds.

### Toolchain options

| Option | How | Notes |
|---|---|---|
| GCC | `--cross-compile aarch64-linux-gnu- --arch arm64` | Standard; requires GCC cross toolchain |
| LLVM + GCC sysroot | `--llvm --cross-compile aarch64-linux-gnu-` | Sysroot auto-detected via `gcc -print-sysroot` |
| LLVM + musl | `--musl-root aarch64-linux-musl-cross/` | Self-contained; no GCC needed; download from [musl.cc](https://musl.cc/) |

The musl toolchain is the recommended LLVM path — it bundles clang targets,
musl libc, and GCC CRT files (`crtbeginT.o`, `libgcc.a`) in one directory:

Applets included: `sh`, `bash`, `mount`, `umount`, `switch_root`, `findfs`,
`udhcpc`, `ip`, `modprobe`, `insmod`, `rmmod`, `lsmod`, `timeout`, `dmesg`,
`ps`, `free`, `ls`, `mkdir`, `mknod`, `echo`, `cat`, `grep`, `cut`, `sleep`.

## Debugging

Uncomment `# set -x` near the top of `boardrd/templates/init.sh.tmpl` and
rebuild to enable verbose execution tracing on the serial console.

## Running tests

```sh
pip install pytest
pytest tests/ -v
```

## License

GPL-2.0 — see individual file headers.

Copyright (C) 2026 Texas Instruments Incorporated - https://www.ti.com/
