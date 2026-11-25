"""Builds the fixtures hayro-spike needs that are not copies of libviprs-tests files.

  python3 gen_fixtures.py manifest        rewrite fixtures/MANIFEST.csv (hash, origin of each file)
  python3 gen_fixtures.py codecs          write the JBIG2 and CCITT G4 sheets into fixtures/
  python3 gen_fixtures.py hostile <dir>   write the hostile inputs into <dir>

Everything is deterministic, so the hostile files are not committed, and the codec sheets are
committed because the encoder (jbig2enc, built from source) is not something CI has.
"""

import csv
import hashlib
import os
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
UPSTREAM = "libviprs-tests tests/fixtures/"


def obj(num, body):
    return b"%d 0 obj\n" % num + body + b"\nendobj\n"


def stream(dict_body, data):
    return b"<< " + dict_body + b" /Length %d >>\nstream\n" % len(data) + data + b"\nendstream"


def build_pdf(bodies, root=1, xref=True, header=b"%PDF-1.7\n"):
    """bodies[i] is the body of object i + 1. Returns a PDF with a classic xref table."""
    out = bytearray(header)
    offsets = []
    for i, body in enumerate(bodies, start=1):
        offsets.append(len(out))
        out += obj(i, body)
    start = len(out)
    if xref:
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(bodies) + 1)
        for off in offsets:
            out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(bodies) + 1,
        root,
        start,
    )
    return bytes(out)


def simple_page(content, resources=b"<< >>", media=b"[0 0 612 792]", extra=b""):
    """Catalog, Pages, Page and one content stream: objects 1 to 4. Extra objects start at 5."""
    return [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox "
        + media
        + b" /Resources "
        + resources
        + b" /Contents 4 0 R "
        + extra
        + b">>",
        stream(b"", content),
    ]


def base_content():
    lines = [b"0.9 0.9 1 rg 50 50 512 692 re f", b"0 0 0.6 RG 4 w 50 50 512 692 re S"]
    for i in range(40):
        y = 60 + i * 16
        lines.append(b"0.2 w 60 %d m 550 %d l S" % (y, y + (i % 7)))
    lines.append(b"1 0 0 rg 300 400 m 400 500 500 300 550 450 c 300 300 l f")
    lines.append(b"BT /F1 24 Tf 70 700 Td (hayro spike hostile base) Tj ET")
    return b"\n".join(lines)


FONT = b"<< /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >>"


def base_pdf():
    return build_pdf(simple_page(base_content(), FONT))


def noise(n, seed=1):
    out = bytearray()
    x = seed
    while len(out) < n:
        x = (x * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        out += x.to_bytes(8, "little")
    return bytes(out[:n])


def hostile_cases():
    """name -> (description, bytes), one entry per hostile input."""
    base = base_pdf()
    cases = {}

    def add(name, desc, data):
        cases[name] = (desc, data)

    add("base", "the untouched base page, so every other row has a control", base)
    add("empty", "zero bytes", b"")
    add("garbage", "4 KiB of noise, no PDF header", noise(4096))
    add("header-only", "just the %PDF header", b"%PDF-1.7\n")
    add("truncated-half", "the base file cut in half", base[: len(base) // 2])
    cut = base.rfind(b"xref")
    add("trailer-missing", "everything from the xref table on removed", base[:cut])
    add("no-eof-marker", "the %%EOF line removed", base.replace(b"%%EOF", b""))
    start = base.rfind(b"startxref")
    add(
        "startxref-wrong",
        "startxref points into the middle of the file",
        base[:start] + b"startxref\n7\n%%EOF\n",
    )
    add(
        "startxref-past-end",
        "startxref points past the end of the file",
        base[:start] + b"startxref\n99999999\n%%EOF\n",
    )
    add(
        "xref-offsets-wrong",
        "every xref entry points 11 bytes off",
        shift_offsets(base, 11),
    )
    add(
        "xref-offsets-zero",
        "every xref offset is zero",
        shift_offsets(base, None),
    )
    add(
        "junk-before-header",
        "1 KiB of noise before %PDF, so every offset is 1024 too low",
        noise(1024, 3) + base,
    )
    add(
        "junk-after-eof",
        "64 KiB of noise after the final %%EOF",
        base + noise(65536, 4),
    )
    add(
        "stream-length-wrong",
        "the content stream /Length says 99999 and the data is shorter",
        base.replace(b"/Length %d" % len(base_content()), b"/Length 99999", 1),
    )

    pages_cycle = simple_page(base_content(), FONT)
    pages_cycle[1] = b"<< /Type /Pages /Kids [2 0 R 3 0 R] /Count 2 >>"
    add("page-tree-cycle", "the Pages node lists itself as a kid", build_pdf(pages_cycle))

    add(
        "form-xobject-recursion",
        "a form XObject that paints itself",
        build_pdf(
            simple_page(
                b"/Fm0 Do",
                b"<< /XObject << /Fm0 5 0 R >> >>",
            )
            + [
                stream(
                    b"/Type /XObject /Subtype /Form /BBox [0 0 612 792]"
                    b" /Resources << /XObject << /Fm0 5 0 R >> >>",
                    b"0 0 100 100 re f /Fm0 Do",
                )
            ]
        ),
    )
    add(
        "pattern-recursion",
        "a tiling pattern whose cell paints with the same pattern",
        build_pdf(
            simple_page(
                b"/Pattern cs /P0 scn 0 0 612 792 re f",
                b"<< /Pattern << /P0 5 0 R >> >>",
            )
            + [
                stream(
                    b"/Type /Pattern /PatternType 1 /PaintType 1 /TilingType 1 /BBox [0 0 20 20]"
                    b" /XStep 20 /YStep 20 /Resources << /Pattern << /P0 5 0 R >> >>",
                    b"/Pattern cs /P0 scn 0 0 10 10 re f",
                )
            ]
        ),
    )
    ref_cycle = simple_page(base_content(), FONT)
    ref_cycle[3] = b"<< /Length 4 0 R >>\nstream\nq Q\nendstream"
    add(
        "length-ref-cycle",
        "the content stream's /Length is a reference to itself",
        build_pdf(ref_cycle),
    )

    add(
        "deep-q-nesting",
        "a million q operators with no Q",
        build_pdf(simple_page(b"q " * 1_000_000 + b"0 0 100 100 re f")),
    )
    add(
        "nested-arrays",
        "200000 nested [ in the content stream",
        build_pdf(simple_page(b"[" * 200_000 + b"]" * 200_000 + b" TJ")),
    )
    nested_dict = b"<< /A " * 100_000 + b"1" + b" >>" * 100_000
    add(
        "nested-dicts",
        "100000 nested dictionaries in the page object",
        build_pdf(simple_page(base_content(), FONT, extra=b"/X " + nested_dict + b" ")),
    )
    add(
        "dash-pathological",
        "a 1e6 pt line stroked with a 0.001 pt dash pattern",
        build_pdf(simple_page(b"[0.001 0.001] 0 d 1 w 0 400 m 1000000 400 l S")),
    )
    add(
        "path-explosion",
        "one path with 400000 segments, stroked",
        build_pdf(
            simple_page(
                b"0 0 m "
                + b" ".join(b"%d %d l" % (i % 600, (i * 7) % 790) for i in range(400_000))
                + b" S"
            )
        ),
    )
    add(
        "huge-mediabox",
        "a MediaBox of 1e9 by 1e9 points",
        build_pdf(simple_page(base_content(), FONT, media=b"[0 0 1000000000 1000000000]")),
    )
    add(
        "mediabox-zero",
        "a MediaBox of zero area",
        build_pdf(simple_page(base_content(), FONT, media=b"[0 0 0 0]")),
    )
    add(
        "mediabox-negative",
        "a MediaBox with the corners the wrong way round",
        build_pdf(simple_page(base_content(), FONT, media=b"[612 792 0 0]")),
    )

    bomb = zlib.compressobj(9)
    chunk = b" " * (1 << 20)
    packed = b"".join(bomb.compress(chunk) for _ in range(1024)) + bomb.flush()
    add(
        "flate-bomb-content",
        "a content stream that inflates to 1 GiB of spaces (%d bytes packed)" % len(packed),
        build_pdf(simple_page(b"")[:3] + [stream(b"/Filter /FlateDecode", packed)]),
    )
    add(
        "huge-declared-image",
        "a 60000 by 60000 8 bit gray image whose data is 16 bytes",
        build_pdf(
            simple_page(
                b"q 612 0 0 792 0 0 cm /Im0 Do Q",
                b"<< /XObject << /Im0 5 0 R >> >>",
            )
            + [
                stream(
                    b"/Type /XObject /Subtype /Image /Width 60000 /Height 60000"
                    b" /ColorSpace /DeviceGray /BitsPerComponent 8",
                    b"\x80" * 16,
                )
            ]
        ),
    )
    img = zlib.compressobj(9)
    zeros = bytes(1 << 20)
    packed_img = b"".join(img.compress(zeros) for _ in range(1024)) + img.flush()
    add(
        "flate-bomb-image",
        "a 32768 by 32768 gray image that really inflates to 1 GiB",
        build_pdf(
            simple_page(
                b"q 612 0 0 792 0 0 cm /Im0 Do Q",
                b"<< /XObject << /Im0 5 0 R >> >>",
            )
            + [
                stream(
                    b"/Type /XObject /Subtype /Image /Width 32768 /Height 32768"
                    b" /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode",
                    packed_img,
                )
            ]
        ),
    )
    return cases


def shift_offsets(pdf, delta):
    """Rewrite the classic xref table with each offset moved by delta (None: zero)."""
    start = pdf.rfind(b"xref\n0 ")
    end = pdf.rfind(b"trailer")
    lines = pdf[start:end].split(b"\n")
    out = [lines[0], lines[1]]
    for line in lines[2:]:
        if line.endswith(b" n "):
            off = int(line[:10])
            out.append(b"%010d 00000 n " % (0 if delta is None else off + delta))
        else:
            out.append(line)
    return pdf[:start] + b"\n".join(out) + pdf[end:]


def write_hostile(outdir):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    cases = hostile_cases()
    with (outdir / "INDEX.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["case", "bytes", "description"])
        for name, (desc, data) in cases.items():
            (outdir / f"{name}.pdf").write_bytes(data)
            w.writerow([name, len(data), desc])
    return list(cases)


def source_page():
    """A 1240 by 1754 (A4 at 150 dpi) bilevel page of text lines and rules, the same every run."""
    from PIL import Image, ImageDraw, ImageFont

    im = Image.new("L", (1240, 1754), 255)
    d = ImageDraw.Draw(im)
    font = ImageFont.load_default(size=26)
    words = "plan section elevation detail note scale sheet revision grid level door window".split()
    for row in range(48):
        text = " ".join(words[(row * 3 + k) % len(words)] for k in range(9))
        d.text((90, 90 + row * 33), f"{row:02d} {text}", fill=0, font=font)
    d.rectangle((60, 60, 1180, 1694), outline=0, width=4)
    d.line((60, 1600, 1180, 1600), fill=0, width=3)
    d.ellipse((900, 1610, 1100, 1690), outline=0, width=3)
    return im.convert("1", dither=Image.Dither.NONE)


def image_pdf(filter_name, data, parms=b"", globals_obj=None):
    dict_body = (
        b"/Type /XObject /Subtype /Image /Width 1240 /Height 1754 /ColorSpace /DeviceGray"
        b" /BitsPerComponent 1 /Filter /" + filter_name.encode() + parms
    )
    bodies = simple_page(
        b"q 595 0 0 842 0 0 cm /Im0 Do Q",
        b"<< /XObject << /Im0 5 0 R >> >>",
        media=b"[0 0 595 842]",
    ) + [stream(dict_body, data)]
    if globals_obj is not None:
        bodies.append(stream(b"", globals_obj))
    return build_pdf(bodies)


def codecs():
    from io import BytesIO

    page = source_page()
    with tempfile.TemporaryDirectory() as tmp:
        png = os.path.join(tmp, "page.png")
        page.save(png)
        generic = subprocess.run(["jbig2", "-p", png], check=True, capture_output=True).stdout
        subprocess.run(["jbig2", "-s", "-p", "-b", os.path.join(tmp, "sym"), png], check=True)
        sym = Path(tmp, "sym.sym").read_bytes()
        pagedata = Path(tmp, "sym.0000").read_bytes()
        tif = BytesIO()
        # one strip, so the strip data is a whole G4 stream
        page.save(tif, format="TIFF", compression="group4", tiffinfo={278: 1754})
    FIXTURES.joinpath("codec-jbig2-generic.pdf").write_bytes(image_pdf("JBIG2Decode", generic))
    FIXTURES.joinpath("codec-jbig2-symbol.pdf").write_bytes(
        image_pdf(
            "JBIG2Decode", pagedata, b" /DecodeParms << /JBIG2Globals 6 0 R >>", globals_obj=sym
        )
    )
    from PIL import Image

    tif.seek(0)
    t = Image.open(tif)
    off, cnt = t.tag_v2[273][0], t.tag_v2[279][0]
    raw = tif.getvalue()[off : off + cnt]
    parms = b" /DecodeParms << /K -1 /Columns 1240 /Rows 1754 /BlackIs1 true >>"
    FIXTURES.joinpath("codec-ccitt-g4.pdf").write_bytes(image_pdf("CCITTFaxDecode", raw, parms))
    page.convert("L").save(FIXTURES / "codec-source.png")


def manifest():
    rows = []
    for p in sorted(FIXTURES.rglob("*")):
        if not p.is_file() or p.name == "MANIFEST.csv":
            continue
        name = p.relative_to(FIXTURES).as_posix()
        if name.startswith("goldens/"):
            source = UPSTREAM + "rotation_libvips_pdfium_expected/" + p.name
        elif name.startswith("codec-"):
            source = "gen_fixtures.py codecs"
        else:
            source = UPSTREAM + p.name
        rows.append([name, hashlib.sha256(p.read_bytes()).hexdigest(), source])
    with (FIXTURES / "MANIFEST.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "sha256", "source"])
        w.writerows(rows)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "manifest":
        manifest()
    elif mode == "codecs":
        codecs()
    elif mode == "hostile":
        print("\n".join(write_hostile(sys.argv[2])))
    else:
        sys.exit(__doc__)
