# Booting over TFTP

The stock bootloader can pull a kernel over Ethernet and boot it out of RAM,
touching no flash at all. This is the iteration loop to use for everything up to
the point where a bootloader replacement becomes unavoidable.

**Verified working** on the reference board: stock kernel served from a laptop,
loaded over a direct Ethernet cable, booted to Android.

## Host side

The stock environment already contains usable values:

```
ipaddr=192.168.1.10
serverip=192.168.1.1
netmask=255.255.255.0
```

So if the host takes `192.168.1.1`, **nothing has to be set on the board at
all**. With a direct cable between host and box:

```sh
sudo ip addr flush dev <iface>
sudo ip addr add 192.168.1.1/24 dev <iface>
sudo ip link set <iface> up
```

Pick a subnet that does not collide with whatever else the host is on. If the
host's Wi-Fi is already using `192.168.1.0/24`, either move the direct link to
another range and `setenv ipaddr` / `setenv serverip` on the board to match, or
switch the Wi-Fi. `setenv` without `saveenv` only touches RAM, so it is safe.

Serve the files with any TFTP daemon; [`../scripts/tftpd.py`](../scripts/tftpd.py)
is a dependency-free read-only one:

```sh
sudo python3 scripts/tftpd.py <dir> --bind 192.168.1.1
```

## Preparing images

The stock kernel is inside the Android boot image, after the 16 KiB header:

```
ANDROID! header: kernel=10354304 B @0x3008000, ramdisk=0 B, page=16384
uImage: name="Linux-4.9.118_D9" size=10354240 load=0x02000000 entry=0x02000000
```

```sh
dd if=p14-boot.img of=uImage bs=16384 skip=1 count=632
```

The device tree is the raw FDT, which is what `scripts/extract-dtb.py` produces
from the `dtbo` partition.

## Board side

```
ping 192.168.1.1
tftp 0x1FFFFC0 uImage
tftp 0x3FFC000 board.dtb
bootm 0x1FFFFC0 - 0x3FFC000
```

Those load addresses are not arbitrary — they are exactly where the stock
`bootcmd` places the same data:

* `bootcmd` reads the Android boot image to `0x1FFBFC0`; the uImage inside it
  begins at +0x4000 (the page size), i.e. **`0x1FFFFC0`**.
* `bootcmd` reads the `dtbo` partition to `0x3FFBFC0` and passes `0x3FFC000` to
  `bootm`, i.e. +0x40 — precisely where the FDT starts inside the DTBO
  container.

Loading the bare uImage and the bare FDT to those two addresses reproduces the
known-good configuration.

`getinfo ddrfree` reports the free DDR window as `0x1000000` .. `0x40000000`, so
both addresses are comfortably inside usable memory.

## Expected output

```
Eth up port phy at 0x02 is connect
Hisilicon ETH net controler
MAC:   E8-BB-3E-xx-xx-xx
UP_PORT : phy status change : LINK=UP : DUPLEX=FULL : SPEED=100M
Using up device
host 192.168.1.1 is alive
```

Note what this reveals: the interface the bootloader calls `up` is the
**Fast Ethernet MAC with its PHY at address 2**, running at 100 Mbit — not the
gigabit `gmac`. This matches `phy_addr=2,1` in the environment and the Linux
side coming up on `f9c30000.hieth`. A board DTS for this hardware wants the
femac node, not the `gmac` node that the Skyworth HC2910 DTS enables.

## The catch: this only boots 32-bit kernels

The stock bootloader is a 32-bit ARM binary and hands control over in AArch32 —
the stock kernel is `armv7l` and is booted as a legacy uImage with ATAGS. Every
`bootm` from this prompt lands in 32-bit state.

Mainline targets **arm64** for this SoC: `ARCH_HI3798MV2X` in U-Boot selects
`ARM64`, and `hi3798cv200` lives in `arch/arm64` in the kernel. So a mainline
arm64 kernel **cannot** be tested through the loop above as-is.

There is also no secure monitor running. The stock `bootcmd`'s first branch
tries to `bootm` the `trustedcore` image and fails with `Wrong Image Format`,
and the kernel log contains no PSCI probe messages while still bringing up all
four cores — consistent with HiSilicon's own SMP bring-up rather than PSCI.
(This is read from the absence of log output, so treat it as strong indication
rather than proof.) Mainline arm64 U-Boot expects PSCI from TF-A, so TF-A has to
come from somewhere.

### A flash-free way around it

The `go` command exists in the stock shell, and l-loader is described by its
author as "a 32 bit executable that transitions the default processor state to
64 bit mode and boots the ARM Trusted Firmware". DDR is already initialised by
the time the stock prompt is reachable, so l-loader's DDR/PLL work is redundant
here — only its mode switch and its hand-off to TF-A are wanted.

That suggests loading a trimmed l-loader plus TF-A plus arm64 U-Boot into RAM
over TFTP and entering it with `go`, which would allow the entire mainline chain
to be exercised without writing a single byte to eMMC. This has **not** been
tried yet; it is the obvious next experiment, and it is worth attempting before
anything that risks the box.
