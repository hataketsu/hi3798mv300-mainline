# Serial console access

## Wiring

UART0 (`0xf8b00000`, PL011) is the console. Settings: **115200 8N1, no flow
control**. On the board it is the usual 3- or 4-pin header — connect `GND`, `RX`,
`TX` from a 3.3 V USB-serial adapter and leave `VCC` unconnected.

Any adapter works; this was captured with a Prolific PL2303
(`067b:2303`), which enumerates as `/dev/ttyUSB0` on Linux.

Confirm the baud rate by looking for printable ASCII — at 115200 the stock
Android shell prompt appears:

```
/system/bin/sh: z: not found
console:/ $
```

## Getting a root shell

The stock build is `userdebug` with `su` present, so:

```sh
su 0 id
# uid=0(root) gid=0(root) groups=0(root),1007(log),3009(readproc) context=u:r:su:s0
```

Note that this is Android's `su`, which takes the UID as its first positional
argument. `su -c 'cmd'` fails with `su: invalid uid/gid '-c'`; use
`su 0 sh -c 'cmd'`.

## Quieting kernel log spam

SELinux is permissive, so every denial is logged to the console and interleaves
with command output, corrupting anything you try to parse. Turn it down first:

```sh
su 0 sh -c 'echo 1 > /proc/sys/kernel/printk'
```

`dmesg -n 1` does **not** work from the shell user (`klogctl: Operation not
permitted`).

## Driving the console from a script

[`../scripts/uart-cmd.py`](../scripts/uart-cmd.py) sends one command and streams
the reply until the prompt returns:

```sh
python3 scripts/uart-cmd.py "su 0 cat /proc/cmdline"
python3 scripts/uart-cmd.py "su 0 sh -c 'dd if=/dev/block/mmcblk0p1 of=/sdcard/fastboot.img bs=1048576'" 300 120
```

Arguments are `<command> [max_seconds] [idle_timeout_seconds]`.

Two things that bite when scripting this:

* The console **echoes the command back**, so never test for a marker string that
  also appears in the command you sent — the echo will match it. Compare file
  sizes or grep counts instead.
* Long commands wrap and interleave with kernel messages. Keep one-liners short,
  or write a script to `/data/local/tmp` and run that.

## ADB over the network

Faster than UART for bulk transfers:

```sh
su 0 setprop service.adb.tcp.port 5555
su 0 stop adbd
su 0 start adbd
# then, from the host:
adb connect <box-ip>:5555
```

## U-Boot

`bootdelay=0` in the stock environment means there is **no autoboot interrupt
window**. Reaching the U-Boot prompt requires either rewriting the `bootargs`
partition to set a non-zero `bootdelay`, or holding whatever recovery key
combination the board exposes. Do not rewrite `bootargs` without first dumping
both it and `bootargsbak`, and having a way to reflash if the board stops
booting.
