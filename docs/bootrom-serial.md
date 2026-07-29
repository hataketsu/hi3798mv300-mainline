# BootROM serial boot

An attempt to get a bootloader into the box over the serial port, so that
overwriting sector 0 would be recoverable. **It does not work yet.** The
BootROM talks, accepts the boot parameters and runs DDR init from them, then
announces success and stops listening without ever taking the bootloader.

Recorded because most of the way there is solid, one real upstream bug came out
of it, and the failure is specific enough to be worth someone else's time.

## Why it matters

Sector 0 of the eMMC is the stock bootloader, not a partition table. Overwrite
it with a bad image and nothing boots — USB included, because it is the stock
bootloader that reads the USB stick. Every other recovery path on this box
(`fastboot#` prompt, the `USB_BOOT` strap) lives inside that same image.

So replacing it is a one-way move unless the BootROM can be made to accept a
bootloader over UART. Until that is demonstrated, this port leaves sector 0
alone. See [emmc-install.md](emmc-install.md), which needs none of this.

## What works

The BootROM announces itself on UART at every power-on, with no key press and
no need for the boot media to be blank:

```
Bootrom start
Boot Media: eMMC
```

It then answers frames. [hiloot](https://github.com/185264646/hiloot) gets:

```
Board info: Hi3798v210, chip 1 (0x000000000137980210)
Board use reg #4
```

The variant index is not fused or strapped to a GPIO — it comes from an ADC
reading, printed by the auxiliary code itself:

```
lsadc voltage min: 000000EA, max: 00000107, aver: 000000FE, index: 00000004
```

Head, auxiliary code and reg table all transfer cleanly at 115200 (~11 KiB/s),
and the auxiliary code runs and initialises DDR:

```
Auxiliary code - v1.00
DDR code - V1.1.2 20160205
Reg Version:  v1.5.0\
Reg Time:     2025/04/10 11:54:30
Reg Name:     hi3798mv3dms1_hi3798mv300_DDR3-1866_1GB_16bitx2_2layers.reg
dms board
Boot auxiliary code success
```

## Where it stops

Immediately after, on the first frame of the bootloader transfer:

```
Send bootimg to 0x0, length 0xbf900...
-> Bootrom success
(retransmitting 1...39)
Error: Timeout
```

The BootROM prints `Bootrom success` before receiving a single byte of the
bootloader, then ignores every frame. The box does not fall back to eMMC
either — the console stays silent and it has to be power cycled.

The stock `fastboot.bin`, which demonstrably boots this board every day,
behaves identically. So this is not a property of the image being sent.

`Boot Media: eMMC` is announced before any of this, which suggests the BootROM
had already decided where the bootloader comes from and used the serial link
only to collect boot parameters. That would mean serial delivery of a
bootloader only happens when the boot media has no valid image — recoverable
exactly when recovery is needed, but impossible to prove on a healthy box.
That is a guess, not a finding.

The [protocol documentation](https://histb-mainline.github.io/software/bootrom/bootstrap.html)
covers the frame format and stops before this point, so the tool's author had
no reference for it either.

## The upstream bug this found

[`185264646/l-loader`](https://github.com/185264646/l-loader) cannot be booted
by the BootROM as shipped. Its `start.S` fills the `DEFAULT_BOOT_REG` slot with
`bin/BOOT_0.reg`:

```asm
.=CONFIG_DEFAULT_BOOT_REG_POS
	.incbin	"bin/BOOT_0.reg"
```

but `BOOT_0.reg` is raw register data with no header — its first 32 bytes are
zero. The auxiliary code reads that slot expecting a *versioned* reg file and
refuses:

```
Invalid reg version
Boot auxiliary code execute failed.
```

The stock bootloader carries a proper one at the same offset, `0x2a40` bytes
running to `CONFIG_PARAM_AREA_SIG_POS` (`0x2EC0`) — which matches the
`DEFAULT_BOOT_REG` size in the
[bootloader layout docs](https://histb-mainline.github.io/software/bootrom/bootloader.html)
exactly:

```
# dd if=fastboot.img bs=1 skip=$((0x480)) count=$((0x2a40)) of=DEFAULT_BOOT.reg
# hireg.py DEFAULT_BOOT.reg
Format: V120
Version: v1.5.0\
Name: hi3798mv3dms1_hi3798mv300_DDR3-1866_1GB_16bitx2_2layers.reg
```

Pointing the slot at that file is what produced `Boot auxiliary code success`
above. Two further changes are needed before the BootROM will run l-loader at
all:

* `LLOADER_TEXT_BASE` back to `0x00C00000`. The working build moves it to
  `0x02000000` because it is entered with `go` from the stock U-Boot, which is
  itself running at `0x00C00000` — see [aarch64-bringup.md](aarch64-bringup.md).
  Entered by the BootROM there is nothing in the way and the image must sit
  where the header's `BOOT_ENTRY` (`0x00C08500`) says.
* `SUPPORT_MULTI_PARAM` cleared. With it set, the auxiliary code indexes the
  reg list by the variant number — #4 here — but l-loader only builds three
  slots (`boot_reg0/1/2` at `0x8600`/`0xa600`/`0xc600`) where the layout allows
  eight (`BOOT_REG0`..`BOOT_REG7`). Index 4 lands in BL1. The stock image is
  single param for the same reason.

## Traps

`bootimg.py` reports dozens of reg params for l-loader:

```
Reg param 0:   0x8600,   0xa600  ( 0x2000)
Reg param 3:   0xe600,  0x10600  ( 0x2000)
...
Reg param 33:  0x4a600, 0x4c600  ( 0x2000)
```

They are an artifact. `Memcpy.cuts` slices everything from `PARAM_START_ADDR`
to the end of the file into `PARAM_ITEM_LEN` chunks, so most of those "tables"
are BL1 and BL31 code. Only three are real.

`hiloot.py --reg N` (added locally) changes which table the host *sends*, not
which one the auxiliary code *reads* — the two are chosen independently, so it
cannot work around a wrong index on its own.

The stock `fastboot.bin` cannot be pushed over serial unmodified: it is single
param, so `params.regs[4]` does not exist and hiloot refuses before sending
anything.

## Recovery that does work

* `Ctrl-C` during the 3-second delay reaches the `fastboot#` prompt, which can
  rewrite any part of the eMMC. Used throughout this port.
* The stock bootloader has a hardware recovery mode: pulling `USB_BOOT` low
  makes it read `bootargs.bin` and `recovery.img` from a USB stick
  ([vendor docs](https://histb-mainline.github.io/software/vendor/fastboot.html)).
  The boot log also mentions `enter the gpio press revocery`. Neither has been
  tested here.

Both live in sector 0. That is the argument for leaving it alone.
