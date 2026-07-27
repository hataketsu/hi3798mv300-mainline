#!/system/bin/sh
#
# Dump every firmware partition of a Hi3798MV200/MV300 box to external storage.
# Run this ON THE BOX as root, e.g. over the serial console:
#
#     su 0 sh /data/local/tmp/dump-partitions.sh /mnt/media_rw/sda1/rom_dump
#
# Notes:
#   * toybox `dd` rejects `bs=1M`; the numeric form is required.
#   * FAT32 cannot hold a file larger than 4 GiB, so the whole eMMC is never
#     imaged in one piece. Partitions are dumped individually instead.
#   * `cache` and `userdata` are skipped: `cache` is scratch space, and
#     `userdata` contains account tokens and should not leave the device
#     casually. Pass -a to include them anyway.

set -u

BS=1048576
DEST="${1:-}"
INCLUDE_DATA=0

if [ "${2:-}" = "-a" ]; then
    INCLUDE_DATA=1
fi

if [ -z "$DEST" ]; then
    echo "usage: $0 <destination-dir> [-a]" >&2
    exit 1
fi

# Partition number : name. Matches the blkdevparts= order in /proc/cmdline.
PARTS="1:fastboot 2:bootargs 3:bootargsbak 4:recovery 5:deviceinfo
6:securestore 7:baseparam 8:pqparam 9:dtbo 10:logo 11:logobak 12:fastplay
13:recoverybak 14:boot 15:misc 16:trustedcore 17:system 19:vendor 20:private"

if [ "$INCLUDE_DATA" = 1 ]; then
    PARTS="$PARTS 18:cache 21:userdata"
fi

mkdir -p "$DEST" || exit 1

for entry in $PARTS; do
    num="${entry%%:*}"
    name="${entry##*:}"
    src="/dev/block/mmcblk0p$num"
    out="$DEST/p$num-$name.img"

    if [ ! -e "$src" ]; then
        echo "skip $name: $src missing"
        continue
    fi

    echo "dumping $name -> $out"
    if ! dd if="$src" of="$out" bs=$BS 2>/dev/null; then
        echo "FAILED: $name" >&2
    fi
done

# eMMC boot areas, which are separate hardware partitions.
for area in boot0 boot1; do
    src="/dev/block/mmcblk0$area"
    [ -e "$src" ] || continue
    echo "dumping emmc $area"
    dd if="$src" of="$DEST/emmc-$area.img" bs=$BS 2>/dev/null || \
        echo "FAILED: emmc $area" >&2
done

sync
echo
echo "done:"
ls -l "$DEST"
