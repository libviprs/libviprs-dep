"""An AC1021 drawing, written byte by byte, so the AC21 allocation sites run.

Four of the eight guards `acadsharp/patches/allocation_ceiling.py` installs sit
on the AC1021 side of `DwgReader`, and nothing in the corpus reaches them. The
recipe that proved the AC18 sites, rewriting four bytes of a plaintext page
header in `g13_line.dwg`, does not reach these, and neither does anything else
that starts from a file in `tests/fixtures`:

  * `real_AC1032.dwg` is not an AC21 file as far as this reader is concerned.
    `readFileHeader` sends AC1024, AC1027 and AC1032 to `readFileHeaderAC18`,
    and `getSectionStream` sends them to `getSectionBuffer18`. Only AC1021
    itself reaches `readFileHeaderAC21`, `getPageBuffer` and
    `getSectionBuffer21`, and there is no AC1021 file in the corpus.
  * There cannot be one that the corpus generator wrote, either. Upstream's
    `DwgWriter` throws `CadNotSupportedException` for AC1021, so the tool that
    writes every `g13_*.dwg` cannot produce the version these guards need.
  * The fields that drive the AC21 allocations are not in plaintext. They live
    in a 0x110-byte metadata block that is LZ77 compressed and then spread
    across the 0x400 bytes at file offset 0x80, so no four-byte edit reaches
    them even given a file to edit.

So the input is built rather than derived. That is only practical because the
two transforms between those bytes and the reader are both invertible and
neither one authenticates anything:

  * `DwgReader.reedSolomonDecoding` is not Reed-Solomon. It is a stride-`factor`
    gather, it corrects nothing and it detects nothing, so the file side of it
    is a pure interleave this module can write. Worth saying plainly, because
    "the AC21 maps are Reed-Solomon coded, so a crafted one would be rejected"
    is the reason these guards were expected to be expensive, and against this
    reader it is not true. The CRCs the header carries are read into fields and
    never compared against anything either.
  * `DwgLZ77AC21Decompressor` has a literal run, which is a store mode: one
    opcode, one length byte, then the bytes themselves. The bytes come out
    permuted within each block by upstream's `copy`, and that permutation is a
    bijection this module inverts.

Everything here is therefore a statement about the pinned reader rather than
about the DWG format, and `test_limits_ac21_declared_sizes.py` rebuilds every
input from this module and checks the sha256 the capture recorded.
"""

import hashlib
import struct

# Where the file header's Reed-Solomon block sits, and its shape. Both are
# fixed by the reader: `readFileMetaData` advances to 0x80 and stops,
# `readFileHeaderAC21` reads 0x400 bytes from there, and the page data area
# starts at 0x480, which is the constant every page offset in the file is
# relative to.
HEADER_RS_OFFSET = 0x80
HEADER_RS_BYTES = 0x400
PAGE_AREA_OFFSET = 0x480

# reedSolomonDecoding(compressed, decoded, 3, 239) for the file header.
HEADER_RS_FACTOR = 3
HEADER_RS_BLOCK = 239
HEADER_DECODED_BYTES = HEADER_RS_FACTOR * HEADER_RS_BLOCK

# The decompressed metadata is a fixed 0x110 bytes, which is 34 little-endian
# ulongs, in the order Dwg21CompressedMetadata reads them.
METADATA_FIELDS = (
    "HeaderSize",
    "FileSize",
    "PagesMapCrcCompressed",
    "PagesMapCorrectionFactor",
    "PagesMapCrcSeed",
    "Map2Offset",
    "Map2Id",
    "PagesMapOffset",
    "PagesMapId",
    "Header2offset",
    "PagesMapSizeCompressed",
    "PagesMapSizeUncompressed",
    "PagesAmount",
    "PagesMaxId",
    "Unknow0x20",
    "Unknow0x40",
    "PagesMapCrcUncompressed",
    "Unknown0xF800",
    "Unknown4",
    "Unknown1",
    "SectionsAmount",
    "SectionsMapCrcUncompressed",
    "SectionsMapSizeCompressed",
    "SectionsMap2Id",
    "SectionsMapId",
    "SectionsMapSizeUncompressed",
    "SectionsMapCrcCompressed",
    "SectionsMapCorrectionFactor",
    "SectionsMapCrcSeed",
    "StreamVersion",
    "CrcSeed",
    "CrcSeedEncoded",
    "RandomSeed",
    "HeaderCRC64",
)
METADATA_BYTES = 8 * len(METADATA_FIELDS)

# getPageBuffer's block size. 0xEF for the page map and 239 for the section
# map, which are the same number written two ways.
PAGE_BLOCK_SIZE = 239
PAGE_BLOCK_STRIDE = 255

# The section this forge names, because ReadHeader is the first section read
# that cannot be skipped: ReadSummaryInfo runs before it and returns an empty
# CadSummaryInfo when the descriptor is absent.
SECTION_NAME = "AcDb:Header"


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------
# DwgLZ77AC21Decompressor.copy, as a permutation
# --------------------------------------------------------------------------
#
# Upstream copies literals in 32-byte blocks with the four-byte groups in the
# order 24, 28, 16, 20, 8, 12, 0, 4, and the tail through one of 31 hand
# written functions, several of which reverse bytes. Writing the plaintext
# into the file therefore means writing it through the inverse of that.
#
# Transcribed one entry per line from _copyMethods, and every entry is checked
# to be a bijection before it is used: a mistranscription that dropped or
# doubled an index would otherwise show up as a fixture that silently carries
# different bytes from the ones it claims.


def _c1(p, s, d):
    p[d] = s


def _c2(p, s, d):
    p[d] = s + 1
    p[d + 1] = s


def _c3(p, s, d):
    p[d] = s + 2
    p[d + 1] = s + 1
    p[d + 2] = s


def _c4(p, s, d):
    for k in range(4):
        p[d + k] = s + k


def _c8(p, s, d):
    _c4(p, s, d)
    _c4(p, s + 4, d + 4)


def _c16(p, s, d):
    _c8(p, s + 8, d)
    _c8(p, s, d + 8)


_TAIL = {
    1: lambda p, s, d: (_c1(p, s, d),),
    2: lambda p, s, d: (_c2(p, s, d),),
    3: lambda p, s, d: (_c3(p, s, d),),
    4: lambda p, s, d: (_c4(p, s, d),),
    5: lambda p, s, d: (_c1(p, s + 4, d), _c4(p, s, d + 1)),
    6: lambda p, s, d: (_c1(p, s + 5, d), _c4(p, s + 1, d + 1), _c1(p, s, d + 5)),
    7: lambda p, s, d: (_c2(p, s + 5, d), _c4(p, s + 1, d + 2), _c1(p, s, d + 6)),
    8: lambda p, s, d: (_c8(p, s, d),),
    9: lambda p, s, d: (_c1(p, s + 8, d), _c8(p, s, d + 1)),
    10: lambda p, s, d: (_c1(p, s + 9, d), _c8(p, s + 1, d + 1), _c1(p, s, d + 9)),
    11: lambda p, s, d: (_c2(p, s + 9, d), _c8(p, s + 1, d + 2), _c1(p, s, d + 10)),
    12: lambda p, s, d: (_c4(p, s + 8, d), _c8(p, s, d + 4)),
    13: lambda p, s, d: (_c1(p, s + 12, d), _c4(p, s + 8, d + 1), _c8(p, s, d + 5)),
    14: lambda p, s, d: (
        _c1(p, s + 13, d),
        _c4(p, s + 9, d + 1),
        _c8(p, s + 1, d + 5),
        _c1(p, s, d + 13),
    ),
    15: lambda p, s, d: (
        _c2(p, s + 13, d),
        _c4(p, s + 9, d + 2),
        _c8(p, s + 1, d + 6),
        _c1(p, s, d + 14),
    ),
    16: lambda p, s, d: (_c16(p, s, d),),
    17: lambda p, s, d: (_c8(p, s + 9, d), _c1(p, s + 8, d + 8), _c8(p, s, d + 9)),
    18: lambda p, s, d: (_c1(p, s + 17, d), _c16(p, s + 1, d + 1), _c1(p, s, d + 17)),
    19: lambda p, s, d: (_c3(p, s + 16, d), _c16(p, s, d + 3)),
    20: lambda p, s, d: (_c4(p, s + 16, d), _c8(p, s + 8, d + 4), _c8(p, s, d + 12)),
    21: lambda p, s, d: (
        _c1(p, s + 20, d),
        _c4(p, s + 16, d + 1),
        _c8(p, s + 8, d + 5),
        _c8(p, s, d + 13),
    ),
    22: lambda p, s, d: (
        _c2(p, s + 20, d),
        _c4(p, s + 16, d + 2),
        _c8(p, s + 8, d + 6),
        _c8(p, s, d + 14),
    ),
    23: lambda p, s, d: (
        _c3(p, s + 20, d),
        _c4(p, s + 16, d + 3),
        _c8(p, s + 8, d + 7),
        _c8(p, s, d + 15),
    ),
    24: lambda p, s, d: (_c8(p, s + 16, d), _c16(p, s, d + 8)),
    25: lambda p, s, d: (_c8(p, s + 17, d), _c1(p, s + 16, d + 8), _c16(p, s, d + 9)),
    26: lambda p, s, d: (
        _c1(p, s + 25, d),
        _c8(p, s + 17, d + 1),
        _c1(p, s + 16, d + 9),
        _c16(p, s, d + 10),
    ),
    27: lambda p, s, d: (
        _c2(p, s + 25, d),
        _c8(p, s + 17, d + 2),
        _c1(p, s + 16, d + 10),
        _c16(p, s, d + 11),
    ),
    28: lambda p, s, d: (
        _c4(p, s + 24, d),
        _c8(p, s + 16, d + 4),
        _c8(p, s + 8, d + 12),
        _c8(p, s, d + 20),
    ),
    29: lambda p, s, d: (
        _c1(p, s + 28, d),
        _c4(p, s + 24, d + 1),
        _c8(p, s + 16, d + 5),
        _c8(p, s + 8, d + 13),
        _c8(p, s, d + 21),
    ),
    30: lambda p, s, d: (
        _c2(p, s + 28, d),
        _c4(p, s + 24, d + 2),
        _c8(p, s + 16, d + 6),
        _c8(p, s + 8, d + 14),
        _c8(p, s, d + 22),
    ),
    31: lambda p, s, d: (
        _c1(p, s + 30, d),
        _c4(p, s + 26, d + 1),
        _c8(p, s + 18, d + 5),
        _c8(p, s + 10, d + 13),
        _c8(p, s + 2, d + 21),
        _c2(p, s, d + 29),
    ),
}


def copy_permutation(length):
    """`perm` such that upstream's copy leaves `dst[i] == src[perm[i]]`."""
    perm = [None] * length
    src = 0
    dst = 0
    remaining = length
    while remaining >= 32:
        _c4(perm, src + 24, dst)
        _c4(perm, src + 28, dst + 4)
        _c4(perm, src + 16, dst + 8)
        _c4(perm, src + 20, dst + 12)
        _c4(perm, src + 8, dst + 16)
        _c4(perm, src + 12, dst + 20)
        _c4(perm, src, dst + 24)
        _c4(perm, src + 4, dst + 28)
        src += 32
        dst += 32
        remaining -= 32
    if remaining:
        _TAIL[remaining](perm, src, dst)

    if sorted(perm) != list(range(length)):
        raise AssertionError(
            f"the copy permutation for {length} bytes is not a bijection, so this "
            "module's transcription of DwgLZ77AC21Decompressor._copyMethods is wrong "
            "and every fixture it writes carries bytes nobody chose"
        )
    return perm


# The literal-run opcode, and the length its one-byte extension starts from.
# readLiteralLength computes `opCode + 8` and treats 0x17 as "read another
# byte and add it", so 0x0F is the opcode that takes an explicit length.
LITERAL_OPCODE = 0x0F
LITERAL_BASE = 0x17
LITERAL_MAX = LITERAL_BASE + 0xFE


def lz77_store(plain):
    """`plain` as an AC21 stream the decompressor copies out verbatim.

    One literal run and nothing else, which is the store mode of a format that
    has one. The run is bounded at `LITERAL_MAX` because past that the length
    goes into 16-bit continuations, and nothing here needs a longer one: the
    largest payload this module writes is the 272-byte header metadata.
    """
    length = len(plain)
    if not LITERAL_BASE <= length <= LITERAL_MAX:
        raise AssertionError(
            f"{length} bytes is outside the single-run literal length this writes "
            f"({LITERAL_BASE} to {LITERAL_MAX})"
        )

    body = bytearray(length)
    for i, src in enumerate(copy_permutation(length)):
        body[src] = plain[i]
    return bytes([LITERAL_OPCODE, length - LITERAL_BASE]) + bytes(body)


def interleave(decoded, factor, block_size, encoded_bytes):
    """`decoded` laid out so `reedSolomonDecoding` gathers it back.

    The mirror of upstream's loop with the assignment turned round, rather
    than a closed form, so the two stay the same shape and the awkward last
    block (`Math.Min(length, blockSize)`) is handled by construction.
    """
    encoded = bytearray(encoded_bytes)
    index = 0
    remaining = len(decoded)
    for i in range(factor):
        cindex = i
        if i >= encoded_bytes:
            break
        size = min(remaining, block_size)
        remaining -= size
        for _ in range(size):
            if cindex >= encoded_bytes:
                raise AssertionError(
                    f"interleaving {len(decoded)} bytes at factor {factor} runs past "
                    f"the {encoded_bytes}-byte block it has to fit in"
                )
            encoded[cindex] = decoded[index]
            index += 1
            cindex += factor
    return bytes(encoded)


def metadata_block(**fields):
    """The 0x110-byte Dwg21CompressedMetadata, from named fields."""
    unknown = set(fields) - set(METADATA_FIELDS)
    if unknown:
        raise AssertionError(f"no such metadata field: {sorted(unknown)}")
    out = b"".join(struct.pack("<Q", fields.get(name, 0)) for name in METADATA_FIELDS)
    assert len(out) == METADATA_BYTES
    return out


def file_metadata():
    """Bytes 0x00 to 0x80, which `readFileMetaData` walks and mostly skips.

    The only field in here the reader does anything with is the code page at
    0x13, and 30 is what every AC1018 and later file in `tests/fixtures`
    carries. The rest is advanced over.
    """
    head = bytearray(HEADER_RS_OFFSET)
    head[0:6] = b"AC1021"
    struct.pack_into("<h", head, 0x13, 30)
    return bytes(head)


def page_block(payload):
    """One `getPageBuffer` block: the payload, padded to the 255 it reads.

    `lenght` is `factor * 255` and `factor` is 1 for everything this writes,
    so the reader asks for 255 bytes whatever the payload is. Padding here
    rather than letting the read come up short keeps the file honest about
    how much of it the reader is entitled to touch.
    """
    if len(payload) > PAGE_BLOCK_STRIDE:
        raise AssertionError(
            f"{len(payload)} bytes does not fit the single {PAGE_BLOCK_STRIDE}-byte "
            "block this writes; a bigger payload needs factor > 1 and an interleave"
        )
    return bytes(payload) + bytes(PAGE_BLOCK_STRIDE - len(payload))


def compressed_total(compressed_size, correction_factor):
    """`totalSize` as getPageBuffer computes it, wrap included."""
    v1 = (compressed_size + 7) & 0xFFFFFFF8
    return (v1 * correction_factor) & 0xFFFFFFFF


def page_block_length(total_size):
    """`lenght` as getPageBuffer computes it, including the int cast that wraps.

    `(int)(totalSize + blockSize - 1L) / blockSize` is a long add narrowed to
    int before the division, so a totalSize near 2^32 does not produce a large
    factor, it produces a small or negative one. That is the whole reason the
    "AC21 compressed page" guard is reachable at all.
    """
    wide = total_size + PAGE_BLOCK_SIZE - 1
    narrowed = wide & 0xFFFFFFFF
    if narrowed >= 0x80000000:
        narrowed -= 0x100000000
    factor = int(narrowed / PAGE_BLOCK_SIZE)  # C# integer division truncates
    return factor * PAGE_BLOCK_STRIDE


def forge(page_area=b"", **metadata):
    """A whole AC1021 file: the metadata, the interleaved header, the pages."""
    decoded = bytearray(HEADER_DECODED_BYTES)
    compressed = lz77_store(metadata_block(**metadata))
    # decodedData[0x18] is ComprLen and the stream starts at 0x20; everything
    # before that is CRCs the reader loads into locals and never checks.
    struct.pack_into("<i", decoded, 0x18, len(compressed))
    struct.pack_into("<i", decoded, 0x1C, METADATA_BYTES)
    decoded[0x20 : 0x20 + len(compressed)] = compressed

    header = interleave(bytes(decoded), HEADER_RS_FACTOR, HEADER_RS_BLOCK, HEADER_RS_BYTES)
    return file_metadata() + header + bytes(page_area)


def page_map(records):
    """The page map's plaintext: one (size, id) pair per record, in order."""
    return b"".join(struct.pack("<qq", size, ident) for size, ident in records)


def section_map(name, pages, decompressed_size=0):
    """One section descriptor's plaintext, with its page entries."""
    encoded_name = name.encode("utf-16-le")
    out = struct.pack(
        "<QQQQqQQQ",
        0,  # CompressedSize
        decompressed_size,  # DecompressedSize, which getSectionBuffer21 ignores
        0,  # Encrypted
        0,  # HashCode
        len(encoded_name),  # SectionNameLength
        0,  # Unknown
        0,  # Encoding, anything but 4 so the page skips the interleave
        len(pages),  # PageCount
    )
    out += encoded_name
    for page in pages:
        out += struct.pack(
            "<QqqQQQQ",
            page["offset"],
            page["size"],
            page["page_number"],
            page["decompressed"],
            page["compressed"],
            0,  # Checksum
            0,  # CRC
        )
    return out


# --------------------------------------------------------------------------
# The four inputs, one per guard
# --------------------------------------------------------------------------
#
# Each one is the smallest file that reaches its site and nothing further: the
# guards run in the order the reader reaches them, so a file that trips the
# first one says nothing about the third. The first three sit in getPageBuffer
# and are reached by the page-map call, which is the first thing
# readFileHeaderAC21 does after the metadata. The fourth is in
# getSectionBuffer21, which is only reached once the page map and the section
# map have both been read, so that one is a whole small drawing.

# The declared values. 64 MiB and 1 GiB are the two the AC18 fixtures use, and
# they are both past the 16 MiB floor these files are measured against.
DECLARED_SIZES = (
    ("64MiB", 1 << 26),
    ("1GiB", 1 << 30),
)

# The size that gets past the page block buffer and lands on the compressed
# page. `lenght` is always larger than `totalSize`, so the only way to reach
# the second guard is a totalSize whose `+ 238` wraps the int cast to a small
# number: `lenght` then comes out 0 and the first guard has nothing to refuse.
# A multiple of 8 because `v1` masks the low three bits off.
WRAPPED_TOTAL_SIZE = 0xFFFFFFF8

# What the control declares. Small enough to pass all four ceilings, which is
# the only thing it is for.
CONTROL_PAGE_BYTES = PAGE_BLOCK_STRIDE


def _base(**overrides):
    fields = {
        "HeaderSize": 0x70,
        "PagesMapCorrectionFactor": 1,
        "PagesMapId": 1,
        "SectionsMapCorrectionFactor": 1,
        "SectionsMapId": 1,
        "StreamVersion": 0x60100,
    }
    fields.update(overrides)
    return fields


def page_block_input(compressed_size):
    """Trips `Check(lenght, ..., "AC21 page block buffer")`.

    One declared field, `PagesMapSizeCompressed`, and the file has no pages at
    all: the buffer is allocated from the header before a page is read.
    """
    return forge(
        **_base(
            PagesMapSizeCompressed=compressed_size,
            PagesMapSizeUncompressed=32,
            PagesMapOffset=0,
        )
    )


def compressed_page_input():
    """Trips `Check(totalSize, ..., "AC21 compressed page")`."""
    return forge(
        **_base(
            PagesMapSizeCompressed=WRAPPED_TOTAL_SIZE,
            PagesMapSizeUncompressed=32,
            PagesMapOffset=0,
        )
    )


def decompressed_page_input(uncompressed_size):
    """Trips `CheckUnsigned(uncompressedSize, ..., "AC21 decompressed page")`.

    The compressed side is 16 bytes, so the two guards before it are handed
    255 and 16 and pass, and the only thing out of proportion is the size the
    page claims it decompresses to.
    """
    return forge(
        **_base(
            PagesMapSizeCompressed=16,
            PagesMapSizeUncompressed=uncompressed_size,
            PagesMapOffset=0,
        )
    )


def section_buffer_input(decompressed_size):
    """Trips `CheckUnsigned(totalLength, ..., "AC21 section buffer")`.

    This one needs the reader to get all the way through readFileHeaderAC21,
    so the file carries a real page map and a real section map. The section is
    AcDb:Header because ReadHeader is the first read that cannot be skipped:
    ReadSummaryInfo runs first and settles for an empty CadSummaryInfo when
    there is no descriptor for it.

    With a sane `decompressed_size` this is the control. It passes all four
    ceilings, the section buffer gets built out of the page, and what refuses
    it is the header parser finding no sentinel, which is the point: the
    ceiling is not what stops an AC1021 file from being read.
    """
    sections = lz77_store(
        section_map(
            SECTION_NAME,
            [
                {
                    "offset": 0,
                    "size": PAGE_BLOCK_STRIDE,
                    "page_number": 2,
                    "decompressed": decompressed_size,
                    "compressed": PAGE_BLOCK_STRIDE,
                }
            ],
            decompressed_size=decompressed_size,
        )
    )
    pages = lz77_store(page_map([(PAGE_BLOCK_STRIDE, 1), (PAGE_BLOCK_STRIDE, 2)]))

    # Record 1 is the section map, record 2 is the section's one page, and the
    # page map itself sits after both because nothing addresses it by record.
    area = page_block(sections) + page_block(b"") + page_block(pages)
    return forge(
        area,
        **_base(
            PagesMapOffset=2 * PAGE_BLOCK_STRIDE,
            PagesMapSizeCompressed=len(pages),
            PagesMapSizeUncompressed=len(page_map([(0, 0), (0, 0)])),
            PagesAmount=2,
            PagesMaxId=2,
            SectionsAmount=2,
            SectionsMapSizeCompressed=len(sections),
            SectionsMapSizeUncompressed=len(
                section_map(
                    SECTION_NAME,
                    [
                        {
                            "offset": 0,
                            "size": 0,
                            "page_number": 0,
                            "decompressed": 0,
                            "compressed": 0,
                        }
                    ],
                )
            ),
        ),
    )


def _case(name, site, declared, field, blob):
    """One case, carrying the bytes as well as their digest.

    The generator writes `blob` and the test recomputes `sha256` from the same
    builder, which is the same contract the AC18 derivation has: a capture is
    only evidence if the input it names can be rebuilt from what is committed.
    """
    return {
        "name": name,
        "scenario": f"declared/{name}",
        "site": site,
        "declared": declared,
        "field": field,
        "blob": blob,
        "bytes": len(blob),
        "sha256": sha256_bytes(blob),
    }


def inputs():
    """Every AC21 input, built here so a test can rebuild and compare digests."""
    out = []
    for label, value in DECLARED_SIZES:
        blob = page_block_input(value)
        out.append(
            _case(
                f"ac21_page_block_{label}",
                "AC21 page block buffer",
                page_block_length(compressed_total(value, 1)),
                "PagesMapSizeCompressed",
                blob,
            )
        )

    blob = compressed_page_input()
    out.append(
        _case(
            "ac21_compressed_page_wrapped",
            "AC21 compressed page",
            compressed_total(WRAPPED_TOTAL_SIZE, 1),
            "PagesMapSizeCompressed",
            blob,
        )
    )

    for label, value in DECLARED_SIZES:
        blob = decompressed_page_input(value)
        out.append(
            _case(
                f"ac21_decompressed_page_{label}",
                "AC21 decompressed page",
                value,
                "PagesMapSizeUncompressed",
                blob,
            )
        )

    for label, value in DECLARED_SIZES:
        blob = section_buffer_input(value)
        out.append(
            _case(
                f"ac21_section_buffer_{label}",
                "AC21 section buffer",
                value,
                "page DecompressedSize",
                blob,
            )
        )
    return out


def control_input():
    return section_buffer_input(CONTROL_PAGE_BYTES)


def control_case():
    """The same craft with nothing inflated, which is what says the four
    refusals above are a bound rather than a reader that cannot read AC1021."""
    blob = control_input()
    return {
        "name": "ac21_control",
        "scenario": "declared/ac21_control",
        "declared": CONTROL_PAGE_BYTES,
        "blob": blob,
        "bytes": len(blob),
        "sha256": sha256_bytes(blob),
    }


# The sites this reaches, which is what `inputs()` has to cover between them.
SITES = (
    "AC21 page block buffer",
    "AC21 compressed page",
    "AC21 decompressed page",
    "AC21 section buffer",
)
