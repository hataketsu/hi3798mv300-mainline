# Building a mainline kernel for this board

**Status: boots.** A v7.2-rc5 kernel with SoC clock support and a board device
tree for this box reaches userspace hand-off on real hardware. All four cores
come up through PSCI, the console hands over from `earlycon` to the real PL011
driver, and cpufreq reads the true CPU frequency off the CRG clock driver. It
stops at `Unable to mount root fs` because there is no rootfs yet — see
[First boot](#first-boot).

## What mainline already has

More than expected. As of v7.2-rc5:

| Piece | State |
|---|---|
| `drivers/mmc/host/dw_mmc-hi3798mv200.c` | **upstream** |
| `Documentation/devicetree/bindings/usb/hisilicon,hi3798mv200-dwc3.yaml` | **upstream** |
| `drivers/net/ethernet/hisilicon/hisi_femac.c` | upstream, but knows nothing about mv200 |
| CRG clock driver | **missing** — only `crg-hi3798cv200.c` exists |
| `hi3798mv200.dtsi` | **missing** |
| pinctrl | **missing** — `drivers/pinctrl/` has no HiSilicon STB driver at all |

The clock driver is the blocker. Nothing else can probe without it: the eMMC,
FEMAC and USB nodes all take their clocks from `&crg` and `&sysctrl`.

## Where the out-of-tree work lives

Yang Xiwen (`forbidden405@outlook.com`) has carried this SoC for years at
[github.com/185264646/linux](https://github.com/185264646/linux). The branches
that matter:

* `b4/clk-mv200` — PLL support and the Hi3798MV200 CRG driver
* `b4/pinctrl` — a HiSilicon pinctrl framework
* `b4/net` — FEMAC support for this core
* `b4/mmc-hi3798mv200` — now upstream, no longer needed
* `wip/trunk` — everything merged together, plus `hi3798mv200.dtsi` and an
  HC2910 board DTS

All of it is based on **v6.8-rc4**.

## Rebasing onto v7.2-rc5

That is a 2.5-year gap, and it turns out to matter far less than it sounds. Only
the clock patches and the device tree are needed to reach a console.

A shallow clone cannot rebase across that gap — there is no common ancestor — so
apply the patches as files instead:

```sh
git clone --bare --filter=blob:none --no-checkout \
    https://github.com/185264646/linux.git yx
cd yx
git format-patch -o /tmp/clk 841c35169323..b4/clk-mv200      # 841c351 = v6.8-rc4
git format-patch -o /tmp/dts -1 02ab914d9bc3   # vendor-prefixes: skyworth
git format-patch -o /tmp/dts -1 c5222de48a97   # bindings: hi3798mv200
git format-patch -o /tmp/dts -1 0e9c96bff925   # dtsi + HC2910 board dts
git format-patch -o /tmp/dts -1 19ee07c32ed3   # fixup for the above

cd ../linux                                    # at v7.2-rc5
git checkout -b histb
git am -3 --empty=drop /tmp/clk/00{28,29,30,31,32,33,34,35,36}-*.patch
git am -3 --empty=drop /tmp/dts/*.patch
```

`--empty=drop` is needed because b4 leaves an empty cover-letter commit in the
series (`clk: hisilicon: add support for Hi3798MV200`), which `git am` otherwise
stops on with `No valid patches in input`.

`git format-patch` on the `b4/clk-mv200` range also emits the v6.8-rc4..rc5
upstream drift as patches 0001–0027; only 0028 onwards are the clock work.

### Three things break

**`vendor-prefixes.yaml` conflicts.** Upstream added `smartfiber` at the same
spot. Keep both, in alphabetical order — `skyworth` first.

**`platform_driver.remove` changed signature.** It returns `void` since v6.11:

```
crg-hi3798mv200.c:471:27: error: initialization of 'void (*)(struct platform_device *)'
from incompatible pointer type 'int (*)(struct platform_device *)'
[-Werror=incompatible-pointer-types]
```

Fixed in [`patches/kernel/0001-*.patch`](../patches/kernel/).

**A dangling DTB entry breaks `make dtbs`.** The DTS patch adds two Makefile
lines but only one `.dts` file — `hi3798mv200-hc2910-2aghd07.dtb` has no source.
Every `dtbs` build stops there, so `hi6220-hikey` and the `hip05/06/07` boards
silently stop being built too. Fixed in
[`patches/kernel/0002-*.patch`](../patches/kernel/).

Nothing else needed touching. The CRG driver itself, the PLL code and the 704-line
dtsi all applied and compiled unchanged.

## Board device tree

[`../dts/hi3798mv300-tvbox.dts`](../dts/hi3798mv300-tvbox.dts). Copy it to
`arch/arm64/boot/dts/hisilicon/` and add a `dtb-$(CONFIG_ARCH_HISI)` line for it.

It exists because the HC2910 DTS is wrong for this box in three ways:

**Memory size.** The HC2910 declares `0x80000000`. This board has 1 GiB. The
stock bootloader's `getinfo ddrfree` reports free DDR as
`0x1000000..0x40000000`, and TF-A is built `POPLAR_DRAM_SIZE=one_gig` to match.

**PHY address.** The dtsi puts the FE PHY at MDIO address 1. On this board it is
at **address 2** — the stock environment says `phy_addr=2,1` and the stock
bootloader's own probe prints `Eth up port phy at 0x02 is connect`. The board
DTS deletes `ethernet-phy@1` and adds `ethernet-phy@2`.

**BL31 is not reserved where this port puts it.** The dtsi reserves
`sml@c00000`, which describes TF-A's *default* layout. This port moved
`LLOADER_TEXT_BASE` to `0x02000000`, because `0x00C00000` is where the stock
bootloader is linked (see [aarch64-bringup.md](aarch64-bringup.md)). BL31 stays
resident after hand-off, and its address follows from that base:

```
LLOADER_TEXT_BASE 0x02000000
BL1_BASE          0x0200f000   <- matches "CPU0 executes at 0x0200f000" in the boot log
BL2_BASE          0x0201f000   (+ BL1_SIZE  0x10000)
BL31_BASE         0x0202c000   (+ BL2_SIZE  0x0d000)
BL31_LIMIT        0x0203f000   (+ BL31_SIZE 0x13000)
```

The board DTS reserves `0x02000000 + 256 KiB`, covering the whole staging area
in one aligned block. Without this the page allocator will eventually hand BL31's
memory to something else, and every PSCI call after that is a lottery.

USB is deliberately left `disabled` for the first boot. None of it is needed to
reach a console, and a PHY that fails to come up can hang the probe before
there is anything to read.

## Build

Per [CLAUDE.md](../README.md) this project builds on a remote machine, never on
the laptop.

```sh
make ARCH=arm64 defconfig
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- Image dtbs -j"$(nproc)"
```

`CONFIG_COMMON_CLK_HI3798MV200=y` comes in automatically — the Kconfig entry is
`default ARCH_HISI`. Result: `arch/arm64/boot/Image` (~50 MB, uncompressed
defconfig) and `hi3798mv300-tvbox.dtb` (~14 KB).

## Two options defconfig does not set

`arm64 defconfig` leaves both of these off, and neither is reachable through
`ARCH_HISI`:

```sh
./scripts/config --enable CONFIG_MMC_DW_HI3798MV200 --enable CONFIG_HISI_FEMAC
make ARCH=arm64 olddefconfig
```

Without the first there is no eMMC and no `mmcblk0`, so no rootfs. Without the
second there is no Ethernet. Confirm they linked rather than trusting `.config`:

```sh
grep -c dw_mci_hi3798mv200 System.map    # nonzero
grep -c hisi_femac System.map            # nonzero
```

`CONFIG_HISI_FEMAC` only builds the upstream driver, which knows nothing about
the mv200 clock/reset layout. `b4/net` is still needed for working Ethernet.

## `earlycon` needs the CPU address, not the DT address

The single thing that cost the most time here. `hi3798mv200.dtsi` declares the
console as `serial@8b00000` with `reg = <0x8b00000 0x1000>`, so
`earlycon=pl011,0x8b00000` looks obviously right. It is wrong, and it hangs the
kernel with **no output whatsoever** past `Starting kernel ...`.

The `soc` node translates:

```dts
soc {
    ranges = <0x0 0x0 0xf0000000 0x10000000>;
};
```

Bus address `0x0` is CPU address `0xf0000000`, so `serial@8b00000` really lives
at **`0xf8b00000`** — the address the vendor device tree uses directly.
Addresses in `reg` are bus addresses; `earlycon` takes a CPU address and does no
translation, because it runs before the DT is walked.

An unmapped `earlycon` address is not a soft failure. The kernel writes to it
inside `parse_early_param`, long before the console or any fault handling
exists, so it dies silently and looks exactly like a kernel that never started.

Check an address from the U-Boot prompt before booting — this prints `A` on the
console if it is the UART data register:

```
mw.l 0xf8b00000 0x41
```

## First boot

The sequence is the AArch64 chain from
[aarch64-bringup.md](aarch64-bringup.md), then `booti` from the mainline U-Boot
prompt. Load addresses stay clear of the `0x02000000` staging area, and
everything is fetched over TFTP by the *stock* bootloader before `go`, because
mainline U-Boot on this board has neither eMMC nor Ethernet:

```
tftp 0x10000000 Image
tftp 0x0f000000 tvbox.dtb
tftp 0x02000000 l-loader.bin
go 0x0203F000
```

```
setenv bootargs "earlycon=pl011,mmio32,0xf8b00000 console=ttyAMA0,115200n8 nokaslr ignore_loglevel"
booti 0x10000000 - 0x0f000000
```

DRAM survives the AArch32→AArch64 warm reset, so the kernel and DTB loaded
before `go` are still in place afterwards.

What the first boot proved:

```
Machine model: Hi3798MV300 TV box
earlycon: pl11 at MMIO32 0x00000000f8b00000
OF: reserved mem: 0x02000000..0x0203ffff nomap non-reusable bl31@2000000
psci: PSCIv1.1 detected in firmware
smp: Brought up 1 node, 4 CPUs
arch_timer: cp15 timer running at 24.00MHz (phys)
f8b00000.serial: ttyAMA0 at MMIO 0xf8b00000 (irq = 14) is a PL011 rev2
printk: console [ttyAMA0] enabled
cpufreq: CPU0: Running at unlisted initial frequency: 798000 kHz
clk: Disabling unused clocks
```

The `cpufreq` line is the one that matters most. Reading a real 798 MHz off the
hardware means the **MV200 CRG driver works unmodified on the MV300** — the
central assumption of this port, and the thing there was no way to test without
booting. All four cores answering PSCI also confirms TF-A's hand-off and that
the `bl31@2000000` reservation is correct; the page allocator never touched
BL31's memory.

It ends in `Kernel panic - not syncing: VFS: Unable to mount root fs on
unknown-block(0,0)`, which is just the missing rootfs.

## Reaching userspace

There is no working storage or network driver yet — see [What still does not
probe](#what-still-does-not-probe) — so the rootfs has to travel inside the
kernel. An embedded initramfs needs no block device, no PHY and no bootloader
argument beyond the console:

```sh
curl -O https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/aarch64/alpine-minirootfs-3.24.1-aarch64.tar.gz
mkdir initramfs && tar xzf alpine-minirootfs-*.tar.gz -C initramfs
ln -sf /bin/busybox initramfs/init

cd linux
./scripts/config --set-str CONFIG_INITRAMFS_SOURCE "$PWD/../initramfs" \
                 --set-val CONFIG_INITRAMFS_ROOT_UID 0 \
                 --set-val CONFIG_INITRAMFS_ROOT_GID 0
make ARCH=arm64 olddefconfig
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- Image dtbs -j"$(nproc)"
```

`Image` grows from 50.9 MB to 54.9 MB. Give the rootfs an `/etc/inittab` that
skips getty entirely — a login prompt is one more thing to get wrong this early:

```
::sysinit:/bin/mount -t proc proc /proc
::sysinit:/bin/mount -t sysfs sysfs /sys
::sysinit:/bin/mount -t devtmpfs devtmpfs /dev
ttyAMA0::respawn:/bin/sh -l
```

Boot it with no `root=` at all. The kernel unpacks the initramfs and runs
`/init` directly:

```
setenv bootargs "earlycon=pl011,mmio32,0xf8b00000 console=ttyAMA0,115200n8 nokaslr"
booti 0x10000000 - 0x0f000000
```

This lands on a shell. From there `cpufreq` reports the CPU running at 1.2 GHz
under load, up from the 798 MHz it booted at, so the CRG driver is not just
reading rates but setting them.

### Loading from a USB stick instead of TFTP

The *vendor* bootloader has working USB, even though the mainline kernel does
not. That removes the dependency on Ethernet, which is worth doing because a
50 MB TFTP transfer over a marginal cable fails halfway and wastes a cycle:

```
usb start
fatload usb 0:1 0x10000000 Image
fatload usb 0:1 0x0f000000 tvbox.dtb
fatload usb 0:1 0x02000000 l-loader.bin
go 0x0203F000
```

`usb start` finds nothing on the first call after a cold boot and finds the
stick on the second — run it twice.

## What still does not probe

Mount debugfs and read the kernel's own record instead of guessing. Deferred
probe is silent, so a driver that never runs looks identical to one that is not
compiled in:

```sh
mount -t debugfs none /sys/kernel/debug
cat /sys/kernel/debug/devices_deferred
```

```
f8b20000.gpio ... f8b29000.gpio
f8a2c000.watchdog       sp805-wdt: Can not get reset
f9820000.mmc            dwmmc_hi3798mv200: parse dt failed
f9830000.mmc            dwmmc_hi3798mv200: parse dt failed
```

Both MMC controllers stop in `dw_mci_parse_dt`, at
`devm_reset_control_get_optional_exclusive(dev, "reset")`. The watchdog names
the same cause outright. The GPIO banks defer separately, on the
`gpio-ranges = <&ioconfig ...>` in the dtsi that needs a pinctrl driver.

Dropping the `pinctrl-0` / `pinctrl-names` properties from the MMC nodes was
necessary but not sufficient: with them, the controllers never reached the
driver at all; without them they reach it and stop on the reset lookup instead.

## The reset controller never registers

All of the above traces back to one line in `drivers/clk/hisilicon/reset.c`.

The obvious suspects were all wrong, and worth listing because each looked
convincing. The CRG *is* bound — `/sys/bus/platform/drivers/hi3798mv200-crg/`
holds `f8a22000.clock-reset-controller`. Its MMIO region *is* claimed, per
`/proc/iomem`. Its clocks *are* registered, and `clk_summary` shows the whole
tree with real rates. So `hisi_reset_init()` ran to completion, which means
`reset_controller_register()` was called.

It was called, and it failed. `hisi_reset_init()` allocates with
`devm_kmalloc()` and sets exactly five fields of the embedded
`reset_controller_dev`. The rest keeps whatever was on the heap. Since the
reset core grew fwnode support it rejects that:

```c
	if ((rcdev->of_node && rcdev->fwnode) ||
	    (rcdev->of_xlate && rcdev->fwnode_xlate))
		return -EINVAL;
```

`rcdev->fwnode` is uninitialised, so registration fails whenever the garbage is
non-NULL — and the return value is discarded, so nothing is logged. The
controller is absent from `reset_controller_list`, every consumer naming it
resolves to `-EPROBE_DEFER`, and deferred probe is silent. A failure with no
message, caused by an error that was thrown away.

The dwc3 oops is the same bug seen from the other side: `rcdev->dev` is
uninitialised too, and `__fwnode_reset_control_get` calls `get_device()` on it.
`a1ff001470cd984e` in the trace is heap garbage.

Fixed in
[`patches/kernel/0003-*.patch`](../patches/kernel/) — `devm_kzalloc()`, and
stop discarding the registration error. This is not board-specific; it affects
every HiSilicon platform that calls `hisi_reset_init()`.

With it applied:

```
sp805-wdt f8a2c000.watchdog: registration successful
dwmmc_hi3798mv200 f9830000.mmc: DW MMC controller at irq 26,32 bit host data width,256 deep fifo
mmc0: new HS400 MMC card at address 0001
mmcblk0: mmc0:0001 M72808 7.13 GiB
mmcblk0boot0 / mmcblk0boot1 / mmcblk0rpmb
```

eMMC runs at HS400/150 MHz. `devices_deferred` drops to the GPIO banks alone.

## The eMMC has no partition table

`/proc/partitions` initially showed `mmcblk0` and the two boot areas and
nothing else, which reads like a partition table the kernel failed to parse.
There is no partition table. The vendor describes the layout entirely through
the kernel command line:

```
blkdevparts=mmcblk0:1M(fastboot),512K(bootargs),512K(bootargsbak),20M(recovery),2M(deviceinfo),8M(securestore),8M(baseparam),8M(pqparam),2M(dtbo),10M(logo),10M(logobak),20M(fastplay),20M(recoverybak),60M(boot),20M(misc),20M(trustedcore),1700M(system),1000M(cache),1000M(vendor),50M(private),-(userdata)
```

Reading that needs `CONFIG_PARTITION_ADVANCED=y` and
`CONFIG_CMDLINE_PARTITION=y`, neither of which defconfig sets. The string is in
[`extracted/uboot-env.txt`](../extracted/uboot-env.txt); order matters, since
the parser assigns partition numbers by position, and `-(userdata)` must stay
last because it means "the rest".

With both set, all 21 partitions appear and mount:

```
mmcblk0: p1(fastboot) p2(bootargs) p3(bootargsbak) p4(recovery) ... p21(userdata)
EXT4-fs (mmcblk0p19): mounted filesystem ro without journal
```

`p19` is `vendor`, and it lists `build.prop`, `app/`, `firmware/`,
`ueventd.rc` — the real stock filesystem, so the whole path from controller to
data is correct, not merely enumerating.

## Still open

**`Failed to set rate to 400000`**, repeatedly, from both controllers. The card
still enumerates because dw_mmc falls back to its own divider, but `clk_set_rate`
on the CIU clock is not doing what the driver asks.

**GPIO banks defer** on `gpio-ranges` pointing at a pinctrl node with no driver.

**Ethernet.** `hisi-femac` registers and `f9c30000.ethernet` exists, but no
`eth0` appears and it is not in the deferred list. The upstream driver has no
mv200 support; `b4/net` is the fix.

**USB** is still disabled here. The dwc3 oops should be gone now that the reset
struct is zeroed, but the inno PHY `reg` mismatch is a separate problem and has
not been retried.
