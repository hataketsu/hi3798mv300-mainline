# Ethernet

The on-board FE MAC (`hieth`) at `0xf9c30000`, 100 Mbit, with an internal FE PHY.

Status: **working**, confirmed on hardware.

```
Generic PHY f9c31100.mdio:01: attached PHY driver
hisi-femac f9c30000.ethernet end0: renamed from eth0
hisi-femac f9c30000.ethernet end0: Link is Up - 100Mbps/Full - flow control off
```

Getting there took three separate defects, each of which hid the next.

## 1. The MDIO driver was never built

```
CONFIG_HISI_FEMAC=y                   # the MAC
# CONFIG_MDIO_HISI_FEMAC is not set   # the MDIO bus -- never built
```

`drivers/net/mdio/mdio-hisi-femac.c` is a separate driver for the MDIO bus that
lives inside the MAC's register window (`mdio@1100`, i.e. `0xf9c31100`). Without
it there is no bus for the PHY to appear on, so `of_phy_connect()` fails no
matter how the DTS is written:

```
hisi-femac f9c30000.ethernet: connect to PHY failed!
```

Enabling `CONFIG_MDIO_HISI_FEMAC` moves the failure one layer down.

## 2. The dtsi's MDIO node has no clock

```
hisi-femac-mdio f9c31100.mdio: probe with driver hisi-femac-mdio failed with error -2
hisi-femac f9c30000.ethernet: connect to PHY failed!
```

`-2` is `-ENOENT`, straight out of `hisi_femac_mdio_probe()`:

```c
data->clk = devm_clk_get(&pdev->dev, NULL);
if (IS_ERR(data->clk)) {
        ret = PTR_ERR(data->clk);
        goto err_out_free_mdiobus;
}
```

`hisilicon-femac-mdio.txt` lists `clocks` as required, but `hi3798mv200.dtsi`
omits it. The MDIO registers sit inside the MAC's window, so the clock that
gates access to them is the MAC interface clock off the AHB -- `clk_femacif`,
`HI3798MV200_ETH_BUS_CLK`, CRG `0xd0` bit 0. See
[`../patches/kernel/0006-arm64-dts-hisilicon-give-the-Hi3798MV200-MDIO-bus-its-clock.patch`](../patches/kernel/0006-arm64-dts-hisilicon-give-the-Hi3798MV200-MDIO-bus-its-clock.patch).

The femac node already claims the same gate. That is fine -- the clock framework
refcounts it, and the vendor bootloader turns on `0xd0` bits `[1:0]` together
anyway.

Probe order is not the problem, incidentally. `hisi_femac_drv_probe()` enables
all three clocks and runs both resets *before* it calls
`of_platform_device_create()` on the `mdio` subnode, so the bus is only scanned
once the FEPHY is clocked and out of reset.

## 3. The PHY is at MDIO address 1, not 2

With the bus registered, the PHY still would not answer:

```
Generic PHY f9c31100.mdio:02: attached PHY driver
phy_id=0x00000000, phy_mode=mii
```

`phy_id` of zero means every MDIO read came back as zero. The bus was fine; it
was being asked the wrong address.

The address is not strapped -- the bootloader writes it into **perictrl
`0xf8a20118`, bits [4:0]**. Read back from a running kernel:

```
# busybox devmem 0xf8a20118 32
0x03000001
```

Bits `[4:0]` are `1`. So the dtsi's own `ethernet-phy@1` was right all along and
the board DTS override to address 2 was wrong. The override came from reading
the stock environment

```
phy_addr=2,1
```

and the stock bootloader's message

```
Eth up port phy at 0x02 is connect
```

as "address 2". The hardware disagrees, and the hardware is what answers MDIO
reads. Whatever `2,1` means, the value that reaches `0xf8a20118[4:0]` is `1`.

The board DTS therefore adds nothing but `status`:

```dts
&femac {
	status = "okay";
};
```

Sanity check once it is up:

```
# ethtool end0 | grep -E 'PHYAD|Speed|Link detected'
	Speed: 100Mb/s
	PHYAD: 1
	Link detected: yes
# cat /sys/bus/mdio_bus/devices/f9c31100.mdio:01/phy_id
0x20669900
```

`0x20669900` is a real ID rather than the `0x00000000` of a bus talking to
nobody.

## What the vendor firmware does

The stock bootloader's FEPHY init, with `r4 = 0xf8a22000` (CRG) and
`r1 = 0xf8a20000` (perictrl):

```
CRG   0x0d0  bic #8              ; udelay(100)
CRG   0x0d0  bic #3              ; MAC clocks off
PERI  0x844  bic #0xa0 orr #0x50 ; FEPHY analog / mux configuration
CRG   0x388  orr #1              ; FEPHY clock enable
PERI  0x118  bic #0x1f  orr addr ; MDIO address
CRG   0x388  orr #0x10           ; assert FEPHY reset, udelay(10)
CRG   0x388  bic #0x10           ; deassert,           udelay(20000)
CRG   0x0d0  orr #3              ; MAC clocks on,      mdelay(5)
```

This matches the dtsi, which is a good sign for the port:

```dts
resets = <&crg 0xd0 3>, <&crg 0x388 4>;
reset-names = "mac", "phy";
```

`0x388` bit 4 is exactly the PHY reset the binding names, and bit 0 is the FEPHY
clock.

Nothing in Linux programs `0xf8a20118` or `0xf8a20844`; the port relies on the
bootloader having done it, the same bet made for the MMC pin muxing. The bet
holds even though `bootcmd` was replaced -- the values are still there at
runtime:

```
0xf8a20118 = 0x03000001   MDIO address 1
0xf8a20844 = 0x00000050   analog config, matches "bic #0xa0 orr #0x50"
0xf8a220d0 = 0x00000003   MAC clocks on
0xf8a22388 = 0x00000001   FEPHY clock on, reset deasserted
```

Neither perictrl register is touched by the `0x388` reset, so both survive into
Linux.

## The MAC address is random

`mac-address` in the DTS is all zeros and nothing fills it in, so the driver
falls back:

```
hisi-femac f9c30000.ethernet: using random MAC address xx:xx:xx:xx:xx:xx
```

In practice the interface is stable anyway -- systemd's udev
`MACAddressPolicy=persistent` derives one from `/etc/machine-id`, so `end0`
keeps the same address across reboots even though the driver calls it random.
Anything that needs the real vendor MAC has to read it out of the stock
partitions and patch the DTS.

## A working alternative

A USB Ethernet dongle (`r8152`, `cdc_ether`) needs none of this and works today,
now that USB host is up.
