#!/usr/bin/env python3
"""Interactive serial terminal with hardware flow control forced off.

The board's UART header has no CTS line. A terminal that enables RTS/CTS -- as
minicom does by default -- will receive normally but never transmit, which looks
exactly like a dead RX pin on the board. This one disables every flow control
mechanism explicitly.

Ctrl+C is passed through to the board rather than interpreted, because it is
what the vendor bootloader tests for to stop autoboot. Quit with Ctrl-].

Usage:
    ./uart-term.py [--port /dev/ttyUSB0] [--baud 115200] [--log FILE] [--catch]

--catch hammers Ctrl+C once the bootloader banner appears, to win the autoboot
race automatically. Power-cycle the board after starting it.
"""

import argparse
import os
import select
import sys
import termios
import tty

import serial

QUIT = b"\x1d"  # Ctrl-]
BANNERS = (b"Bootrom start", b"Fastboot 3.3.0")
PROMPT = b"fastboot#"
TOO_LATE = (b"Starting kernel", b"Uncompressing Linux")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--log")
    ap.add_argument("--catch", action="store_true",
                    help="hammer Ctrl+C at the banner to stop autoboot")
    args = ap.parse_args()

    ser = serial.Serial(args.port, args.baud, timeout=0,
                        rtscts=False, dsrdtr=False, xonxoff=False)
    log = open(args.log, "wb") if args.log else None

    banner = f"[{args.port} @{args.baud} 8N1, no flow control — Ctrl-] to quit]"
    print(banner, flush=True)
    if args.catch:
        print("[--catch: power-cycle the board now]", flush=True)

    # Modem lines are informational only. CTS low is normal on a 3-wire cable
    # and is precisely what trips up a terminal configured for RTS/CTS.
    try:
        print(f"[CTS={ser.cts} DSR={ser.dsr} CD={ser.cd}]", flush=True)
    except (OSError, serial.SerialException):
        pass

    stdin = sys.stdin.fileno()
    saved = termios.tcgetattr(stdin)
    window = b""
    armed = False

    try:
        tty.setraw(stdin)
        while True:
            ready, _, _ = select.select([stdin, ser.fileno()], [], [], 0.02)

            if ser.fileno() in ready:
                chunk = ser.read(4096)
                if chunk:
                    os.write(sys.stdout.fileno(), chunk)
                    if log:
                        log.write(chunk)
                        log.flush()
                    if args.catch:
                        window = (window + chunk)[-4096:]
                        if not armed and any(b in window for b in BANNERS):
                            armed = True
                            ser.write(b"\x03" * 64)
                        if PROMPT in window or any(b in window for b in TOO_LATE):
                            armed = False
                            window = b""

            if armed:
                ser.write(b"\x03" * 8)

            if stdin in ready:
                key = os.read(stdin, 1024)
                if QUIT in key:
                    ser.write(key.split(QUIT)[0])
                    break
                ser.write(key)
    finally:
        termios.tcsetattr(stdin, termios.TCSADRAIN, saved)
        ser.close()
        if log:
            log.close()
        print("\r\n[closed]", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
