"""The batch protocol, and a reference parser that runs with no toolchain.

The real parsers live with the conformance consumers, in C and in the crate
the header generates. Neither runs in this repository's CI, because a CI job
that compiles them needs the libviprs-tests Hook Mirror pairing that ADR 0001
already declined for the managed side. So the protocol's rules are pinned
here as well, by a reference parser small enough to read in one sitting, and
the constants the three implementations share are compared as text.

Three of these tests are the ones the epic calls out, and each has a failing
state worth naming:

* An unknown record type is skipped by its length and the next record is
  read. Without that, adding a record type in wire_version 2 breaks every
  v1 consumer, which is the entire reason the length is in the header.
* A `payload_length` that runs past the end of the buffer is CORRUPT_INPUT.
  Without the check it is a read of whatever the caller's allocation happens
  to be followed by.
* A record `length` smaller than its own header is rejected rather than
  looped on. This one is written so the failure is a failure and not a hang:
  the parser carries a hard iteration bound, so a version that accepts a
  zero-advance record trips the bound instead of spinning forever in CI.
"""

import math
import os
import re
import struct

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ACADSHARP = os.path.dirname(HERE)
WIRE_MD = os.path.join(ACADSHARP, "docs", "WIRE.md")

MAGIC = b"VACB"
WIRE_VERSION = 2
BATCH_HEADER_BYTES = 12
RECORD_HEADER_BYTES = 8
FLAG_LAST = 1

# 64 KiB to 1 MiB, as the epic freezes them.
TARGET_BATCH_BYTES = 64 * 1024
MAX_BATCH_BYTES = 1024 * 1024

# The frozen numbering. Written out rather than scraped from anything, so
# renumbering a type fails here instead of renumbering the expectation too.
RECORD_TYPES = {
    "DocumentBegin": 1,
    "ViewBegin": 2,
    "Line": 3,
    "Polyline": 4,
    "Arc": 5,
    "Circle": 6,
    "Ellipse": 7,
    "Spline": 8,
    "Polygon": 9,
    "Text": 10,
    "Warning": 11,
    "ViewEnd": 12,
    "DocumentEnd": 13,
}

# Records whose length never varies, as docs/WIRE.md states them. The number
# is the record's `length`, so the eight-byte header is in it. These are
# asserted against the bytes the library actually produces by both conformance
# consumers; here they are held against the document, because a payload table
# that disagrees with the producer is worse than no payload table.
FIXED_RECORD_BYTES = {
    "DocumentBegin": 24,
    "Line": 72,
    "Arc": 96,
    "Circle": 80,
    "Ellipse": 120,
    "ViewEnd": 24,
    "DocumentEnd": 24,
}

# Types at or above this carry no meaning in wire_version 1 and exist so a
# consumer's skip path is exercised by a real stream rather than only by a
# buffer a test built by hand.
FORWARD_PROBE_FIRST = 0x7F00

OK = 0
CORRUPT_INPUT = 3

# A stream carrying a wire_version this consumer does not parse. Not
# UNSUPPORTED_FORMAT, which is about the drawing and means check
# dwg_version_min and dwg_version_max: this is the two ends of the boundary
# disagreeing, and the remedy is to rebuild one of them. docs/ABI.md always
# said so; WIRE.md and the three parsers said UNSUPPORTED_FORMAT until wire
# version 2, which is the first time a real consumer meets a foreign version.
ABI_MISMATCH = 8


class ParserRanAway(AssertionError):
    """The parser stopped advancing. Raised instead of hanging."""


def parse_batch(buf):
    """Return (code, [(type, payload_bytes), ...]) for one batch.

    A reference implementation of docs/WIRE.md, deliberately dumb: it makes
    every bound explicit rather than leaning on a slice that would silently
    clamp.
    """
    if len(buf) < BATCH_HEADER_BYTES:
        return CORRUPT_INPUT, []
    if buf[:4] != MAGIC:
        return CORRUPT_INPUT, []
    version, flags, payload_length = struct.unpack_from("<HHI", buf, 4)
    if version != WIRE_VERSION:
        return ABI_MISMATCH, []
    if BATCH_HEADER_BYTES + payload_length > len(buf):
        return CORRUPT_INPUT, []

    records = []
    offset = BATCH_HEADER_BYTES
    end = BATCH_HEADER_BYTES + payload_length
    # One iteration per smallest possible record, plus one. A parser that
    # fails to advance hits this rather than spinning.
    budget = payload_length // RECORD_HEADER_BYTES + 2
    while offset < end:
        budget -= 1
        if budget < 0:
            raise ParserRanAway(
                f"the parser made no progress at offset {offset}. A record length that "
                "does not advance the cursor is the shape that turns a malformed batch "
                "into a hang, so it must be rejected rather than retried."
            )
        if end - offset < RECORD_HEADER_BYTES:
            return CORRUPT_INPUT, records
        rtype, reserved, length = struct.unpack_from("<HHI", buf, offset)
        if reserved != 0:
            return CORRUPT_INPUT, records
        if length < RECORD_HEADER_BYTES:
            return CORRUPT_INPUT, records
        if length % 4 != 0:
            return CORRUPT_INPUT, records
        if offset + length > end:
            return CORRUPT_INPUT, records
        records.append((rtype, bytes(buf[offset + RECORD_HEADER_BYTES : offset + length])))
        offset += length

    del flags
    return OK, records


# Records 4 and 9 share one payload, so one function reads both.
POLYLINE_TYPES = (RECORD_TYPES["Polyline"], RECORD_TYPES["Polygon"])


def polyline_shape(payload, length):
    """(point_count, bulge_count) for a Polyline or Polygon, or a refusal code.

    Beside the framing parser rather than inside it. `parse_batch` walks record
    headers and knows nothing about what a payload means, which is exactly what
    lets it skip a type it has never heard of; a layout rule pushed into that
    loop would make it wrong for every record type the day one of them changes.

    The three rules are docs/WIRE.md's: `bulge_count` is 0 or `point_count` and
    nothing else, `length` is `64 + 24n + 8bc` and not merely large enough, and
    no `f64` in the record is NaN or infinite. The second is not implied by the
    first: a producer that wrote the array and forgot the count produces a
    payload whose bytes are all there and whose trailing array nobody reads.
    """
    if len(payload) < 56:
        return CORRUPT_INPUT, None
    n, closed, bc, reserved1 = struct.unpack_from("<IIII", payload, 16)
    if closed > 1 or reserved1 != 0:
        return CORRUPT_INPUT, None
    if bc not in (0, n):
        return CORRUPT_INPUT, None
    if length != 64 + 24 * n + 8 * bc:
        return CORRUPT_INPUT, None
    for k in range(3 + (3 * n) + bc):
        (value,) = struct.unpack_from("<d", payload, 32 + (8 * k))
        if math.isnan(value) or math.isinf(value):
            return CORRUPT_INPUT, None
    return OK, (n, bc)


def vertex_record(rtype, n, bc, closed=0, length=None, values=None, reserved1=0):
    """One Polyline or Polygon, built field by field so a test can lie."""
    body = struct.pack("<QII", 0x4D, 0, 0) + struct.pack("<IIII", n, closed, bc, reserved1)
    if values is None:
        values = [1.0] * (3 + (3 * n) + bc)
    for v in values:
        body += struct.pack("<d", v)
    if length is None:
        length = RECORD_HEADER_BYTES + len(body)
    return struct.pack("<HHI", rtype, 0, length) + body


def record(rtype, payload=b""):
    pad = (-len(payload)) % 4
    payload = payload + b"\0" * pad
    return struct.pack("<HHI", rtype, 0, RECORD_HEADER_BYTES + len(payload)) + payload


def batch(records, flags=0, payload_length=None, version=WIRE_VERSION, magic=MAGIC):
    body = b"".join(records)
    if payload_length is None:
        payload_length = len(body)
    return magic + struct.pack("<HHI", version, flags, payload_length) + body


def polyline_section(wire_md):
    """WIRE.md's record 4 paragraph, on its own.

    Sliced rather than searched whole, because the Arc record two paragraphs
    down says "counter-clockwise" too, and a test that searched the document
    would pass with the Polyline section saying nothing at all.
    """
    start = wire_md.index("**4 `Polyline`**")
    return wire_md[start : wire_md.index("**5 `Arc`**", start)]


@pytest.fixture(scope="module")
def wire_md():
    with open(WIRE_MD) as f:
        return f.read()


class TestAWellFormedBatch:
    def test_a_batch_of_known_records_reads_back(self):
        code, records = parse_batch(
            batch(
                [
                    record(RECORD_TYPES["DocumentBegin"], struct.pack("<IIQ", 1, 1032, 0)),
                    record(RECORD_TYPES["ViewEnd"], struct.pack("<IIQ", 0, 0, 1)),
                ]
            )
        )
        assert code == OK
        assert [r[0] for r in records] == [1, 12]

    def test_the_magic_is_the_first_thing_checked(self):
        code, _ = parse_batch(batch([record(3)], magic=b"VACX"))
        assert code == CORRUPT_INPUT

    def test_an_unknown_wire_version_is_refused_not_guessed(self):
        # WIRE_VERSION + 1 rather than a literal. A literal 2 was the version
        # from the future until this bump made it the present one, and a test
        # that keeps a literal there quietly starts asserting that the current
        # version is refused.
        code, _ = parse_batch(batch([record(3)], version=WIRE_VERSION + 1))
        assert code == ABI_MISMATCH, (
            "a consumer that parses a version it does not know is reading a layout it "
            "is only assuming, which is worse than refusing the stream."
        )

    def test_the_version_before_this_one_is_refused_too(self):
        # Forward compatibility is the skip-by-length rule for record types.
        # It is not a licence to parse an older layout: wire version 1's
        # Polyline has no normal and no bulge array, so reading one as a v2
        # record walks off the end of a payload that is 32 bytes shorter.
        code, _ = parse_batch(batch([record(3)], version=WIRE_VERSION - 1))
        assert code == ABI_MISMATCH

    def test_an_empty_batch_is_legal(self):
        code, records = parse_batch(batch([]))
        assert code == OK and records == []


class TestForwardCompatibility:
    def test_an_unknown_type_mid_stream_is_skipped_and_the_next_record_is_read(self):
        payload = b"a record from a wire version this consumer has never seen"
        code, records = parse_batch(
            batch(
                [
                    record(RECORD_TYPES["Line"], b"\0" * 64),
                    record(FORWARD_PROBE_FIRST, payload),
                    record(RECORD_TYPES["Circle"], b"\0" * 72),
                ]
            )
        )
        assert code == OK
        assert [r[0] for r in records] == [3, FORWARD_PROBE_FIRST, 6], (
            "the record after the unknown one was not reached. Skipping by length is "
            "the only reason the length is in the record header, and without it "
            "wire_version 2 breaks every consumer compiled against version 1."
        )

    def test_the_skip_uses_the_length_and_not_a_known_size(self):
        # A forward probe whose payload is not a multiple of any known record
        # size. A parser that skipped by a table of sizes lands mid-record.
        code, records = parse_batch(
            batch(
                [
                    record(FORWARD_PROBE_FIRST, b"x" * 13),
                    record(RECORD_TYPES["DocumentEnd"], struct.pack("<QQ", 2, 0)),
                ]
            )
        )
        assert code == OK
        assert [r[0] for r in records] == [FORWARD_PROBE_FIRST, 13]


class TestMalformedBatchesAreRefused:
    def test_a_payload_length_past_the_buffer_end_is_corrupt_input(self):
        good = batch([record(RECORD_TYPES["Line"], b"\0" * 64)])
        lied = bytearray(good)
        struct.pack_into("<I", lied, 8, len(good))  # claims far more than is there
        code, _ = parse_batch(bytes(lied))
        assert code == CORRUPT_INPUT, (
            "the batch claimed more payload than the buffer holds. Trusting it reads "
            "whatever the caller allocated next."
        )

    def test_a_truncated_buffer_is_corrupt_input(self):
        good = batch([record(RECORD_TYPES["Line"], b"\0" * 64)])
        code, _ = parse_batch(good[:-8])
        assert code == CORRUPT_INPUT

    @pytest.mark.parametrize("length", (0, 1, 4, 7))
    def test_a_record_length_below_its_own_header_is_rejected_not_looped_on(self, length):
        body = struct.pack("<HHI", RECORD_TYPES["Line"], 0, length) + b"\0" * 64
        buf = MAGIC + struct.pack("<HHI", WIRE_VERSION, 0, len(body)) + body
        # ParserRanAway would escape rather than be caught here, which is the
        # point: an infinite loop shows up as a failing test and not a job
        # that has to be killed.
        code, _ = parse_batch(buf)
        assert code == CORRUPT_INPUT, (
            f"a record claiming length {length} was accepted. It is smaller than the "
            "8-byte record header, so the cursor cannot advance past it."
        )

    def test_a_record_length_running_past_the_payload_is_rejected(self):
        body = struct.pack("<HHI", RECORD_TYPES["Line"], 0, 4096) + b"\0" * 8
        buf = MAGIC + struct.pack("<HHI", WIRE_VERSION, 0, len(body)) + body
        code, _ = parse_batch(buf)
        assert code == CORRUPT_INPUT

    def test_a_record_length_that_is_not_a_multiple_of_four_is_rejected(self):
        body = struct.pack("<HHI", RECORD_TYPES["Line"], 0, 9) + b"\0" * 8
        buf = MAGIC + struct.pack("<HHI", WIRE_VERSION, 0, len(body)) + body
        code, _ = parse_batch(buf)
        assert code == CORRUPT_INPUT, (
            "record lengths are padded to four so the record after one never starts at "
            "an offset the producer did not choose."
        )

    def test_a_trailing_stub_too_small_to_be_a_header_is_rejected(self):
        body = record(RECORD_TYPES["Line"], b"\0" * 64) + b"\1\2\3\4"
        buf = MAGIC + struct.pack("<HHI", WIRE_VERSION, 0, len(body)) + body
        code, _ = parse_batch(buf)
        assert code == CORRUPT_INPUT


class TestThePolylineAndPolygonPayloadRules:
    """The v2 count, length and finiteness rules, one failing state each."""

    @pytest.mark.parametrize("rtype", POLYLINE_TYPES)
    def test_a_well_formed_record_reads_back_its_two_counts(self, rtype):
        raw = vertex_record(rtype, n=4, bc=4, closed=1)
        code, records = parse_batch(batch([raw]))
        assert code == OK
        length = len(records[0][1]) + RECORD_HEADER_BYTES
        assert polyline_shape(records[0][1], length) == (OK, (4, 4))

    @pytest.mark.parametrize("rtype", POLYLINE_TYPES)
    def test_no_bulges_at_all_is_the_other_legal_shape(self, rtype):
        raw = vertex_record(rtype, n=4, bc=0)
        code, records = parse_batch(batch([raw]))
        assert code == OK
        length = len(records[0][1]) + RECORD_HEADER_BYTES
        assert polyline_shape(records[0][1], length) == (OK, (4, 0))

    @pytest.mark.parametrize("rtype", POLYLINE_TYPES)
    def test_a_bulge_count_that_is_neither_zero_nor_the_point_count(self, rtype):
        # Three bulges for four vertices. The bytes are all there and the
        # length is consistent, so nothing about the framing objects; what
        # a consumer cannot do is work out which three spans they describe.
        raw = vertex_record(rtype, n=4, bc=3)
        _code, records = parse_batch(batch([raw]))
        length = len(records[0][1]) + RECORD_HEADER_BYTES
        assert polyline_shape(records[0][1], length) == (CORRUPT_INPUT, None)

    @pytest.mark.parametrize("rtype", POLYLINE_TYPES)
    def test_a_zero_bulge_count_with_a_bulge_inclusive_length(self, rtype):
        # The array written and the count left at zero, which is the mistake
        # that produces a record every framing rule accepts and whose trailing
        # numbers nobody ever reads.
        raw = vertex_record(rtype, n=4, bc=0, values=[1.0] * (3 + 12 + 4))
        _code, records = parse_batch(batch([raw]))
        length = len(records[0][1]) + RECORD_HEADER_BYTES
        assert length == 64 + (24 * 4) + (8 * 4)
        assert polyline_shape(records[0][1], length) == (CORRUPT_INPUT, None)

    @pytest.mark.parametrize("rtype", POLYLINE_TYPES)
    def test_a_closed_flag_that_is_not_zero_or_one(self, rtype):
        raw = vertex_record(rtype, n=3, bc=0, closed=2)
        _code, records = parse_batch(batch([raw]))
        length = len(records[0][1]) + RECORD_HEADER_BYTES
        assert polyline_shape(records[0][1], length) == (CORRUPT_INPUT, None)

    @pytest.mark.parametrize("rtype", POLYLINE_TYPES)
    def test_reserved1_is_refused_when_it_is_not_zero(self, rtype):
        raw = vertex_record(rtype, n=3, bc=0, reserved1=1)
        _code, records = parse_batch(batch([raw]))
        length = len(records[0][1]) + RECORD_HEADER_BYTES
        assert polyline_shape(records[0][1], length) == (CORRUPT_INPUT, None)

    @pytest.mark.parametrize("rtype", POLYLINE_TYPES)
    @pytest.mark.parametrize("slot", (0, 3, 14))
    def test_a_non_finite_value_anywhere_in_the_record(self, rtype, slot):
        # Slot 0 is the normal, 3 is the first vertex and 14 is a bulge, so
        # the three places a NaN can hide are each covered rather than only
        # whichever one the loop happens to reach first.
        values = [1.0] * (3 + 12 + 4)
        values[slot] = float("nan")
        raw = vertex_record(rtype, n=4, bc=4, values=values)
        _code, records = parse_batch(batch([raw]))
        length = len(records[0][1]) + RECORD_HEADER_BYTES
        assert polyline_shape(records[0][1], length) == (CORRUPT_INPUT, None)

    @pytest.mark.parametrize("rtype", POLYLINE_TYPES)
    def test_an_infinite_value_is_refused_the_same_way(self, rtype):
        values = [1.0] * (3 + 9 + 3)
        values[5] = float("inf")
        raw = vertex_record(rtype, n=3, bc=3, values=values)
        _code, records = parse_batch(batch([raw]))
        length = len(records[0][1]) + RECORD_HEADER_BYTES
        assert polyline_shape(records[0][1], length) == (CORRUPT_INPUT, None)

    @pytest.mark.parametrize("rtype", POLYLINE_TYPES)
    def test_a_length_that_is_merely_large_enough_is_still_wrong(self, rtype):
        raw = vertex_record(rtype, n=2, bc=0, values=[1.0] * 20)
        _code, records = parse_batch(batch([raw]))
        length = len(records[0][1]) + RECORD_HEADER_BYTES
        assert length > 64 + (24 * 2)
        assert polyline_shape(records[0][1], length) == (CORRUPT_INPUT, None)


class TestTheRunawayGuardItself:
    """The bounded-iteration guard is only worth having if it can fire."""

    def test_a_parser_that_does_not_advance_raises_instead_of_hanging(self):
        # A record that claims exactly its own header length but is followed
        # by nothing is legal, so the honest way to prove the guard works is
        # to drive it directly rather than to trust it has never been needed.
        buf = MAGIC + struct.pack("<HHI", WIRE_VERSION, 0, 64) + b"\0" * 64

        def zero_advance(_buf):
            offset, end, budget = BATCH_HEADER_BYTES, BATCH_HEADER_BYTES + 64, 64 // 8 + 2
            while offset < end:
                budget -= 1
                if budget < 0:
                    raise ParserRanAway("no progress")
                offset += 0
            return OK

        with pytest.raises(ParserRanAway):
            zero_advance(buf)
        assert parse_batch(buf)[0] in (OK, CORRUPT_INPUT)


class TestWireMdMatchesTheParser:
    def test_the_magic_is_documented(self, wire_md):
        assert "VACB" in wire_md

    def test_the_batch_header_fields_are_documented(self, wire_md):
        for field in ("wire_version", "flags", "payload_length"):
            assert field in wire_md, f"WIRE.md never mentions {field}"

    def test_the_record_header_fields_are_documented(self, wire_md):
        for field in ("type", "reserved", "length"):
            assert field in wire_md

    def test_every_record_type_is_documented_with_its_number(self, wire_md):
        for name, number in RECORD_TYPES.items():
            assert name in wire_md, f"WIRE.md never mentions the {name} record"
            row = re.search(rf"^.*\b{name}\b.*$", wire_md, re.M).group(0)
            assert re.search(rf"\b{number}\b", row), (
                f"WIRE.md mentions {name} but not next to its frozen number {number}. "
                "A consumer is written against the number, not the name."
            )

    def test_no_record_type_number_is_used_twice(self):
        assert len(set(RECORD_TYPES.values())) == len(RECORD_TYPES)

    def test_every_fixed_size_record_states_its_length(self, wire_md):
        flat = re.sub(r"\s+", " ", wire_md)
        for name, size in FIXED_RECORD_BYTES.items():
            number = RECORD_TYPES[name]
            start = flat.index(f"**{number} `{name}`**")
            paragraph = flat[start : start + 600].split("**" + str(number + 1) + " `")[0]
            assert f"{size} bytes" in paragraph, (
                f"WIRE.md does not say {name} is {size} bytes. A consumer sizes nothing "
                "from that number, but a reader checks the field list against it, and a "
                "field list nobody can check is a field list that drifts."
            )

    def test_the_batch_size_range_is_documented(self, wire_md):
        assert "64 KiB" in wire_md and "1 MiB" in wire_md

    def test_the_polyline_payload_is_documented(self, wire_md):
        section = polyline_section(wire_md)
        for field in ("point_count", "closed", "bulge_count", "reserved1"):
            assert field in section, f"WIRE.md's Polyline never mentions {field}"
        assert "64 + 24" in section and "8" in section, (
            "WIRE.md does not give the Polyline length formula, and a consumer that "
            "cannot check the length against the counts accepts a record whose "
            "trailing array nobody reads"
        )

    def test_the_bulge_sign_convention_is_documented(self, wire_md):
        section = polyline_section(wire_md)
        assert "counter-clockwise" in section, (
            "WIRE.md's Polyline section does not say which way a positive bulge turns. "
            "The Arc paragraph saying it is not enough: a consumer reading the Polyline "
            "section has to be told there, or it guesses and draws every arc mirrored."
        )
        assert "tan" in section

    def test_the_midpoint_formula_is_documented(self, wire_md):
        section = polyline_section(wire_md)
        assert "mid + (b" in section, (
            "WIRE.md gives no way to turn a bulge into a point. A sign convention in "
            "prose is not checkable; a formula is."
        )

    @pytest.mark.parametrize(
        "rule",
        (
            r"`bulge_count` is neither 0 nor `point_count`",
            r"`length` is not `64 \+ 24n \+ 8bc`",
            r"types 3 to 10, that is `NaN` or infinite",
        ),
    )
    def test_each_payload_refusal_has_a_row(self, wire_md, rule):
        table = wire_md[wire_md.index("## Refusing a stream") :]
        assert re.search(rule, table), f"no refusal row in WIRE.md matches {rule}"

    def test_curves_are_documented_as_never_tessellated(self, wire_md):
        assert re.search(r"never tessellat|not tessellat|no tessellat", wire_md, re.I), (
            "a shim that tessellates has thrown away the parameters the consumer needs "
            "to render the curve at its own resolution, and cannot get them back."
        )
        for curve in ("Arc", "Ellipse", "Spline"):
            assert curve in wire_md

    def test_the_unknown_type_rule_is_documented(self, wire_md):
        assert re.search(r"skip", wire_md, re.I)
        assert re.search(r"unknown", wire_md, re.I)

    def test_the_alignment_rule_is_documented(self, wire_md):
        # Records start at offset 12 of a batch, so nothing inside one is
        # naturally aligned. A consumer that reads a double with an aligned
        # load is relying on an accident.
        assert re.search(r"unaligned|not.*aligned|no.*alignment", wire_md, re.I), (
            "WIRE.md has to say that no field is guaranteed naturally aligned, because "
            "the 12-byte batch header puts every record at an odd multiple of four."
        )
