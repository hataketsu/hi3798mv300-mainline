# Running mainline arm64 firmware without touching eMMC

**Status: working.** ARM Trusted Firmware and mainline U-Boot 2026.07 boot on this
box in AArch64, loaded entirely over TFTP into RAM. Not a single byte is written
to eMMC; power-cycling returns the box to stock Android.

```
LOADER:  Switched to aarch64 mode
LOADER:  Entering ARM TRUSTED FIRMWARE
LOADER:  CPU0 executes at 0x0200f000
NOTICE:  BL1: v2.8(release)
NOTICE:  BL1: Booting BL2
NOTICE:  BL2: v2.8(release)
NOTICE:  BL1: Booting BL31
NOTICE:  BL31: v2.8(release)

U-Boot 2026.07-gb635d43bca42
Model: Skyworth HC2910 with board label 2AGHD05
HC2910#
```

## Why this is needed

The stock bootloader is a 32-bit ARM binary and hands off in AArch32. Mainline
targets arm64 for this SoC. So a mainline kernel cannot simply be `bootm`-ed
from the stock prompt — the processor has to be switched to 64-bit first, and
that is l-loader's job.

Normally l-loader runs from the BootROM. Here it is entered from the *stock
U-Boot* with `go`, which works because by that point DDR is already initialised,
making l-loader's DDR/PLL setup redundant. Only its mode switch is wanted.

The switch itself is the ARMv8 RMR warm reset, in `l-loader/start.S`:

```asm
    ldr  r6, =BL1_BASE
    str  r6, [r4, r5]            @ RVBAR_EL3  <- BL1 entry  (0xF8A80000 + 0x34)
    orr  r0, r0, #0xF
    str  r0, [r4, r5]            @ AARCH64 mode bit for all 4 cores (+0x30)
    ...
    mrc  p15, 0, r2, c12, c0, 2  @ read RMR
    orr  r2, r2, #0x3            @ AA64 | RR
    mcr  p15, 0, r2, c12, c0, 2
    isb
    wfi                          @ core warm-resets, resumes at RVBAR in AArch64
```

DRAM contents survive a warm reset, so the images already loaded stay put.

## Build

Three components, in order. `$W` is a shared working directory.

```sh
# 1. mainline U-Boot -> becomes BL33
cd $W/u-boot
make CROSS_COMPILE=aarch64-linux-gnu- hc2910_2aghd05_defconfig
make CROSS_COMPILE=aarch64-linux-gnu- -j$(nproc)

# 2. TF-A
cd $W/arm-trusted-firmware        # github.com/185264646/ATF-hi3798mv2x, branch hi3798mv2x_mainline
make CROSS_COMPILE=aarch64-linux-gnu- all fip SPD=none PLAT=hi3798mv2x \
     POPLAR_DRAM_SIZE=one_gig POPLAR_RECOVERY=1 \
     BL33=$W/u-boot/u-boot.bin -j$(nproc)

# 3. l-loader
cd $W/l-loader                    # github.com/185264646/l-loader, branch hi3798mv200
cp $W/arm-trusted-firmware/build/hi3798mv2x/release/{bl1.bin,fip.bin} atf/
export ARM_TRUSTED_FIRMWARE=$W/arm-trusted-firmware
make RECOVERY=1 CROSS_COMPILE=arm-none-eabi-
```

### Three build settings that are not optional

**`POPLAR_RECOVERY=1`.** Without it BL1 reads the FIP from **eMMC** at
`FIP_BASE_EMMC` (`0x40000`) via the MMC block driver. Loaded from RAM that
offset holds the stock `fastboot` partition, and BL1 fails with:

```
ERROR:   error initializing fip
ERROR:   Failed to load BL2 firmware.
```

With recovery set, `plat_storage.c` registers an `io_memmap` device at
`FIP_BASE` instead and reads the FIP straight out of RAM. A useful sanity check:
the flag shrinks BL1 from 20613 to 14485 bytes, because the MMC driver is
compiled out.

**`POPLAR_DRAM_SIZE=one_gig`.** The default is `two_gig`. This board has 1 GiB,
and TF-A would otherwise map memory that does not exist.

**`LLOADER_TEXT_BASE` must be moved.** It defaults to `0x00C00000` in
`plat/hisilicon/hi3798mv2x/include/poplar_layout.h` — which is exactly where the
stock bootloader is linked (confirmed independently from its chip-ID table and
from `CONFIG_BOOT_ENTRY = 0x00C08500`). TFTP-ing l-loader to that address
overwrites the running U-Boot mid-transfer. Since entry is via `go` rather than
the BootROM, the base is free to choose:

```c
#define LLOADER_TEXT_BASE   0x02000000
#define FIP_BASE            0x02040000
```

`getinfo ddrfree` on the stock prompt reports free DDR as `0x1000000` ..
`0x40000000`, so `0x02000000` is safely inside it and clear of both the
bootloader at `0x00C00000` and TF-A's BL32 window at `0x03000000`.

### One post-build fix

`l-loader`'s Makefile finishes with `scripts/truncate_minimal.py`, which strips
trailing zero bytes. That cuts the file *shorter than the embedded FIP*, because
the FIP's tail and l-loader's `.tail` section are zeros:

```
l-loader.bin as built : 780288  (0xBE800)
FIP spans             : 0x40000 .. 0xBF622
```

Pad the file back out before use. Only zeros were removed, so this restores it
exactly — verify by comparing the md5 of the embedded FIP against `fip.bin`:

```sh
python3 -c "
import hashlib
d=bytearray(open('l-loader.bin','rb').read())
d += b'\0'*(0x100000-len(d))
open('l-loader.bin','wb').write(d)
print(hashlib.md5(bytes(d[0x40000:0x40000+FIP_SIZE])).hexdigest())"
```

## Running it

Serve `l-loader.bin` over TFTP (see [tftp-boot.md](tftp-boot.md)), then from the
stock `fastboot#` prompt:

```
tftp 0x02000000 l-loader.bin
go 0x0203F000
```

`0x0203F000` is the `reset` symbol — the mode-switch code — skipping the header
areas that only the BootROM cares about. Confirm the address for a given build
with `arm-none-eabi-nm l-loader | grep reset`; `go 0x02008500`
(`_checked_area_start`) also works since it branches there.

## Known gaps

* **`DRAM: 2 GiB`** until the board DTS is corrected — `hi3798mv200-hc2910-2aghd05.dts`
  declares `reg = <0x0 0x0 0x0 0x80000000>`; this board needs `0x40000000`.
* **`*** Warning - No block device`** — eMMC does not come up in U-Boot.
  `drivers/clk/` in mainline U-Boot has **no histb clock driver** at all, even
  though `hi3798mv200.dtsi` references `&crg` with
  `hisilicon,hi3798mv200-crg`. The controller registers as `mmc@9830000` but no
  block device appears, so `CONFIG_ENV_IS_IN_MMC` falls back to the default
  environment. The hardware itself is clocked — l-loader's `reset` path pokes
  the eMMC CRG directly (`0xf8a220a0 = 0x47103`, `0xf8a2209c = 0x40103`, 50 MHz)
  — so this is a driver-plumbing gap, not a hardware one.
* The board still identifies as the Skyworth HC2910. A dedicated defconfig and
  DTS for this board is the obvious next piece of work.

## What this unlocks

A mainline arm64 kernel can now be tested the same way — no eMMC writes, no
recovery button, and no risk beyond a power cycle. That removes the reason to
flash anything while the port is still being brought up.
