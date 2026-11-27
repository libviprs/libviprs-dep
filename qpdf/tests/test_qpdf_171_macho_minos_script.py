"""#171: macho_minos.py fails closed.

The checker reads Mach-O headers by hand, so anything it cannot read has to be
a failure rather than a pass: a Fat64 image, a byte-swapped header, or a mac
archive in which it finds no Mach-O at all. These run the script itself, so they
need no mac-arm64 archive and run on every host.
"""

import os
import struct
import subprocess
import sys

import pytest
from test_qpdf_verify_archive import ar, macho

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "macho_minos.py")


def check(tmp_path, *blobs, limit="11.0"):
    paths = []
    for i, blob in enumerate(blobs):
        p = tmp_path / f"f{i}"
        p.write_bytes(blob)
        paths.append(str(p))
    return subprocess.run(
        [sys.executable, "-I", SCRIPT, limit, *paths], capture_output=True, text=True
    )


def fat(*slices, magic=0xCAFEBABE):
    """A fat image holding the given thin images, big endian like the real thing."""
    out = struct.pack(">II", magic, len(slices))
    offset = 8 + 20 * len(slices)
    body = b""
    for s in slices:
        out += struct.pack(">IIIII", 0x0100000C, 0, offset + len(body), len(s), 14)
        body += s
    return out + body


def macho32(minos=(11, 0)):
    """A 32-bit Mach-O with one LC_BUILD_VERSION command saying macOS <minos>."""
    major, minor = minos
    header = b"\xce\xfa\xed\xfe" + struct.pack("<IIIIII", 12, 0, 6, 1, 24, 0)
    cmd = struct.pack("<IIIIII", 0x32, 24, 1, (major << 16) | (minor << 8), 14 << 16, 0)
    return header + cmd


def test_171_a_fat64_image_is_refused(tmp_path):
    r = check(tmp_path, fat(macho("arm64", (26, 0)), magic=0xCAFEBABF))
    assert r.returncode != 0
    assert "Fat64" in r.stderr or "fat64" in r.stderr.lower()


@pytest.mark.parametrize("swapped", [b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce"])
def test_171_a_byte_swapped_mach_o_is_refused(tmp_path, swapped):
    r = check(tmp_path, swapped + macho("arm64")[4:])
    assert r.returncode != 0
    assert "byte" in r.stderr.lower() or "unsupported" in r.stderr.lower()


def test_171_files_holding_no_mach_o_at_all_are_refused(tmp_path):
    r = check(tmp_path, b"\x7fELF" + b"\0" * 60, ar(b"not an object"))
    assert r.returncode != 0
    assert "no Mach-O" in r.stderr


def test_171_one_mach_o_among_other_files_is_enough(tmp_path):
    r = check(tmp_path, b"\x7fELF" + b"\0" * 60, macho("arm64", (11, 0)))
    assert r.returncode == 0, r.stderr


def test_171_a_fat_image_is_read_slice_by_slice(tmp_path):
    ok = check(tmp_path, fat(macho("arm64", (11, 0)), macho("x64", (10, 15))))
    assert ok.returncode == 0, ok.stderr
    bad = check(tmp_path, fat(macho("arm64", (11, 0)), macho("x64", (13, 0))))
    assert bad.returncode != 0 and "13.0" in bad.stderr


def test_171_a_32_bit_mach_o_is_read(tmp_path):
    ok = check(tmp_path, macho32((10, 9)))
    assert ok.returncode == 0, ok.stderr
    bad = check(tmp_path, macho32((12, 0)))
    assert bad.returncode != 0 and "12.0" in bad.stderr


def test_171_a_header_with_no_load_commands_has_no_deployment_target(tmp_path):
    header = bytearray(macho("arm64")[:32])
    struct.pack_into("<II", header, 16, 0, 0)  # ncmds and sizeofcmds
    header = bytes(header)
    r = check(tmp_path, header)
    assert r.returncode != 0
    assert "no deployment target" in r.stderr
