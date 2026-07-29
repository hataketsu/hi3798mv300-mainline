#!/bin/bash
# Copy the running Debian root to the box's eMMC.
#
# Run this ON the box, booted from USB. It writes only to the rootfs partition
# and to the three vendor partitions that hold the kernel, device tree and
# l-loader. It does not touch sector 0, where the stock bootloader lives, and
# it does not touch the U-Boot environment -- changing bootcmd is a separate,
# reversible step done from the `fastboot#` prompt.
#
# The eMMC has no partition table. The vendor describes its layout purely
# through blkdevparts= on the kernel command line, and sector 0 is the
# bootloader itself, so an MBR cannot be written there. /dev/mmcblk0p17 exists
# only because the kernel was told about it; see docs/emmc-install.md.
#
# Recovery is at the bootloader, not by plugging the stick back in. The
# initramfs lists USB before eMMC but takes whichever appears first, and eMMC
# is always ready while USB needs a couple of seconds to enumerate -- so eMMC
# wins even with a stick inserted. To get back to USB, interrupt the boot with
# Ctrl-C and put the old bootcmd back.
set -euo pipefail

ROOTFS_DEV=/dev/mmcblk0p17
BOOT_DEV=/dev/mmcblk0p14        # 60 MiB, vendor "boot"
DTB_DEV=/dev/mmcblk0p13         # 20 MiB, vendor "recoverybak"
LLOADER_DEV=/dev/mmcblk0p12     # 20 MiB, vendor "fastplay"
MNT=/mnt/emmc-root

die() { echo "error: $*" >&2; exit 1; }

# ------------------------------------------------------------------ checks
[ "$(id -u)" = 0 ] || die "run as root"
[ -b "$ROOTFS_DEV" ] || die "$ROOTFS_DEV missing -- is blkdevparts= on the kernel command line?"

running_root=$(findmnt -no SOURCE /)
case "$running_root" in
	/dev/mmcblk0*) die "already running from eMMC; boot from USB to run this" ;;
esac
echo "running from $running_root, installing to $ROOTFS_DEV"

for f in /boot/Image /boot/tvbox.dtb /boot/l-loader.bin; do
	[ -f "$f" ] || die "$f missing"
done

# The three images have to fit the partitions they are written to.
check_fits() {
	local file=$1 dev=$2 name=$3
	local size avail
	size=$(stat -c%s "$file")
	avail=$(( $(cat /sys/class/block/"$(basename "$dev")"/size) * 512 ))
	[ "$size" -le "$avail" ] || die "$name is $size bytes, $dev holds $avail"
	printf '  %-16s %9d B into %s (%d B)\n' "$name" "$size" "$dev" "$avail"
}
echo "size checks:"
check_fits /boot/Image        "$BOOT_DEV"    Image
check_fits /boot/tvbox.dtb    "$DTB_DEV"     tvbox.dtb
check_fits /boot/l-loader.bin "$LLOADER_DEV" l-loader.bin

# ------------------------------------------------------------------ rootfs
echo
echo "=== mkfs on $ROOTFS_DEV ==="
umount "$MNT" 2>/dev/null || true
mkfs.ext4 -q -F -L HISTB_EMMC -m 1 "$ROOTFS_DEV"
mkdir -p "$MNT"
mount "$ROOTFS_DEV" "$MNT"

echo "=== copying root ==="
# -x keeps rsync on one filesystem, so /boot (vfat on USB), /proc, /sys and the
# destination itself are all skipped without needing to name them.
rsync -aHAX --numeric-ids --info=progress2 \
	--exclude='/tmp/*' --exclude='/var/tmp/*' --exclude='/var/cache/apt/archives/*' \
	-x / "$MNT/"

# Directories rsync skipped because they are mount points, needed at boot.
mkdir -p "$MNT"/{proc,sys,dev,run,tmp,boot,mnt,media}
chmod 1777 "$MNT/tmp"

echo "=== fstab ==="
cat > "$MNT/etc/fstab" <<EOF
# Root on eMMC. The partition exists only because blkdevparts= on the kernel
# command line describes it; the eMMC carries no partition table, so there is
# no PARTUUID to key off and the device node is named directly.
$ROOTFS_DEV	/	ext4	errors=remount-ro	0 1

# No /boot entry. On eMMC the kernel, device tree and l-loader live at raw
# offsets in vendor partitions, not in a filesystem.
EOF
cat "$MNT/etc/fstab"

# This unit resizes the root filesystem to fill its partition on first boot and
# fails noisily here: it looks for a PARTUUID that eMMC partitions do not have,
# and mkfs already sized the filesystem correctly.
rm -f "$MNT/etc/systemd/system/multi-user.target.wants/growroot.service" \
      "$MNT/etc/systemd/system/growroot.service" 2>/dev/null || true

sync
umount "$MNT"
echo "rootfs done"

# ------------------------------------------------------------------ boot images
echo
echo "=== writing boot images ==="
write_img() {
	local file=$1 dev=$2 name=$3
	dd if="$file" of="$dev" bs=1M conv=fsync status=none
	local size
	size=$(stat -c%s "$file")
	if cmp -s -n "$size" "$file" "$dev"; then
		echo "  $name -> $dev  OK"
	else
		die "$name verify failed against $dev"
	fi
}
write_img /boot/Image        "$BOOT_DEV"    Image
write_img /boot/tvbox.dtb    "$DTB_DEV"     tvbox.dtb
write_img /boot/l-loader.bin "$LLOADER_DEV" l-loader.bin
sync

# ------------------------------------------------------------------ bootcmd
echo
echo "=== done ==="
echo
echo "The eMMC now holds a root filesystem and the three boot images. Nothing"
echo "boots from it yet -- the stock bootloader still loads everything over USB."
echo
echo "To switch, interrupt the boot with Ctrl-C to reach the fastboot# prompt"
echo "and set bootcmd to read from eMMC instead. Escape every ';' with a"
echo "backslash, this U-Boot runs each fragment immediately otherwise:"
echo
blocks() { echo $(( ( $(stat -c%s "$1") + 511 ) / 512 )); }
start() { echo $(( $(cat /sys/class/block/"$(basename "$1")"/start) )); }
printf '  setenv bootcmd "mmc read 0 0x10000000 0x%x 0x%x\\;mmc read 0 0x0f000000 0x%x 0x%x\\;mmc read 0 0x02000000 0x%x 0x%x\\;go 0x0203F000"\n' \
	"$(start $BOOT_DEV)"    "$(blocks /boot/Image)" \
	"$(start $DTB_DEV)"     "$(blocks /boot/tvbox.dtb)" \
	"$(start $LLOADER_DEV)" "$(blocks /boot/l-loader.bin)"
echo '  saveenv'
echo
echo "Do NOT wrap the value in double quotes. This U-Boot's simple parser keeps"
echo "the opening quote as part of the first word and the command dies with"
echo "  Unknown command '\"mmc' - try 'help'"
echo "leaving the kernel unloaded. Backslash-escaped semicolons are enough."
echo
echo "Save the old value first so it can be put back:"
echo '  printenv bootcmd'
echo
echo "After this the box boots entirely from eMMC. Inserting the USB stick does"
echo "not override it -- the initramfs takes whichever root appears first, and"
echo "eMMC is ready before USB finishes enumerating. To go back, restore the"
echo "old bootcmd from the fastboot# prompt."
