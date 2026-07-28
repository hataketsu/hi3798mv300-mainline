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

## Terminal settings: turn hardware flow control off

The header carries `GND`, `RX` and `TX` only — there is no `CTS`. A terminal
configured for RTS/CTS therefore waits forever for a clear-to-send that never
arrives, and **silently refuses to transmit**. Receive is unaffected, which makes
this look convincing as a hardware fault: the board's log scrolls past normally,
and nothing typed reaches it.

`minicom` ships with `Hardware Flow Control : Yes` as its default, so it exhibits
exactly this out of the box. Either turn it off (`Ctrl-A O` → *Serial port
setup* → `F`) or use a terminal that does not enable it.
[`../scripts/uart-term.py`](../scripts/uart-term.py) is a dependency-light one
that sets `rtscts=False` explicitly and leaves `Ctrl+C` free to pass through to
the board:

```sh
python3 scripts/uart-term.py            # Ctrl-] to quit
python3 scripts/uart-term.py --catch    # hammer Ctrl+C to win the autoboot race
```

Before blaming the wiring, check that nothing else already holds the port —
two processes on one tty fight over termios, and the second one to open it can
have its settings overwritten by the first:

```sh
fuser -v /dev/ttyUSB0
```

## Reaching the U-Boot prompt

Despite `bootdelay=0` in the stock environment, the vendor bootloader **does**
offer an interrupt window and prints an invitation for it:

```
Press Ctrl+C to stop autoboot
```

`Ctrl+C` — not the usual any-key — is what it tests for. The window is short and
the round trip over USB serial is 20–40 ms, so pressing it by hand is a race.
Two things make it reliable:

* Start sending **at the banner** (`Bootrom start` / `Fastboot 3.3.0`), not at
  the invitation. Bytes then arrive continuously through the window.
* Do not send *before* the banner. The bootloader drains pending input before
  testing for the key, so anything sent earlier is discarded.

`scripts/uart-term.py --catch` and `boot-mainline.py` both implement this. No
recovery key combination is needed, and nothing has to be written to flash.
