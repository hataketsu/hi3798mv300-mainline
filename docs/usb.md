# USB

USB 2.0 host works: EHCI and OHCI both register, and the box boots its root
filesystem off a USB stick. USB 3.0 (xhci/dwc3/combphy) is still disabled.

Getting there needed the PHY reverse engineered out of the stock bootloader,
because neither the vendor device tree nor the out-of-tree dtsi describes it
usably.

## Why the device tree is no help

The vendor DTS has no PHY node at all. Its OTG node maps OHCI, EHCI and one
stray word, and the BSP does the rest in C:

```dts
hi3798cv200.hiusbotg {
    compatible = "hiusbotg";
    reg = <0xf9880000 0x10000  0xf9890000 0x10000  0xf8a2012c 0x04>;
};
```

The mainline-bound dtsi puts the PHY under a `usb2phy-test-bus` node with no
`reg` of its own, expecting a bus driver that exists in no tree. The upstream
driver ioremaps its own `reg`, so it fails outright:

```
hisi-inno-phy f9865000.usb-phy-bus:usb-phy: error -EINVAL: invalid resource
```

## A wrong turn worth recording

Guessing `0xf8a2012c` from the vendor OTG node looked reasonable: upstream
hi3798cv200 puts its PHY test port at `0x120` in the same block, and a single
32-bit word is exactly what the INNO test-port protocol drives.

It bound, and both host controllers registered — and then every port timed out:

```
hisi-inno-phy f8a2012c.usb2-phy: Support 2 ports in maximum
ehci-platform f9890000.usb: port 2 reset error -110
```

A PHY that accepts writes to the wrong address looks exactly like a dead PHY.

## What the bootloader actually does

Its USB2 init routine selects a base by chip ID:

```
ldr   r2, [pc, #236]   ; 0x37980210   <- this chip
cmpeq r0, r2
bne   <other model>
ldr   r4, [pc, #212]   ; 0xf9865fff
```

then writes through `r4` with negative offsets:

```
str r3, [r4, #-4071]  -> 0xf9865018   reg 0x06 = 0x04
str r3, [r4, #-4087]  -> 0xf9865008   reg 0x02 = 0x6c
str r3, [r4, #-4095]  -> 0xf9865000   reg 0x00 = 0x18
str r3, [r4, #-4079]  -> 0xf9865010   reg 0x04 = 0xd7
str r3, [r4, #-3979]  -> 0xf9865074   reg 0x1d = 0x1e
str r3, [r4, #-3971]  -> 0xf986507c   reg 0x1f = 0x6e
```

each repeated `0x400` further along for port 1, then:

```
CRG 0xbc  bic #0x300      ; deassert UTMI resets for both ports
CRG 0xb8  orr #0x7f
CRG 0xb8  bic #0x37000    ; deassert the host controllers
```

Two conclusions:

**The register access method is different.** This SoC does not bit-bang a test
port with address, data and a clock bit the way hi3798cv200 does. Its PHY
registers are **mapped directly** — a 4 KiB window at `0xf9865000`, one 32-bit
word per 8-bit register, port 1 at `+0x400`. That is what Yang Xiwen's
Hi3798MV200 patch implements: `of_iomap()` per port child, `writel(data,
base + addr * 4)`.

**Clearing bits 8 and 9 of CRG `0xbc`** confirms the dtsi's per-port
`resets = <&crg 0xbc 8>` / `<&crg 0xbc 9>` are right.

## The device tree that works

```dts
&soc {
	tvbox_usb2_phy: usb2-phy@9865000 {
		compatible = "hisilicon,hi3798mv200-usb2-phy";
		reg = <0x9865000 0x1000>;
		clocks = <&crg HI3798MV200_USB2_PHY2_REF_CLK>;
		resets = <&crg 0xbc 4>, <&crg 0xbc 15>;
		#address-cells = <1>;
		#size-cells = <1>;
		ranges = <0x0 0x9865000 0x1000>;

		tvbox_usb2_port0: phy@0   { reg = <0x0   0x400>; resets = <&crg 0xbc 8>; #phy-cells = <0>; };
		tvbox_usb2_port1: phy@400 { reg = <0x400 0x400>; resets = <&crg 0xbc 9>; #phy-cells = <0>; };
	};
};
```

The node keeps a `reg` of its own because the driver still ioremaps resource 0,
and each port child gets a real `reg` so `of_iomap()` can translate it. The
second reset, `<&crg 0xbc 15>`, is the one the dtsi hangs on the test-bus node;
nothing drives that bus here, so the PHY releases it itself — which is why the
driver moved to `reset_control_array`.

The bus's APB clock is left as the bootloader set it, on the same reasoning as
the MMC pin muxing.

## Patches

Two from Yang Xiwen's `b4/inno-phy`, two local:

* `phy: hisilicon: hisi-inno-phy: enable clocks for every ports`
* `phy: hisilicon: hisi-inno-phy: add support for Hi3798MV200 INNO PHY`
* [`0004`](../patches/kernel/0004-phy-hisi-inno-phy-use-the-current-reset-array-API.patch)
  — `devm_reset_control_array_get(dev, shared, optional)` no longer exists; the
  two booleans became `enum reset_control_flags`
* [`0005`](../patches/kernel/0005-phy-hisi-inno-phy-program-the-Hi3798MV300-setup-registers.patch)
  — the five extra registers above, which the upstream driver never writes

## Still open

`clk_ignore_unused` is currently required or USB dies the moment the clock
framework gates unused clocks:

```
[    1.157628] clk: Disabling unused clocks
[    1.284416] ehci-platform f9890000.usb: port 2 reset error -110
```

Some clock in the USB path is claimed by nobody. Note it is *not* the APB clock
the test-bus node names — `HI3798MV200_APB_CLK` is registered as a fixed-rate
clock, which has no gate and cannot be disabled. The real one has not been
identified yet.
