#!/usr/bin/env python3
"""Run one command on the box over the serial console and stream the reply.

Usage:
    ./uart-cmd.py "<command>" [max_seconds] [idle_seconds] [--port /dev/ttyUSB0]

The console echoes back whatever is sent, so output always begins with a
mangled copy of the command itself. Never test for a marker string that also
appears in the command you sent — the echo will match it. Compare sizes or
grep counts instead.

Requires pyserial.
"""

import sys
import time

import serial

DEFAULT_PORT = "/dev/ttyUSB0"
BAUD = 115200
PROMPT_TAIL = (b"$ ", b"# ")


def run(port, command, max_seconds, idle_seconds):
    ser = serial.Serial(port, BAUD, timeout=0.5)
    try:
        ser.reset_input_buffer()
        ser.write((command + "\n").encode())

        buf = b""
        start = last = time.time()
        while time.time() - start < max_seconds:
            chunk = ser.read(8192)
            if chunk:
                buf += chunk
                last = time.time()
                sys.stdout.write(chunk.decode("utf-8", "replace"))
                sys.stdout.flush()
                continue
            # Idle. Stop once the prompt has been back for a moment, since
            # kernel log lines can still arrive after it.
            if any(buf[-30:].endswith(p) for p in PROMPT_TAIL) and \
                    time.time() - last > 1.0:
                return 0
            if time.time() - last > idle_seconds:
                sys.stderr.write("\n[idle timeout]\n")
                return 2
        sys.stderr.write("\n[max timeout]\n")
        return 2
    finally:
        ser.close()


def main(argv):
    port = DEFAULT_PORT
    if "--port" in argv:
        i = argv.index("--port")
        port = argv[i + 1]
        del argv[i : i + 2]

    if len(argv) < 2:
        print(__doc__)
        return 1

    command = argv[1]
    max_seconds = float(argv[2]) if len(argv) > 2 else 60.0
    idle_seconds = float(argv[3]) if len(argv) > 3 else 20.0
    return run(port, command, max_seconds, idle_seconds)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
