# Hardware map

Everything here is derived from the stock firmware: the device tree embedded in
`boot`/`recovery`/`dtbo`, the U-Boot binary in the `fastboot` partition, and live
queries against a running stock Android over the serial console.

## SoC identification

The vendor device tree uses one compatible for the whole family:

```
model = "Hisilicon";
compatible = "hi3798mv200-series";
```

The actual die is identified at runtime. U-Boot carries a chip table at file
offset `0xdd050` in the `fastboot` partition (link base `0x00C00000`, entry
stride `0x38`):

| Name | Chip ID |
|---|---|
| Hi3798Mv200 | `0x37986200` |
| Hi3798Mv300 | `0x37980210` |

This board reports `Hi3798MV300` (`ro.hisilicon.product`, `ro.build.product`,
`ro.product.vendor.device`).

## CPU

Four Cortex-A53 cores, `enable-method = "psci"`, ARMv7 32-bit userspace in the
stock build (the cores are ARMv8, so a 64-bit mainline port is possible).

* `arm,armv7-timer`, `arm,armv8-pmuv3`
* DVFS operating points reported by the stock kernel:
  `400, 600, 800, 1200, 1600, 2000 MHz`

Note `/proc/cpuinfo` prints `BogoMIPS: 16.00` — that is the 24 MHz architected
timer, not the core clock.

## GPU

ARM Mali-450 MP (`arm,mali-450`, `arm,mali-utgard`) at `0xf9200000`, supplied by
the `vdd-gpu` regulator at `0xf8a23020` (`hisilicon,hi3798mv200-volt`).

Operating points, typical-typical silicon bin:

| MV200 (`operating-points-tt`) | MV300 (`operating-points-tt-98mv300`) |
|---|---|
| 200 MHz @ 0.870 V | 200 MHz @ 0.880 V |
| 300 MHz @ 0.870 V | 300 MHz @ 0.880 V |
| 400 MHz @ 0.870 V | 400 MHz @ 0.880 V |
| 500 MHz @ 0.870 V | 500 MHz @ 0.880 V |
| — | 540 MHz @ 0.880 V |
| 600 MHz @ 0.870 V | 600 MHz @ 0.880 V |
| 675 MHz @ 0.900 V | 675 MHz @ 0.900 V |
| 750 MHz @ 0.910 V | — |
| 800 MHz @ 0.930 V | 860 MHz @ 0.990 V |

The DT also carries `-ss` (slow) and `-ff` (fast) bins and a separate
`-youtube` set that drops the 750 MHz step.

Default frequency 600 MHz, max 800 MHz on MV200 / 860 MHz on MV300.

Mainline's `lima` driver targets exactly this GPU family.

## Peripheral addresses

Taken from the `aliases` node of the vendor DT.

| Block | Address | Vendor compatible | Notes |
|---|---|---|---|
| UART0 | `0xf8b00000` | `arm,pl011` | console, `ttyAMA0`, 115200 |
| UART2 | `0xf8b02000` | `arm,pl011` | |
| UART3 | `0xf8b03000` | `arm,pl011` | disabled by `nouart3` bootarg |
| I2C0 | `0xf8b10000` | `hisilicon,hi-i2c` | 400 kHz |
| I2C1 | `0xf8b11000` | `hisilicon,hi-i2c` | 400 kHz |
| I2C2 | `0xf8b12000` | `hisilicon,hi-i2c` | 400 kHz |
| SPI0 | `0xf8b1a000` | `arm,pl022` | |
| SD | `0xf9820000` | `himciv200.SD` | |
| eMMC | `0xf9830000` | `himciv200.MMC` | `mmcblk0`, 8 GB |
| SDIO | `0xf9c40000` | `himciv200.SD` | RTL8822BS Wi-Fi/BT sits here |
| FE MAC | `0xf9c30000` | `hisilicon,hieth` | 100 Mbit, `eth0` |
| GMAC | `0xf9840000` | `hisilicon,higmac` | second MAC, unpopulated |
| OHCI | `0xf9880000` | `generic-ohci` | USB 1.1 |
| EHCI | `0xf9890000` | `generic-ehci` | USB 2.0 |
| xHCI | `0xf98a0000` | `generic-xhci` | USB 3.0 |
| UDC | `0xf98c0000` | `hiudc` | device mode |
| SATA/AHCI | `0xf9900000` | `generic-ahci` | disabled by `nosata` |
| PCIe | `0xf0001000` | `snps,dw-pcie` | disabled by `nopcie` |
| NAND (FMC) | `0xf9950000` | `hisilicon.hifmc100` | disabled by `nofmc` (board boots eMMC) |
| GPU | `0xf9200000` | `arm,mali-450` | |
| GPU regulator | `0xf8a23020` | `hisilicon,hi3798mv200-volt` | |
| DDR watchzone | `0xf8a35000` | `hisilicon.ddr_watchzone` | |
| IR receiver | — | `hisilicon,hix5hd2-ir` | mainline driver exists |
| Watchdog | — | `hisilicon,hisp805` | SP805 |
| Timers | — | `hisilicon,hisp804` | SP804 |
| Thermal | — | `arm,hisi-thermal` | mainline driver exists |
| Clock/reset | — | `hisilicon,clock-reset` | **CRG — no mainline driver, see status doc** |

Reference clock frequencies present in the DT: 24 MHz, 54 MHz, 75 MHz.

## Memory

* 1 GB DDR3-1866. U-Boot picks a DDR training profile by board; this one matches
  `hi3798mv3dms1_hi3798mv300_DDR3-1866_1GB_16bitx2_2layers.reg`
  (a 2 GB / 4-layer variant `hi3798mv3dmf_..._2GB_8bitx4_4layers.reg` also ships
  in the same U-Boot).
* Kernel gets `mem=988M`, with `mmz=ddr,0,0,44M` reserved for the media pipeline
  and `vmalloc=500M`.
* Reserved regions in the DT: `0x30000000+0x100000` and `0x0+0x1000`.

## Storage

eMMC `mmcblk0`, 7 471 104 KiB usable (~7.1 GiB):

```
name    M72808
manfid  0x000070
cid     7001004d3732383038xxxxxxxxxxxxxx
        ^^^^^^ manufacturer / OEM fields
              ^^^^^^^^^^^^ "M72808" in ASCII
                          ^^^^^^^^^^^^^^ per-unit serial and date, redacted
```

Plus `mmcblk0boot0`, `mmcblk0boot1` (4 MiB each) and `mmcblk0rpmb`.

## Network interfaces

Per-unit halves are redacted; only the OUI is meaningful here.

| Interface | MAC | Notes |
|---|---|---|
| `eth0` | `e8:bb:3e:xx:xx:xx` | stored in the `deviceinfo` partition as ASCII at offset 0 |
| `wlan0` | `30:88:41:xx:xx:xx` | RTL8822BS |
| `p2p0` | `32:88:41:xx:xx:xx` | Wi-Fi Direct, derived from `wlan0` with the locally-administered bit set |

The stock vendor image also ships a `uwe5621_bsp_sdio` module (Unisoc UWE5621),
so the same board design is sold with either Wi-Fi module.
