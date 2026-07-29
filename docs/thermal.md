# Temperature sensor

The SoC reports its own temperature now:

```
# cat /sys/class/thermal/thermal_zone0/type
soc-thermal
# cat /sys/class/thermal/thermal_zone0/temp
62000
```

Moonraker picks that up with no configuration, so a Klipper install shows the
SoC temperature in Fluidd:

```
# curl -s localhost:7125/machine/proc_stats | jq .result.cpu_temp
62.0
```

## What it measures

| state | temperature |
|---|---|
| idle | 54–56 °C |
| four-thread load, 180 s | rises 62 → 64 °C and plateaus |
| 30 s after load | 55 °C |

Trip points are 95 °C passive and 105 °C critical, taken from the vendor
device tree, so there is about 30 °C of headroom at full tilt. The case gets
warm to the touch, which is what a fanless box at 64 °C die temperature feels
like; it is not close to trouble.

These numbers are lower than they used to be: the cores now idle at 900 MHz
rather than a permanent 1.2 GHz. See [cpufreq.md](cpufreq.md).

## Why mainline's hisi_thermal does not fit

`drivers/thermal/hisi_thermal.c` matches `hisilicon,tsensor` (Hi6220) and
`hisilicon,hi3660-tsensor`. This is a different block: different registers,
different sampling scheme, different conversion. Nothing about it is
documented and there is no binding — the vendor device tree carries

```dts
hisi-sensor@0 {
	compatible = "arm,hisi-thermal";
	#thermal-sensor-cells = <0x01>;
};
```

with no `reg` at all, because the vendor driver hardcodes the address.

The address and the maths come from
`drivers/hisilicon/thermal/hi3798cvx-thermal.c` in the HiSilicon
HiSTBLinuxV100R005C00 BSP, which is public — see
[bootrom-serial.md](bootrom-serial.md) for where.

## How it reads

The sensor sits in the PMC block at `0xf8a23000`:

| offset | |
|---|---|
| `0x28` | control — write `0x6005` to enable, `0` to stop |
| `0x30`–`0x3c` | four words, two 10-bit samples each |

Enable, wait ~16 ms to settle, read eight samples, average, then a linear fit:

```
millicelsius = ((avg - 125) * 165 / 806 - 40) * 1000
```

Readings outside −40…150 °C are rejected rather than reported. The zone's
critical trip powers the board off, and one spurious sample should not be able
to do that.

Before the driver existed the same thing could be done by hand, which is how
the numbers above were first taken:

```sh
devmem 0xf8a23028 32 0x6005
sleep 0.05
# sum (v & 0x3ff) + ((v >> 16) & 0x3ff) over 0xf8a23030..0xf8a2303c
devmem 0xf8a23028 32 0x0
```

## Cooling map

The passive trip throttles the CPU:

```
# cat /sys/class/thermal/cooling_device0/type
cpufreq-cpu0
# cat /sys/class/thermal/cooling_device0/max_state
2
```

Three states, one per operating point — 1200, 900 and 600 MHz. This only
became possible once the CPU clock could actually be changed; for most of this
port's life cpufreq accepted rate changes without acting on them, so a passive
trip had nothing to act through. See [cpufreq.md](cpufreq.md).

It has yet to fire. Four threads for three minutes:

```
  t+ 15s  1200 MHz  62 C  cooling 0
  t+ 90s  1200 MHz  63 C  cooling 0
  t+180s  1200 MHz  64 C  cooling 0
```

64 °C against a 95 °C trip, so the map is insurance rather than something the
box relies on. The critical trip still protects the part.

Scaling did lower the numbers at the top of this page, though: idle now sits at
900 MHz instead of 1.2 GHz, and sustained load peaks at 64 °C rather than the
70 °C measured before.

## Patches
* [`0008`](../patches/kernel/0008-thermal-hisilicon-add-the-Hi3798MV200-MV300-temperature-sensor.patch)
  — the driver, `drivers/thermal/hi3798-thermal.c`
* [`0009`](../patches/kernel/0009-arm64-dts-hisilicon-add-the-Hi3798MV200-tsensor-and-thermal-zone.patch)
  — the `tsensor` node and its zone

Both apply to a clean v7.2-rc5 tree on top of the earlier patches in that
directory.
