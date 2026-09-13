"""What a uint32 count can really hold, and whether DocumentEnd counts itself.

Two gaps in `docs/WIRE.md`, and the code that has to agree with what it says.

Every count on this wire is a `uint32` and every bound in
`viprs_acad_limits_v1` is a `uint64`, so a consumer author reading the two
documents together could reasonably set `max_polyline_points` to four billion
and expect a stream. They would not get one: a record's `length` is written
from a signed 32-bit cursor, so nothing longer than 2^31 - 1 bytes is ever
emitted, and every count inherits a ceiling from that. A limit above a ceiling
is a limit the wire cannot carry, and until it was written down the only way
to find out was to hit it.

The numbers below are not compared against a copy of themselves. The two
`point_count` ceilings are recomputed from the `length` formula `WIRE.md`
states in its own `Polyline` section, and the three string ceilings from the
fixed prefixes the two conformance consumers assert independently of each
other and of this file.

`DocumentEnd.total_records` is the smaller half: the field said "totals for
the whole stream" and nothing about whether the record saying so is in the
total. Both consumers already count it, so this is the document catching up
with two oracles, and the check is that the sentence and `DecodeSession`'s
arithmetic still agree.
"""

import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ACADSHARP = os.path.dirname(HERE)
WIRE_MD = os.path.join(ACADSHARP, "docs", "WIRE.md")
ENCODER_CS = os.path.join(ACADSHARP, "native", "Wire", "RecordEncoder.cs")
SESSION_CS = os.path.join(ACADSHARP, "native", "Wire", "DecodeSession.cs")
CONFORMANCE_C = os.path.join(HERE, "conformance", "c", "conformance.c")
CONFORMANCE_RS = os.path.join(HERE, "conformance", "rust", "src", "payload.rs")

# The producer's cursor is an int, so this is the longest record that can be
# framed at all. Everything in this module is a consequence of it.
MAX_RECORD_BYTES = 2**31 - 1

# The ceilings table's own heading, so the parse below reads one section and
# not whichever table happens to be nearby.
CEILINGS_HEADING = "### Ceilings the counts cannot exceed"


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def wire():
    return read(WIRE_MD)


@pytest.fixture(scope="module")
def wire_flat(wire):
    """WIRE.md with its hard wrapping collapsed.

    The document is wrapped to a terminal width, so a sentence these checks
    look for lands across two lines as often as not, and asserting on the
    wrapped text fails on a reflow, which is a fact about an editor rather than
    about the contract.
    """
    return re.sub(r"\s+", " ", wire)


def section(text, heading):
    """One `###` section of a markdown document, heading excluded."""
    at = text.index(heading) + len(heading)
    rest = text[at:]
    end = re.search(r"^#{2,3} ", rest, re.M)
    return rest[: end.start()] if end else rest


@pytest.fixture(scope="module")
def ceilings(wire):
    """`{field phrase: value}` out of the ceilings table."""
    rows = re.findall(
        r"^\|\s*(`\w+`[^|]*?)\s*\|\s*([\d,]+)\s*\|",
        section(wire, CEILINGS_HEADING),
        re.M,
    )
    return {field: int(value.replace(",", "")) for field, value in rows}


def ceiling_for(ceilings, field, must_contain=None):
    hits = [
        v
        for k, v in ceilings.items()
        if k.startswith(f"`{field}`") and (must_contain is None or must_contain in k)
    ]
    assert len(hits) == 1, (
        f"WIRE.md's ceilings table has {len(hits)} rows for {field} "
        f"{'' if must_contain is None else 'mentioning ' + must_contain!r}: {ceilings}"
    )
    return hits[0]


def pad4(n):
    return -n % 4


def largest_string_len(prefix):
    """The longest string a record with `prefix` fixed bytes can carry.

    The padding to a multiple of four comes out of the same budget as the
    bytes, so the answer is not simply `MAX_RECORD_BYTES - prefix`: it is that,
    rounded down to where the padded record still fits.
    """
    for n in range(MAX_RECORD_BYTES - prefix, -1, -1):
        if prefix + n + pad4(n) <= MAX_RECORD_BYTES:
            return n
    raise AssertionError("unreachable: a zero-length string always fits")


@pytest.fixture(scope="module")
def polyline_formula(wire):
    """The `length` formula WIRE.md gives for records 4 and 9, as a callable.

    Read out of the document rather than written here, so the ceilings below
    are checked against what a consumer author is actually told.
    """
    m = re.search(r"`length` is `(\d+) \+ (\d+)n \+ (\d+)bc`", wire)
    assert m, "WIRE.md no longer states the Polyline length formula"
    fixed, per_point, per_bulge = (int(g) for g in m.groups())
    return lambda n, bc: fixed + per_point * n + per_bulge * bc


def fixed_prefix(record):
    """The fixed bytes of a variable-length record, from both consumers.

    `conformance.c` and `payload.rs` each assert a record's whole length as a
    fixed part plus the string and its padding. They were written against the
    specification separately, so agreeing with each other is the check, and
    this returns the number only when they do.
    """
    c = re.search(
        rf"want_len\(len, (\d+)u \+ (\d+)u \+ [^,]+, \"{record} length\"\)",
        read(CONFORMANCE_C),
    )
    rs = re.search(
        rf"len_is\(r, (\d+) \+ (\d+) \+ [^,]+, \"{record} length\"\)",
        read(CONFORMANCE_RS),
    )
    assert c and rs, f"neither consumer states a variable length for {record}"
    from_c = int(c.group(1)) + int(c.group(2))
    from_rs = int(rs.group(1)) + int(rs.group(2))
    assert from_c == from_rs, (
        f"the two consumers disagree about {record}'s fixed bytes: {from_c} and {from_rs}"
    )
    return from_c


@pytest.fixture(scope="module")
def take_body():
    """`Cursor.Take`, on its own.

    Every write on this wire goes through it, so the bound arithmetic is in one
    place and a check for it can be too.
    """
    text = read(ENCODER_CS)
    at = text.index("Span<byte> Take(int n)")
    rest = text[at:]
    end = re.search(r"\n\t\t\tpublic ", rest)
    assert end, "Take is no longer followed by a public member of Cursor"
    return rest[: end.start()]


class TestTheTableIsReadable:
    """The positive controls. Every assertion below compares a parsed number
    against a computed one, and a parse that found nothing compares nothing."""

    def test_the_document_carries_a_ceilings_table(self, ceilings):
        assert len(ceilings) >= 5, (
            f"WIRE.md's ceilings table parsed to {ceilings}. Every check in this module "
            "reads it, so an unparsed table would make all of them pass over nothing."
        )

    def test_the_consumers_state_a_fixed_prefix(self):
        for record in ("ViewBegin", "Text", "Warning"):
            assert fixed_prefix(record) > 0


class TestTheCeilingsAreTheArithmetic:
    def test_a_record_is_never_longer_than_a_signed_int(self, wire_flat):
        assert "2^31 - 1" in wire_flat, (
            "WIRE.md gives no record-length ceiling, so nothing below it has a reason"
        )
        assert re.search(r"signed 32-bit|signed thirty-two", wire_flat), (
            "WIRE.md states the ceiling without saying where it comes from. It is not a "
            "property of the frame, which holds a uint32: it is the producer's own "
            "cursor, and a reader who does not know that will expect 2^32 - 1."
        )

    def test_the_point_count_ceiling_without_bulges(self, ceilings, polyline_formula):
        n = ceiling_for(ceilings, "point_count", "bulge_count` 0")
        assert polyline_formula(n, 0) <= MAX_RECORD_BYTES
        assert polyline_formula(n + 1, 0) > MAX_RECORD_BYTES, (
            f"WIRE.md says {n:,} vertices is the ceiling, but one more still fits "
            "inside a record. A ceiling below the real one refuses a drawing the wire "
            "could have carried."
        )

    def test_the_point_count_ceiling_with_one_bulge_per_vertex(self, ceilings, polyline_formula):
        n = ceiling_for(ceilings, "point_count", "one bulge per vertex")
        assert polyline_formula(n, n) <= MAX_RECORD_BYTES
        assert polyline_formula(n + 1, n + 1) > MAX_RECORD_BYTES

    @pytest.mark.parametrize(
        "field,record",
        (("name_len", "ViewBegin"), ("byte_len", "Text"), ("message_len", "Warning")),
    )
    def test_the_string_ceilings(self, ceilings, field, record):
        stated = ceiling_for(ceilings, field)
        assert stated == largest_string_len(fixed_prefix(record)), (
            f"WIRE.md says {field} reaches {stated:,}, and record {record} has "
            f"{fixed_prefix(record)} fixed bytes plus a string padded to a multiple of "
            "four. The padding comes out of the same budget as the bytes."
        )

    def test_a_limit_above_a_ceiling_is_called_what_it_is(self, wire_flat):
        assert "A limit set above a ceiling is a limit the wire cannot carry" in wire_flat, (
            "WIRE.md gives the ceilings and never says what setting a bound past one "
            "means. That is the whole reason a consumer author needs them: the limits "
            "struct takes a uint64 and will accept every value above every ceiling here."
        )


class TestTheEncoderHonoursThem:
    """Reading, not running.

    Driving `Cursor` past `int.MaxValue` needs a record about 2 GiB long, which
    means a fixture that big and an allocation to match, in a container, for
    one branch. So this is read rather than run, and the `checked` context is
    what makes reading enough: it is not a comment about intent, it is the
    arithmetic refusing to wrap.
    """

    def test_a_negative_count_is_refused(self, take_body):
        assert re.search(r"if \(n < 0\)", take_body), (
            "Cursor.Take accepts a negative length. That is not a hypothetical: a "
            "caller may set max_polyline_points anywhere in a uint64, and the double "
            "count for a polyline at 90 million vertices overflows int when it is "
            "multiplied by eight, which arrives here as a negative n."
        )
        assert "Result.LimitExceeded" in take_body, (
            "Cursor.Take refuses something without LIMIT_EXCEEDED. A count the wire "
            "cannot carry is a bound breached, which is what docs/ABI.md says that "
            "code means, and it is the code every other bound on this path reports."
        )

    def test_the_position_arithmetic_cannot_wrap(self, take_body):
        assert "checked(" in take_body, (
            "Cursor._pos advances in an unchecked context, so a record past 2 GiB "
            "wraps the cursor to a negative position and the encoder writes a record "
            "whose length header is a lie rather than refusing it"
        )
        unguarded = re.findall(r"_pos\s*(?:\+=|=\s*(?!checked)\S)", take_body)
        assert not unguarded, (
            f"Cursor.Take advances _pos outside a checked context: {unguarded}. One "
            "arithmetic site is the whole point; a second one is where the wrap comes "
            "back."
        )


class TestDocumentEndSaysWhetherItCountsItself:
    def test_the_document_says_it(self, wire_flat):
        assert re.search(
            r"total_records` counts every record in the stream, .*?this record included",
            wire_flat,
        ), (
            "WIRE.md still says only that DocumentEnd carries totals for the whole "
            "stream. A consumer tallying records as it parses has to know whether the "
            "record carrying the number is in it, and off by one is exactly the size "
            "of the mistake."
        )

    def test_it_is_view_end_plus_two(self, wire_flat):
        assert "ViewEnd`'s `record_count` plus two" in wire_flat

    def test_the_shim_still_agrees(self, wire_flat):
        # The arithmetic the sentence describes, read out of the code that
        # produces both numbers. A document that says "plus two" beside a shim
        # that moved to plus three is the failure this catches, and it is the
        # one a consumer cannot see.
        text = read(SESSION_CS)
        view = re.search(r"Primitive\.ViewEnd\(_viewIndex, _items \+ (\d+)ul\)", text)
        document = re.search(r"Primitive\.DocumentEnd\(_items \+ (\d+)ul,", text)
        assert view and document, "DecodeSession no longer computes either total here"
        assert int(document.group(1)) - int(view.group(1)) == 2, (
            f"DecodeSession emits ViewEnd at _items + {view.group(1)} and DocumentEnd "
            f"at _items + {document.group(1)}, which is not the plus two WIRE.md now "
            "promises"
        )
