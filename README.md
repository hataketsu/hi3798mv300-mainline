# Hi3798MV300 — mainline Linux port

Notes, extracted data, and work-in-progress device trees for bringing **mainline Linux**
to a generic HiSilicon **Hi3798MV300** Android TV box.

The stock firmware is Android 9 (API 28) on kernel 4.9.118 from the HiSilicon
`HiSTBLinuxV100R005C00` BSP, with `ro.build.version.release` spoofed to "14".

> **Status: Debian 13 runs on this box, off a USB stick, with Wi-Fi.**
> Power on and it reaches a login prompt unattended — no serial console, nothing
> typed. Console, all four cores, clocks, eMMC, USB 2.0 host and Wi-Fi work.
> Ethernet, GPIO, pinctrl and USB 3.0 do not yet.
>
> **The eMMC still holds stock Android.** The only write ever made to it is the
> U-Boot `bootcmd`, and the original is saved alongside it, so the box goes back
> to Android with one `setenv`.

## Where it got to

| | |
|---|---|
| Boot chain | vendor bootloader → l-loader → TF-A → mainline U-Boot 2026.07 → Linux 7.2-rc5, all from USB |
| CPU | 4× Cortex-A53 via PSCI, cpufreq 798 MHz → 1.2 GHz |
| eMMC | HS400 @ 150 MHz, all 21 vendor partitions parse and mount |
| USB 2.0 | EHCI + OHCI, roots the system off a USB stick |
| Wi-Fi | RTL8822BS on SDIO, mainline `rtw88`, firmware 27.2.0 |
| Userland | Debian 13 trixie arm64, systemd, sshd |
| Ethernet | `end0` up, 100 Mbit full duplex — needed a kconfig, a missing MDIO clock and the right PHY address |
| IR | `hix5hd2-ir` at `0xf8001000`, raw pulses on `/dev/lirc0` and scancodes after the in-kernel decoders |
| LEDs | both front-panel LEDs on `gpio5`, exposed as `/sys/class/leds` with triggers |
| GPIO/pinctrl | `gpio5` works; gpio0-gpio9 defer forever, waiting on a pinctrl driver |
| USB 3.0 | still disabled |

Details: [docs/debian-usb.md](docs/debian-usb.md), [docs/usb.md](docs/usb.md),
[docs/wifi.md](docs/wifi.md), [docs/ethernet.md](docs/ethernet.md),
[docs/ir.md](docs/ir.md), [docs/leds.md](docs/leds.md),
[docs/kernel.md](docs/kernel.md), [patches/kernel/](patches/kernel/).

> Mainline **U-Boot already supports this SoC family** and builds unpatched for
> the Skyworth HC2910 (Hi3798MV200) — see [docs/uboot.md](docs/uboot.md).
> The secure-boot fuse on this unit is **not burned**, so an unsigned bootloader
> is accepted — see [docs/secure-boot.md](docs/secure-boot.md).
> Kernels can also be served over TFTP and booted from RAM — see
> [docs/tftp-boot.md](docs/tftp-boot.md) and
> [docs/aarch64-bringup.md](docs/aarch64-bringup.md).

## The box

| | |
|---|---|
| SoC | HiSilicon Hi3798MV300 (`chipid 0x37980210`), quad Cortex-A53 up to 2.0 GHz |
| GPU | ARM Mali-450 MP (Utgard), 200–860 MHz |
| RAM | 1 GB DDR3-1866 (`mem=988M`, 44 MB carved out for the media zone) |
| Storage | 8 GB eMMC (`M72808`, manfid `0x70`) |
| Ethernet | HiSilicon FE MAC (`hieth`) @ `0xf9c30000`, 100 Mbit |
| Wi-Fi/BT | Realtek RTL8822BS over SDIO (`0xb822`) — 802.11ac + Bluetooth |
| Console | UART0 (PL011) @ `0xf8b00000`, `ttyAMA0`, **115200 8N1** |
| Secure world | ARM Trusted Firmware in the `trustedcore` partition; `enable-method = "psci"` |

Full peripheral map in [docs/hardware.md](docs/hardware.md).

## What is here

```
docs/
  hardware.md          SoC block addresses, peripherals, clocks, DVFS tables
  mainline-status.md   what is in mainline today, what is missing, patch links
  vendor-firmware.md   partition table, U-Boot environment, stock build metadata
  boot-log.md          annotated stock boot, bootcmd decoded, memory layout
  uboot.md             mainline U-Boot support, boot chain, stock shell commands
  secure-boot.md       how to tell whether the OTP fuse is burned (it is not)
  tftp-boot.md         booting a kernel from RAM over Ethernet, touching no flash
  aarch64-bringup.md   TF-A + mainline U-Boot running in 64-bit mode, no eMMC writes
  kernel.md            rebasing the out-of-tree CRG driver onto v7.2-rc5, board DTS
  debian-usb.md        Debian rootfs on USB, unattended boot, the eMMC env change
  usb.md               USB2 PHY reverse engineered out of the stock bootloader
  wifi.md              RTL8822BS on SDIO, and the dtsi typo that hid the controller
  ethernet.md          three defects between the FE MAC and its PHY, and the fixes
  ir.md                IR receiver, the sysctrl clock it needs, enabling the decoders
  leds.md              finding two undocumented front-panel LEDs by measurement
  uart-access.md       serial console wiring, flow control, reaching the U-Boot prompt
dts/
  hi3798mv300-tvbox.dts  board device tree for this box
  vendor/              device tree decompiled from the stock firmware (reference)
  mainline/            work-in-progress mainline .dtsi / .dts
patches/
  kernel/              fixups needed on top of the out-of-tree kernel series
extracted/
  uboot-env.txt        U-Boot environment as plain text
logs/
  stock-boot-uart.log  full stock boot over UART (MAC redacted)
scripts/
  tftpd.py             dependency-free read-only TFTP server for the host
  uart-term.py         serial terminal with hardware flow control forced off
  uart-cmd.py          run a command on the box over the serial console
  mk-initramfs.sh      minimal busybox initramfs that switch_roots to the USB root
  write-usb-rootfs.sh  write the Debian rootfs and kernel to the box's USB stick
  extract-dtb.py       pull FDT blobs out of boot/recovery/dtbo images
  dump-partitions.sh   dump every firmware partition to external storage
  tvbox-panel.py       browser panel: drive the LEDs, watch IR live, probe GPIO
  tvbox-gpio-helper.py privileged PL061 access for the panel, bank/pin only
```

## What is deliberately *not* here

No firmware images. `boot.img`, `system.img`, `vendor.img`, `trustedcore.img` and
friends are proprietary HiSilicon/Google binaries — redistributing them would be
copyright infringement, and `userdata` from a used box contains account tokens.
`.gitignore` blocks `*.img` and `userdata*` so a stray `git add .` cannot leak them.

Dump your own with [`scripts/dump-partitions.sh`](scripts/dump-partitions.sh).

The decompiled device tree in `dts/vendor/` is kept because it is the DTS the
GPL-licensed vendor kernel is built against, and it is the primary reference for
every register address in this port.

## Why this SoC is worth the effort

Nearly everything needed is already generic: PL011 UART, ARM architected timer,
GIC, PSCI, DesignWare MMC, DesignWare USB3, Synopsys PCIe, and — unusually for a
cheap TV box — a **Mali-450**, which mainline supports through the open `lima`
driver. The SoC-specific gap is small and mostly mechanical.

## Contributing

Register dumps, boot logs, and DTS fragments for other Hi3798MV200/MV300/MV310
boards are welcome. Please do not attach firmware images to issues.

## License

GPL-2.0-or-later. See [LICENSE](LICENSE).
