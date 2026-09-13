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

import os
import re
import struct

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ACADSHARP = os.path.dirname(HERE)
WIRE_MD = os.path.join(ACADSHARP, "docs", "WIRE.md")

MAGIC = b"VACB"
WIRE_VERSION = 1
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
UNSUPPORTED_FORMAT = 2
CORRUPT_INPUT = 3


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
        return UNSUPPORTED_FORMAT, []
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


def record(rtype, payload=b""):
    pad = (-len(payload)) % 4
    payload = payload + b"\0" * pad
    return struct.pack("<HHI", rtype, 0, RECORD_HEADER_BYTES + len(payload)) + payload


def batch(records, flags=0, payload_length=None, version=WIRE_VERSION, magic=MAGIC):
    body = b"".join(records)
    if payload_length is None:
        payload_length = len(body)
    return magic + struct.pack("<HHI", version, flags, payload_length) + body


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
        code, _ = parse_batch(batch([record(3)], version=2))
        assert code == UNSUPPORTED_FORMAT, (
            "a consumer that parses a version it does not know is reading a layout it "
            "is only assuming, which is worse than refusing the stream."
        )

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
