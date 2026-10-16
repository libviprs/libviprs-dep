"""MULTILEADER lowers onto records the wire already has (#86).

A MULTILEADER is a composite: leader lines, an optional landing, an arrowhead,
and content that is either text or a block. Everything but the arrowhead is
geometry the file holds, and it was 15 of the 88 refusals on `real_AC1032.dwg`,
second only to POINT. LEADER landed with #110 and proved the shape of the
answer; this is the one in that group that really expands, because block
content is a block and goes through the walk's own stack the way an INSERT does,
under the same depth bound.

What each case in `g13_multileader.dwg` is for is written beside `WriteMultiLeader`.
The structure is asserted as a sequence of record kinds per entity, because the
order is part of what a consumer reads (warnings first, then geometry, the way
LEADER does), and the values are asserted against `wire3_reference`, which
shares no code with the decoder.
"""

import math
import re

import pytest
import wire3_reference as ref
from g13_support import kinds, records

FIXTURE = "g13_multileader.dwg"
REAL = "real_AC1032.dwg"


def stream(fixture):
    """The fixture's records without the reader's own notifications."""
    out = []
    for r in records(fixture):
        if r["kind"] == "Warning" and "code=READER_NOTIFICATION" in r["rest"]:
            continue
        code = re.search(r" code=(\S+)", r["rest"])
        out.append(
            {
                "kind": r["kind"],
                "code": code.group(1) if code else None,
                "flags": r["flags"],
                "rest": r["rest"],
            }
        )
    return out


def signature(items):
    return [f"{i['kind']}:{i['code']}" if i["kind"] == "Warning" else i["kind"] for i in items]


def polyline_points(rest):
    m = re.search(r" pts=\[([^\]]*)\]", rest)
    return [
        tuple(float(x) for x in t)
        for t in re.findall(r"\(([-0-9.]+),([-0-9.]+),([-0-9.]+)\)", m.group(1))
    ]


class TestTheStructure:
    # One entry per MULTILEADER, in the order the fixture writes them.
    TOP = [
        ["Warning:ARROWHEAD_NOT_DRAWN", "Polyline", "Polyline", "Line", "Text"],
        ["Polyline", "Circle", "Line"],
        ["Polyline", "Line"],
        [
            "Warning:ARROWHEAD_NOT_DRAWN",
            "Warning:MULTILEADER_FEATURES_IGNORED",
            "Polyline",
            "Line",
            "Text",
        ],
        ["Warning:UNSUPPORTED_ENTITY"],
    ]
    PLAIN = ["Warning:ARROWHEAD_NOT_DRAWN", "Polyline", "Line", "Text"]

    def test_the_sequence_of_records_per_entity(self):
        got = signature(stream(FIXTURE))
        want = [s for entity in self.TOP for s in entity] + self.PLAIN + self.PLAIN
        assert got == want, f"\n got {got}\nwant {want}"

    def test_nothing_was_refused_as_a_kind(self):
        refused = [
            i
            for i in stream(FIXTURE)
            if i["kind"] == "Warning" and "MULTILEADER is not a primitive" in i["rest"]
        ]
        assert not refused, refused

    def test_only_the_block_half_is_marked_as_expanded(self):
        items = stream(FIXTURE)
        flags = [i["flags"] for i in items]
        # Top level: ML2's block (circle, line) and ML3's nested line are
        # expanded from a block, everything else is at the top. Then both
        # insertions of the block half are expanded from the INSERT.
        assert flags[: len(sum(self.TOP, []))] == [
            0,
            0,
            0,
            0,
            0,
            0,
            1,
            1,
            0,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
        ]
        assert set(flags[len(sum(self.TOP, [])) :]) == {1}


class TestTheValues:
    def test_two_leader_lines_a_landing_and_the_text(self):
        items = stream(FIXTURE)
        lines = [i for i in items[:5] if i["kind"] == "Polyline"]
        assert polyline_points(lines[0]["rest"]) == [
            (0.0, 0.0, 0.0),
            (4.0, 5.0, 0.0),
            (8.0, 6.0, 0.0),
        ]
        assert polyline_points(lines[1]["rest"]) == [(12.0, 0.0, 0.0), (8.0, 6.0, 0.0)]
        assert "closed=0" in lines[0]["rest"]
        landing = items[3]
        assert landing["kind"] == "Line"
        assert polyline_points(landing["rest"].replace("pts=", "pts=")) == [
            (8.0, 6.0, 0.0),
            (10.0, 6.0, 0.0),
        ]
        text = items[4]["rest"]
        assert ref.close(ref.triple(text, "p"), (10.5, 6.0, 0.0))
        assert ref.close(ref.scalar(text, "h"), 2.5)
        assert ref.close(ref.scalar(text, "rot"), 0.25)
        assert 'value="Note ∅ 5"' in text, "the MIF escape is resolved like any other record 10"

    def test_block_content_is_placed_by_its_location_rotation_and_scale(self):
        items = stream(FIXTURE)
        circle = [i for i in items if i["kind"] == "Circle"]
        assert len(circle) == 1
        assert ref.close(ref.triple(circle[0]["rest"], "c"), (110.0, 7.0, 0.0))
        assert ref.close(ref.scalar(circle[0]["rest"], "r"), 2.0), "a uniform scale of 2 doubles it"
        line = [i for i in items if i["kind"] == "Line"][1]
        pts = polyline_points(line["rest"])
        c, s = math.cos(0.5), math.sin(0.5)
        assert ref.close(pts[0], (110.0, 7.0, 0.0))
        assert ref.close(pts[1], (110.0 + 4.0 * c, 7.0 + 4.0 * s, 0.0)), pts

    def test_a_block_inside_the_content_is_reached_through_the_walk(self):
        items = stream(FIXTURE)
        nested = [i for i in items if i["kind"] == "Line" and i["flags"] == 1][1]
        pts = polyline_points(nested["rest"])
        c, s = math.cos(0.5), math.sin(0.5)

        def place(p):
            x, y = 2.0 * p[0], 2.0 * p[1]
            return (110.0 + x * c - y * s, 7.0 + x * s + y * c, 0.0)

        # The inner insertion sits at (1, 1) in the nested block and the line
        # it draws runs 3 up from there.
        assert ref.close(pts[0], place((1.0, 1.0))), pts
        assert ref.close(pts[1], place((1.0, 4.0))), pts

    def test_an_empty_one_says_why_nothing_came_out(self):
        items = stream(FIXTURE)
        empty = [i for i in items if i["code"] == "UNSUPPORTED_ENTITY"]
        assert len(empty) == 1 and 'message="MULTILEADER ' in empty[0]["rest"]
        assert "no leader lines" in empty[0]["rest"]

    @pytest.mark.parametrize(
        "insertion,start", [(ref.SHEARED, 0), (ref.MIRRORED, 1)], ids=["non-uniform", "mirrored"]
    )
    def test_the_block_half_follows_the_insertion(self, insertion, start):
        items = stream(FIXTURE)
        tail = items[-8:]
        part = tail[start * 4 : start * 4 + 4]
        poly = part[1]
        want = [
            insertion.point(p) for p in [(100.0, 0.0, 0.0), (104.0, 5.0, 0.0), (108.0, 6.0, 0.0)]
        ]
        got = polyline_points(poly["rest"])
        for g, w in zip(got, want):
            assert ref.close(g, w), f"{got} against {want}"
        landing = polyline_points(part[2]["rest"])
        assert ref.close(landing[0], insertion.point((108.0, 6.0, 0.0)))
        assert ref.close(landing[1], insertion.point((110.0, 6.0, 0.0)))
        assert ref.close(ref.triple(part[3]["rest"], "p"), insertion.point((110.5, 6.0, 0.0)))


class TestWhatIsNotDrawnIsSaid:
    def test_a_frame_and_a_line_break_are_one_warning_that_names_them(self):
        items = stream(FIXTURE)
        said = [i for i in items if i["code"] == "MULTILEADER_FEATURES_IGNORED"]
        assert len(said) == 1
        assert "frame" in said[0]["rest"] and "break" in said[0]["rest"]

    def test_the_arrowhead_warning_is_the_one_leader_already_uses(self):
        said = [i for i in stream(FIXTURE) if i["code"] == "ARROWHEAD_NOT_DRAWN"]
        assert len(said) == 4, (
            "two MULTILEADERs at the top ask for one, and so does the block half, twice"
        )


class TestTheRealDrawing:
    def test_none_of_the_fifteen_is_refused_as_a_kind(self):
        left = [
            r
            for r in records(REAL)
            if r["kind"] == "Warning" and 'message="MULTILEADER is not' in r["rest"]
        ]
        assert not left, f"{len(left)} MULTILEADERs are still refused on {REAL}"

    def test_something_came_out_for_them(self):
        assert kinds(REAL).get("Polyline", 0) > 31, kinds(REAL)
