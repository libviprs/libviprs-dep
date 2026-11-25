#!/usr/bin/env python3
"""Regenerate the two PDF fixtures qpdf/scripts/verify_archive.sh checks against.

good.pdf is a one-page PDF with a correct xref table and a Flate stream.
corrupt.pdf is the same file with the Flate data damaged and the trailer
cut off, so `qpdf --check` has to complain about it.
"""

import os
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))


def build():
    content = zlib.compress(b"BT /F1 12 Tf 20 100 Td (qpdf check) Tj ET\n")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(content)
        + content
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out), content


def main():
    good, content = build()
    damaged = bytearray(good)
    start = good.index(content)
    for i in range(start + 4, start + len(content) - 2):
        damaged[i] ^= 0xFF
    corrupt = bytes(damaged)[: good.index(b"trailer")]
    for name, data in (("good.pdf", good), ("corrupt.pdf", corrupt)):
        with open(os.path.join(HERE, "fixtures", name), "wb") as f:
            f.write(data)


if __name__ == "__main__":
    main()
