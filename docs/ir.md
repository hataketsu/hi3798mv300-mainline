# IR receiver

The on-board IR receiver at `0xf8001000`, driven by mainline's
`drivers/media/rc/ir-hix5hd2.c`. Working:

```
hix5hd2-ir f8001000.ir: no power-reg
rc rc0: hix5hd2-ir as /devices/virtual/rc/rc0
rc rc0: lirc_dev: driver hix5hd2-ir registered at minor = 0, raw IR receiver, no transmitter
input: hix5hd2-ir as /devices/virtual/rc/rc0/input0
```

The driver has been in tree since 2014. Nothing here needed writing — the block
simply had no device tree node and the kconfig was off.

## The device tree node

`hi3798mv200.dtsi` had no IR node at all. The vendor tree describes it as

```dts
ir@f8001000 {
	compatible = "hisilicon,hix5hd2-ir";
	reg = <0xf8001000 0x1000>;
	interrupts = <0x00 0x2f 0x04>;
	clocks = <0x0a 0x384>;
	linux,rc-map-name = "rc-hisi";
};
```

which maps onto mainline cleanly. The clock is the interesting part: the vendor
clock provider takes a raw register offset, but the equivalent gate already
exists in `crg-hi3798mv200.c`, in the **sysctrl** half rather than the CRG:

```c
{ HI3798MV200_IR_CLK, "clk_ir", "clk_osc",
	CLK_SET_RATE_PARENT, 0x48, 4, 0, },
```

So the node needs `<&sysctrl HI3798MV200_IR_CLK>`, matching how
`hi3798cv200.dtsi` wires up the same block. `0xf8001000` sits in the always-on
region next to sysctrl (`0xf8000000`) and `gpio5` (`0xf8004000`) — a remote has
to be able to wake a box that is off.

`no power-reg` in the log is harmless: it is the driver noting the absence of
the deprecated `hisilicon,power-syscon` property, which the binding says not to
use in new device trees.

## Kconfig

```
CONFIG_RC_CORE=y
CONFIG_LIRC=y
CONFIG_IR_HIX5HD2=y
CONFIG_IR_NEC_DECODER=y      # plus rc-5, rc-6, jvc, sony, sanyo, sharp,
                             # mce_kbd, xmp, imon, rc-mm
```

`CONFIG_LIRC` matters: without it there is no `/dev/lirc0` and raw pulse
timings cannot be read at all, which makes "is the receiver even wired up"
impossible to answer.

## The decoders start switched off

A fresh `rc0` enables only the `lirc` protocol:

```
# cat /sys/class/rc/rc0/protocols
[lirc]
```

Raw pulses arrive, no scancodes do. Each decoder has to be turned on:

```sh
for p in nec rc-5 rc-6 jvc sony rc-5-sz sanyo sharp mce_kbd xmp imon rc-mm; do
	echo "+$p" > /sys/class/rc/rc0/protocols
done
```

`+all` is rejected — the interface takes one protocol at a time. This does not
survive a reboot; a udev rule does:

```
ACTION=="add", SUBSYSTEM=="rc", KERNEL=="rc0", RUN+="/bin/sh -c \"for p in nec rc-5 rc-6 jvc sony rc-5-sz sanyo sharp mce_kbd xmp imon rc-mm; do echo +$p > /sys/class/rc/%k/protocols; done\""
```

## No keymap

The board device tree sets no `linux,rc-map-name`, so the driver falls back to
`RC_MAP_EMPTY` and no scancode maps to a key. That is deliberate: the remote
this box shipped with has not been identified, and a wrong keymap turns
"unknown scancode" into "wrong keypress", which is harder to debug.

Scancodes are still visible. `rc-core` emits `EV_MSC`/`MSC_SCAN` before it
looks up a keycode, so every press shows up on `/dev/input/event0` whether or
not it maps to anything. Mainline carries `rc-hisi-poplar` and
`rc-hisi-tv-demo` if one of them turns out to match.

## Watching it work

Two independent views, which answer different questions:

| source | shows | answers |
|---|---|---|
| `/dev/lirc0` | raw pulse/space timings, µs | is the receiver wired up at all |
| `/dev/input/event0` | scancodes, after the decoders | which protocol is this remote |

`scripts/tvbox-panel.py` displays both live in a browser, with the pulse train
drawn as a waveform and NEC decoded inline. Pulses but no scancodes means the
decoders are off; nothing at all means the receiver or its clock is not up.

For a quick check without the panel, `ir-keytable -t` does the same from a
terminal, and the `rc-feedback` LED trigger gives a hardware-only answer:

```sh
echo rc-feedback > /sys/class/leds/red:activity/trigger
```

The LED then blinks on every reception, so a remote can be tested with no
console at all.
