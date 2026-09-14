"""Three things `docs/WIRE.md` has to say for a geometry record to be usable.

Handed over from the lane that makes them true, which owns the flattener change
and not this file. Two are drift and one is a hole:

* Warning 106 said "not a similarity", which includes a reflection, and a
  mirror preserves every shape the wire carries exactly.
* `ViewBegin`'s extents are outside the finiteness guarantee, which was read as
  permission to put a `NaN` there. They carry an inverted box instead, and a
  consumer told nothing about it reads `1e20` as an extent.
* Record 5 fixes the sense of its angles and never says where zero is, so an
  arc is not reconstructible from the wire, which is the one thing this
  document exists to make possible.

The angle checks below are the interesting ones. `WIRE.md` now states the
arbitrary axis algorithm as a block of arithmetic and states two worked
examples of it in prose, and these run the first against the second. Neither is
written out here, so editing one and not the other fails rather than passing
quietly, and the threshold gets a control that shows what happens without it.
"""

import math
import os
import re

import pytest
from g13_support import arbitrary_axis, curve_record, records, vertex_record

HERE = os.path.dirname(os.path.abspath(__file__))
WIRE_MD = os.path.join(os.path.dirname(HERE), "docs", "WIRE.md")

# The algorithm block, as the document lays it out. Both candidate axes and the
# threshold come out of it; nothing about the rule is written here.
ALGORITHM = re.compile(
    r"n = normalize\(nx, ny, nz\)\s+"
    r"if \|nx\| < (\S+) and \|ny\| < \1:\s+ax = \(([-\d, ]+)\) × n\s+"
    r"otherwise:\s+ax = \(([-\d, ]+)\) × n",
)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def wire():
    return read(WIRE_MD)


@pytest.fixture(scope="module")
def flat(wire):
    """WIRE.md with its hard wrapping collapsed, so a reflow is not a failure."""
    return re.sub(r"[ \t]*\n[ \t]*", " ", wire)


def vector(text):
    return tuple(float(part) for part in text.split(","))


def cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def norm(v):
    return sum(c * c for c in v) ** 0.5


def unit(v):
    length = norm(v)
    return tuple(c / length for c in v)


@pytest.fixture(scope="module")
def documented_algorithm(wire):
    """The document's own algorithm, as a callable, with its threshold."""
    m = ALGORITHM.search(wire)
    assert m, (
        "WIRE.md no longer states the arbitrary axis algorithm in a form this can "
        "read. Without it an Arc's angles have a direction and no origin, and a "
        "consumer cannot draw the arc at all."
    )
    numerator, denominator = m.group(1).split("/")
    threshold = float(numerator) / float(denominator)
    near, away = vector(m.group(2)), vector(m.group(3))

    def axis(n, band=None):
        # The normalize step is the document's first line, and it is the
        # difference between the two branches for any normal near the band.
        n = unit(n)
        band = threshold if band is None else band
        pick = near if abs(n[0]) < band and abs(n[1]) < band else away
        return cross(pick, n)

    return threshold, axis


@pytest.fixture(scope="module")
def arbitrary_axis_from_doc(documented_algorithm):
    """Just the axis half, for the checks that never touch the threshold."""
    return documented_algorithm[1]


class TestTheDocumentIsReadable:
    """The positive controls, because every check below parses the document."""

    def test_the_algorithm_parses(self, documented_algorithm):
        threshold, _ = documented_algorithm
        assert 0 < threshold < 1, f"the parsed threshold is {threshold}"

    def test_the_warning_table_still_parses(self, wire):
        rows = re.findall(r"^\|\s*(\d+)\s*\|\s*`([A-Z0-9_]+)`\s*\|", wire, re.M)
        assert len(rows) >= 7, f"WIRE.md's warning-code table parsed to {rows}"


class TestRecordFiveSaysWhereZeroIs:
    def test_the_world_xy_case_is_world_x(self, flat, documented_algorithm):
        # The case almost every drawing is, stated in prose and computed from
        # the block above it.
        assert re.search(r"For a normal of `\(0, 0, 1\)`.*?zero is world `\+X`", flat), (
            "WIRE.md no longer works the common normal through the algorithm, which is "
            "the example that tells a reader they have understood it"
        )
        _, axis = documented_algorithm
        assert unit(axis((0.0, 0.0, 1.0))) == pytest.approx((1.0, 0.0, 0.0)), (
            "the algorithm WIRE.md states does not put zero along world +X for a normal "
            "of (0, 0, 1), which is what the paragraph beside it claims"
        )

    def test_the_case_nobody_guesses(self, flat, documented_algorithm):
        m = re.search(r"For a normal of `\(0, 1, 0\)` it is `\((-?\d+), (-?\d+), (-?\d+)\)`", flat)
        assert m, (
            "WIRE.md dropped the worked example for a normal of (0, 1, 0). It is the "
            "one a reader cannot guess and the one that shows the algorithm is not the "
            "identity."
        )
        stated = tuple(float(g) for g in m.groups())
        _, axis = documented_algorithm
        assert unit(axis((0.0, 1.0, 0.0))) == pytest.approx(stated), (
            f"WIRE.md says a normal of (0, 1, 0) gives {stated}, and the algorithm it "
            "states two paragraphs earlier does not agree"
        )

    def test_a_point_can_be_reconstructed(self, flat):
        assert "cos t · ax + sin t · ay" in flat, (
            "WIRE.md gives the axes and never says what to do with them. A consumer "
            "reconstructs a point on the arc or the angles are decoration."
        )

    def test_the_threshold_is_a_real_number(self, flat, documented_algorithm):
        threshold, _ = documented_algorithm
        assert threshold == pytest.approx(1 / 64)
        assert re.search(r"`1/64` is a real number", flat), (
            "WIRE.md states the threshold and not that it is a real number. An "
            "implementation that writes it as an integer division gets zero, which is "
            "the failure the next check demonstrates."
        )

    def test_the_band_is_what_makes_the_common_case_work_at_all(self, documented_algorithm):
        # The control for the sentence above. With the threshold rounded to
        # zero the first branch never fires, and the world z normal crosses
        # with itself: there is no axis at all, not merely a different one.
        _, axis = documented_algorithm
        assert norm(axis((0.0, 0.0, 1.0), band=0.0)) == pytest.approx(0.0), (
            "an integer-division threshold no longer breaks the world z normal, so the "
            "warning WIRE.md carries about it is describing nothing"
        )

    def test_just_off_the_axis_is_ninety_degrees_out(self, documented_algorithm):
        # The other half of why the band exists, and the reason it is a band
        # rather than an equality test: the two branches disagree by a right
        # angle on a normal that is nearly, but not exactly, world z.
        _, axis = documented_algorithm
        n = unit((1e-3, 0.0, 1.0))
        inside = unit(axis(n))
        outside = unit(axis(n, band=0.0))
        dot = sum(a * b for a, b in zip(inside, outside))
        assert abs(dot) == pytest.approx(0.0, abs=1e-6), (
            f"the two branches agree to within a dot product of {dot} on a normal just "
            "off world z, so the band is not buying what WIRE.md says it buys"
        )


class TestTheExtentsCarryASentinel:
    def test_they_are_never_non_finite(self, flat):
        assert re.search(r"`ViewBegin`'s extents are never `NaN` and never infinite", flat), (
            "WIRE.md scopes the finiteness guarantee to geometry and stops there, which "
            "reads as permission to put a NaN in the extents. A bounding box that is NaN "
            "in every direction is a renderer that draws nothing and says why to nobody."
        )

    def test_the_sentinel_is_stated_in_both_directions(self, flat):
        assert "`1e20`" in flat and "`-1e20`" in flat, (
            "WIRE.md names no sentinel, so a consumer reads 1e20 as an extent and frames "
            "a view twenty orders of magnitude too big"
        )

    def test_the_comparison_a_consumer_makes_is_given(self, flat):
        assert "`min_x > max_x`" in flat, (
            "WIRE.md gives the sentinel values and not the test. An inverted box is only "
            "useful because the comparison is one a consumer can make without knowing "
            "the constants."
        )

    def test_it_says_the_box_does_not_say_why(self, flat):
        assert re.search(r"The box does not say why", flat), (
            "WIRE.md presents the sentinel without saying an empty view and a damaged "
            "one are indistinguishable through it, which is the thing a consumer would "
            "otherwise assume it could tell"
        )


@pytest.fixture(scope="module")
def row(wire):
    """Warning 106's row, on its own."""
    m = re.search(r"^\|\s*106\s*\|\s*`NON_UNIFORM_BLOCK_SCALE`\s*\|(.*)\|$", wire, re.M)
    assert m, "WIRE.md's table no longer carries a row for 106"
    return m.group(1)


class TestWarningOneOhSixDescribesWhatRaisesIt:
    def test_it_no_longer_says_similarity(self, row):
        assert "similarity" not in row, (
            "row 106 still describes what raises it as a transform that is not a "
            "similarity. That includes a reflection, and a mirror preserves an Arc, an "
            "Ellipse and a bulge exactly: the records follow it. A warning that fires on "
            "an exact transform teaches a consumer to ignore the warning."
        )

    def test_it_says_what_does_raise_it(self, row):
        assert re.search(r"does not scale .*? uniformly", row), (
            f"row 106 does not say what raises it. It reads: {row.strip()!r}"
        )

    def test_it_rules_a_reflection_out_by_name(self, row):
        assert "reflection" in row and "mirror" in row, (
            "row 106 leaves a reader to work out whether a mirror counts. It is the case "
            "the wording used to get wrong, so it is the case worth naming."
        )


# --------------------------------------------------- a normal that is not +Z
#
# Until #71 landed there was nothing in the corpus to point this at. For a
# normal of (0, 0, 1) the arbitrary axis algorithm is the identity, so a
# document that stated it wrongly and a document that stated it correctly
# produced the same stream on every fixture there was, and the two worked
# examples above were checked against each other and against nothing else.
#
# `g13_ocs_rotated.dwg` is an insertion whose extrusion is +Y, so every record
# in it carries a normal of (0, 1, 0): the second branch, and the example the
# document says nobody guesses.

ROTATED = "g13_ocs_rotated.dwg"
OFF_AXIS_NORMAL = (0.0, 1.0, 0.0)


@pytest.fixture(scope="module")
def arc():
    return curve_record(only(ROTATED, "Arc")["rest"])


@pytest.fixture(scope="module")
def polyline():
    return vertex_record(only(ROTATED, "Polyline")["rest"])


def only(fixture, kind):
    found = [r for r in records(fixture) if r["kind"] == kind]
    assert len(found) == 1, f"{fixture} carries {len(found)} {kind} records, expected one"
    return found[0]


def scale(v, k):
    return tuple(c * k for c in v)


def minus(a, b):
    return tuple(x - y for x, y in zip(a, b))


class TestTheAlgorithmAgreesWithTheOneTheTestsUse:
    """Two implementations written from the same specification and not from
    each other. `g13_support.arbitrary_axis` came from the lane that made the
    flattener run it; this one is parsed out of the document a consumer reads.
    Agreeing across the branch boundary is the check."""

    @pytest.mark.parametrize(
        "normal",
        (
            (0.0, 0.0, 1.0),
            (0.0, 0.0, -1.0),
            (0.0, 1.0, 0.0),
            (1.0, 0.0, 0.0),
            (1.0, 2.0, 2.0),
            # Either side of the 1/64 band, which is the only place the two
            # branches can disagree and the only place a threshold matters.
            (1.0 / 64.0 - 1e-9, 0.0, 1.0),
            (1.0 / 64.0 + 1e-9, 0.0, 1.0),
        ),
    )
    def test_the_document_and_the_support_module_pick_the_same_axis(
        self, arbitrary_axis_from_doc, normal
    ):
        mine = unit(arbitrary_axis_from_doc(normal))
        theirs = arbitrary_axis(normal)
        assert mine == pytest.approx(theirs), (
            f"for a normal of {normal} the algorithm WIRE.md states picks {mine} and "
            f"the one the tests run picks {theirs}. One of them is what the shim does "
            "and the other is what a consumer would build, so a consumer drawing this "
            "arc puts it somewhere the drawing does not."
        )


class TestAFixtureActuallyExercisesTheSecondBranch:
    """The document's convention, run against a recorded stream whose normal is
    not `+Z`, with the polyline in the same view as the witness.

    The arc's angles and the polyline's vertices are independent: one is read
    through the plane frame and the other is three points the flattener lifted,
    with no angle anywhere in it. So the polyline fixes the two axes the record
    is measured in without using the document at all, and the arc has to land
    on them.
    """

    def test_the_normal_is_not_the_identity_case(self, arc):
        # The positive control, and the whole reason this fixture is the one.
        assert arc["normal"] == pytest.approx(OFF_AXIS_NORMAL), (
            f"the Arc in {ROTATED} carries a normal of {arc['normal']}, so it no longer "
            "exercises the branch the algorithm exists for. For (0, 0, 1) the algorithm "
            "is the identity and this whole class passes over nothing."
        )

    def test_the_fixture_still_has_the_shape_this_reads(self, arc, polyline):
        # Everything below is arithmetic between these two records, so a
        # fixture that changed shape has to fail here rather than quietly
        # start asserting something else.
        points, _, normal = polyline
        assert normal == pytest.approx(OFF_AXIS_NORMAL)
        assert len(points) == 3
        assert arc["c"] == pytest.approx(points[0]), (
            "the arc's centre and the polyline's first vertex are both the block's "
            "origin, which is what lets one measure the other"
        )
        assert arc["a0"] == pytest.approx(0.0)
        assert arc["a1"] == pytest.approx(math.pi / 2.0)
        for span in (minus(points[1], points[0]), minus(points[2], points[1])):
            assert norm(span) == pytest.approx(2.0 * arc["r"]), (
                "each span of the polyline is twice the arc's radius, which is what "
                "makes the comparison below a whole number rather than a tolerance"
            )

    def test_the_angle_origin_is_where_the_polyline_says_it_is(
        self, arc, polyline, arbitrary_axis_from_doc
    ):
        # Angle zero, reconstructed with WIRE.md's own formula, against the
        # direction the polyline's first span runs. The polyline knows nothing
        # about angles: its vertices are the block's own x axis and y axis
        # lifted, so if the document named the wrong axis this is where it
        # shows.
        points, _, _ = polyline
        ax = unit(arbitrary_axis_from_doc(arc["normal"]))
        start = tuple(c + arc["r"] * a for c, a in zip(arc["c"], ax))
        assert start == pytest.approx(scale(minus(points[1], points[0]), 0.5)), (
            f"the document puts angle zero at {start} and the polyline's first span "
            "runs the other way. An arc drawn from this document would be reflected "
            "or rotated out of the plane the drawing put it in."
        )

    def test_a_quarter_turn_lands_on_the_second_span(self, arc, polyline, arbitrary_axis_from_doc):
        # And the other axis, which is the one the sense of the angles fixes.
        # Getting ay backwards is a mirrored arc, which is the failure that
        # survives every check that only looks at where zero is.
        points, _, _ = polyline
        ax = unit(arbitrary_axis_from_doc(arc["normal"]))
        ay = cross(unit(arc["normal"]), ax)
        end = tuple(c + arc["r"] * a for c, a in zip(arc["c"], ay))
        assert end == pytest.approx(scale(minus(points[2], points[1]), 0.5)), (
            f"a quarter turn counter-clockwise lands at {end}, and the polyline turns "
            "the other way. The angles would run backwards for every record whose "
            "normal is not +Z."
        )


# ----------------------------------------------------- one section, one name
#
# `WIRE.md` carried two sections called "Geometry that needs a lookup", and
# they contradicted each other. The `###` one said the external-resource kinds
# emit `ENTITY_REFUSED_BY_DESIGN`; the `##` one said, in bold as its whole
# point, `UNSUPPORTED_ENTITY`. Both were right for the lane that wrote them,
# neither lane could see the other, and being in different places is exactly
# why both merged clean.
#
# The bolded rule also named four examples and got three of them wrong by this
# document's own warning table. So the merged section carries the disposition
# rule as a table a reader can check, and the two classes below hold the two
# halves: the name is unique, and the codes are all three still there.

LOOKUP_HEADING = "Geometry that needs a lookup"

# One disposition per code, each with an exemplar the old bolded sentence
# filed under the wrong one.
DISPOSITIONS = (
    ("105", "UNRESOLVED_BLOCK", "external reference"),
    ("109", "ENTITY_REFUSED_BY_DESIGN", "SHX"),
    ("100", "UNSUPPORTED_ENTITY", "MLINE"),
)


def headings(text):
    """Every ATX heading, as (level, title), with fenced code skipped."""
    out = []
    fenced = False
    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        m = re.match(r"^(#+)\s+(\S.*?)\s*$", line)
        if m:
            out.append((len(m.group(1)), m.group(2)))
    return out


@pytest.fixture(scope="module")
def lookup_section(wire):
    """The one `## Geometry that needs a lookup`, down to the next `##`."""
    start = wire.index(f"\n## {LOOKUP_HEADING}\n")
    rest = wire[start + 1 :]
    end = rest.index("\n## ", 1)
    return rest[:end]


class TestEveryHeadingIsItsOwn:
    """A repeated heading is a defect even once the contradiction is gone.

    Two sections with one name cannot be cited: "see Geometry that needs a
    lookup" picks out neither. They also collide on anchor, because every
    generator that makes an id out of the text emits
    `#geometry-that-needs-a-lookup` twice and one of the two links silently
    goes to the wrong section. This document ships inside every archive and
    claims to be sufficient on its own, so a reader has nothing else to check
    it against.
    """

    def test_the_reader_finds_the_headings_at_all(self, wire):
        found = headings(wire)
        assert len(found) > 10, f"WIRE.md parsed to {found}, which is not its outline"
        assert (1, "The VACB batch protocol, wire version 2") in found

    def test_no_two_headings_carry_the_same_text(self, wire):
        seen = {}
        for level, title in headings(wire):
            seen.setdefault(title, []).append(level)
        repeated = {t: lv for t, lv in seen.items() if len(lv) > 1}
        assert not repeated, (
            f"WIRE.md has more than one heading called each of {sorted(repeated)}, at "
            f"levels {repeated}. Two sections with one name cannot be cited, they "
            "collide on anchor in anything that generates ids from the text, and the "
            "last pair of them said opposite things about the same warning code for a "
            "whole campaign because neither lane could see the other."
        )


class TestTheLookupSectionSaysWhichCode:
    """The contradiction, closed by making the answer a table.

    "It emits a warning and no geometry record" is the part both versions
    agreed on. Which warning is the part they did not, and it is the part a
    consumer branches on: 105 means go and find the missing piece, 109 means
    stop waiting, 100 means wait.
    """

    def test_the_bolded_rule_names_no_single_code(self, lookup_section):
        bold = re.findall(r"\*\*(.+?)\*\*", lookup_section, re.S)
        assert bold, "the lookup section no longer states its rule in bold"
        rule = bold[0]
        named = [code for _n, code, _e in DISPOSITIONS if code in rule]
        assert not named, (
            f"the lookup rule is stated in bold as emitting {named}, which is the shape "
            "that made two sections of this document contradict each other. The rule is "
            "that a warning is emitted and no geometry record; which warning is three "
            "answers and belongs in the table under it."
        )

    @pytest.mark.parametrize("number,code,exemplar", DISPOSITIONS)
    def test_each_disposition_is_in_the_section(self, lookup_section, number, code, exemplar):
        row = [ln for ln in lookup_section.splitlines() if ln.startswith("|") and code in ln]
        assert len(row) == 1, (
            f"the lookup section carries {len(row)} disposition rows mentioning {code}. "
            "Each of the three codes gets exactly one, or a reader deciding what to do "
            "with a warning has to guess which row is theirs."
        )
        assert number in row[0], (
            f"{code}'s row in the lookup section does not carry the number {number}, "
            f"and the number is what a consumer branches on. It reads: {row[0]!r}"
        )
        assert exemplar in lookup_section, (
            f"the lookup section no longer mentions {exemplar!r}, which is one of the "
            f"cases the old single-code rule filed under the wrong one of these three"
        )

    def test_the_three_codes_are_the_ones_the_table_defines(self, wire):
        # The control: the section is only useful if the codes it hands out
        # are codes this document defines, with those numbers.
        documented = {
            m.group(2): m.group(1)
            for m in re.finditer(r"^\|\s*(\d+)\s*\|\s*`([A-Z0-9_]+)`\s*\|", wire, re.M)
        }
        for number, code, _exemplar in DISPOSITIONS:
            assert documented.get(code) == number, (
                f"the lookup section sends a consumer to {code} as {number} and the "
                f"warning-code table says {documented.get(code)}"
            )
