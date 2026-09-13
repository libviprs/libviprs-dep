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

import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WIRE_MD = os.path.join(os.path.dirname(HERE), "docs", "WIRE.md")

# The algorithm block, as the document lays it out. Both candidate axes and the
# threshold come out of it; nothing about the rule is written here.
ALGORITHM = re.compile(
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
def arbitrary_axis(wire):
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
        band = threshold if band is None else band
        pick = near if abs(n[0]) < band and abs(n[1]) < band else away
        return cross(pick, n)

    return threshold, axis


class TestTheDocumentIsReadable:
    """The positive controls, because every check below parses the document."""

    def test_the_algorithm_parses(self, arbitrary_axis):
        threshold, _ = arbitrary_axis
        assert 0 < threshold < 1, f"the parsed threshold is {threshold}"

    def test_the_warning_table_still_parses(self, wire):
        rows = re.findall(r"^\|\s*(\d+)\s*\|\s*`([A-Z0-9_]+)`\s*\|", wire, re.M)
        assert len(rows) >= 7, f"WIRE.md's warning-code table parsed to {rows}"


class TestRecordFiveSaysWhereZeroIs:
    def test_the_world_xy_case_is_world_x(self, flat, arbitrary_axis):
        # The case almost every drawing is, stated in prose and computed from
        # the block above it.
        assert re.search(r"For a normal of `\(0, 0, 1\)`.*?zero is world `\+X`", flat), (
            "WIRE.md no longer works the common normal through the algorithm, which is "
            "the example that tells a reader they have understood it"
        )
        _, axis = arbitrary_axis
        assert unit(axis((0.0, 0.0, 1.0))) == pytest.approx((1.0, 0.0, 0.0)), (
            "the algorithm WIRE.md states does not put zero along world +X for a normal "
            "of (0, 0, 1), which is what the paragraph beside it claims"
        )

    def test_the_case_nobody_guesses(self, flat, arbitrary_axis):
        m = re.search(r"For a normal of `\(0, 1, 0\)` it is `\((-?\d+), (-?\d+), (-?\d+)\)`", flat)
        assert m, (
            "WIRE.md dropped the worked example for a normal of (0, 1, 0). It is the "
            "one a reader cannot guess and the one that shows the algorithm is not the "
            "identity."
        )
        stated = tuple(float(g) for g in m.groups())
        _, axis = arbitrary_axis
        assert unit(axis((0.0, 1.0, 0.0))) == pytest.approx(stated), (
            f"WIRE.md says a normal of (0, 1, 0) gives {stated}, and the algorithm it "
            "states two paragraphs earlier does not agree"
        )

    def test_a_point_can_be_reconstructed(self, flat):
        assert "cos t · ax + sin t · ay" in flat, (
            "WIRE.md gives the axes and never says what to do with them. A consumer "
            "reconstructs a point on the arc or the angles are decoration."
        )

    def test_the_threshold_is_a_real_number(self, flat, arbitrary_axis):
        threshold, _ = arbitrary_axis
        assert threshold == pytest.approx(1 / 64)
        assert re.search(r"`1/64` is a real number", flat), (
            "WIRE.md states the threshold and not that it is a real number. An "
            "implementation that writes it as an integer division gets zero, which is "
            "the failure the next check demonstrates."
        )

    def test_the_band_is_what_makes_the_common_case_work_at_all(self, arbitrary_axis):
        # The control for the sentence above. With the threshold rounded to
        # zero the first branch never fires, and the world z normal crosses
        # with itself: there is no axis at all, not merely a different one.
        _, axis = arbitrary_axis
        assert norm(axis((0.0, 0.0, 1.0), band=0.0)) == pytest.approx(0.0), (
            "an integer-division threshold no longer breaks the world z normal, so the "
            "warning WIRE.md carries about it is describing nothing"
        )

    def test_just_off_the_axis_is_ninety_degrees_out(self, arbitrary_axis):
        # The other half of why the band exists, and the reason it is a band
        # rather than an equality test: the two branches disagree by a right
        # angle on a normal that is nearly, but not exactly, world z.
        _, axis = arbitrary_axis
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
