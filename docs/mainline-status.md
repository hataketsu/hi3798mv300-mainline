# Mainline status

Checked against a `torvalds/linux` checkout at **v7.2-rc** (July 2026).

## What is already in mainline

Grepping the tree for `hi3798mv` returns:

```
Documentation/devicetree/bindings/mmc/hisilicon,hi3798cv200-dw-mshc.yaml
Documentation/devicetree/bindings/phy/hisilicon,inno-usb2-phy.yaml
Documentation/devicetree/bindings/usb/hisilicon,hi3798mv200-dwc3.yaml
drivers/mmc/host/dw_mmc-hi3798mv200.c
drivers/mmc/host/Kconfig
drivers/mmc/host/Makefile
drivers/phy/hisilicon/phy-hisi-inno-usb2.c
drivers/usb/dwc3/dwc3-of-simple.c
```

| Block | Compatible | State |
|---|---|---|
| eMMC / SD / SDIO | `hisilicon,hi3798mv200-dw-mshc` | **merged** — `dw_mmc-hi3798mv200.c` |
| USB3 | `hisilicon,hi3798mv200-dwc3` | **merged** — via `dwc3-of-simple` |
| USB2 PHY | `hisilicon,hi3798mv100-usb2-phy` | **merged** — `phy-hisi-inno-usb2.c` |
| GPU | `arm,mali-450` | **merged** — `lima` |
| UART | `arm,pl011` | generic |
| Timer / GIC / PSCI | ARM generic | generic |
| Watchdog | `arm,sp805` | generic |
| Thermal | `arm,hisi-thermal` | in tree |
| IR | `hisilicon,hix5hd2-ir` | in tree |
| Ethernet | `hisilicon,hisi-femac-v1/v2` | driver `hisi_femac.c` in tree, **MV200/MV300 compatible not added** |
| Wi-Fi | RTL8822BS over SDIO | **merged** — `rtw88`, `drivers/net/wireless/realtek/rtw88/rtw8822bs.c` |

The Wi-Fi module is a pleasant surprise: the stock image loads Realtek's
out-of-tree `rtl8822bs.ko`, but mainline `rtw88` has had a native SDIO backend
(`sdio.c` plus `rtw8822bs.c`) for years. No vendor blob needed.

The full set of `hisilicon,hi3798*` compatibles currently accepted by mainline:

```
hisilicon,hi3798cv200                hisilicon,hi3798cv200-perictrl
hisilicon,hi3798cv200-combphy        hisilicon,hi3798cv200-poplar
hisilicon,hi3798cv200-crg            hisilicon,hi3798cv200-sysctrl
hisilicon,hi3798cv200-dw-mshc        hisilicon,hi3798cv200-usb2-phy
hisilicon,hi3798cv200-gmac           hisilicon,hi3798cv200-xhci
hisilicon,hi3798cv200-pcie           hisilicon,hi3798mv100-usb2-phy
                                     hisilicon,hi3798mv200-dw-mshc
                                     hisilicon,hi3798mv200-dwc3
```

## What is missing

### 1. CRG driver — the blocker

`drivers/clk/hisilicon/` contains `crg-hi3798cv200.c` and nothing for
MV100/MV200/MV300. Without clock and reset support nothing else probes.

Yang Xiwen (`forbidden405@outlook.com`) posted the work upstream but it never
landed:

* *"clk: hisilicon: add support for Hi3798MV200"* — v4 on 2024-02-23, superseded
  by v5 on 2024-02-24. Adds `drivers/clk/hisilicon/crg-hi3798mv200.c` (462
  lines), `include/dt-bindings/clock/hisilicon,hi3798mv200-crg.h`,
  `hisilicon,hi3798mv200-sysctrl.h`, and a generic
  `Documentation/devicetree/bindings/clock/hisilicon,hisi-crg.yaml`.
* An earlier series *"Add CRG driver for Hi3798MV100 SoC"* reached v5 in March
  2023, also unmerged. It renames `Hi3798CV200` to `Hi3798` and factors out
  common code — a prerequisite the MV200 series builds on.
* *"net: hisi-femac: add support for Hi3798MV200, remove unmaintained
  compatibles"* — same author, also unmerged.

**Rebasing and re-submitting these series is the highest-value first task.**
The code exists and was reviewed; it needs someone to carry it forward.

### 2. No SoC or board device tree

`arch/arm64/boot/dts/hisilicon/` has only `hi3798cv200.dtsi` and
`hi3798cv200-poplar.dts`. Nothing for MV100/MV200/MV300, and
`Documentation/devicetree/bindings/arm/hisilicon/hisilicon.yaml` documents only
`hisilicon,hi3798cv200-poplar`.

`hi3798cv200.dtsi` is nonetheless the right starting template — the SoCs are
close relatives, which is exactly why the vendor ships one `hi3798mv200-series`
compatible for the whole family.

### 3. Pinctrl

There is no `drivers/pinctrl/hisilicon/` at all. `hi3798cv200-poplar` uses
`pinctrl-single`; the same approach should work here, driven by the pin mux
tables in the vendor DT.

### 4. Ethernet

`hisi_femac.c` is in tree but only binds `hisi-femac-v1/v2` and
`hi3516cv300-femac`. The vendor DT calls the block `hisilicon,hieth` at
`0xf9c30000`. Expect a small compatible addition plus verification of the
internal PHY reset sequence.

### 5. Everything media

VDEC/VENC/VPSS/HDMI/audio are HiSilicon's proprietary "bigfish" stack with no
mainline equivalent and no public documentation. A mainline port realistically
targets **headless / console / simple framebuffer**, with `lima` giving GPU
acceleration if a display pipeline is ever written. Treat video decode as out of
scope.

## 64-bit or 32-bit?

The cores are Cortex-A53. The vendor ships a 32-bit ARM kernel and userspace, but
`hi3798cv200` is an `arch/arm64` platform in mainline. Targeting **arm64** is the
better path: it follows the existing in-tree relative, avoids a dead-end ABI, and
the ARM Trusted Firmware already present in the `trustedcore` partition exposes
PSCI, which the vendor DT already uses (`enable-method = "psci"`).

## Suggested order of work

1. Rebase the MV100 CRG prerequisite series onto current mainline; get it building.
2. Rebase the MV200 CRG series on top. MV300 shares the CRG layout — confirm
   against the vendor `hisilicon,clock-reset` node before assuming.
3. Write `hi3798mv200.dtsi` starting from `hi3798cv200.dtsi`, filling addresses
   from [hardware.md](hardware.md).
4. Add a board `hi3798mv300-<board>.dts` with UART0, eMMC, and memory only.
5. Boot to a serial console via the stock U-Boot (`bootm`, load over TFTP first —
   the U-Boot environment already has `ipaddr`/`serverip` set).
6. Add eMMC, then USB, then Ethernet, then `lima`.
7. Submit upstream in the same order.

## Prior art worth reading

| Project | What it is |
|---|---|
| `JasonFreeLab/HiSTBLinuxV100R005C00SPC060` | HiSilicon BSP for MV100 / CV200 / MV200 / MV300 |
| `07bug/HiSTBLinuxV100R005C00SPC060` | same BSP, kernel 4.4.35, Docker-capable |
| `leandrotsampa/hisilicon-kernel` | vendor kernel 4.4.35 for CV200 / MV200 |
| `leandrotsampa/e2d-hi3798mv200` | Debian + Enigma2 + Kodi on MV200, vendor kernel |

All of these run **vendor** kernels, not mainline. As far as this survey found,
no one has booted mainline on an MV200 or MV300.

## Sources

- [drivers/clk/hisilicon in torvalds/linux](https://github.com/torvalds/linux/tree/master/drivers/clk/hisilicon)
- [arch/arm64/boot/dts/hisilicon in torvalds/linux](https://github.com/torvalds/linux/tree/master/arch/arm64/boot/dts/hisilicon)
- [hisilicon,hi3798mv200-dwc3.yaml](https://raw.githubusercontent.com/torvalds/linux/master/Documentation/devicetree/bindings/usb/hisilicon,hi3798mv200-dwc3.yaml)
- [Documentation/devicetree/bindings/arm/hisilicon/hisilicon.yaml](https://raw.githubusercontent.com/torvalds/linux/master/Documentation/devicetree/bindings/arm/hisilicon/hisilicon.yaml)
- [clk: hisilicon: add support for Hi3798MV200 (v4, Patchew)](https://patchew.org/linux/20240223-clk-mv200-v4-0-3e37e501d407@outlook.com/)
- [clk: hisilicon: add support for Hi3798MV200 (LWN)](https://lwn.net/Articles/963217/)
- [PATCH v5: Add CRG driver for Hi3798MV100 SoC (lore)](https://lore.kernel.org/all/d3b057408117a71bcd153f4a91bcdfe1.sboyd@kernel.org/T/)
- [net: hisi-femac: add support for Hi3798MV200 (lore)](https://lore.kernel.org/lkml/5cecd33c-7436-4b2a-84c2-8a28c87b26b3@linaro.org/t/)
- [config_mmc_dw_hi3798mv200 (kernelconfig.io)](https://www.kernelconfig.io/config_mmc_dw_hi3798mv200)
- [Armbian forum: Armbian for HiSilicon Hi3798mv300](https://forum.armbian.com/topic/18910-armbian-for-hisilicon-hi3798mv300/)
- [XDA: Hisilicon HI3798MV200](https://xdaforums.com/t/hisilicon-hi3798mv200.4183891/)
