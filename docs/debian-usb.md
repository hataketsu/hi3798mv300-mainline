# Debian on a USB stick

A full Debian 13 (trixie) arm64 userland booting from USB, with the eMMC left
alone. The box powers on and reaches a login prompt with nothing typed.

## Boot chain

Four stages, because the SoC's BootROM only trusts the vendor bootloader:

```
BootROM
  -> vendor "Fastboot 3.3.0" (AArch32) -- reads bootcmd from eMMC
     -> loads Image, tvbox.dtb, l-loader.bin from the USB stick into RAM
     -> go 0x0203F000
        -> l-loader switches to AArch64, enters TF-A
           -> BL1 -> BL2 -> BL31 -> BL33 = mainline U-Boot 2026.07
              -> booti 0x10000000 - 0x0f000000
                 -> mainline Linux -> initramfs -> switch_root -> Debian
```

The vendor bootloader stays in charge of the first hop and is never reflashed.
Only its environment changes, which is a 64 KiB write at eMMC offset
`0x00100000` and reversible from the saved copy in
[`../extracted/uboot-env-backup-2026-07-29.txt`](../extracted/uboot-env-backup-2026-07-29.txt).

### The environment change

`saveenv` is the only write this port makes to eMMC. The stock Android command
is preserved first so the box can be put back:

```
setenv bootcmd_android "mmc read 0 0x3ED00000 0x5F000 0x4000\;bootm 0x3EF00000\;mmc read 0 0x1FFBFC0 0x37000 0xC800\;mmc read 0 0x3FFBFC0 0x18000 0x3C\;bootm 0x1FFBFC0 - 0x3FFC000"
setenv bootcmd "usb start\;usb start\;fatload usb 0:1 0x10000000 Image\;fatload usb 0:1 0x0f000000 tvbox.dtb\;fatload usb 0:1 0x02000000 l-loader.bin\;go 0x0203F000"
saveenv
```

**Escape every `;` with a backslash.** This U-Boot uses the simple parser, which
splits on `;` before it considers quotes. Written with plain quotes, the shell
runs each fragment immediately -- `usb start`, the `fatload`s and then `go` --
and the box boots on the spot instead of setting the variable.

`usb start` is issued twice on purpose: the first call after a cold boot finds
no devices and the second finds the stick.

`bootdelay=3` is kept so Ctrl+C always gets back to `fastboot#`.

The mainline U-Boot half is baked in at build time, because it has no writable
environment on this board (`*** Warning - No block device, using default
environment`):

```
CONFIG_BOOTDELAY=2
CONFIG_BOOTCOMMAND="booti 0x10000000 - 0x0f000000"
CONFIG_USE_BOOTARGS=y
CONFIG_BOOTARGS="earlycon=pl011,mmio32,0xf8b00000 console=ttyAMA0,115200n8 nokaslr rootwait clk_ignore_unused"
```

Rebuilding it means rebuilding the FIP and l-loader too, and
`l-loader/atf/fip.bin` is a **manual copy** -- the l-loader Makefile does not
pull it from the TF-A build directory. Forgetting to refresh it silently
produces an l-loader carrying the previous U-Boot. Verify by comparing the
md5 of the embedded FIP at offset `0x40000` against `fip.bin`, and remember to
pad the output back to 1 MiB after `truncate_minimal.py` strips its tail.

## `clk_ignore_unused`

Without it USB dies at `clk: Disabling unused clocks`:

```
ehci-platform f9890000.usb: port 2 reset error -110
```

Some clock the USB path depends on is claimed by nobody, so the framework
gates it as unused. `clk_ignore_unused` is a workaround, not a fix -- the real
answer is to find the clock and claim it in the right node.

## Partition layout

The stick keeps the vendor tooling's MBR, disk signature `26b14414`:

| | | |
|---|---|---|
| `sda1` | 512 MiB vfat, `HISTB_BOOT`, PARTUUID `26b14414-01` | `Image`, `tvbox.dtb`, `l-loader.bin` |
| `sda2` | rest, ext4, `HISTB_ROOT`, PARTUUID `26b14414-02` | Debian |

`/etc/fstab` mounts by PARTUUID, not by `/dev/sda*`, because enumeration order
depends on which USB port comes up first. That also means **cloning to a bigger
stick must copy the whole disk**, not the partitions -- a fresh partition table
gets a new signature and the box stops booting.

After cloning, the filesystem does not grow on its own:

```sh
growpart /dev/sda 2 && resize2fs /dev/sda2
```

The partition has to be extended before `resize2fs` has anywhere to go.

## Building the rootfs

`debootstrap --variant=minbase` plus the essentials, built on the build server
with qemu-user-static binfmt. See [`../scripts/`](../scripts/). Roughly 500 MB
with kernel modules and Realtek firmware.

The image is made with `mke2fs -d`, so no loop mount and no root privileges
beyond the mkfs itself:

```sh
truncate -s 1400M debian-root.img
mkfs.ext4 -F -L HISTB_ROOT -d debian debian-root.img
```

It is written smaller than the partition on purpose and grown on first boot.

### Three things that bite in a chroot

**DNS.** Pointing `/etc/resolv.conf` at `/run/systemd/resolve/stub-resolv.conf`
is wrong twice over: the file does not exist inside a chroot, and
`systemd-resolved` is a **separate package** in trixie that `minbase` does not
install. Either install it or write a plain `resolv.conf`.

**Time.** The board has no RTC (`RTC time: n/a`), so the clock starts at the
epoch every boot and apt rejects every release file:

```
Sub-process /usr/bin/sqv returned an error code (1), error message is:
Verifying signature: Not live until 2026-07-29T02:32:53Z
```

That is a *clock* error, not a corrupt mirror. Install `systemd-timesyncd`,
which `minbase` also omits.

**Modules tarballs.** Debian is usr-merged: `/lib` is a symlink to `usr/lib`.
A tarball containing a real `lib/` directory extracted at `/` **replaces that
symlink with a directory**, `/lib/ld-linux-aarch64.so.1` disappears, and every
dynamically linked binary on the system fails at once:

```
bash: /usr/bin/uname: cannot execute: required file not found
```

Install modules with `INSTALL_MOD_PATH` pointing into the rootfs, or pack the
tarball as `usr/lib/modules/...`. Recovery is possible but tedious: boot with
`rdinit=/bin/sh` so the kernel runs the initramfs busybox instead of
switch_rooting into the broken tree, then restore the symlink by hand.

## initramfs

Deliberately tiny -- busybox plus musl, about 1.7 MB, embedded via
`CONFIG_INITRAMFS_SOURCE`. Built by
[`../scripts/mk-initramfs.sh`](../scripts/mk-initramfs.sh).

An embedded initramfs with an `/init` is *always* the root; the kernel never
looks at `root=`. So this one mounts the real root and `switch_root`s into it,
and drops to a shell if it cannot find one -- which matters when USB is the
only path to the rootfs and USB is the thing being debugged.

It walks `/dev/sd?2 /dev/sd?1 /dev/mmcblk0p*` looking for a partition with
`/sbin/init` rather than trusting a device name, because devtmpfs gives no
`by-partuuid` symlinks without udev.

One non-obvious detail: `/dev` is empty until the initramfs mounts devtmpfs, so
init inherits no stdin. A rescue shell with no stdin reads EOF, exits, and
takes the kernel with it:

```
Kernel panic - not syncing: Attempted to kill init!
```

Hence the explicit `exec </dev/console >/dev/console 2>&1`.

## Wi-Fi

RTL8822BS on SDIO, driven by mainline `rtw88`. See
[`wifi.md`](wifi.md) for the SDIO controller and the dtsi typo that hid it.

rtw88 is built as **modules**, not built in. The SDIO card is found about a
second into boot, long before the root filesystem exists, and a built-in driver
would ask for `rtw8822b_fw.bin` before `/lib/firmware` is readable.
