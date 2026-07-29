# CPU frequency

The cores run at a fixed **1188 MHz** and nothing in Linux can change it. The
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
| `subs`+`bne` — 1 cycle | 1189 Miter/s | **1188.8 MHz** |
| plus one `nop` — 2 cycles | 596 Miter/s | **1193.0 MHz** |
| plus two `nop` — 3 cycles | 399 Miter/s | **1196.0 MHz** |

Adding one instruction halves the rate exactly, so the loop is issue-limited
and the cycle model holds. The answer is 1188 MHz.

Forcing the governor anywhere makes no difference:

| requested | apll | measured |
|---|---|---|
| 1600 MHz | 1596 MHz | 1194 Miter/s |
| 600 MHz | 600 MHz | 1187 Miter/s |
| 400 MHz | 600 MHz | 1185 Miter/s |
| 1600 MHz | 1596 MHz | 1184 Miter/s |

`sysbench` agrees — 241–242 events/s at every setting, which is the same figure
measured at every point in this port.

## Where 1188 MHz comes from

Exactly one clock in the tree matches:

```
apll  1596000000     bpll  1000000000     dpll   464000000
vpll   900000000     hpll  1188000000     epll   858000000
qpll   800000000
```

`hpll` is 1188 MHz — 0.05 % from the measurement. It never moves, and
`clk_summary` shows it with no consumers at all.

Meanwhile `apll`, which the CRG driver says feeds `clk_cpu`, gets reprogrammed
constantly by `schedutil` — 1596 MHz one moment, 600 or 1200 the next — with no
effect on anything. Two reads seconds apart:

```
apll  1596000000  ...  clk_cpu  1596000000
apll  1200000000  ...  clk_cpu  1200000000
```

So `drivers/clk/hisilicon/crg-hi3798mv200.c` describes `clk_cpu` as a mux over
`apll` and a set of fixed clocks, and that description does not correspond to
the hardware feeding the cores.

## Switching the mux does not help either

The obvious fix — drop `CLK_SET_RATE_PARENT` so the mux reparents instead of
the core reprogramming `apll` — was built, booted and measured. It works
perfectly at every level except the one that matters:

```
CRG 0x48 = 0x00000603      bits[2:0] = 3      the mux moved
clk_cpu  = 1350000000                          the framework believes it
cooling_device0 type=cpufreq-cpu0 max=7        one state per OPP
```

The mux tracked every request — 3, 5, 2, 7, 6 for 1600 down to 400 MHz — and
`sysbench` returned 241–242 events/s throughout. `apll` fell to 798 MHz once it
was no longer selected, with no consumers left, and throughput still did not
move.

Reverted. It bought nothing and left `schedutil` flipping an unidentified clock
mux during normal operation, which is a poor trade on a box running Klipper.

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

## What it would take

1. Find what actually clocks the cores. `hpll` is the candidate on rate alone;
   nothing in the CRG driver claims it, so the register that selects and
   divides it has not been identified.
2. Write a PWM regulator for the CPU supply and give the OPP table
   `opp-microvolt`, or frequency changes are either pointless or unsafe.
3. Note that the OPP table's 1600 MHz entry is fiction — the part runs 1188.

The payoff is idle power on a mains-powered box that peaks at 70 °C against a
95 °C trip. See [thermal.md](thermal.md). It is not worth doing before someone
answers (1).

## Consequences for the rest of this port

Benchmark figures elsewhere are at 1188 MHz, not the 1.6 GHz the OPP table
advertises:

| | |
|---|---|
| sysbench, 1 thread | 242 events/s |
| sysbench, 4 threads | 963 events/s (3.97× scaling) |
| AES-256-CBC, 8 KiB | 556 MB/s |
| SHA256, 8 KiB | 471 MB/s |
| memcpy, 1 thread | 2963 MB/s |
| eMMC read / write | 168 / 31 MB/s |
