#!/bin/bash
# Write the Debian rootfs and the kernel onto the box's USB stick.
#
# The stick is the one the box already boots from: an MBR-partitioned disk with
# signature 26b14414, a vfat boot partition and an ext4 root. This script refuses
# to touch anything else, because the difference between the stick and the
# laptop's own disk is one character of device name.
#
# Usage: sudo ./write-usb-rootfs.sh /dev/sdX [rootfs.tar.gz] [kernel-dir]
set -euo pipefail

DISK=${1:?usage: $0 /dev/sdX [rootfs.tar.gz] [kernel-dir]}
TARBALL=${2:-rootfs/debian-rootfs.tar.gz}
KDIR=${3:-tftp}

BOOT_PARTUUID=26b14414-01
ROOT_PARTUUID=26b14414-02

[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
[ -b "$DISK" ] || { echo "$DISK is not a block device" >&2; exit 1; }
[ -f "$TARBALL" ] || { echo "no rootfs tarball at $TARBALL" >&2; exit 1; }
[ -f "$KDIR/Image" ] || { echo "no kernel at $KDIR/Image" >&2; exit 1; }

# Identify by PARTUUID, not by device name. If the signature does not match, this
# is somebody else's disk and we stop before doing damage.
boot=$(lsblk -rno PATH,PARTUUID "$DISK" | awk -v u=$BOOT_PARTUUID '$2==u{print $1}')
root=$(lsblk -rno PATH,PARTUUID "$DISK" | awk -v u=$ROOT_PARTUUID '$2==u{print $1}')
if [ -z "$boot" ] || [ -z "$root" ]; then
	echo "$DISK does not carry the expected partitions." >&2
	echo "Wanted PARTUUID $BOOT_PARTUUID (boot) and $ROOT_PARTUUID (root); found:" >&2
	lsblk -o PATH,SIZE,FSTYPE,LABEL,PARTUUID "$DISK" >&2
	exit 1
fi

echo "boot partition: $boot"
echo "root partition: $root"
echo
echo "This ERASES $root and replaces the kernel on $boot."
read -rp "Type the disk name again to confirm ($DISK): " confirm
[ "$confirm" = "$DISK" ] || { echo "aborted"; exit 1; }

umount "$boot" "$root" 2>/dev/null || true

echo "== root: mkfs.ext4 =="
mkfs.ext4 -F -L HISTB_ROOT "$root"

mnt=$(mktemp -d)
trap 'umount "$mnt/boot" 2>/dev/null || true; umount "$mnt" 2>/dev/null || true; rmdir "$mnt"' EXIT

mount "$root" "$mnt"
echo "== root: unpacking $TARBALL =="
tar --numeric-owner -xzf "$TARBALL" -C "$mnt"

mkdir -p "$mnt/boot"
mount "$boot" "$mnt/boot"
echo "== boot: copying kernel and dtb =="
cp "$KDIR/Image" "$mnt/boot/Image"
cp "$KDIR/tvbox.dtb" "$mnt/boot/tvbox.dtb"
[ -f "$KDIR/l-loader.bin" ] && cp "$KDIR/l-loader.bin" "$mnt/boot/l-loader.bin"

sync
echo "done"
