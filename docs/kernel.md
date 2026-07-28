# Building a mainline kernel for this board

**Status: builds.** A v7.2-rc5 kernel with SoC clock support and a board device
tree for this box compiles cleanly. It has **not been booted yet** — see
[Untested](#untested) below.

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

## Untested

The kernel has not run on hardware yet. When it does, the sequence is the
AArch64 chain from [aarch64-bringup.md](aarch64-bringup.md) followed by a
`booti` from the mainline U-Boot prompt, with load addresses clear of the
`0x02000000` staging area:

```
tftp 0x10000000 Image
tftp 0x0f000000 tvbox.dtb
booti 0x10000000 - 0x0f000000
```

Things worth expecting to go wrong, in order of likelihood:

* No console output at all past `Starting kernel`, because `earlycon` is not on
  the command line. Add `earlycon=pl011,0x8b00000` before blaming the port.
* The CRG driver's register offsets were derived for the MV200. This SoC is the
  **MV300**; the vendor device tree calls the family `hi3798mv200-series`, which
  is the reason for treating them as compatible, but that is an assumption this
  boot will be the first real test of.
* FEMAC has no upstream mv200 support, so `b4/net` is still needed for
  networking. It was left out here to keep the first boot minimal.
