# Stock firmware

## Build metadata

| Property | Value |
|---|---|
| `ro.build.display.id` | `ATV-20250829-201YS300` |
| `ro.build.fingerprint` | `google/walley/eros-p1:10/D9PRO5G/eng.rom.20250829.161627:userdebug/test-keys` |
| `ro.build.version.sdk` | `28` (Android 9 Pie) |
| `ro.build.version.release` | `14` — **spoofed**, contradicted by the SDK level and the fingerprint |
| `ro.build.date` | Fri Aug 29 16:15:30 CST 2025 |
| Kernel | `4.9.118_D9 #3518 SMP PREEMPT Fri Aug 29 16:20:40 CST 2025 armv7l` |
| `ro.product.vendor.name` | `Jupiter` |
| `ro.product.board` | `exdroid` |
| `ro.hardware` / `androidboot.hardware` | `bigfish` |
| `ro.serialno` | `0123456789` — placeholder, not per-unit |

The build is `userdebug` signed with `test-keys`, SELinux is set to **permissive**
by the kernel command line, and `su` is present: `su 0 id` returns
`uid=0(root) ... context=u:r:su:s0`. No exploit is needed for root.

## Partition table

From `blkdevparts=` in the kernel command line, confirmed against
`/proc/partitions`:

| # | Name | Size | Notes |
|---|---|---|---|
| p1 | `fastboot` | 1 MiB | U-Boot, link base `0x00C00000` |
| p2 | `bootargs` | 512 KiB | U-Boot environment |
| p3 | `bootargsbak` | 512 KiB | byte-identical backup of p2 |
| p4 | `recovery` | 20 MiB | |
| p5 | `deviceinfo` | 2 MiB | eth0 MAC in ASCII at offset 0, then display base params |
| p6 | `securestore` | 8 MiB | |
| p7 | `baseparam` | 8 MiB | |
| p8 | `pqparam` | 8 MiB | picture-quality tables |
| p9 | `dtbo` | 2 MiB | Android DTBO image, magic `d7b7ab1e` |
| p10 | `logo` | 10 MiB | |
| p11 | `logobak` | 10 MiB | |
| p12 | `fastplay` | 20 MiB | |
| p13 | `recoverybak` | 20 MiB | |
| p14 | `boot` | 60 MiB | `ANDROID!` header, uImage at `0x4000`, FDT appended at `0x9df898` |
| p15 | `misc` | 20 MiB | |
| p16 | `trustedcore` | 20 MiB | ARM Trusted Firmware / TEE |
| p17 | `system` | 1700 MiB | ext4, mounted as `/` (`skip_initramfs`) |
| p18 | `cache` | 1000 MiB | ext4 |
| p19 | `vendor` | 1000 MiB | ext4 |
| p20 | `private` | 50 MiB | ext4 |
| p21 | `userdata` | rest (~3.3 GiB) | ext4 |

Also present: `mmcblk0boot0`, `mmcblk0boot1` (4 MiB each), `mmcblk0rpmb`.

## Device tree

The same 17 896-byte FDT appears in three places and is byte-identical
(`md5 4257988dcde5671b40fca19dec98b2bd`) in all of them:

* `dtbo` partition, at offset `0x40` (single DTBO entry, id 0, rev 0)
* `boot` partition, at offset `0x9df898`
* `recovery` partition, at offsets `0x971240` and `0xae8040`

FDT version 17, last compatible version 16. Decompiled copy in
[`../dts/vendor/`](../dts/vendor/).

## U-Boot

Version string in the binary: `0.1.0.1 - HiFone B02`. Also carries
`Hi3798CV200_20150730_001` and HDMI/OTP/SDK helper strings, so it is the standard
HiSilicon STB U-Boot.

`bootdelay=0`, so there is no autoboot interrupt window over the serial console
by default. Changing it requires rewriting the `bootargs` partition — do that
only with a working recovery path.

Full environment in [`../extracted/uboot-env.txt`](../extracted/uboot-env.txt).
Stored CRC32 `0x6409A180`. Highlights:

```
bootdelay=0
verify=n
baudrate=115200
ipaddr=192.168.1.10
serverip=192.168.1.1
netmask=255.255.255.0
bootfile="uImage"
phy_intf=mii,rgmii
use_mdio=0,1
phy_addr=2,1
bootcmd=mmc read 0 0x3ED00000 0x5F000 0x4000;bootm 0x3EF00000; \
        mmc read 0 0x1FFBFC0 0x37000 0xC800; mmc read 0 0x3FFBFC0 0x18000 0x3C; \
        bootm 0x1FFBFC0 - 0x3FFC000
bootargs_512M=mem=512M mmz=ddr,0,0,32M vmalloc=500M
bootargs_768M=mem=768M mmz=ddr,0,0,32M vmalloc=500M
bootargs_1G=mem=988M mmz=ddr,0,0,44M vmalloc=500M
bootargs_2G=mem=992M mmz=ddr,0,0,44M vmalloc=500M
```

The network variables are already populated, which makes **TFTP the natural way
to test a mainline kernel** without touching eMMC at all.

Full stock `bootargs`:

```
androidboot.hardware=bigfish androidboot.selinux=permissive
androidboot.serialno=0123456789 console=ttyAMA0,115200
blkdevparts=mmcblk0:<see table above>
hbcomp=/dev/block/mmcblk0p17 skip_initramfs init=/init root=/dev/mmcblk0p17
androidboot.dtbo_idx=0 dongle nopcie nosata nouart3 nofmc pq=noacmuhd
mem=988M mmz=ddr,0,0,44M vmalloc=500M
```

`nopcie nosata nouart3 nofmc` disable blocks this board does not populate.

## Kernel modules loaded at runtime

```
rtl8822bs            2883584   Realtek Wi-Fi/BT over SDIO
rtk_btusb              49152   Realtek Bluetooth
uwe5621_bsp_sdio     1019904   Unisoc UWE5621 Wi-Fi/BT (alternate module)
sprdbt_tty             28672   Unisoc Bluetooth TTY
hi_sdio_detect         16384   HiSilicon SDIO card detect
ahci_platform / libahci_platform / libahci
xhci_plat_hcd / ohci_platform / ehci_platform
tntfs                 528384   Tuxera NTFS
g_ffs                  16384   USB gadget
```

Two different Wi-Fi stacks ship in one image, so the same board is manufactured
with either a Realtek or a Unisoc module.
