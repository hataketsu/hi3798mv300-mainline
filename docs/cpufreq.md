# CPU frequency

The cores scale between **600 MHz and 1.2 GHz**, and `schedutil` drives them:

```
# cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_available_frequencies
600000 900000 1200000
# cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq      # idle
900000
# cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq      # under load
1200000
```

sysbench at four threads, with the governor pinned to `userspace`:

| requested | events/s | ratio |
|---|---|---|
| 600 MHz | 480.9 | 1.000 |
| 900 MHz | 723.4 | 1.504 |
| 1200 MHz | 965.7 | 2.008 |

Linear in the requested frequency to within 0.5%, which is the point — for
most of this port's life the numbers in that column were identical at every
setting.

## What was wrong

`cpufreq` accepted every request, moved a clock mux, updated its own
bookkeeping, and had no effect on the CPU whatsoever. Every layer lied
convincingly, so it is worth recording how the real answer was reached.

Timing a loop of known cycle cost against the architected timer, which runs at
a fixed 24 MHz and is not affected by any of this:

```c
uint64_t t0 = cntvct_el0();
__asm__ volatile("1: subs %0, %0, #1\n bne 1b\n" : "+r"(N) :: "cc");
uint64_t t1 = cntvct_el0();
```

| loop body | measured | implied clock |
|---|---|---|
| `subs`+`bne` — 1 cycle | 1189 Miter/s | 1188.8 MHz |
| plus one `nop` — 2 cycles | 596 Miter/s | 1193.0 MHz |
| plus two `nop` — 3 cycles | 399 Miter/s | 1196.0 MHz |

Adding one instruction halves the rate exactly, so the loop is issue-limited
and the cycle model holds; the register arithmetic below gives 1200 MHz, so
the loop costs 1.006 cycles per iteration rather than exactly one. That
correction is applied to every figure in this file.

Forcing the governor anywhere made no difference:

| requested | apll claimed | measured |
|---|---|---|
| 1600 MHz | 1596 MHz | 1194 Miter/s |
| 600 MHz | 600 MHz | 1187 Miter/s |
| 400 MHz | 600 MHz | 1185 Miter/s |
| 1600 MHz | 1596 MHz | 1184 Miter/s |

## Where the frequency actually comes from

The cores are fed by a dedicated **CPU PLL**, and the vendor changes their
frequency by retuning it — not by moving any mux and not by writing the PLL
config registers. `mpu_clk_set_rate()` in
`source/msp/drv/pm/pm_v200/clock_mpu.c` of the HiSTBLinuxV100R005C00 BSP:

```c
postdiv1 = (PERI_CRG_PLL0.cpu_pll_cfg0_apb >> 24) & 0x7;
postdiv2 = (PERI_CRG_PLL0.cpu_pll_cfg0_apb >> 28) & 0x7;
refdiv   = (PERI_CRG_PLL1.cpu_pll_cfg1_apb >> 12) & 0x3f;
fbdiv    = (rate * postdiv1 * postdiv2 * refdiv) / 24000;

PERI_CRG105.apll_tune_int_cfg  = fbdiv;
PERI_CRG106.apll_tune_frac_cfg = 0;
PERI_CRG107.apll_tune_step_int = 1;
PERI_CRG109.apll_tune_mode     = 1;
PERI_CRG109.apll_tune_en       = 0;   /* strobe */
PERI_CRG109.apll_tune_en       = 1;
while (PERI_CRG165.apll_tune_busy) udelay(10);
```

`CRGn` sits at CRG base + `n * 4`, so with the CRG at `0xf8a22000`:

| register | address | |
|---|---|---|
| `PERI_CRG_PLL0` | `0xf8a22000` | postdiv1 [26:24], postdiv2 [30:28] |
| `PERI_CRG_PLL1` | `0xf8a22004` | refdiv [17:12], fbdiv [11:0] |
| `PERI_CRG18` | `0xf8a22048` | source select and handshake |
| `PERI_CRG105` | `0xf8a221a4` | `apll_tune_int_cfg` — the new fbdiv |
| `PERI_CRG106`–`109` | `0xf8a221a8`–`b4` | frac, step, mode and enable |
| `PERI_CRG165` | `0xf8a22294` | `apll_tune_int` [11:0], `apll_tune_busy` [13] |

Reading them back at 1.2 GHz:

```
PLL0   = 0x12000000   postdiv1 = 2, postdiv2 = 1
PLL1   = 0x0000210a   refdiv   = 2, fbdiv(reg) = 266
CRG105 = 0x000000c8   apll_tune_int_cfg = 200
CRG165 = 0x000010c8   apll_tune_int     = 200

rate = 200 * 24 MHz / (2 * 1 * 2) = 1200 MHz
```

The tune register overrides the divider in `PLL1`, which is why the raw
`fbdiv` of 266 does not describe the running clock, and why `recalc_rate` has
to read `CRG165` rather than the config register the bootloader left behind.

`hpll` being 1188 MHz is a coincidence that led this investigation astray for
a while. It has no consumers and is not involved.

## The source select, and why moving it did nothing

`PERI_CRG18` at `0xf8a22048` is the selector the mainline CRG driver already
describes:

```
bits [2:0]  cpu_freq_sel_cfg_crg   0 = CPU PLL, 1 = 200m, 2 = 800m, 3 = 1350m,
                                   4 = 24m,     5 = 1200m, 6 = 400m, 7 = 600m
bit  [9]    cpu_begin_cfg_bypass
bit  [10]   cpu_sw_begin_cfg
bit  [12]   cpu_clk_pctrl
```

That mapping is exactly `cpu_mux_p[]` in `crg-hi3798mv200.c`, the offset is
right, and the live value is `0x600` — source 0, both handshake bits already
set. Moving the field still changed nothing measurable, and the vendor never
writes it either: `mpu_clk_set_rate()` only reads it back, in
`mpu_clk_get_rate()`, to report which source is live.

This matters for more than curiosity. The mux parents are exactly the old OPP
table, so the clock core would pick one whenever a request matched it exactly,
try to reparent, and leave the rate unchanged. That is why the first working
version of the retune only moved between two frequencies: 600 and 1200 MHz
were the only points the PLL could hit exactly *and* win the tie against a
fixed parent.

```
before CLK_SET_RATE_NO_REPARENT
  requested 1200 MHz  CRG105 = 200   measured 1197 MHz   <- PLL
  requested  800 MHz  CRG105 = 200   measured 1199 MHz   <- mux, no effect
  requested  600 MHz  CRG105 = 100   measured  598 MHz   <- PLL
  requested  400 MHz  CRG105 = 100   measured  597 MHz   <- mux, no effect
  requested  200 MHz  CRG105 = 100   measured  599 MHz   <- mux, no effect
```

`CLK_SET_RATE_NO_REPARENT` on `clk_cpu` sends every request to the PLL.

## Which rates are reachable

With the dividers the bootloader programs, `rate = fbdiv * 24 MHz / 4`, so the
PLL lands on whole multiples of **6 MHz** and nothing else. Of the old OPP
table only 600 and 1200 MHz qualified; 24, 200, 400 and 800 MHz did not, and
asking for them produced a neighbouring rate that `cpufreq` would then report
as if it were exact.

The table is now 600, 900 and 1200 MHz.

## Waiting for the tune to finish

`apll_tune_step_int` is 1, so the PLL walks to its target one divider step at
a time — and `apll_tune_busy` drops before the walk has finished. Polling only
the busy bit leaves `recalc_rate` reading whatever the PLL was passing through:

```
requested 1200 MHz   cpuinfo_cur_freq  924000    measured 1197 MHz
requested  900 MHz   cpuinfo_cur_freq 1182000    measured  886 MHz
requested  600 MHz   cpuinfo_cur_freq  876000    measured  596 MHz
```

Waiting for `apll_tune_int` in `CRG165` to reach the requested `fbdiv` fixes
it, and everything then agrees:

```
requested 1200  CRG105=200  CRG165=200  cpuinfo=1200000   measured 1199 MHz
requested  900  CRG105=150  CRG165=150  cpuinfo= 900000   measured  901 MHz
requested  600  CRG105=100  CRG165=100  cpuinfo= 600000   measured  597 MHz
```

## Why it stops at 1.2 GHz

The part will run at 1.35 and 1.6 GHz, and the OPP table used to say so. It
cannot here, because nothing in mainline drives the supply.

There is no PMIC on this board — six discrete buck converters, and the SoC
trims them by driving a PWM into the feedback node, which is why the vendor
code reaches for PWM registers instead of an I²C regulator. The PWMs live in
the same PMC block at `0xf8a23000` as the temperature sensor:

| register | address | |
|---|---|---|
| `PERI_PMC6` | `0xf8a23018` | CPU supply on most boards |
| `PERI_PMC7` | `0xf8a2301c` | CPU supply on **this** board |
| `PERI_PMC8` | `0xf8a23020` | core supply |

Duty is bits [31:16], period bits [15:0], and

```
period = ((vmax - vmin) * PWM_CLASS) / PWM_STEP + 1
duty   = ((vmax - volt) * PWM_CLASS) / PWM_STEP + 1
                                 PWM_CLASS = 2, PWM_STEP = 5 mV
```

Higher duty means *lower* voltage. All three read a period of 221, which pins
the limits to the one table in `hi_drv_pmoc.h` that produces it —
`CPU_VMAX 1250`, `CPU_VMIN 700` — and recovers the operating point:

| register | raw | duty | voltage |
|---|---|---|---|
| `PERI_PMC6` | `0x003d00dd` | 61 | 1100 mV |
| `PERI_PMC7` | `0x004100dd` | 65 | **1090 mV** |
| `PERI_PMC8` | `0x007900dd` | 121 | 950 mV |

So the cores sit on about 1.09 V, which the bootloader chose for the 1.2 GHz
it also programmed — its own reg table writes `0x006900dd` (990 mV) and then
raises the rail before handing over, which is the frequency/voltage ordering
the vendor DVFS code enforces. Running slower on that voltage is free.
Running faster on it is not, so the ceiling is the operating point handed
over rather than the PLL's capability.

### The shared rail

`device_volt_scale()` has a board quirk that matters:

```c
#if defined(CHIP_TYPE_hi3798mv300)
	if (((SC_GENm[5] >> 29) & 0x07) == 0x2) /* dms board */
	{
		/* The dms board cpu volt use core pwm */
		pwm_reg = PERI_PMC7;
	}
#endif
```

and `cpu_volt_scale()` then sets `cur_core_volt = volt` for the same board.
This unit reports `dms board` — the auxiliary code prints it during every
serial boot — so **the CPU and the SoC core share a rail**. Lowering the CPU
voltage for a lower OPP would lower the core voltage with it, taking DDR and
the eMMC controller along. That is a much bigger claim than undervolting a CPU
with its own supply, and it is why this port scales frequency only.

## What is still missing

A `pwm-regulator` on the PMC block, with `opp-microvolt` and a `cpu-supply`
in the device tree. Mainline has the regulator driver; what is missing is a
PWM driver for that block. Until then the voltage is fixed and the OPP table
has to live under it — the shared rail above means getting this wrong costs
more than a hang.

## The vendor does not use cpufreq either

```
CONFIG_CPU_FREQ=y
# CONFIG_CPUFREQ_DT is not set
CONFIG_CPU_FREQ_DEFAULT_GOV_USERSPACE=y
```

and its device tree gives the CPUs no `clocks` and no `operating-points` at
all:

```dts
CPU0: cpu@0 {
	compatible = "arm,cortex-a53";
	device_type = "cpu";
	reg = <0>;
	enable-method = "psci";
};
```

Frequency is handled instead by a driver in the media stack,
`source/msp/drv/pm/hi_cpufreq.c`, handing off to `pm_v200/hi_dvfs.c`. That
does full DVFS: per-corner SVB voltage tables plus per-chip OTP trim
(`cpu_volt_svb_default`, `cpu_volt_otp_adjust`), an AVS pass, and enforced
ordering — raise voltage, wait 10 ms, then raise frequency; going down, lower
frequency, wait, then lower voltage.

## Effect on the rest of this port

Idle now sits at 900 MHz instead of 1.2 GHz, and the sustained-load
temperature dropped from 70 °C to 64 °C. See [thermal.md](thermal.md).

Benchmark figures elsewhere in this repo were taken at a fixed 1200 MHz:

| | |
|---|---|
| sysbench, 1 thread | 242 events/s |
| sysbench, 4 threads | 963 events/s (3.97× scaling) |
| AES-256-CBC, 8 KiB | 556 MB/s |
| SHA256, 8 KiB | 471 MB/s |
| memcpy, 1 thread | 2963 MB/s |
| eMMC read / write | 168 / 31 MB/s |

They still stand — 1200 MHz is still the top of the table — but anything
measured under `schedutil` will now depend on how long the governor takes to
ramp.

## Patches
* [`0010`](../patches/kernel/0010-clk-hisilicon-hi3798mv200-retune-the-CPU-PLL-on-a-rate-change.patch)
  — the PLL ops and the mux flag
* [`0011`](../patches/kernel/0011-arm64-dts-hisilicon-fit-the-Hi3798MV200-OPPs-to-the-CPU-PLL.patch)
  — the OPP table and the thermal cooling map
