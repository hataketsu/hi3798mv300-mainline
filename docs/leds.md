# Front-panel LEDs

Two LEDs, red and blue, both on `gpio5` — the PL061 at `0xf8004000`. Working:

```
/sys/class/leds/red:activity
/sys/class/leds/blue:power
```

| LED | gpio5 line | polarity | default trigger |
|---|---|---|---|
| red | 0 | active high | `cpu` |
| blue | 2 | active high | `heartbeat` |

Line 1 is an output too, and is deliberately left alone. See below.

## Why gpio5 and not gpio0-gpio9

`gpio5` is the odd bank. The other nine PL061s sit together at
`0xf8b2xxxx` in the peripheral domain; `gpio5` is at `0xf8004000`, in the
always-on block next to sysctrl (`0xf8000000`) and the IR receiver
(`0xf8001000`). That is exactly where a power indicator belongs: it has to
stay lit in standby, when the peripheral domain is down.

It matters for a second reason. Every other bank carries

```dts
gpio-ranges = <&ioconfig 0 0 3>, <&ioconfig 3 3 4>;
```

which makes gpiolib wait for a pinctrl driver that mainline does not have —
`hisilicon,hi3798mv200-ioconfig` matches nothing, so gpio0-gpio9 sit in
deferred probe forever. `gpio5` has no `gpio-ranges`, so it probes on its own:

```
pl061_gpio f8004000.gpio: PL061 GPIO chip registered
```

The LEDs work today only because of that accident. Anything on the other banks
needs the pinctrl driver written first.

## Finding them

Neither LED is described anywhere. The vendor device tree has no LED node, and
the vendor userspace hides the pin numbers in a closed library — see
[the vendor side](#what-the-vendor-does) below. So they were found by
measurement.

### Step 1: read every bank

`GPIODIR` (offset `0x400`, 1 = output) and `GPIODATA` (offset `0x3fc` reads all
eight lines) on all ten banks, from a running kernel:

```
bank    base        GPIODIR  GPIODATA  outputs
gpio0   0xf8b20000  0x00     0x02      —
gpio1   0xf8b21000  0x00     0x00      —
gpio2   0xf8b22000  0x00     0x20      —
gpio3   0xf8b23000  0x00     0x00      —
gpio4   0xf8b24000  0x08     0x0c      line 3, high
gpio5   0xf8004000  0x03     0x02      line 0 low, line 1 high
gpio6   0xf8b26000  0x00     0x00      —
gpio7   0xf8b27000  0x00     0x08      —
gpio8   0xf8b28000  0x00     0x40      —
gpio9   0xf8b29000  0x00     0x00      —
```

Only `gpio4` and `gpio5` have outputs configured at all. `gpio5` having two
adjacent ones, in the always-on block, is a strong hint.

### Step 2: drive them and look at the box

PL061 `GPIODATA` is address-masked: bits `[9:2]` of the offset select which
lines an access touches, so writing one line needs no read-modify-write and
cannot race the kernel. Line *n* lives at offset `(1 << n) << 2`.

```
# busybox devmem 0xf8004004 32 0x01   # line 0 high
# busybox devmem 0xf8004008 32 0x02   # line 1 high
```

Walking all four combinations while watching the box:

| line 0 | line 1 | visible |
|---|---|---|
| 0 | 0 | nothing |
| **1** | 0 | **red** |
| 0 | 1 | nothing |
| **1** | 1 | **red** |

Red is line 0, active high. Line 1 lights nothing.

### Step 3: the blue LED was invisible to the scan

The register scan could never have found the blue LED, because the bootloader
leaves its line an **input**. A pin in input mode is high impedance — the SoC
reads it and drives nothing, so the LED gets no current, and `GPIODIR` shows
nothing worth investigating. Under the stock firmware it is Android that
switches the pin to output, long after the bootloader is done.

Finding it meant turning each remaining line of `gpio5` into an output in turn
and watching. Line 2 lit the blue LED.

`scripts/tvbox-panel.py` exists for exactly this: it exposes every line of
every bank as a button that pulses the pin high for two seconds and then
restores its original direction, so an unidentified pin is never left driven.

## Line 1 is left unassigned

Line 1 is an output that the bootloader sets high and that lights no LED.
Until what it does is known it is not described in the device tree — handing an
unidentified output to a driver risks toggling something that matters, and a
`heartbeat` trigger on it would do so continuously.

`gpio4` line 3 is in the same category: an output the bootloader leaves high,
purpose unknown, deliberately untouched.

## What the vendor does

The vendor stack turns out to be a dead end worth documenting, so nobody
repeats the search.

`/vendor/bin/gpio-led` is the only binary with a promising name. It is a 15 KB
stripped ARM32 ELF, started from `init.bigfish.rc`:

```
service vendor.gpio-led /vendor/bin/gpio-led
    class main
    user system
    group system
    oneshot
```

Its imports are just `HI_UNF_GPIO_Init`, `HI_UNF_GPIO_DeInit`,
`property_get`, `sleep` and `strcmp`, and the whole program disassembles to:

```c
int main(void) {
    char val[96] = {0};
    int ret = HI_UNF_GPIO_Init();
    if (ret) ALOGE("%s: %d ErrorCode=0x%x", "gpio-led.cpp", 43, ret);
    do {
        property_get("service.bootanim.exit", val, "0");
        sleep(1);
    } while (strcmp(val, "1") != 0);
    HI_UNF_GPIO_DeInit();
    return 0;
}
```

It waits for the boot animation to finish and calls no function that sets a
pin. The pin numbers live inside `libhi_msp.so`, behind the `HI_UNF_GPIO`
abstraction, and the vendor device tree has no LED node at all. Measuring the
hardware was faster than following that thread.

The SoC also has a dedicated front-panel controller — the vendor tree names
interrupts `keyled_ct1642` and `keyled_std` (both SPI 48) and the CRG has
`HI3798MV200_LEDC_CLK` — but this board does not use it. Its two LEDs are
plain GPIOs.

## Changing what they show

Any trigger the kernel offers works:

```sh
echo heartbeat  > /sys/class/leds/red:activity/trigger   # kernel alive
echo mmc0       > /sys/class/leds/blue:power/trigger     # eMMC activity
echo default-on > /sys/class/leds/blue:power/trigger     # solid, like stock
echo none       > /sys/class/leds/red:activity/trigger   # manual
echo 1          > /sys/class/leds/red:activity/brightness
```

The device tree defaults are `cpu` for red and `heartbeat` for blue. Stock
firmware instead lights blue solid once Android finishes booting; `default-on`
reproduces that.

Note that the sysfs directory name comes from the device tree's `color` and
`function`, so changing `function` renames it. Anything matching on the old
name — a udev rule, a script — breaks silently. Match on `red:*` instead.
