#!/usr/bin/env python3
"""Extract flattened device tree blobs from HiSilicon STB firmware images.

Handles two containers:

  * Android DTBO images (magic 0xd7b7ab1e), e.g. the `dtbo` partition
  * raw partition dumps with an FDT appended, e.g. `boot` and `recovery`

Usage:
    ./extract-dtb.py OUTDIR IMAGE [IMAGE ...]

Every blob found is written to OUTDIR and, if `dtc` is available, decompiled
to a matching .dts next to it.
"""

import os
import shutil
import struct
import subprocess
import sys

FDT_MAGIC = b"\xd0\x0d\xfe\xed"
DTBO_MAGIC = 0xD7B7AB1E


def fdt_header(buf, off):
    """Return (totalsize, version, last_comp_version) or None if malformed."""
    if off + 28 > len(buf):
        return None
    magic, total, _struct_off, _str_off, _rsv_off, ver, last = struct.unpack(
        ">7I", buf[off : off + 28]
    )
    if magic != 0xD00DFEED:
        return None
    return total, ver, last


def extract_dtbo(buf, stem, outdir):
    """Yield paths written from an Android DTBO container."""
    magic, total_size, hdr_sz, ent_sz, ent_cnt, ent_off, page_sz, ver = struct.unpack(
        ">8I", buf[:32]
    )
    print(
        f"  DTBO container: total={total_size} entries={ent_cnt} "
        f"page={page_sz} version={ver}"
    )
    written = []
    for i in range(ent_cnt):
        off = ent_off + i * ent_sz
        dt_size, dt_off, dt_id, dt_rev = struct.unpack(">4I", buf[off : off + 16])
        blob = buf[dt_off : dt_off + dt_size]
        hdr = fdt_header(blob, 0)
        if hdr is None:
            print(f"  entry {i}: not a valid FDT at 0x{dt_off:x}, skipping")
            continue
        path = os.path.join(outdir, f"{stem}-dtbo{i}.dtb")
        with open(path, "wb") as fh:
            fh.write(blob)
        print(
            f"  entry {i}: id={dt_id} rev={dt_rev} size={dt_size} "
            f"@0x{dt_off:x} -> {path}"
        )
        written.append(path)
    return written


def extract_appended(buf, stem, outdir):
    """Yield paths written by scanning for bare FDTs in a raw image."""
    written = []
    off = 0
    n = 0
    while True:
        i = buf.find(FDT_MAGIC, off)
        if i < 0:
            break
        off = i + 4
        hdr = fdt_header(buf, i)
        if hdr is None:
            continue
        total, ver, _last = hdr
        # Guard against ARM instruction sequences that happen to contain the
        # magic: a real FDT is >1 KiB, fits in the image, and is version 16/17.
        if not (16 <= ver <= 17 and 1024 < total <= len(buf) - i):
            continue
        path = os.path.join(outdir, f"{stem}-{n}.dtb")
        with open(path, "wb") as fh:
            fh.write(buf[i : i + total])
        print(f"  FDT @0x{i:x} size={total} version={ver} -> {path}")
        written.append(path)
        n += 1
    return written


def decompile(paths):
    if not shutil.which("dtc"):
        print("dtc not found, skipping decompilation")
        return
    for path in paths:
        dts = path[:-4] + ".dts"
        result = subprocess.run(
            ["dtc", "-I", "dtb", "-O", "dts", "-o", dts, path],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print(f"  decompiled -> {dts}")
        else:
            print(f"  dtc failed on {path}: {result.stderr.strip()}")


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    outdir, images = argv[1], argv[2:]
    os.makedirs(outdir, exist_ok=True)

    written = []
    for image in images:
        with open(image, "rb") as fh:
            buf = fh.read()
        stem = os.path.splitext(os.path.basename(image))[0]
        print(f"{image} ({len(buf)} bytes)")
        if len(buf) >= 32 and struct.unpack(">I", buf[:4])[0] == DTBO_MAGIC:
            written += extract_dtbo(buf, stem, outdir)
        else:
            written += extract_appended(buf, stem, outdir)

    if not written:
        print("no device trees found")
        return 1
    decompile(written)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
