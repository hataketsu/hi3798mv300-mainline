# Bootloader

Two separate things share this page: the **stock HiSilicon "Fastboot"** shell
that ships on the box, and **mainline U-Boot**, which already supports this SoC
family.

## Mainline U-Boot already builds for Hi3798MV2xx

U-Boot 2026.07 carries a HiSTB platform and a Hi3798MV200 TV box:

```
arch/arm/mach-histb/              ARCH_HISTB -> ARCH_HI3798MV2X (selects ARM64)
arch/arm/dts/hi3798mv200.dtsi
arch/arm/dts/hi3798mv200-hc2910-2aghd05.dts
board/skyworth/hc2910-2aghd05/
configs/hc2910_2aghd05_defconfig
```

`TARGET_HC2910_2AGHD05` is the Skyworth HC2910 (board label 2AGHD05), a
Hi3798MV200 set-top box with 2 GiB DRAM and 8 GiB eMMC.

It builds clean, unpatched:

```sh
export CROSS_COMPILE=aarch64-linux-gnu-
make hc2910_2aghd05_defconfig
make -j$(nproc)
# -> u-boot.bin, 484312 bytes
```

`hi3798mv200.dtsi` is worth reading even if you only care about the kernel: it
already maps **CRG at `0x8a22000`**, sysctrl at `0x8000000`, perictrl at
`0x8a20000`, combphy, `pinconf-single` at `0x8a21000`, uart0, sd0, emmc, gmac,
ohci, ehci and sd1 — with the `hisilicon,hi3798mv200-crg` and
`hisilicon,hi3798mv200-sysctrl` compatibles that the kernel side is still
missing. Somebody has already done the clock-tree mapping work here.

### Boot chain

U-Boot is only the last of three pieces:

```
BootROM
 └─ l-loader (32-bit)   DDR/PLL init from AUXCODE.img + BOOT_[0..2].reg,
 │                      switches the core to 64-bit
 └─ TF-A BL31           the poplar port works for now; a mainline MV200 port
 │                      is still in progress
 └─ U-Boot (arm64)      CONFIG_POSITION_INDEPENDENT, CONFIG_TEXT_BASE=0
```

The MV200 l-loader fork lives at <https://github.com/185264646/l-loader>.

### What has to change for this board

1. **MV300 is not MV200.** The Kconfig says "Hi3798M V2XX series"; the chip IDs
   differ (`0x37980210` vs `0x37986200`). Assume the CRG layout matches, but
   verify it.
2. **DDR differs.** HC2910 declares 2 GiB (`reg = <0x0 0x0 0x0 0x80000000>`);
   this board has 1 GiB, so the memory node becomes `0x40000000` and l-loader
   needs this board's DDR init. The stock bootloader embeds exactly that, near
   the start of the `fastboot` partition:
   ```
   0x480  "v120v1.5.0" and "2025/04/10 11:54:30"
   0x4a0  "hi3798mv3dms1_hi3798mv300_DDR3-1866_1GB_16bitx2_2layers.reg"
   0x4dd  register write script (0xf8a22000 ...)
   ```
3. **Ethernet differs.** The HC2910 DTS enables `gmac@9840000` in RGMII mode with
   an external PHY at address 3. This board's Linux interface comes up on the FE
   MAC (`f9c30000.hieth`), though the stock bootloader reports `gmac0`. Confirm
   which is actually wired before copying the node.
4. `CONFIG_NO_NET=y` in the defconfig — TFTP needs networking enabled plus a
   working MAC driver.

## Stock "Fastboot" shell

Interrupt autoboot with **Ctrl+C** (not a countdown — `bootdelay=0`) to reach
`fastboot#`.

Note there is no `env` command; use `printenv`.

### Command inventory

```
?  base  bootm  bootp  clear_bootf  cmp  cp  crc32  ddr  dtimg
fatinfo  fatload  fatls  fdt  getinfo  go  help  hibernate
loadb  loady  loop  md  mii  mm  mmc  mmcinfo  mtest  mw
nand  nboot  nm  ping  printenv  rarpboot  reset  saveenv  setenv
tftp  unzip  uploadx  usb  usbboot  version

otp_burntoecurechipset   Burn to secure chipset, please be careful !!!
otp_getcustomerkey       otp_getcustomerkey
otp_getstbprivdata       otp_getstbprivdata
otp_gettrustzonestat     Get TEE status
otp_setstbprivdata       StbPrivData
otpreadall               read otp, for example otpreadall
otpwrite                 write otp, for example otpwrite address value
```

`ddr` has subcommands (`training`, `tr`, `wl`, `gate`, `dataeye`, `vref`, `hw`,
`mpr`, `ac`, `lpca`) for DDR training and for dumping training results.

### Commands that are safe to read with

`version`, `getinfo`, `printenv`, `mmcinfo 0`, `md`, `mii`, `bdinfo`,
`otp_gettrustzonestat`, `otpreadall`, `crc32`, `fdt`, `dtimg`.

### Commands that must not be run casually

| Command | Why |
|---|---|
| `otp_burntoecurechipset` | **Burns a one-way fuse.** Converts the SoC to secure-boot-only. Irreversible; the box will then refuse unsigned bootloaders forever. |
| `otpwrite` | Writes raw OTP. One-way, per-bit. |
| `otp_setstbprivdata` | Writes OTP-backed data. |
| `saveenv` | Rewrites the `bootargs` partition. Fine when intended, brick-adjacent when not. |
| `mmc write` / `mm` / `mw` / `nand` | Direct writes to flash or memory. |

`otp_getcustomerkey` and `otp_getstbprivdata` are reads, but they print
key material — do not paste their output into a public issue.

## Testing a kernel without touching eMMC

The environment already has `ipaddr=192.168.1.10`, `serverip=192.168.1.1` and
`netmask=255.255.255.0`, and Ethernet comes up in the bootloader. Serving an
image over TFTP and `bootm`-ing it from RAM leaves flash untouched and is the
correct way to iterate before considering a bootloader replacement.
