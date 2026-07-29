# CPU frequency

The cores run at a fixed **1200 MHz** and nothing in Linux can change it. The
`cpufreq` interface accepts requests, moves a clock mux, updates its own
bookkeeping, and has no effect on the CPU whatsoever.

This is worth documenting carefully because every layer lies convincingly.

## What the interface claims

```
# cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_available_frequencies
24000 200000 400000 600000 800000 1200000 1350000 1600000
# cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq
1200000
# cat /sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_cur_freq
1596000
```

`htop` and anything else reading `scaling_cur_freq` will happily show a
frequency that moves around. None of it is real.

## What the CPU actually does

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
| plus one `nop` — 2 cycles | 596 Miter/s | **1193.0 MHz** |
| plus two `nop` — 3 cycles | 399 Miter/s | **1196.0 MHz** |

Adding one instruction halves the rate exactly, so the loop is issue-limited
and the cycle model holds. The register arithmetic below gives 1200 MHz, so the
loop costs 1.006 cycles per iteration rather than exactly one.

Forcing the governor anywhere makes no difference:

| requested | apll | measured |
|---|---|---|
| 1600 MHz | 1596 MHz | 1194 Miter/s |
| 600 MHz | 600 MHz | 1187 Miter/s |
| 400 MHz | 600 MHz | 1185 Miter/s |
| 1600 MHz | 1596 MHz | 1184 Miter/s |

`sysbench` agrees — 241–242 events/s at every setting, which is the same figure
measured at every point in this port.

## Where 1200 MHz comes from

The cores are fed by a dedicated **CPU PLL**, and the vendor changes their
frequency by retuning it — not by moving any mux. `mpu_clk_set_rate()` in
`source/msp/drv/pm/pm_v200/clock_mpu.c` reads the dividers, computes a new
feedback divider, and drives a tune sequence:

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
| `PERI_CRG165` | `0xf8a22294` | `apll_tune_int`, `apll_tune_busy` |

Reading them back on this box:

```
PLL0   = 0x12000000   postdiv1 = 2, postdiv2 = 1
PLL1   = 0x0000210a   refdiv   = 2, fbdiv(reg) = 266
CRG105 = 0x000000c8   apll_tune_int_cfg = 200
CRG165 = 0x000010c8   apll_tune_int     = 200

rate = 200 * 24 MHz / (2 * 1 * 2) = 1 200 000 kHz
```

The tune register overrides the divider in `PLL1`, which is why the raw `fbdiv`
of 266 does not describe the running clock. **1200 MHz**, against a measured
1188.8 MHz assuming exactly one cycle per loop iteration — 1.006 cycles per
iteration, which is what that loop costs.

`hpll` being 1188 MHz is a coincidence and led this investigation astray for a
while. It has no consumers and is not involved.

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

That mapping is exactly `cpu_mux_p[]` in `crg-hi3798mv200.c`, and the offset is
right too. Live value is `0x600`: source 0, and both handshake bits already
set.

So the mainline driver has the correct register and the correct parent list,
and moving the field still changed nothing measurable. The vendor never touches
that field either — `mpu_clk_set_rate()` only reads it, in `mpu_clk_get_rate()`,
to know which source to report. Every frequency change the vendor makes is a
PLL retune with the selector left on 0.

What mainline is missing is the retune sequence. `hisi_pll_set_rate()` writes
the PLL registers at CRG offset 0 directly and never drives `CRG105`–`CRG109`
or waits on `apll_tune_busy`, so the rate it reports — 1596, 1200, 600 MHz as
`schedutil` moves it — is bookkeeping with nothing behind it.

## The vendor does not do this either

The vendor kernel does not use `cpufreq-dt`:

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
`source/msp/drv/pm/hi_cpufreq.c`, which hands off to `pm_v200/hi_dvfs.c`. That
does real DVFS, and the frequency is the easy half:

* CPU supply voltage is a **PWM** in the PMC block — `PWM_CPU = PERI_PMC6`, the
  same block at `0xf8a23000` as the temperature sensor
* voltages come from per-corner SVB tables plus **per-chip OTP trim**
  (`cpu_volt_svb_default`, `cpu_volt_otp_adjust`), with an AVS pass afterwards
* ordering is enforced: raise voltage, wait 10 ms, then raise frequency; going
  down, lower frequency, wait, then lower voltage
* there is a board-specific branch keyed on `SC_GENm[5] >> 29 == 0x2`, the
  "dms board" this unit reports

None of that exists in mainline. The `cpu_opp_table` in `hi3798mv200.dtsi`
carries no `opp-microvolt` and there is no `cpu-supply` regulator, so even if
the clock could be changed the voltage would stay where firmware left it.

## The supply is a PWM into a buck, not a PMIC

There is no PMIC on this board — six discrete buck converters, and the SoC
trims them by driving a PWM into the feedback node. That is why the vendor
code reaches for PWM registers rather than an I²C regulator.

The PWMs live in the same PMC block as the temperature sensor:

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

Higher duty means *lower* voltage. Reading the registers back gives a period
of 221 on all three, which pins the limits to the one table in
`hi_drv_pmoc.h` that produces it — `CPU_VMAX 1250`, `CPU_VMIN 700` — and lets
the current operating point be recovered:

| register | raw | duty | voltage |
|---|---|---|---|
| `PERI_PMC6` | `0x003d00dd` | 61 | 1100 mV |
| `PERI_PMC7` | `0x004100dd` | 65 | **1090 mV** |
| `PERI_PMC8` | `0x007900dd` | 121 | 950 mV |

So the cores sit at 1188 MHz on about 1.09 V. The bootloader's own reg table
writes `0x006900dd` to `PERI_PMC7` — duty 105, 990 mV — so the stock
bootloader raises the CPU rail before handing over to Linux, which is the
frequency/voltage ordering the vendor DVFS code enforces.

### Which rail is a problem here

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
serial boot — so **the CPU and the SoC core share a rail**. Dropping the CPU
voltage for a lower OPP would drop the core voltage with it, which is a much
bigger claim than undervolting a CPU that has its own supply.

## What it would take

1. Implement the PLL retune. The registers and the sequence are known — see
   above — but `hisi_pll_set_rate()` does not use them, so a `clk_set_rate()`
   on the CPU clock updates bookkeeping and nothing else.
2. Wire the supply up as a `pwm-regulator` — mainline already has the driver;
   what is missing is a PWM driver for the PMC block — and give the OPP table
   `opp-microvolt` plus a `cpu-supply`. Without that a frequency change is
   either pointless or unsafe.
3. Account for the shared rail on this board before lowering any voltage.
4. Note that the OPP table's 1600 MHz entry is fiction — the part runs 1188.

The payoff is idle power on a mains-powered box that peaks at 70 °C against a
95 °C trip. See [thermal.md](thermal.md). It is not worth doing before someone
answers (1).

## Consequences for the rest of this port

Benchmark figures elsewhere are at 1200 MHz, not the 1.6 GHz the OPP table
advertises:

| | |
|---|---|
| sysbench, 1 thread | 242 events/s |
| sysbench, 4 threads | 963 events/s (3.97× scaling) |
| AES-256-CBC, 8 KiB | 556 MB/s |
| SHA256, 8 KiB | 471 MB/s |
| memcpy, 1 thread | 2963 MB/s |
| eMMC read / write | 168 / 31 MB/s |
