# BootROM serial boot

Overwriting sector 0 is the one change on this box that cannot be undone from
software: the stock bootloader lives there, and every recovery path — the
`fastboot#` prompt, the `USB_BOOT` strap — lives inside it. So before replacing
it, the BootROM had to be shown accepting a bootloader over UART.

It does not, and the reason turns out to be structural rather than a bug:
**the BootROM always takes its bootloader from the boot medium selected by a
pin strap.** Serial only supplies the boot parameters and the DDR init that
runs before it. There is no "boot from serial" while the strapped medium still
holds a valid image.

That is worth writing down, because the failure looks exactly like a broken
tool for a long time.

## What the serial link does do

The BootROM announces itself at every power-on — no key press, no need for the
flash to be blank:

```
Bootrom start
Boot Media: eMMC
```

and then answers frames. [hiloot](https://github.com/185264646/hiloot) gets a
chip ID and a board variant:

```
Board info: Hi3798v210, chip 1 (0x000000000137980210)
Board use reg #4
```

The variant is not fused and not a GPIO — it comes from an ADC reading, which
the auxiliary code prints itself:

```
lsadc voltage min: 000000EA, max: 00000107, aver: 000000FE, index: 00000004
```

Head, auxiliary code and reg table transfer cleanly at 115200 (~11 KiB/s), and
the auxiliary code runs and brings DDR up:

```
Auxiliary code - v1.00
DDR code - V1.1.2 20160205
Reg Version:  v1.5.0\
Reg Time:     2025/04/10 11:54:30
Reg Name:     hi3798mv3dms1_hi3798mv300_DDR3-1866_1GB_16bitx2_2layers.reg
dms board
Boot auxiliary code success
```

That is the whole of it. Immediately afterwards:

```
Send bootimg to 0x1000000, length 0xe2000...
-> Bootrom success
(retransmitting 1...39)
Error: Timeout
```

`Bootrom success` arrives before a single byte of the bootloader is sent, and
the device then ignores every frame — with `-d` there is not one received
message across all the retransmits. It does not fall back to the flash either:
the console stays silent and the box has to be power cycled.

The stock `fastboot.bin`, which boots this board every day, behaves identically.
So it is not a property of the image.

## Why: the medium is strapped, not negotiated

The vendor BSP is public, and it answers this directly. From
`source/boot/miniboot/arm/hi3798mv2x/boot/cpu.c` — the file for this exact SoC:

```c
static int hi3798mv2x_boot_media(char **media)
{
	/* read from pin */
	boot_media = readl(REG_BASE_PERI_CTRL + REG_START_MODE);
	boot_media = ((boot_media >> NORMAL_BOOTMODE_OFFSET)
		& NORMAL_BOOTMODE_MASK);

	switch (boot_media) {
	case BOOT_FROM_SPI:      ... "SPI Flash"
	case BOOT_FROM_NAND:     ... "NAND"
	case BOOT_FROM_SPI_NAND: ... "SPI-NAND"
	case BOOT_FROM_EMMC:     ... "eMMC"
	}
}
```

and `arm/hi3798mv2x/include/platform.h`:

```c
#define REG_BASE_PERI_CTRL        0xF8A20000
#define REG_START_MODE            0x0000
#define NORMAL_BOOTMODE_OFFSET    9
#define NORMAL_BOOTMODE_MASK      7

#define BOOT_FROM_SPI        0x0
#define BOOT_FROM_NAND       0x1
#define BOOT_FROM_SD         0x2
#define BOOT_FROM_EMMC       0x3
#define BOOT_FROM_SPI_NAND   0x4
#define BOOT_FROM_SYNC_NAND  0x5
#define BOOT_FROM_DDR        0x8
```

Read back from the running box:

```
# busybox devmem 0xf8a20000 32
0x51400600
                ^^^
0x600 = 0000 0110 0000 0000  ->  bits[11:9] = 011 = 3 = BOOT_FROM_EMMC
```

So `Boot Media: eMMC` is a decision the BootROM made from a pin before any
frame was exchanged. `Bootrom success` means it finished loading the bootloader
**from eMMC** and jumped. The image pushed over serial was never a candidate.

None of the media values is a serial port. There is no code path to look for.

## The rescue that follows from this

Strap the boot mode to a medium this board does not populate. The boot log
already shows `no found nand device`, and there is no SPI flash either, so
either `BOOT_FROM_SPI` (0) or `BOOT_FROM_NAND` (1) leaves the BootROM with
nothing to load — which is the state a factory board is in, and exactly what
HiSilicon's own production tool relies on.

That tool ships in the BSP as
`tools/windows/HiPro-serial/HiPro-serial_en/HiPro-serial.exe`, and its readme
describes programming a **bare board**:

> HiPro-serial is used for bare board programming in the process of production
> [...] put the image named usb_update.bin you want to programming

A bare board has an empty eMMC, so the BootROM cannot boot from it, and the
serial link becomes the only way in.

The strap is latched at reset, so writing `0xF8A20000` from Linux and rebooting
achieves nothing — it has to be a physical pull on the boot-mode pins, held
during power-on and released afterwards. Nothing is written and nothing is
lost, which makes it the one recovery worth having before sector 0 is ever
touched.

**Untested here.** The board's strap resistors have not been located.

## The l-loader bug this turned up

Separately, [`185264646/l-loader`](https://github.com/185264646/l-loader)
cannot be booted by the BootROM as shipped. Its `start.S` fills the
`DEFAULT_BOOT_REG` slot with `bin/BOOT_0.reg`:

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

The vendor's own `start.S` for this SoC uses a separate file for that slot:

```asm
.=CONFIG_DEFAULT_BOOT_REG_POS
	.incbin	CONFIG_BOOTREG_DEFAULT
```

and the stock bootloader carries a proper one at the same offset, `0x2a40`
bytes running to `CONFIG_PARAM_AREA_SIG_POS` (`0x2EC0`) — matching the
`DEFAULT_BOOT_REG` size in the
[bootloader layout docs](https://histb-mainline.github.io/software/bootrom/bootloader.html):

```
# dd if=fastboot.img bs=1 skip=$((0x480)) count=$((0x2a40)) of=DEFAULT_BOOT.reg
# hireg.py DEFAULT_BOOT.reg
Format: V120
Version: v1.5.0\
Name: hi3798mv3dms1_hi3798mv300_DDR3-1866_1GB_16bitx2_2layers.reg
```

Pointing the slot at that file is what turns `Invalid reg version` into
`Boot auxiliary code success`. Two further changes are needed before the
BootROM will run l-loader at all:

* `LLOADER_TEXT_BASE` back to `0x00C00000`. The working build moves it to
  `0x02000000` because it is entered with `go` from the stock U-Boot, which is
  itself running at `0x00C00000` — see [aarch64-bringup.md](aarch64-bringup.md).
* `SUPPORT_MULTI_PARAM` cleared. With it set the auxiliary code indexes the reg
  list by the variant number — #4 here — but l-loader builds three slots where
  the layout allows eight (`BOOT_REG0`..`BOOT_REG7`). Index 4 lands in BL1.

The vendor header also carries three fields l-loader never writes:
`CONFIG_SUPPORT_EXT_AREA_POS`, `CONFIG_SUPPORT_EXT_AREA_LEN_POS` and
`CONFIG_SCS_SIM_FLAG_POS`.

## The auxiliary code cannot be read

It arrives encrypted and the BootROM decrypts it — `Decrypt auxiliary code
...OK` in the log. The blob is 21504 bytes, of which the first 32 are a
plaintext ID string and the rest measures 7.971 bits/byte of entropy across all
256 byte values.

The BSP ships only the built blob — `auxcode_sign_hi3798mv300.img`, byte for
byte the same as the one extracted from this box's bootloader — with no source
anywhere in the SDK. The key is in the BootROM, and the mask ROM is not mapped
once Linux is up: `/proc/iomem` shows DRAM from `0x00000000` and no ROM
aperture.

It does not matter. What the aux code would have explained is already answered
by `cpu.c` above.

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
which one the auxiliary code *reads*; the two are chosen independently.

The stock `fastboot.bin` cannot be pushed over serial unmodified either: it is
single param, so `params.regs[4]` does not exist and hiloot refuses before
sending anything.

## Where the sources are

The whole HiSilicon BSP is on GitHub — no vendor tooling, no forum downloads:

```
JasonFreeLab/HiSTBLinuxV100R005C00SPC060
  source/boot/fastboot/arch/arm/cpu/hi3798mv2x/start.S   boot header layout
  source/boot/miniboot/arm/hi3798mv2x/boot/cpu.c         boot medium selection
  source/boot/miniboot/arm/hi3798mv2x/include/platform.h register addresses
  tools/windows/HiPro-serial/                            production burn tool
```

Protocol framing is documented at
[histb-mainline](https://histb-mainline.github.io/software/bootrom/bootstrap.html):
`CMD_HEAD 0xfe`, `CMD_DATA 0xda` (max 1024 B), `CMD_TAIL 0xed`, replies `0xaa`
accept / `0x55` CRC error. [`OpenIPC/burn`](https://github.com/OpenIPC/burn)
implements the same framing for the Hi35xx and Goke camera parts and is easier
to read than any of it.

## Recovery that works today

* `Ctrl-C` during the 3-second delay reaches the `fastboot#` prompt, which can
  rewrite any part of the eMMC. Used throughout this port.
* The stock bootloader reads `bootargs.bin` and `recovery.img` off a USB stick
  when `USB_BOOT` is pulled low
  ([vendor docs](https://histb-mainline.github.io/software/vendor/fastboot.html)),
  and the boot log mentions `enter the gpio press revocery`.

Both live in sector 0, which is the argument for leaving it alone — see
[emmc-install.md](emmc-install.md), which needs none of this.
