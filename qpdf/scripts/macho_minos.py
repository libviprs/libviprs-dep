#!/usr/bin/env python3
"""Check the macOS deployment target of every Mach-O in the given files.

Usage: macho_minos.py <max-minos> <file>...

Each file may be a thin or fat Mach-O, or a static library (an ar archive) whose
members are Mach-O objects. Anything else is skipped. A Mach-O whose deployment
target is newer than <max-minos>, or that has none at all, is reported on stderr
and the exit status is 1. This is what `otool -l` shows as minos, read straight
from the load commands so it also works on a Linux host that has no otool.
"""

import struct
import sys

LC_VERSION_MIN_MACOSX = 0x24
LC_BUILD_VERSION = 0x32
MAGICS_LE = {0xFEEDFACF: 32, 0xFEEDFACE: 28}  # magic -> header size, little endian
FAT_MAGIC = 0xCAFEBABE


def parse(text):
    """'11.0' or '11.0.1' as a comparable tuple."""
    return tuple(int(p) for p in text.split("."))


def show(packed):
    major, minor, patch = packed >> 16, (packed >> 8) & 0xFF, packed & 0xFF
    return f"{major}.{minor}" + (f".{patch}" if patch else "")


def minos_of(data):
    """The deployment target packed as xxxx.yy.zz, or None when there is no command for it."""
    (magic,) = struct.unpack_from("<I", data, 0)
    header = MAGICS_LE[magic]
    ncmds, sizeofcmds = struct.unpack_from("<II", data, 16)
    offset = header
    found = None
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", data, offset)
        if cmd == LC_BUILD_VERSION:
            found = struct.unpack_from("<I", data, offset + 12)[0]
        elif cmd == LC_VERSION_MIN_MACOSX and found is None:
            found = struct.unpack_from("<I", data, offset + 8)[0]
        if size < 8:
            break
        offset += size
    return found


def objects(data):
    """Yield the thin Mach-O images inside data: itself, fat slices, or ar members."""
    if len(data) < 8:
        return
    (magic,) = struct.unpack_from("<I", data, 0)
    if magic in MAGICS_LE:
        yield data
    elif struct.unpack_from(">I", data, 0)[0] == FAT_MAGIC:
        (count,) = struct.unpack_from(">I", data, 4)
        for i in range(count):
            offset, size = struct.unpack_from(">II", data, 8 + 20 * i + 8)
            yield from objects(data[offset : offset + size])
    elif data.startswith(b"!<arch>\n"):
        pos = 8
        while pos + 60 <= len(data):
            name = data[pos : pos + 16].decode("latin-1")
            size = int(data[pos + 48 : pos + 58].decode("latin-1").strip() or 0)
            body = data[pos + 60 : pos + 60 + size]
            if name.startswith("#1/"):
                body = body[int(name[3:].strip()) :]
            yield from objects(body)
            pos += 60 + size + (size % 2)


def main(argv):
    limit = parse(argv[1])
    bad = 0
    for path in argv[2:]:
        with open(path, "rb") as f:
            data = f.read()
        for obj in objects(data):
            try:
                packed = minos_of(obj)
            except struct.error:
                packed = None  # truncated load commands: no usable deployment target
            if packed is None:
                print(f"{path}: a Mach-O with no deployment target", file=sys.stderr)
                bad += 1
            elif parse(show(packed)) > limit:
                print(
                    f"{path}: deployment target {show(packed)} is newer than {argv[1]}",
                    file=sys.stderr,
                )
                bad += 1
                break
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
