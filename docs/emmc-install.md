# Debian on eMMC

The box boots Debian from its own eMMC with no USB stick attached. Working:

```
# findmnt -no SOURCE,FSTYPE /
/dev/mmcblk0p17 ext4
# systemctl --failed
0 loaded units listed.
```

The stock bootloader is untouched. It still owns sector 0 and still does the
first hop; only what it loads changed.

```
BootROM
  -> vendor Fastboot 3.3.0        (eMMC sector 0, unmodified)
     -> mmc read Image, tvbox.dtb, l-loader.bin from eMMC into RAM
        -> go 0x0203F000
           -> l-loader -> BL1 -> BL2 -> BL31 -> BL33 = U-Boot 2026.07
              -> booti 0x10000000 - 0x0f000000
                 -> Linux -> initramfs -> switch_root /dev/mmcblk0p17
```

## The eMMC has no partition table

This is the fact everything else follows from. Sector 0 is not an MBR — it is
the bootloader:

```
# dd if=/dev/mmcblk0 bs=512 count=1 | od -An -tx1 | head -1
 3e 21 00 ea 00 00 00 00 00 00 00 00 00 00 00 00
```

`0xea00213e` is an ARM branch. Writing a partition table there destroys the
bootloader and the box stops booting entirely.

The vendor instead describes all 21 partitions with `blkdevparts=` on the
kernel command line. Mainline can read the same syntax — `CONFIG_CMDLINE_PARTITION=y`
— so the layout survives the port unchanged.

## Layout

Everything up to and including `trustedcore` keeps the vendor geometry, so the
stock bootloader still finds what it expects. The five Android partitions after
it — `system`, `cache`, `vendor`, `private`, `userdata` — are replaced by one
rootfs.

```
blkdevparts=mmcblk0:1M(fastboot),512K(bootargs),512K(bootargsbak),20M(recovery),
2M(deviceinfo),8M(securestore),8M(baseparam),8M(pqparam),2M(dtbo),10M(logo),
10M(logobak),20M(fastplay),20M(recoverybak),60M(boot),20M(misc),20M(trustedcore),
-(rootfs)
```

| part | offset | size | holds |
|---|---|---|---|
| p1 `fastboot` | 0 | 1 MiB | **stock bootloader — never write here** |
| p2/p3 `bootargs` | 1 MiB | 512 KiB each | U-Boot environment |
| p5 `deviceinfo` | 22 MiB | 2 MiB | the board's real MAC, in ASCII |
| p12 `fastplay` | 70 MiB | 20 MiB | `l-loader.bin` |
| p13 `recoverybak` | 90 MiB | 20 MiB | `tvbox.dtb` |
| p14 `boot` | 110 MiB | 60 MiB | `Image` |
| p17 `rootfs` | 210 MiB | 6.9 GiB | Debian |

The offsets were checked against the eMMC before anything was written — reading
1 MiB at each computed boundary and looking for the expected content:

```
0 MiB    3e 21 00 ea          ARM branch, bootloader
1 MiB    "verify=n", "bau..." U-Boot environment
2 MiB    ANDROID!             recovery
22 MiB   "xx:xx:xx:xx:xx:xx"  deviceinfo, the board's MAC as ASCII
48 MiB   d7 b7 ab 1e          dtbo magic
50 MiB   "LOGO_TAB"           logo
110 MiB  ANDROID!             boot
```

`fastplay`, `recoverybak` and `boot` are Android leftovers this port has no use
for, which is why the three boot images live there rather than in a filesystem.

## The command line is compiled in

Mainline U-Boot on this board has no writable environment, so `blkdevparts=`
had to go into `CONFIG_BOOTARGS` and the whole chain rebuilt: U-Boot, then TF-A
with the new `u-boot.bin` as BL33, then l-loader. See
[aarch64-bringup.md](aarch64-bringup.md) for the flags that are not optional.

Without it there are no `/dev/mmcblk0pN` nodes at all, only the raw 7 GiB
`/dev/mmcblk0`, and the initramfs finds nothing to boot.

## No root= either

There is no `root=` on the command line and there never was. The kernel carries
a busybox initramfs — `CONFIG_INITRAMFS_SOURCE` — which walks the candidates
and picks the first block device holding an executable `/sbin/init`:

```sh
for d in /dev/sd?2 /dev/sd?1 /dev/mmcblk0p*; do
	mount -t ext4 -o ro "$d" /mnt/root 2>/dev/null || continue
	[ -x /mnt/root/sbin/init ] && root="$d" && break
	umount /mnt/root
done
```

So moving the root filesystem needs no bootloader change — the initramfs finds
it wherever it is. `/etc/fstab` on eMMC names the device directly, since
partitions that exist only on a command line have no PARTUUID to key off.

## Installing

[`../scripts/install-to-emmc.sh`](../scripts/install-to-emmc.sh) runs on the box
while it is still booted from USB. It refuses to run if the root is already on
eMMC, checks each image fits the partition it is going into, copies the running
system with `rsync -aHAX -x`, writes an eMMC-specific `/etc/fstab`, and verifies
each written image with `cmp` before reporting success.

It also removes `growroot.service` from the copy: that unit resizes the root
filesystem on first boot by looking up a PARTUUID, which eMMC partitions do not
have, and `mkfs` has already sized the filesystem correctly. On USB it fails
every boot and leaves systemd `degraded`.

## Switching bootcmd

From the `fastboot#` prompt, reached with Ctrl-C during the 3-second delay:

```
setenv bootcmd mmc read 0 0x10000000 0x37000 0x18dc5\;mmc read 0 0x0f000000 0x2d000 0x1e\;mmc read 0 0x02000000 0x23000 0x800\;go 0x0203F000
saveenv
```

Block numbers are partition offsets in 512-byte sectors: `0x37000` = 110 MiB,
`0x2d000` = 90 MiB, `0x23000` = 70 MiB. Counts are the image sizes rounded up.

**Do not wrap the value in double quotes.** This U-Boot's simple parser keeps
the opening quote as part of the first word:

```
Unknown command '"mmc' - try 'help'

MMC read: dev # 0, block # 184320, count 30 ... 30 blocks read: OK
MMC read: dev # 0, block # 143360, count 2048 ... 2048 blocks read: OK
## Starting application at 0x0203F000 ...
...
Bad Linux ARM64 Image magic!
```

The first read is swallowed, the other two succeed, and U-Boot is handed an
empty address where the kernel should be. Backslash-escaped semicolons on their
own work fine.

## Recovery is at the bootloader

Inserting the USB stick does **not** override the eMMC root, despite the
initramfs listing `/dev/sd?2` first. It takes whichever candidate appears
first, and eMMC is ready immediately while USB needs a second or two to
enumerate — so on the first pass only `/dev/mmcblk0p17` exists and it wins.

To go back to USB, restore the old value from the `fastboot#` prompt:

```
setenv bootcmd usb start\;usb start\;fatload usb 0:1 0x10000000 Image\;fatload usb 0:1 0x0f000000 tvbox.dtb\;fatload usb 0:1 0x02000000 l-loader.bin\;go 0x0203F000
saveenv
```

That path stays available because sector 0 was never touched. The stock
bootloader also has a hardware recovery mode — pulling `USB_BOOT` low makes it
read `bootargs.bin` and `recovery.img` off a USB stick — and the boot log shows
it looking for a recovery key press:

```
enter the gpio press revocery
```

Both of those disappear if the stock bootloader is ever overwritten. See
[bootrom-serial.md](bootrom-serial.md) for why that is not worth doing yet.
