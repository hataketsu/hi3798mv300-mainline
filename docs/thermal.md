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
| idle | 56–58 °C |
| four-thread load, 90 s | rises 65 → 70 °C and plateaus |
| 20 s after load | 62 °C |

Trip points are 95 °C passive and 105 °C critical, taken from the vendor
device tree, so there is about 25 °C of headroom at full tilt. The case gets
warm to the touch, which is what a fanless box at 70 °C die temperature feels
like; it is not close to trouble.

Worth knowing, because the CPU has no idle states worth the name here — see
[the cpufreq section](#no-cooling-map-yet).

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

## No cooling map yet

The zone has trips but no cooling device, because there is nothing to throttle
with: **cpufreq on this SoC accepts frequency changes and does not act on
them.**

```
# cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq
1200000
# cat /sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_cur_freq
1596000
```

`scaling_cur_freq` is what was asked for; `cpuinfo_cur_freq` and
`/sys/kernel/debug/clk/clk_summary` both say the hardware never moves off
1.596 GHz. Benchmarks confirm it — sysbench returns 242.3 events/s whether the
governor is pinned to 600 MHz or 1.6 GHz, and a shell loop takes 12.1 s either
way. Monitoring tools that read `scaling_cur_freq`, htop included, show a
frequency that changes; it is not the one the CPU is running at.

The cause is in `drivers/clk/hisilicon/crg-hi3798mv200.c`. `clk_cpu` is a mux
whose parents match the OPP table exactly:

```c
static const char *const cpu_mux_p[] = { "apll", "200m", "800m", "1350m",
	"24m", "1200m", "400m", "600m" };
/*                   1600     200     800     1350
                       24    1200     400      600  */

{ HI3798MV200_CPU_CLK, "clk_cpu", cpu_mux_p, ARRAY_SIZE(cpu_mux_p),
	CLK_SET_RATE_PARENT, 0x48, 0, 3, CLK_MUX_ROUND_CLOSEST, cpu_mux_table },
```

so the hardware plainly intends the mux to be switched. But `clk_cpu` carries
`CLK_SET_RATE_PARENT`, and `apll` is a PLL with a working `.set_rate`, so the
clock core keeps the mux where it is and tries to reprogram the PLL instead of
reparenting. CRG `0x48` bits [2:0] stay at 0 — `apll` — no matter what is
requested:

```
# devmem 0xf8a22048 32
0x00000600      bits[2:0] = 0 = apll
```

Dropping `CLK_SET_RATE_PARENT` from that entry is the obvious candidate fix.
It is untested here: the box does real work now, the benefit is idle power
rather than safety given the 25 °C of headroom, and changing the CPU clock mux
on a running system is not a free experiment. A passive trip and a
`cooling-maps` entry should follow once it works.

## Patches

* [`0008`](../patches/kernel/0008-thermal-hisilicon-add-the-Hi3798MV200-MV300-temperature-sensor.patch)
  — the driver, `drivers/thermal/hi3798-thermal.c`
* [`0009`](../patches/kernel/0009-arm64-dts-hisilicon-add-the-Hi3798MV200-tsensor-and-thermal-zone.patch)
  — the `tsensor` node and its zone

Both apply to a clean v7.2-rc5 tree on top of the earlier patches in that
directory.
