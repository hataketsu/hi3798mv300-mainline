# Wi-Fi

An RTL8822BS on SDIO, working on mainline `rtw88` with no out-of-tree code:

```
mmc2: new high speed SDIO card at address 0001
rtw88_8822bs mmc2:0001:1: Firmware version 27.2.0, H2C version 13
wlan0: associated
```

Firmware is Debian's `firmware-realtek` (`rtw88/rtw8822b_fw.bin`).

## Finding the controller

The vendor DTS names the SDIO controller but says nothing about what is on it:

```dts
himciv200.SD@f9c40000 {
    compatible = "hi3798mv200,himciv200";
    reg = <0xf9c40000 0x1000  0xf8a21090 0x20>;
    caps = <0x8007000f>;    /* 4-bit, HS, SDIO IRQ, SDR12/25/50 */
    caps2 = <0x4000>;
};
```

The stock Android boot log resolves it -- an SELinux denial, of all things,
prints the full sysfs path:

```
/sys/devices/platform/soc/f9c40000.himciv200.SD/mmc_host/mmc2/mmc2:0001/mmc2:0001:1/net/wlan0/address
```

So `0xf9c40000` -> `mmc2` -> `wlan0`. That is `sd1` in the mainline dtsi.

Usefully, the vendor node declares **no power-enable GPIO**, unlike most
Realtek SDIO parts. Nothing has to be sequenced, which is just as well because
the GPIO banks still do not probe -- they defer forever on
`gpio-ranges = <&ioconfig ...>` waiting for a pinctrl driver.

## The dtsi typo

`sd1` never probed. No `mmc2`, no dmesg line, and nothing in
`/sys/kernel/debug/devices_deferred` either:

```
$ ls /sys/class/mmc_host/
mmc0  mmc1

$ ls /sys/bus/platform/drivers/dwmmc_hi3798mv200/
bind  f9820000.mmc  f9830000.mmc  module  uevent  unbind
```

The platform device existed and `status` was `okay`, so it was not a disabled
node. Comparing the three controllers:

```
sd0, emmc:  compatible = "hisilicon,hi3798mv200-dw-mshc"
sd1:        compatible = "hi3798mv200,dw-mshc"        <- vendor and family transposed
driver:     { .compatible = "hisilicon,hi3798mv200-dw-mshc" }
```

`sd1` matched no driver at all. A device with no driver is not deferred, it is
simply ignored -- which is why `devices_deferred` was empty and the failure was
completely silent. The board DTS overrides the compatible.

## Configuration

`wpa_supplicant` with the SSID as hex, because it contains non-ASCII:

```
network={
	ssid=5433206e68c3a0203130207068c3b26e6720333038
	psk=<pbkdf2 of passphrase and ssid>
	key_mgmt=WPA-PSK
	priority=5
}
```

Hex avoids any question of how the file is encoded, and the PSK is stored
hashed rather than as the passphrase. `iw dev wlan0 scan | grep SSID` is the
authoritative source for the exact bytes -- a name that looks plain can carry
diacritics that do not survive being typed by hand.

`systemd-networkd` does DHCP:

```
[Match]
Name=wlan0

[Network]
DHCP=yes
```
