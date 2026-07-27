# Stock boot log

Captured over UART0 at 115200 8N1. Raw log:
[`../logs/stock-boot-uart.log`](../logs/stock-boot-uart.log) (two consecutive
boots, identical). The Ethernet MAC and a swap UUID are redacted; nothing else
is altered.

## What the bootloader prints

```
Fastboot 3.3.0 (psqiu@) (Aug 29 2025 - 16:20:37)

Fastboot:      Version 3.3.0
Build Date:    Aug 29 2025, 16:21:43
CPU:           Hi3798Mv300
Boot Media:    eMMC
DDR Size:      1GB
...
SDK Version: HiSTBAndroidV800R001C00SPC120_update_v2019061009
Net:   up, down, gmac0
```

"Fastboot" is HiSilicon's name for their U-Boot fork, not Android fastboot.

This settles the SoC question without any register poking: the bootloader itself
prints **Hi3798Mv300**. It also names the exact BSP release,
`HiSTBAndroidV800R001C00SPC120_update_v2019061009`, which is the tree to compare
against when porting drivers.

`Net: up, down, gmac0` means Ethernet is alive in the bootloader, so **TFTP is
available for testing kernels without writing anything to eMMC**.

## Autoboot can be interrupted

```
Press Ctrl+C to stop autoboot
```

Despite `bootdelay=0` in the environment, this fork offers a **Ctrl+C** hook
rather than a countdown. Pressing it lands on a `fastboot#` prompt. This is what
makes reading the OTP fuses — and therefore judging whether a custom bootloader
is even possible — feasible.

## eMMC as the bootloader sees it

```
MID:         0x70
Chip Size:   7296M Bytes (High Capacity)
Name:        "M72808"
Chip Type:   MMC        Version: 5.1
Speed:       100000000Hz  Mode: HS400
Voltage:     1.8V         Bus Width: 8bit
```

Tuning output before that (`scan edges:2 p2f:5 f2p:7`, `Tuning SampleClock. mix
set phase:[02/07] ele:[06/13]`) is HS400 sample-clock training — the same thing
`dw_mmc-hi3798mv200.c` has to do in mainline.

## Environment location

```
Boot Env on eMMC
    Env Offset:          0x00100000
    Env Size:            0x00010000
    Env Range:           0x00010000
```

1 MiB into the eMMC — exactly the start of the `bootargs` partition — and only
64 KiB of that 512 KiB partition is used.

## Decoding bootcmd

The stock `bootcmd` issues three `mmc read`s. Converting each block offset
(`× 512`) against the partition table:

| `mmc read` offset | byte offset | partition | outcome |
|---|---|---|---|
| `0x5F000` (389120 blk), 8 MiB | 190 MiB | `trustedcore` | `Wrong Image Format for bootm command` — **fails, skipped** |
| `0x37000` (225280 blk), 25 MiB | 110 MiB | `boot` | uImage `Linux-4.9.118_D9`, 9.9 MiB, load/entry `0x02000000` |
| `0x18000` (98304 blk), 30 KiB | 48 MiB | `dtbo` | FDT placed at `0x3ffc000` — 17 896 bytes, matches the blob extracted from `boot`/`recovery`/`dtbo` |

So the first branch is a failed attempt to `bootm` the TEE image; it always
fails and execution falls through to the real kernel. Not a fault.

The kernel is booted as a **legacy uImage plus FDT plus ATAGS**:

```
## Booting kernel from Legacy Image at 01ffffc0 ...
   Image Name:   Linux-4.9.118_D9
   Load Address: 02000000   Entry Point: 02000000
## Flattened Device Tree blob at 03ffc000
ATAGS [0x00000100 - 0x000005BC], 1212Bytes
```

## Memory layout

```
cma: Reserved 44 MiB at 0x39800000       <- the mmz media zone
cma: Reserved  4 MiB at 0x3d800000
hisi_iommu_ptable_addr: phy 0x1ec00000  size 0x400000
Memory: 916052K/1011712K available (49152K cma-reserved, 422696K highmem)
```

Bootloader-reserved region, printed twice (before and after loading the kernel):

```
Start Addr: 0x3DBFE000   Bound Addr: 0x8D92000   Free Addr: 0x3C7B6000
```

## Odds and ends

* `set_serialno: MAGICNUM not set` — the `deviceinfo` partition has no serial
  magic, which is why `ro.serialno` is the placeholder `0123456789`.
* `enter the gpio press revocery` (sic) — a GPIO/button recovery path exists.
  Worth identifying before any flashing experiment.
* `mac:e8:bb:3e:xx:xx:xx` — the bootloader reads the MAC out of `deviceinfo`,
  confirming the ASCII string at offset 0 of that partition.
* `Only HI3796MV2X chip support` — a feature gated to a different SoC. Harmless.
* `Found flash memory controller hifmc100. / no found nand device.` — the NAND
  controller exists but is unpopulated, consistent with the `nofmc` boot arg.

## Display

The bootloader log contains **no logo output** — none of the
`BOOT_GFX_DisplayLogoWithLayer` / `boot logo show!!!` messages that exist in the
binary appear on this boot. So it cannot be assumed that the bootloader leaves a
live framebuffer scanning out over HDMI, which is the premise of a
`simple-framebuffer` handoff. This needs checking from the `fastboot#` prompt
before planning around it.

On the kernel side there is exactly one HDMI line:

```
ERROR-HI_HDMI: DRV_HDMI_KeyLoad[4353]: Load hdcp key error!
```

HDCP keys fail to load — irrelevant to a mainline port, but it confirms the
display path is entirely inside the proprietary bigfish modules with nothing
described in the device tree.
