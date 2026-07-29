#!/bin/sh
# Build a minimal initramfs that hands off to the Debian root on the USB stick.
#
# The previous initramfs was a whole Alpine tree used as the root filesystem
# itself. That cannot work now: an embedded initramfs with an /init is always
# the root, so the kernel would never look at root= at all. This one mounts the
# real root and switch_roots into it, and drops to a shell if it cannot -- which
# matters because USB on this board is still unproven, and a board that hangs
# with no prompt tells you nothing.
set -e

SRC=${SRC:-$HOME/hi3798/initramfs}
OUT=${OUT:-$HOME/hi3798/initramfs-min}

rm -rf "$OUT"
mkdir -p "$OUT"/bin "$OUT"/lib "$OUT"/dev "$OUT"/proc "$OUT"/sys "$OUT"/mnt/root

cp "$SRC/bin/busybox" "$OUT/bin/busybox"
# busybox here is musl-linked, so the loader and libc have to come along.
cp -a "$SRC"/lib/ld-musl-aarch64.so.1 "$OUT/lib/" 2>/dev/null || \
	cp -a "$SRC"/lib/libc.musl-aarch64.so.1 "$OUT/lib/"
for f in "$SRC"/lib/libc.musl-aarch64.so.1 "$SRC"/lib/ld-musl-aarch64.so.1; do
	[ -e "$f" ] && cp -a "$f" "$OUT/lib/" || true
done
ln -sf busybox "$OUT/bin/sh"

cat > "$OUT/init" <<'EOF'
#!/bin/sh
/bin/busybox --install -s /bin 2>/dev/null

mount -t proc     none /proc
mount -t sysfs    none /sys
mount -t devtmpfs none /dev 2>/dev/null

# The kernel prints "unable to open an initial console" because /dev is empty
# until the mount above, so init inherits no stdin at all. A rescue shell with
# no stdin reads EOF, exits immediately, and takes the kernel down with
# "Attempted to kill init!" -- which is exactly what happened on the first try.
exec </dev/console >/dev/console 2>&1

echo "initramfs: looking for the Debian root"

# devtmpfs gives us /dev/sdXN but no by-partuuid symlinks -- that is udev's job,
# and udev does not exist yet. So walk the candidates and identify the right one
# by what is on it rather than by label.
root=""
for try in $(seq 1 30); do
	for d in /dev/sd?2 /dev/sd?1 /dev/mmcblk0p*; do
		[ -b "$d" ] || continue
		mount -t ext4 -o ro "$d" /mnt/root 2>/dev/null || continue
		if [ -x /mnt/root/sbin/init ] || [ -L /mnt/root/sbin/init ]; then
			umount /mnt/root
			root="$d"
			break
		fi
		umount /mnt/root
	done
	[ -n "$root" ] && break
	[ "$try" = 1 ] && echo "initramfs: waiting for USB to enumerate"
	sleep 1
done

if [ -z "$root" ]; then
	echo "initramfs: no root filesystem found -- dropping to a shell"
	echo "initramfs: 'cat /proc/partitions' shows what the kernel did see"
	exec /bin/sh
fi

echo "initramfs: root is $root"
mount -t ext4 "$root" /mnt/root || { echo "initramfs: mount failed"; exec /bin/sh; }

umount /proc /sys
exec switch_root /mnt/root /sbin/init
EOF
chmod +x "$OUT/init"

echo "MKINITRAMFS_DONE"
du -sh "$OUT"
