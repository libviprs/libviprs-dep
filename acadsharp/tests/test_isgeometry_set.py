"""`WireFormat.IsGeometry` is a set somebody decided, not a range that happened (#134).

It was the contiguous range 3 to 10, spelled as `FirstGeometryType` and
`LastGeometryType`, on the argument that the numbering is frozen so the range is
contiguous by construction. That is true of the thirteen record types wire 2
has and false of every record after them: 11, 12 and 13 are `Warning`,
`ViewEnd` and `DocumentEnd`, so the first geometry record a later wire adds is
numbered 14 and sits outside the range.

`Flattener.Finite()` returns early for anything `IsGeometry` refuses. A NaN
coordinate in record 14 would therefore cross the wire untouched, while
`docs/WIRE.md`'s promise that no geometry record carries a non-finite value
stayed literally true, nothing failed to compile, and no test of either half
noticed. ADR 0002 and ADR 0003 both say to change the range into a set before
any such record exists. These tests are that instruction made checkable.

The runtime half (a fixture carrying a NaN in a record past 13, expected to
produce warning 107 and no record) cannot exist before a record past 13 does.
It arrives with record 14, and `TestEveryTypeIsClassified` is what makes
forgetting it loud: a new `Type*` constant that is not in the set fails here
until somebody puts it in, or says in this file that it is not geometry.
"""

import os
import re

import pytest

ACAD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
WIRE_FORMAT = os.path.join(ACAD, "native", "Wire", "WireFormat.cs")
WIRE_MD = os.path.join(ACAD, "docs", "WIRE.md")
ENCODER = os.path.join(ACAD, "native", "Wire", "RecordEncoder.cs")

# The record types that are not geometry: stream framing and diagnostics. A new
# type constant has to be in the IsGeometry set or in this list, and putting it
# here is a statement that a consumer may meet a non-finite value in it.
NOT_GEOMETRY = {"DocumentBegin", "ViewBegin", "Warning", "ViewEnd", "DocumentEnd"}


def read(path):
    with open(path) as f:
        return f.read()


def type_constants():
    return {
        m.group(1): int(m.group(2))
        for m in re.finditer(r"public const ushort Type(\w+)\s*=\s*(\d+);", read(WIRE_FORMAT))
    }


def is_geometry():
    src = read(WIRE_FORMAT)
    m = re.search(r"static bool IsGeometry\(ushort type\)\s*\{(.*?)\n\t\t\}", src, re.S)
    assert m, "WireFormat.cs has no IsGeometry(ushort type) method this test can read"
    body = re.sub(r"//[^\n]*", "", m.group(1))
    return set(re.findall(r"\bType(\w+)\b", body)), body


class TestTheRangeIsGone:
    @pytest.mark.parametrize("name", ["FirstGeometryType", "LastGeometryType"])
    def test_the_range_endpoints_are_deleted(self, name):
        for path in (WIRE_FORMAT, ENCODER):
            code = re.sub(r"//[^\n]*", "", read(path))
            assert name not in code, (
                f"{name} is still spelled in {os.path.basename(path)}. A range with named "
                "ends is a second spelling of the answer that somebody extends instead of "
                "the set"
            )

    def test_is_geometry_does_not_compare_against_bounds(self):
        _, body = is_geometry()
        assert not re.search(r"[<>]=?", body), (
            "IsGeometry still compares against bounds. It has to name each type it "
            "accepts, so a record numbered past 13 is not geometry by default or by luck"
        )


class TestEveryTypeIsClassified:
    def test_the_set_is_the_type_constants_less_framing(self):
        members, _ = is_geometry()
        constants = type_constants()
        unknown = members - set(constants)
        assert not unknown, f"IsGeometry names types that are not constants: {sorted(unknown)}"
        expected = set(constants) - NOT_GEOMETRY
        assert members == expected, (
            f"IsGeometry holds {sorted(members)} and the type constants less framing are "
            f"{sorted(expected)}. A new record type has to be added to the set, or to "
            "NOT_GEOMETRY in this file with a reason, so Flattener.Finite() cannot skip it "
            "by accident"
        )

    def test_wire_2_geometry_is_still_exactly_these_eight(self):
        members, _ = is_geometry()
        assert {"Line", "Polyline", "Arc", "Circle", "Ellipse", "Spline", "Polygon", "Text"} <= (
            members
        ), "a wire 2 geometry record fell out of IsGeometry, so its finiteness is unchecked"


class TestTheDocumentSaysTheSameSet:
    def test_no_numeric_range_survives_in_the_finiteness_text(self):
        wire = read(WIRE_MD)
        assert not re.search(r"types? 3 to 10", wire), (
            "WIRE.md still defines the geometry records as a numeric range. Say which "
            "records, so a record 14 is covered by the sentence or visibly outside it"
        )

    def test_the_finiteness_section_names_every_member_of_the_set(self):
        wire = read(WIRE_MD)
        start = wire.index("## Every number in a geometry record is finite")
        section = wire[start : wire.index("\n## ", start + 1)]
        members, _ = is_geometry()
        for name in sorted(members):
            assert f"`{name}`" in section, (
                f"the finiteness section never names `{name}`, which IsGeometry covers"
            )

    def test_the_refusal_row_does_not_use_a_range_either(self):
        wire = read(WIRE_MD)
        table = wire[wire.index("## Refusing a stream") :]
        row = [r for r in table.splitlines() if "NaN" in r and "infinite" in r]
        assert row, "no refusal row about a non-finite f64"
        assert "3 to 10" not in row[0], f"the refusal row still says a range: {row[0]}"
