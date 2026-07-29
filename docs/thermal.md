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

and `clk_cpu` carries `CLK_SET_RATE_PARENT`, so with `apll` offering a working
`.set_rate` the clock core keeps the mux where it is and reprograms the PLL
instead of reparenting. CRG `0x48` bits [2:0] stay at 0 — `apll` — whatever is
requested:

```
# devmem 0xf8a22048 32
0x00000600      bits[2:0] = 0 = apll
```

### Dropping that flag does not help

Removing `CLK_SET_RATE_PARENT` and adding `#cooling-cells` plus a
`cooling-maps` entry was tried, built and booted. Everything downstream did
exactly what it was supposed to:

```
CRG 0x48 = 0x00000603      bits[2:0] = 3      the mux moved
clk_cpu  = 1350000000                          the core believes it
cooling_device0 type=cpufreq-cpu0 max=7        eight states, one per OPP
```

and the mux tracked every request — 3, 5, 2, 7, 6 for 1600, 1200, 800, 600,
400 MHz, with `cpuinfo_cur_freq` agreeing each time.

The CPU ignored all of it:

| requested | mux | sysbench |
|---|---|---|
| 1600 MHz | 3 | 241.3 events/s |
| 1200 MHz | 5 | 242.2 events/s |
| 800 MHz | 2 | 242.2 events/s |
| 600 MHz | 7 | 242.2 events/s |
| 400 MHz | 6 | 241.1 events/s |

`apll` also fell to 798 MHz once it stopped being the mux's selected parent,
with no consumers left in `clk_summary` — and performance still did not move.
If the cores were fed by `apll`, halving it would have halved throughput.

So `clk_cpu` is not the clock the CPU runs on, and CRG `0x48` bits [2:0] drive
something else. The CRG driver's description is wrong somewhere, and the real
CPU clock has not been found. Everything measured across this port — before
the change, after it, at every OPP — comes out at ~242 events/s, which is
about 1.6 GHz on this core.

The change was reverted. It bought nothing measurable and left `schedutil`
flipping an unidentified clock mux during normal operation, which is a poor
trade on a box that runs Klipper. Anyone picking this up again should start by
finding what actually feeds the cores, not by adjusting flags on `clk_cpu`.

## Patches

* [`0008`](../patches/kernel/0008-thermal-hisilicon-add-the-Hi3798MV200-MV300-temperature-sensor.patch)
  — the driver, `drivers/thermal/hi3798-thermal.c`
* [`0009`](../patches/kernel/0009-arm64-dts-hisilicon-add-the-Hi3798MV200-tsensor-and-thermal-zone.patch)
  — the `tsensor` node and its zone

Both apply to a clean v7.2-rc5 tree on top of the earlier patches in that
directory.
