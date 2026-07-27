#!/usr/bin/env python3
"""Minimal read-only TFTP server, for serving boot images to a board.

Usage:
    sudo ./tftpd.py [rootdir] [--bind 0.0.0.0]

Serves files out of rootdir (default ./tftp) over UDP port 69. Read requests
only — write requests are rejected. Supports the blksize, tsize and timeout
options so large images transfer at a sane speed.
"""

import os
import socket
import struct
import sys

RRQ, WRQ, DATA, ACK, ERROR, OACK = 1, 2, 3, 4, 5, 6


def send_error(sock, addr, code, msg):
    sock.sendto(struct.pack(">HH", ERROR, code) + msg.encode() + b"\0", addr)


def parse_request(payload):
    parts = payload.split(b"\0")
    filename = parts[0].decode("ascii", "replace")
    mode = parts[1].decode("ascii", "replace").lower() if len(parts) > 1 else "octet"
    opts = {}
    rest = parts[2:]
    for i in range(0, len(rest) - 1, 2):
        if rest[i]:
            opts[rest[i].decode("ascii", "replace").lower()] = rest[i + 1].decode(
                "ascii", "replace"
            )
    return filename, mode, opts


def serve_file(root, filename, addr, opts):
    safe = os.path.normpath("/" + filename).lstrip("/")
    path = os.path.join(root, safe)
    if not os.path.isfile(path):
        return None, f"not found: {safe}"
    return path, None


def handle(root, sock, addr, payload):
    filename, _mode, opts = parse_request(payload)
    path, err = serve_file(root, filename, addr, opts)
    if err:
        print(f"  {addr[0]}: {err}")
        send_error(sock, addr, 1, "File not found")
        return

    size = os.path.getsize(path)
    blksize = 512
    xfer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    xfer.settimeout(5)

    ack_opts = {}
    if "blksize" in opts:
        blksize = max(8, min(int(opts["blksize"]), 65464))
        ack_opts["blksize"] = str(blksize)
    if "tsize" in opts:
        ack_opts["tsize"] = str(size)
    if "timeout" in opts:
        ack_opts["timeout"] = opts["timeout"]

    print(f"  {addr[0]}: RRQ {filename} ({size} bytes, blksize={blksize})")

    if ack_opts:
        pkt = struct.pack(">H", OACK)
        for k, v in ack_opts.items():
            pkt += k.encode() + b"\0" + v.encode() + b"\0"
        xfer.sendto(pkt, addr)
        try:
            data, addr = xfer.recvfrom(1024)
        except socket.timeout:
            print("  timeout waiting for OACK ack")
            xfer.close()
            return
        if struct.unpack(">H", data[:2])[0] != ACK:
            xfer.close()
            return

    with open(path, "rb") as fh:
        block = 1
        sent = 0
        while True:
            chunk = fh.read(blksize)
            pkt = struct.pack(">HH", DATA, block & 0xFFFF) + chunk
            for attempt in range(5):
                xfer.sendto(pkt, addr)
                try:
                    data, addr = xfer.recvfrom(1024)
                except socket.timeout:
                    continue
                op, ackblk = struct.unpack(">HH", data[:4])
                if op == ACK and ackblk == (block & 0xFFFF):
                    break
                if op == ERROR:
                    msg = data[4:].split(b"\0")[0].decode("ascii", "replace")
                    print(f"  client error: {msg}")
                    xfer.close()
                    return
            else:
                print(f"  giving up at block {block}")
                xfer.close()
                return
            sent += len(chunk)
            block += 1
            if len(chunk) < blksize:
                break
    print(f"  done: {sent}/{size} bytes")
    xfer.close()


def main(argv):
    root = os.path.abspath(argv[1]) if len(argv) > 1 and not argv[1].startswith("-") \
        else os.path.abspath("tftp")
    bind = "0.0.0.0"
    if "--bind" in argv:
        bind = argv[argv.index("--bind") + 1]

    if not os.path.isdir(root):
        print(f"no such directory: {root}")
        return 1

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((bind, 69))
    except PermissionError:
        print("binding UDP/69 needs root — run with sudo")
        return 1

    print(f"tftpd serving {root} on {bind}:69")
    print("files: " + ", ".join(sorted(os.listdir(root))))
    while True:
        payload, addr = sock.recvfrom(2048)
        op = struct.unpack(">H", payload[:2])[0]
        if op == RRQ:
            handle(root, sock, addr, payload[2:])
        elif op == WRQ:
            print(f"  {addr[0]}: WRQ rejected (read-only server)")
            send_error(sock, addr, 2, "server is read-only")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
