"""TOLERANCE, refused on a fixture that carries nothing else. (POINT left this file.)

POINT stopped being refused when wire version 3 added record 14 `Point`, and the
assertions that were written to go red on exactly that are gone with it. What
replaced them is `test_point_records.py`, which says what the same fixture
holds now. TOLERANCE stays here, for the reason the docstring below gives.

Both DWGs have been in `tests/fixtures` since #94 and neither was ever dumped,
so until now the only evidence that either kind is refused came from the two
real drawings, where the refusal sits among a hundred other records and cannot
say which entity produced which sentence. `regenerate.py`'s `DUMPED` is the
upstream control for that: a fixture missing from it can never reach
`MANIFEST.json` however often anybody regenerates, so the file was carried and
not checked, which is what `CARRIED_NOT_RECORDED` in `test_adapter_stream.py`
existed to say out loud.

Recording them was not implementing them. POINT stayed on code 100 until a
record could hold a marker, and TOLERANCE is refused on purpose (109, since
#86) because its frame is computed from a formatted string rather than read out
of the file. So what the
TOLERANCE assertions pin is a refusal, and they are written to go red the day
it lowers to a record, which is the day somebody should be reading this file
anyway.

The counts are split by `flags` on purpose. Each fixture puts the kind at top
level and again inside a block that is inserted twice, so a flattener that
stopped walking into an expanded insertion would keep every top-level refusal
and lose the rest, and a total alone would not say which half went missing.
"""

import re

import pytest
from g13_support import kinds, records
from test_adapter_stream import CARRIED_NOT_RECORDED, DUMPED, ONCE_RECORDED

TOLERANCE = "g13_tolerance.dwg"
FIXTURES = (TOLERANCE,)

# The two codes an entity refusal can carry. A READER_NOTIFICATION is the
# backing reader talking about the document with item_handle 0, and is not one.
REFUSAL_CODES = ("UNSUPPORTED_ENTITY", "ENTITY_REFUSED_BY_DESIGN")

# What `Corpus.WriteTolerance` puts in the file, as
# (DXF kind, top-level entities, entities reached by expanding an insertion).
# `AddBlockInstances` adds one block and inserts it twice, so a block member
# is refused once per insertion and not once per member.
WRITTEN = {
    TOLERANCE: ("TOLERANCE", 2, 2),
}

WARNING_RE = re.compile(r' code=(\S+) message="((?:[^"\\]|\\.)*)"')


def entity_warnings(fixture):
    """Every refusal in one dump, with the kind its sentence names and its flags.

    The kind comes off the first word of the message because that is the
    corpus convention `docs/WIRE.md`'s row for code 100 states and
    `test_refusal_decisions.py` leans on: the sentence names the source
    format's type first, so a reader that never parses the rest of it can
    still say which kind was refused.
    """
    out = []
    for r in records(fixture):
        if r["kind"] != "Warning":
            continue
        m = WARNING_RE.search(r["rest"])
        assert m, f"{fixture}: a Warning record with no code and message: {r['rest']!r}"
        code, message = m.group(1), m.group(2)
        if code not in REFUSAL_CODES:
            continue
        out.append(
            {
                "code": code,
                "kind": message.split(" ")[0],
                "flags": r["flags"],
                "handle": r["handle"],
                "message": message,
            }
        )
    return out


class TestTheEvidenceIsThere:
    """The positive control, and it is not decoration.

    Every assertion below is of the shape "this dump holds n refusals and no
    geometry". A dump this reader could not find, or one that parsed to
    nothing, holds no geometry either and would satisfy half of that for the
    wrong reason. So the first thing each fixture is asked is whether there is
    anything in it at all.
    """

    @pytest.mark.parametrize("fixture", FIXTURES)
    def test_the_dump_has_a_record_for_everything_written(self, fixture):
        _, top, expanded = WRITTEN[fixture]
        assert len(records(fixture)) >= top + expanded, (
            f"{fixture}'s dump parsed to {len(records(fixture))} records and the "
            f"writer put {top + expanded} entities in the drawing, so the counts "
            "below would be comparing a claim against a file this reader cannot read"
        )


class TestTheViewSaysItHoldsNothing:
    """The other witness in this dump, and the one nothing else provides.

    Every entity in the fixture is refused, so the model space this drawing
    describes comes out of the adapter with no geometry in it at all,
    and the decoder says so with `EMPTY_VIEW` rather than emitting a view that
    looks like a successful decode of a blank drawing. `g13_empty_view.dwg`
    carries that code on a document that genuinely has nothing in it; this one
    carries it on a document full of entities the flattener would not take,
    which is the harder case and the one a consumer actually meets.

    It is also the sharpest thing here to go red the day the kind lands: a
    fixture that emits one record stops being an empty view.
    """

    @pytest.mark.parametrize("fixture", FIXTURES)
    def test_the_only_view_in_it_is_empty(self, fixture):
        empty = [w for w in records(fixture) if "code=EMPTY_VIEW" in w["rest"]]
        assert len(empty) == 1, (
            f"{fixture} carries {len(empty)} EMPTY_VIEW warnings. Every entity in it "
            "is refused, so its one view holds no geometry and the decoder says so "
            "once"
        )
        assert empty[0]["handle"] == "0", (
            "EMPTY_VIEW is about the view rather than about an entity, so it carries "
            f"item_handle 0 and this one carries {empty[0]['handle']}"
        )


class TestToleranceIsStillRefused:
    """TOLERANCE, refused because its geometry is computed, not read.

    Its geometry is a feature-control frame whose boxes are computed from a
    formatted string and a dimension style's text height, not read out of the
    file, so lowering it means implementing a text layout engine and then
    agreeing with AutoCAD's. That is a promise this boundary is not in a
    position to make, and a wire 3 does not change it.
    """

    def test_tolerance_is_still_refused_on_its_own_fixture(self):
        kind, top, expanded = WRITTEN[TOLERANCE]
        refused = entity_warnings(TOLERANCE)
        assert [w["kind"] for w in refused] == [kind] * (top + expanded), (
            f"{TOLERANCE} holds {[w['kind'] for w in refused]} and the writer puts "
            f"{top + expanded} {kind}s in it"
        )
        assert {w["code"] for w in refused} == {"ENTITY_REFUSED_BY_DESIGN"}, (
            "TOLERANCE is refused on 109 since #86: its frame is computed from a "
            "formatted string and a text height, not read out of the file, and a "
            "promise to agree with AutoCAD's layout is not one this boundary makes. "
            "That is a decision, which is what the row in docs/adr/0002 records, and "
            "not work nobody has got to, which is what 100 says"
        )

    def test_both_halves_of_the_fixture_are_refused(self):
        kind, top, expanded = WRITTEN[TOLERANCE]
        refused = entity_warnings(TOLERANCE)
        counts = (
            sum(1 for w in refused if w["flags"] == 0),
            sum(1 for w in refused if w["flags"] == 1),
        )
        assert counts == (top, expanded), (
            f"{TOLERANCE} carries {top} top-level {kind}s and {expanded} reached by "
            f"expanding its two insertions, and the dump splits {counts[0]} and "
            f"{counts[1]}"
        )

    def test_nothing_in_the_fixture_became_geometry(self):
        assert set(kinds(TOLERANCE)) == {"Warning"}, (
            f"{TOLERANCE} now emits {sorted(set(kinds(TOLERANCE)))}, so TOLERANCE has "
            "stopped being refused"
        )


class TestTheCarriedListIsEmpty:
    """The allow-list POINT and TOLERANCE were the last names on.

    `CARRIED_NOT_RECORDED` is an excuse, and an excuse that never shrinks is a
    list of things nobody checks. It was five names when the campaign started
    and the other three came off in the branches that recorded them, which is
    the discipline it was written for. POINT and TOLERANCE were the last of it.

    It stays in the tree as an empty tuple rather than going away, because the
    guard that reads it is the one that catches the next DWG landing without a
    `DUMPED` line, and an empty list is the state that guard is meant to be in.
    """

    def test_the_two_fixtures_left_the_carried_list(self):
        assert CARRIED_NOT_RECORDED == (), (
            "CARRIED_NOT_RECORDED still excuses "
            f"{list(CARRIED_NOT_RECORDED)} from being checked by anything"
        )

    @pytest.mark.parametrize("fixture", FIXTURES)
    def test_each_one_is_dumped_and_on_the_ratchet(self, fixture):
        assert fixture in DUMPED, (
            f"{fixture} is not in regenerate.py's DUMPED, so it gets no dump and no "
            "MANIFEST.json entry and the assertions above read a file that is not there"
        )
        assert fixture in ONCE_RECORDED, (
            f"{fixture} is dumped and is not on ONCE_RECORDED, so taking its dump away "
            "again later would be a green diff"
        )
