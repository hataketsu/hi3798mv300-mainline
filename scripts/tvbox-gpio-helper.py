#!/usr/bin/env python3
"""Privileged PL061 register access for tvbox-panel, and nothing else.

The panel runs unprivileged; poking GPIO registers needs /dev/mem, which does
not. Rather than run the web server as root, it calls this helper through
sudo. The helper is the security boundary: it takes a bank index and a pin
number, never an address, so a bug or a hostile request in the web layer
cannot reach memory outside the ten PL061 windows listed below.

    tvbox-gpio-helper.py dump
    tvbox-gpio-helper.py dir  <bank> <pin> in|out
    tvbox-gpio-helper.py set  <bank> <pin> 0|1
    tvbox-gpio-helper.py restore <bank> <dir-hex>

PL061 register map, relative to each bank:
    0x000-0x3fc  GPIODATA, address bits [9:2] mask which pins the access
                 touches -- so writing pin n means offset (1 << n) << 2 and
                 no read-modify-write race with the kernel
    0x400        GPIODIR, 1 = output
    0x420        GPIOAFSEL, 1 = hardware controls the pin
"""

from __future__ import annotations

import json
import mmap
import os
import sys

# dtsi node name -> physical base. gpio5 is the odd one: it lives at
# 0xf8004000 in the always-on block, not with the rest at 0xf8b2xxxx.
BANKS = [
    ("gpio0", 0xF8B20000),
    ("gpio1", 0xF8B21000),
    ("gpio2", 0xF8B22000),
    ("gpio3", 0xF8B23000),
    ("gpio4", 0xF8B24000),
    ("gpio5", 0xF8004000),
    ("gpio6", 0xF8B26000),
    ("gpio7", 0xF8B27000),
    ("gpio8", 0xF8B28000),
    ("gpio9", 0xF8B29000),
]

GPIODATA_ALL = 0x3FC
GPIODIR = 0x400
GPIOAFSEL = 0x420
PAGE = 0x1000


class Bank:
    def __init__(self, base: int) -> None:
        self.fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        self.map = mmap.mmap(self.fd, PAGE, offset=base)

    def close(self) -> None:
        self.map.close()
        os.close(self.fd)

    def read(self, offset: int) -> int:
        return int.from_bytes(self.map[offset : offset + 4], "little")

    def write(self, offset: int, value: int) -> None:
        self.map[offset : offset + 4] = (value & 0xFFFFFFFF).to_bytes(4, "little")


def check_bank(argument: str) -> int:
    index = int(argument)
    if not 0 <= index < len(BANKS):
        raise SystemExit(f"bank out of range: {index}")
    return index


def check_pin(argument: str) -> int:
    pin = int(argument)
    if not 0 <= pin <= 7:
        raise SystemExit(f"pin out of range: {pin}")
    return pin


def cmd_dump() -> int:
    result = []
    for index, (name, base) in enumerate(BANKS):
        bank = Bank(base)
        try:
            direction = bank.read(GPIODIR)
            data = bank.read(GPIODATA_ALL)
            afsel = bank.read(GPIOAFSEL)
        finally:
            bank.close()
        result.append(
            {
                "index": index,
                "name": name,
                "base": f"0x{base:08x}",
                "dir": direction & 0xFF,
                "data": data & 0xFF,
                "afsel": afsel & 0xFF,
                "pins": [
                    {
                        "pin": pin,
                        "output": bool(direction >> pin & 1),
                        "level": bool(data >> pin & 1),
                        "hw": bool(afsel >> pin & 1),
                    }
                    for pin in range(8)
                ],
            }
        )
    json.dump(result, sys.stdout)
    return 0


def cmd_dir(bank_arg: str, pin_arg: str, mode: str) -> int:
    index, pin = check_bank(bank_arg), check_pin(pin_arg)
    if mode not in ("in", "out"):
        raise SystemExit("direction must be in or out")
    bank = Bank(BANKS[index][1])
    try:
        direction = bank.read(GPIODIR)
        if mode == "out":
            direction |= 1 << pin
        else:
            direction &= ~(1 << pin)
        bank.write(GPIODIR, direction)
        print(f"0x{bank.read(GPIODIR) & 0xFF:02x}")
    finally:
        bank.close()
    return 0


def cmd_set(bank_arg: str, pin_arg: str, level: str) -> int:
    index, pin = check_bank(bank_arg), check_pin(pin_arg)
    if level not in ("0", "1"):
        raise SystemExit("level must be 0 or 1")
    bank = Bank(BANKS[index][1])
    try:
        # Masked write: only this pin is affected, whatever else is going on.
        bank.write((1 << pin) << 2, 0xFF if level == "1" else 0x00)
        print(f"0x{bank.read(GPIODATA_ALL) & 0xFF:02x}")
    finally:
        bank.close()
    return 0


def cmd_restore(bank_arg: str, dir_hex: str) -> int:
    index = check_bank(bank_arg)
    value = int(dir_hex, 16)
    if not 0 <= value <= 0xFF:
        raise SystemExit("direction mask must be 0x00-0xff")
    bank = Bank(BANKS[index][1])
    try:
        bank.write(GPIODIR, value)
        print(f"0x{bank.read(GPIODIR) & 0xFF:02x}")
    finally:
        bank.close()
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    command = argv[1]
    try:
        if command == "dump" and len(argv) == 2:
            return cmd_dump()
        if command == "dir" and len(argv) == 5:
            return cmd_dir(argv[2], argv[3], argv[4])
        if command == "set" and len(argv) == 5:
            return cmd_set(argv[2], argv[3], argv[4])
        if command == "restore" and len(argv) == 4:
            return cmd_restore(argv[2], argv[3])
    except ValueError as exc:
        print(f"bad argument: {exc}", file=sys.stderr)
        return 2
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
