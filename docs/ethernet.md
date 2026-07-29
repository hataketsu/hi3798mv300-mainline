# Ethernet

The on-board FE MAC (`hieth`) at `0xf9c30000`, 100 Mbit, with an internal FE PHY.

Status: the MAC probes but never attaches to its PHY, so no `eth0` exists:

```
hisi-femac f9c30000.ethernet: connect to PHY failed!
```

## The cause is a missing kconfig, not the hardware

```
CONFIG_HISI_FEMAC=y                   # the MAC
# CONFIG_MDIO_HISI_FEMAC is not set   # the MDIO bus -- never built
```

`drivers/net/mdio/mdio-hisi-femac.c` is a separate driver for the MDIO bus that
lives inside the MAC's register window (`mdio@1100`, i.e. `0xf9c31100`). Without
it there is no bus for the PHY to appear on, so `of_phy_connect()` fails no
matter how the DTS is written. Enabling `CONFIG_MDIO_HISI_FEMAC` is the fix.

**Not yet confirmed on hardware.** The kernel is built; the box has not booted
it.

## What the vendor firmware does

Worth recording even though the fix turned out to be elsewhere, because it
proves the register setup is not the problem and documents behaviour that is
invisible from the device tree.

The stock bootloader's FEPHY init, with `r4 = 0xf8a22000` (CRG) and
`r1 = 0xf8a20000` (perictrl):

```
CRG   0x0d0  bic #8              ; udelay(100)
CRG   0x0d0  bic #3              ; MAC clocks off
PERI  0x844  bic #0xa0 orr #0x50 ; FEPHY analog / mux configuration
CRG   0x388  orr #1              ; FEPHY clock enable
PERI  0x118  bic #0x1f  orr addr ; MDIO address  <-- see below
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

### The PHY's MDIO address is software-programmed

The interesting find. The address is not strapped -- the bootloader writes it
into **perictrl `0xf8a20118`, bits [4:0]**, taken from the `phy_addr`
environment variable:

```
phy_addr=2,1
```

which is also why the stock bootloader prints:

```
Eth up port phy at 0x02 is connect
```

The board DTS therefore says address 2:

```dts
&mdio {
	/delete-node/ ethernet-phy@1;

	tvbox_fephy: ethernet-phy@2 {
		reg = <2>;
		#phy-cells = <0>;
	};
};
```

This is only correct **because the vendor environment says so**. Change
`phy_addr` in the U-Boot environment and the DTS has to change with it. Nothing
in Linux programs `0xf8a20118`; the port relies on the bootloader having done it,
the same bet made for the MMC pin muxing.

`0xf8a20844` (FEPHY analog configuration) is likewise left as the bootloader set
it. Neither register is touched by the `0x388` reset, so both survive into
Linux.

## A working alternative

A USB Ethernet dongle (`r8152`, `cdc_ether`) needs none of this and works today,
now that USB host is up. Useful if the on-board MAC turns out to need more than
a kconfig.
