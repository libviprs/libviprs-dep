"""An AC1018 drawing, written byte by byte, so the AC18 descriptor guards run.

Two of the eight guards `acadsharp/patches/allocation_ceiling.py` installs sit
in `readFileHeaderAC18`, in the loop that walks the data-section map, and
nothing in the corpus reaches them:

  * `CheckDescriptor(PageCount, DecompressedSize, ...)` at `DwgReader.cs:915`,
    which runs straight after a descriptor's name and before the loop over its
    pages. The loop runs `PageCount` times and the buffer `getSectionBuffer18`
    allocates afterwards is `DecompressedSize * LocalSections.Count`, so both
    fields are unbounded counts until this refuses them.
  * `CheckPageOffset(Offset, DecompressedSize, ...)` at `DwgReader.cs:927`,
    which runs straight after a page's start offset and before the gap fill
    that adds one empty page per `DecompressedSize` until it reaches that
    offset.

The recipe that proved the AC18 sinks in #77, rewriting four bytes of a
plaintext page header in `g13_line.dwg`, does not reach either. Both fields
live inside the LZ77 stream the section map is stored as, and the page header
in front of it is the only part of that page written in the clear.

So the input is built rather than derived, the same way `ac21_forge.py` builds
the AC1021 side. Against this reader that is cheaper than it sounds, because
nothing between these bytes and the descriptor loop authenticates anything:

  * The 0x6C bytes at 0x80 that the reader calls encrypted are XORed with a
    fixed keystream, and `CRC32StreamHandler` derives it from a 16-bit linear
    congruential generator seeded with 1 rather than from anything in the file.
    `keystream` below is that generator, checked against the magic sequence
    `DwgReader` transcribes in its own comment.
  * The file ID string in that block is compared, and a mismatch raises a
    notification rather than an exception, so even that is advisory.
  * Every CRC and checksum in the page headers is read into a local and never
    compared with anything.
  * `DwgLZ77AC18Decompressor` has a literal run, which is a store mode, and
    unlike the AC21 decompressor's it is a plain `src.Read` into a `dst.Write`
    with no permutation in between. A whole payload therefore stores as one
    opcode, its length, the bytes, and the terminator.

Everything here is a statement about the pinned reader rather than about the
DWG format, and `test_limits_ac18_declared_sizes.py` rebuilds every input from
this module and checks the sha256 the capture recorded.
"""

import hashlib
import struct

# What `readFileMetaData` walks to, and what it leaves behind it.
HEADER_BLOCK_OFFSET = 0x80
HEADER_BLOCK_BYTES = 0x6C
# 0x6C of header block plus the 20 bytes `readFileHeaderAC18` reads and
# discards. Every page-map record seeker is counted from here, because
# `total` starts at 0x100 before the first record is read.
PAGE_AREA_OFFSET = 0x100

# `getPageHeaderData`'s first field. The page map and the section map are the
# two pages this writes, and the reader finds each by address rather than by
# scanning, so these are labels rather than anchors.
PAGE_MAP_MAGIC = 0x41630E3B
SECTION_MAP_MAGIC = 0x4163003B
DATA_PAGE_MAGIC = 0x4163043B

# `decryptDataSection`'s mask, before the page's own position is XORed in.
DATA_PAGE_MASK = 0x4164536B

# The section this forge names. `ReadHeader` is the first read in `Read()`
# that cannot be skipped: `ReadSummaryInfo` runs before it and settles for an
# empty `CadSummaryInfo` when there is no descriptor for it.
SECTION_NAME = "AcDb:Header"

# The ceiling a file this small is measured against: 16 MiB, the floor, since
# 64 times a two-kilobyte file is far below it. Everything this module
# declares is chosen against this number, so `inputs()` checks that every file
# it writes is small enough for the floor to be what applies.
FLOOR_BYTES = 16 * 1024 * 1024
CEILING_FACTOR = 64

# ViprsAllocationCeiling.MaxSectionPages, the bound on count rather than bytes.
MAX_SECTION_PAGES = 1048576


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------
# The header block's keystream
# --------------------------------------------------------------------------
#
# `DwgReader` calls the 0x6C bytes at 0x80 encrypted and prints the key in a
# comment, and `CRC32StreamHandler`'s array constructor is where the XOR
# happens. The key is a Microsoft-style LCG seeded with 1, taking bits 16 to
# 23 of each step, so it is the same 108 bytes for every file ever written and
# this module can generate it.

MAGIC_SEED = 1
MAGIC_MULTIPLIER = 0x343FD
MAGIC_INCREMENT = 0x269EC3

# The first 32 bytes as DwgReader.cs:753-754 writes them out, which is what
# `keystream` is checked against. A generator that drifted from upstream's
# would otherwise produce a file whose header decodes to bytes nobody chose.
MAGIC_PREFIX = bytes(
    (
        0x29, 0x23, 0xBE, 0x84, 0xE1, 0x6C, 0xD6, 0xAE,
        0x52, 0x90, 0x49, 0xF1, 0xF1, 0xBB, 0xE9, 0xEB,
        0xB3, 0xA6, 0xDB, 0x3C, 0x87, 0x0C, 0x3E, 0x99,
        0x24, 0x5E, 0x0D, 0x1C, 0x06, 0xB7, 0x47, 0xDE,
    )
)


def keystream(length):
    """`CRC32StreamHandler`'s magic sequence, as many bytes as asked for."""
    out = bytearray(length)
    seed = MAGIC_SEED
    for i in range(length):
        seed = (seed * MAGIC_MULTIPLIER + MAGIC_INCREMENT) & 0xFFFFFFFF
        out[i] = (seed >> 16) & 0xFF
    return bytes(out)


if keystream(len(MAGIC_PREFIX)) != MAGIC_PREFIX:
    raise AssertionError(
        "this module's transcription of CRC32StreamHandler's generator does not "
        "produce the magic sequence DwgReader.cs prints, so every header block it "
        "writes decodes to something else"
    )


# --------------------------------------------------------------------------
# DwgLZ77AC18Decompressor, as a store mode and as an oracle
# --------------------------------------------------------------------------
#
# `DecompressToDest` reads one opcode and dispatches on it. Two of its three
# entry paths are literal copies, and `copy` is `src.Read` into `dst.Write`
# with nothing in between, so either one stores a payload verbatim. Which one
# to use is only a question of how long the payload is:
#
#   * an opcode above 0x11 copies `opcode - 17` bytes, so 1 to 238 of them,
#   * an opcode whose high nibble is zero copies `literalCount + 3` bytes, and
#     `literalCount` reads its own continuation bytes, so it has no ceiling.
#
# Either way the byte after the payload becomes the next opcode, and 0x11
# terminates before the back-reference loop runs.

LITERAL_TERMINATOR = 0x11
# The opcode byte is `length + 17` and it has to stay a byte, so 238 is the
# longest payload the one-byte form carries.
SHORT_MAX = 0xFF - LITERAL_TERMINATOR
# The continuation form's shortest payload: opcode 0x00, then a length byte of
# 1, which `literalCount` turns into 0x0F + 1 and `DecompressToDest` turns
# into that plus 3.
LONG_MIN = 0x0F + 1 + 3


def lz77_store(plain):
    """`plain` as an AC18 stream the decompressor copies out verbatim.

    Checked against `lz77_decompress`, which is this module's transcription of
    upstream's `DecompressToDest`, on every call. The AC21 forge proves its
    literal run is a bijection before it uses it, for the same reason: a
    mistranscription here is a fixture that silently carries different bytes
    from the ones it claims to.
    """
    length = len(plain)
    if length < 1:
        raise AssertionError("an empty payload has no literal run to store it in")

    if length <= SHORT_MAX:
        stream = bytes([length + LITERAL_TERMINATOR]) + bytes(plain)
    else:
        # 0xFF per leading zero byte, then 0x0F plus the terminating byte,
        # then the 3 DecompressToDest adds.
        carries = (length - LONG_MIN) // 0xFF
        last = length - LONG_MIN - carries * 0xFF + 1
        stream = bytes([0x00]) + bytes(carries) + bytes([last]) + bytes(plain)
    stream += bytes([LITERAL_TERMINATOR])

    got = lz77_decompress(stream)
    if got != bytes(plain):
        raise AssertionError(
            f"the {length}-byte literal run this wrote decompresses to {len(got)} "
            "bytes that are not the ones it was given, so this module's reading of "
            "DwgLZ77AC18Decompressor is wrong"
        )
    return stream


def lz77_decompress(stream):
    """`DwgLZ77AC18Decompressor.DecompressToDest`, transcribed.

    Only as much of it as `lz77_store` needs to be checked against, which is
    the whole of the literal handling and the terminator. The back-reference
    loop is transcribed too rather than left out, because "this input never
    reaches the part I did not write" is exactly the claim an oracle is for.
    """
    src = _Cursor(stream)
    dst = bytearray()

    opcode = src.byte()
    if opcode > 0x11:
        opcode = _lz77_copy(opcode - 17, src, dst)
    if (opcode & 0xF0) == 0:
        opcode = _lz77_copy(_literal_count(opcode, src) + 3, src, dst)

    while opcode != 0x11:
        if opcode < 0x10 or opcode >= 0x40:
            compressed = (opcode >> 4) - 1
            opcode2 = src.byte()
            offset = ((opcode >> 2 & 3) | (opcode2 << 2)) + 1
        elif opcode < 0x20:
            compressed = _compressed_bytes(opcode, 0b0111, src)
            offset = (opcode & 8) << 11
            opcode, offset = _two_byte_offset(offset, 0x4000, src)
        else:
            compressed = _compressed_bytes(opcode, 0b00011111, src)
            opcode, offset = _two_byte_offset(0, 1, src)

        # dst.Read/dst.Write through a temp buffer of Min(compressed, offset),
        # which is how upstream copies a run longer than its own offset.
        position = len(dst)
        chunk = bytes(dst[position - offset : position - offset + min(compressed, offset)])
        while compressed > 0:
            dst += chunk[: min(compressed, offset)]
            compressed -= offset

        lit = opcode & 3
        if lit == 0:
            opcode = src.byte()
            if (opcode & 0b11110000) == 0:
                lit = _literal_count(opcode, src) + 3
        if lit > 0:
            opcode = _lz77_copy(lit, src, dst)

    return bytes(dst)


class _Cursor:
    def __init__(self, data):
        self.data = data
        self.at = 0

    def byte(self):
        if self.at >= len(self.data):
            raise AssertionError("the literal run runs past the end of its own stream")
        value = self.data[self.at]
        self.at += 1
        return value

    def take(self, count):
        out = self.data[self.at : self.at + count]
        if len(out) != count:
            raise AssertionError("the literal run runs past the end of its own stream")
        self.at += count
        return out


def _lz77_copy(count, src, dst):
    dst += src.take(count)
    return src.byte()


def _literal_count(code, src):
    lowbits = code & 0b1111
    if lowbits == 0:
        last = src.byte()
        while last == 0:
            lowbits += 0xFF
            last = src.byte()
        lowbits += 0x0F + last
    return lowbits


def _compressed_bytes(opcode, valid_bits, src):
    count = opcode & valid_bits
    if count == 0:
        last = src.byte()
        while last == 0:
            count += 0xFF
            last = src.byte()
        count += valid_bits + last
    return count + 2


def _two_byte_offset(offset, addend, src):
    first = src.byte()
    second = src.byte()
    return second, offset + addend + (first >> 2) + (second << 6)


# --------------------------------------------------------------------------
# The pieces of the file
# --------------------------------------------------------------------------


def file_metadata(version=b"AC1018"):
    """Bytes 0x00 to 0x80, which `readFileMetaData` walks and mostly skips.

    The only field in here this reader carries forward is the code page at
    0x13, and 30 is what every AC1018 and later file in `tests/fixtures`
    declares. Everything else is advanced over, and the walk ends at 0x80
    exactly, which is where the header block has to start.
    """
    head = bytearray(HEADER_BLOCK_OFFSET)
    head[0:6] = version
    struct.pack_into("<h", head, 0x13, 30)
    return bytes(head)


def header_block(page_map_address, section_map_id):
    """The 0x6C bytes at 0x80, XORed with the keystream on the way out.

    Two of these fields decide where the reader goes next and the rest are
    read into properties `DwgReader` never looks at again, so they are written
    the way a real file writes them and left there.
    """
    plain = bytearray(HEADER_BLOCK_BYTES)
    plain[0:12] = b"AcFssFcAJMB\0"
    struct.pack_into("<i", plain, 0x10, 0x6C)
    struct.pack_into("<i", plain, 0x14, 0x04)
    struct.pack_into("<i", plain, 0x24, 1)
    struct.pack_into("<i", plain, 0x44, 0x20)
    struct.pack_into("<i", plain, 0x48, 0x80)
    struct.pack_into("<i", plain, 0x4C, 0x40)
    # 0x54 is the page map address and the reader adds 0x100 to it, which is
    # the same 0x100 every record seeker is counted from.
    struct.pack_into("<Q", plain, 0x54, page_map_address - PAGE_AREA_OFFSET)
    struct.pack_into("<I", plain, 0x5C, section_map_id)
    key = keystream(HEADER_BLOCK_BYTES)
    return bytes(p ^ k for p, k in zip(plain, key))


def page_header(section_type, decompressed_size, compressed_size, compression=2):
    """The 20 plaintext bytes `getPageHeaderData` reads.

    `decompressed_size` is load bearing beyond the obvious: `HugeMemoryStream`
    hands back a fixed-length buffer, so it is the decompressed stream's
    `Length` as well as its capacity, and the page-map loop reads until it.
    Declaring more than the payload decompresses to leaves the tail zeroed and
    the loop reads a run of record 0s out of it.
    """
    return struct.pack(
        "<iiiii", section_type, decompressed_size, compressed_size, compression, 0
    )


def page_map(records):
    """The page map's plaintext: one (number, size) pair per record, in order.

    Seekers are not in the file. The reader assigns them by running total from
    0x100, so the sizes here are what decides where it looks for each page.
    """
    return b"".join(struct.pack("<ii", number, size) for number, size in records)


def section_map(descriptors, ndescriptions=None):
    """The data-section map's plaintext, the thing this whole module is for."""
    count = len(descriptors) if ndescriptions is None else ndescriptions
    out = struct.pack("<iiiii", count, 0x02, 0x00007400, 0x00, count)
    for descriptor in descriptors:
        name = descriptor.get("name", SECTION_NAME).encode("cp1252")
        if len(name) > 64:
            raise AssertionError(f"{name!r} does not fit the 64-byte name field")
        pages = descriptor.get("pages", ())
        out += struct.pack("<Q", descriptor["compressed_size"])
        out += struct.pack("<i", descriptor["page_count"])
        # Read as a signed Int32 and cast to ulong, so everything this
        # declares stays inside the positive half: a negative value is a
        # different experiment and it does not reach these guards.
        out += struct.pack("<i", descriptor["decompressed_size"])
        out += struct.pack("<i", 0)
        out += struct.pack("<i", descriptor.get("compression", 2))
        out += struct.pack("<i", descriptor.get("section_id", 1))
        out += struct.pack("<i", 0)
        out += name + bytes(64 - len(name))
        for page in pages:
            out += struct.pack(
                "<iiQ", page["page_number"], page["compressed_size"], page["offset"]
            )
    return out


def data_page(seeker, payload):
    """One data page: `decryptDataSection`'s 32-byte header, then the payload.

    The header is XORed with `0x4164536B ^ position`, and position is where
    the page starts, so a page cannot be moved without rewriting it. Only the
    compressed size and the page size are read back into anything.
    """
    mask = (DATA_PAGE_MASK ^ seeker) & 0xFFFFFFFF
    fields = (DATA_PAGE_MAGIC, 1, len(payload), len(payload), 0, 0, 0, 0)
    head = b"".join(struct.pack("<I", (f ^ mask) & 0xFFFFFFFF) for f in fields)
    return head + bytes(payload)


def forge(descriptors, payload=None, version=b"AC1018", ndescriptions=None):
    """A whole AC1018 file: the header, the section map, the page map.

    The layout is the smallest one the reader accepts. Record 1 is the section
    map, record 2 is the single data page when there is one, and the page map
    itself sits after both without a record of its own, because the reader
    reaches it by the address in the header rather than by number.
    """
    section_plain = section_map(descriptors, ndescriptions=ndescriptions)
    section_stream = lz77_store(section_plain)
    area = page_header(SECTION_MAP_MAGIC, len(section_plain), len(section_stream))
    area += section_stream
    records = [(1, len(area))]

    if payload is not None:
        seeker = PAGE_AREA_OFFSET + len(area)
        page = data_page(seeker, lz77_store(payload))
        records.append((2, len(page)))
        area += page

    map_plain = page_map(records)
    map_stream = lz77_store(map_plain)
    map_address = PAGE_AREA_OFFSET + len(area)
    area += page_header(PAGE_MAP_MAGIC, len(map_plain), len(map_stream)) + map_stream

    head = file_metadata(version) + header_block(map_address, 1) + bytes(20)
    if len(head) != PAGE_AREA_OFFSET:
        raise AssertionError(f"the header came out {len(head)} bytes, not {PAGE_AREA_OFFSET}")
    return head + area


def read_back(blob):
    """Walk a forged file the way `readFileHeaderAC18` does, and hand back what
    it would have put in the descriptors.

    The point of having it is that a fixture is only evidence if the numbers it
    claims to declare are the numbers a reader would find, and the only reader
    that can say so here is this one: pytest has no .NET. It is the same walk,
    in the same order, off the same three anchors the file is built from, so a
    layout mistake shows up as a walk that cannot find its way rather than as a
    capture nobody can account for.
    """
    if blob[:6] not in (b"AC1018", b"AC1024", b"AC1027", b"AC1032"):
        raise AssertionError(f"{blob[:6]!r} is not a signature the AC18 reader takes")
    key = keystream(HEADER_BLOCK_BYTES)
    block = bytes(
        b ^ k for b, k in zip(blob[HEADER_BLOCK_OFFSET : HEADER_BLOCK_OFFSET + HEADER_BLOCK_BYTES], key)
    )
    if block[:12] != b"AcFssFcAJMB\0":
        raise AssertionError("the header block does not decode to the file ID string")
    map_address = struct.unpack_from("<Q", block, 0x54)[0] + PAGE_AREA_OFFSET
    section_map_id = struct.unpack_from("<I", block, 0x5C)[0]

    magic, size, _compressed, _type, _crc = struct.unpack_from("<iiiii", blob, map_address)
    if magic != PAGE_MAP_MAGIC:
        raise AssertionError(f"no page map at {map_address}, found {magic:#x}")
    plain = lz77_decompress(blob[map_address + 20 :])[:size]
    records = {}
    total = PAGE_AREA_OFFSET
    for at in range(0, len(plain), 8):
        number, length = struct.unpack_from("<ii", plain, at)
        if number >= 0:
            records[number] = total
        total += length

    magic, size, _compressed, _type, _crc = struct.unpack_from(
        "<iiiii", blob, records[section_map_id]
    )
    if magic != SECTION_MAP_MAGIC:
        raise AssertionError(f"no section map at record {section_map_id}")
    plain = lz77_decompress(blob[records[section_map_id] + 20 :])[:size]

    count = struct.unpack_from("<i", plain, 0)[0]
    at = 20
    out = []
    for _ in range(count):
        compressed_size = struct.unpack_from("<Q", plain, at)[0]
        page_count, decompressed_size = struct.unpack_from("<ii", plain, at + 8)
        name = plain[at + 32 : at + 96].split(b"\0")[0].decode("cp1252")
        at += 96
        pages = []
        for _ in range(page_count):
            page_number, page_compressed = struct.unpack_from("<ii", plain, at)
            offset = struct.unpack_from("<Q", plain, at + 8)[0]
            pages.append(
                {"page_number": page_number, "compressed_size": page_compressed, "offset": offset}
            )
            at += 16
        out.append(
            {
                "name": name,
                "page_count": page_count,
                "decompressed_size": decompressed_size,
                "compressed_size": compressed_size,
                "pages": pages,
                "records": records,
            }
        )
    return out


def descriptor(page_count, decompressed_size, offsets=(0,), compressed_size=None):
    """One section descriptor with a page entry per offset.

    Every page entry names record 1, which is the section map itself. Nothing
    reads a page's bytes until `getSectionBuffer18`, long after both guards
    have run, and pointing the entries at a record that exists is what lets
    the same file be run against an unpatched build: a page number with no
    record raises `KeyNotFoundException` inside the descriptor loop, which
    would refuse the input before it could allocate anything.
    """
    if compressed_size is None:
        compressed_size = page_count * decompressed_size
    return {
        "page_count": page_count,
        "decompressed_size": decompressed_size,
        "compressed_size": compressed_size,
        "pages": [
            {"page_number": 1, "compressed_size": 0x20, "offset": offset}
            for offset in offsets
        ],
    }


# --------------------------------------------------------------------------
# The inputs, one per branch of the two guards
# --------------------------------------------------------------------------

# Two declared sizes per branch that takes a size, a gigabyte apart, so a
# capture can say the allocation does not move with the number. The same pair
# the AC18 and AC21 fixtures already use.
DECLARED_SIZES = (
    ("64MiB", 1 << 26),
    ("1GiB", 1 << 30),
)

# The product case: neither factor is refused on its own and their product is.
# 3 pages is far below MaxSectionPages and 8 MiB is half the ceiling, and 24
# MiB is past it.
PRODUCT_PAGES = 3
PRODUCT_PAGE_SIZE = 8 * 1024 * 1024

# The gap case: an offset inside the ceiling whose quotient is not. The gap
# fill adds one page per DecompressedSize, so an 8-byte page size turns 16 MB
# of offset into two million objects, and the buffer they add up to is still
# under the byte ceiling. This is the one MaxSectionPages exists for.
GAP_PAGE_SIZE = 8
GAP_OFFSET = 16000000

# The page size the offset cases declare, which has to pass CheckDescriptor
# for the offset to be what refuses them.
OFFSET_PAGE_SIZE = 0x400

# What the control declares. A real page of a plausible size, one gap in front
# of it so the fill loop runs and stops, and a payload behind it so the
# section buffer is assembled out of something.
CONTROL_PAGE_SIZE = 0x400
CONTROL_OFFSET = 0x400


def descriptor_page_size_input(value):
    """Trips `CheckDescriptor`'s page-size branch."""
    return forge([descriptor(1, value)])


def descriptor_zero_input():
    """Trips the same branch with the value that makes the gap fill hang.

    `decompressSizeCounter += descriptor.DecompressedSize` is the only thing
    that advances the gap fill, so a zero page size and any offset above it is
    a loop that never ends while `LocalSections` keeps growing. That is why
    this case is recorded patched and never run against a build without the
    guard: the recorder in `tests/fixtures/gen/regenerate.py` calls
    `subprocess.run` with no timeout, so a hang there is a recorder that never
    returns rather than a measurement.
    """
    return forge([descriptor(1, 0, offsets=(0x400,), compressed_size=0)])


def descriptor_product_input():
    """Trips `CheckDescriptor`'s product branch, with both factors legal."""
    return forge(
        [descriptor(PRODUCT_PAGES, PRODUCT_PAGE_SIZE, offsets=(0,) * PRODUCT_PAGES)]
    )


def page_offset_input(value):
    """Trips `CheckPageOffset`'s offset branch.

    The descriptor in front of it declares a page size the ceiling accepts, so
    whatever refuses this file is the offset and not the descriptor.
    """
    return forge([descriptor(1, OFFSET_PAGE_SIZE, offsets=(value,))])


def page_offset_gap_input():
    """Trips `CheckPageOffset`'s gap branch, with the offset itself legal."""
    return forge([descriptor(1, GAP_PAGE_SIZE, offsets=(GAP_OFFSET,))])


def control_input():
    """The same forge with nothing inflated.

    It reaches both guards and passes both: a 1 KB page size, one page, and an
    offset one page further in, so the gap fill adds a single empty page and
    stops. `getSectionBuffer18` then allocates two pages' worth, zero-fills the
    empty one and decompresses the real one into the rest, and what refuses the
    file is `DwgHeaderReader` finding no header in it. That is the pair the
    refusals need: a ceiling that refused every forged AC18 map would produce
    the same captures without it.
    """
    return forge(
        [
            {
                "page_count": 1,
                "decompressed_size": CONTROL_PAGE_SIZE,
                "compressed_size": 2 * CONTROL_PAGE_SIZE,
                "pages": [
                    {
                        "page_number": 2,
                        "compressed_size": CONTROL_PAGE_SIZE,
                        "offset": CONTROL_OFFSET,
                    }
                ],
            }
        ],
        payload=bytes(CONTROL_PAGE_SIZE),
    )


def _case(name, guard, site, declared, declared_text, field, blob):
    """One case, carrying the bytes as well as their digest.

    The generator writes `blob` and the test recomputes `sha256` from this
    same builder, which is the contract the AC18 and AC21 derivations already
    hold to: a capture is evidence only if the input it names can be rebuilt
    from what is committed.
    """
    if CEILING_FACTOR * len(blob) >= FLOOR_BYTES:
        raise AssertionError(
            f"{name} is {len(blob)} bytes, big enough that 64 times its length is "
            f"past {FLOOR_BYTES}. Every number this module declares was chosen "
            "against the floor, so the ceiling it is measured against has moved."
        )
    return {
        "name": name,
        "scenario": f"declared/{name}",
        "guard": guard,
        "site": site,
        "declared": declared,
        "declared_text": declared_text,
        "field": field,
        "blob": blob,
        "bytes": len(blob),
        "sha256": sha256_bytes(blob),
    }


DESCRIPTOR_SITE = f"section descriptor '{SECTION_NAME}'"
OFFSET_SITE = f"section '{SECTION_NAME}' page offset"


def inputs():
    """Every AC18 input, built here so a test can rebuild and compare digests."""
    out = []
    for label, value in DECLARED_SIZES:
        out.append(
            _case(
                f"ac18_descriptor_page_size_{label}",
                "CheckDescriptor",
                f"{DESCRIPTOR_SITE} page size",
                value,
                str(value),
                "descriptor DecompressedSize",
                descriptor_page_size_input(value),
            )
        )

    out.append(
        _case(
            "ac18_descriptor_page_size_zero",
            "CheckDescriptor",
            f"{DESCRIPTOR_SITE} page size",
            0,
            "0",
            "descriptor DecompressedSize",
            descriptor_zero_input(),
        )
    )

    out.append(
        _case(
            "ac18_descriptor_product",
            "CheckDescriptor",
            DESCRIPTOR_SITE,
            PRODUCT_PAGES * PRODUCT_PAGE_SIZE,
            f"{PRODUCT_PAGES} pages of {PRODUCT_PAGE_SIZE}",
            "descriptor PageCount times DecompressedSize",
            descriptor_product_input(),
        )
    )

    for label, value in DECLARED_SIZES:
        out.append(
            _case(
                f"ac18_page_offset_{label}",
                "CheckPageOffset",
                OFFSET_SITE,
                value,
                str(value),
                "page Offset",
                page_offset_input(value),
            )
        )

    out.append(
        _case(
            "ac18_page_offset_gap",
            "CheckPageOffset",
            f"{OFFSET_SITE} gap",
            GAP_OFFSET,
            str(GAP_OFFSET),
            "page Offset over DecompressedSize",
            page_offset_gap_input(),
        )
    )
    return out


def control_case():
    blob = control_input()
    return {
        "name": "ac18_control",
        "scenario": "declared/ac18_control",
        "declared": CONTROL_PAGE_SIZE,
        "blob": blob,
        "bytes": len(blob),
        "sha256": sha256_bytes(blob),
    }


# The two guards this reaches, and the branches of each that an input covers.
# `inputs()` has to cover every one of them between them.
BRANCHES = (
    (f"{DESCRIPTOR_SITE} page size", "CheckDescriptor"),
    (DESCRIPTOR_SITE, "CheckDescriptor"),
    (OFFSET_SITE, "CheckPageOffset"),
    (f"{OFFSET_SITE} gap", "CheckPageOffset"),
)
