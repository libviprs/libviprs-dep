"""A view's extents are four numbers, or they are the box that says "none".

``docs/WIRE.md`` promises that no record of type 3 to 10 carries an ``f64``
that is ``NaN`` or infinite, and ``Flattener.Finite`` enforces it.
``ViewBegin`` is record 2 and is deliberately outside that range: its extents
are a bounding box the source reports rather than a shape anybody draws, and a
view holding nothing has no finite one, so promising a number there would mean
inventing one.

That is defensible and it is also a hole. A layout's extents are four doubles
a file holds, so a drawing can hand the shim a ``NaN`` and it crosses. A
consumer that reads ``min_x`` as ``NaN`` gets exactly the failure the guarantee
exists to prevent: one ``NaN`` in a bounding box makes every comparison against
it false, so the box is ``NaN`` in every direction by the time anything has
used it, and there is no value it could have compared against to find out.

The fix is the option the issue calls a documented sentinel, picked because it
is the only one of the three that needs no change to the wire format, the
header or the frozen record layout. Extents that are not four finite numbers
are reported as the inverted box: ``min`` at ``+1e20`` and ``max`` at
``-1e20``, which is the pair AutoCAD itself writes into ``EXTMIN`` and
``EXTMAX`` for a drawing with nothing in it. A consumer asks ``min_x > max_x``
and gets "this view has no usable extents" from a comparison that works,
rather than from one that cannot.

What this does not do is separate "this view is empty" from "this drawing is
damaged": both report the same box. Telling those apart wants a warning code
of its own, and a warning code wants a row in ``docs/WIRE.md``.

The failing state, recorded before the fix: ``g13_bad_extents.dwg`` reported
``NaN,-Infinity,Infinity,NaN`` for both of its views, straight out of the file.
"""

import math

import pytest
from g13_support import kinds, manifest

MANIFEST = manifest()
FIXTURE_NAMES = sorted(MANIFEST["fixtures"])

BAD = "g13_bad_extents.dwg"

# The inverted box, which is the statement "there are no extents here".
ABSENT_MIN = 1e20
ABSENT_MAX = -1e20


def extents(fixture):
    """Every view's extents for a fixture, as tuples of four floats.

    The capture holds them as strings because JSON has no spelling for a
    ``NaN`` or an infinity, which is the whole reason this file can see one.
    """
    recorded = MANIFEST["fixtures"][fixture].get("view_extents")
    assert recorded is not None, (
        f"{fixture} has no view_extents in MANIFEST.json, so nothing here is "
        "checking it. Rerun acadsharp/tests/fixtures/gen/regenerate.py"
    )
    return [tuple(float(x) for x in row.split(",")) for row in recorded]


class TestTheCaptureCarriesExtentsAtAll:
    """The positive control. Every assertion below walks the recorded extents,
    and a walk over nothing agrees with everything."""

    def test_every_dumped_fixture_reports_its_views(self):
        empty = [f for f in FIXTURE_NAMES if not extents(f)]
        assert not empty, f"{empty} recorded no view extents at all"

    def test_the_reader_can_see_a_value_that_is_not_finite(self):
        # The control for the control: float("nan") is what the parser above
        # produces from the capture, and a reader that quietly turned one into
        # a zero would make the whole module pass over the defect.
        assert math.isnan(float("NaN"))
        assert math.isinf(float("Infinity")) and math.isinf(float("-Infinity"))


class TestNoViewReportsANumberThatIsNotOne:
    @pytest.mark.parametrize("fixture", FIXTURE_NAMES)
    def test_every_recorded_extent_is_finite(self, fixture):
        for index, box in enumerate(extents(fixture)):
            for name, value in zip(("min_x", "min_y", "max_x", "max_y"), box):
                assert math.isfinite(value), (
                    f"{fixture} view {index} reports {name} as {value}. A consumer "
                    "that compares against it gets false whichever way the "
                    "comparison is written, and the record gives it no way to find "
                    "out why"
                )


class TestExtentsThatAreNotNumbersBecomeTheAbsentBox:
    """g13_bad_extents.dwg holds a NaN and both infinities in one view."""

    def test_the_fixture_still_carries_all_four_shapes(self):
        # The fixture asks for NaN, -Infinity, +Infinity and NaN. If a future
        # DwgWriter started refusing one of those, this module would go on
        # passing while covering less, so the count is pinned here rather than
        # assumed.
        assert len(extents(BAD)) >= 1

    @pytest.mark.parametrize("index", (0, 1))
    def test_the_view_says_it_has_no_extents(self, index):
        box = extents(BAD)[index]
        assert box == (ABSENT_MIN, ABSENT_MIN, ABSENT_MAX, ABSENT_MAX), (
            f"view {index} of {BAD} reports {box}, which is neither four usable "
            "numbers nor the inverted box that says there are none"
        )

    def test_a_consumer_can_tell_with_one_comparison(self):
        for box in extents(BAD):
            assert box[0] > box[2] and box[1] > box[3], (
                "min is not past max, so `min_x > max_x` does not say "
                "'no extents' and a consumer has nothing cheap to test"
            )

    def test_the_drawing_itself_still_crosses(self):
        # The bounding box is metadata. A view whose extents are unusable is
        # not a decode that failed, and the entity in it has to still arrive.
        assert MANIFEST["fixtures"][BAD]["decode_code"] == "OK"
        assert kinds(BAD).get("Line", 0) == 1


class TestAViewWithRealExtentsIsStillDistinguishable:
    """The control beside the one above.

    Without it, "the absent box means absent" could be satisfied by a shim
    that reported the absent box for every view in every drawing.
    """

    @pytest.mark.parametrize("fixture", [f for f in FIXTURE_NAMES if f != BAD])
    def test_min_is_not_past_max(self, fixture):
        for index, box in enumerate(extents(fixture)):
            assert box[0] <= box[2] and box[1] <= box[3], (
                f"{fixture} view {index} reports {box}, which reads as the absent "
                "box on a drawing whose extents are perfectly good"
            )

    def test_at_least_one_fixture_reports_a_box_with_area(self):
        wide = [
            f
            for f in FIXTURE_NAMES
            for box in extents(f)
            if box[2] > box[0] and box[3] > box[1]
        ]
        assert wide, (
            "no fixture in the corpus reports extents with any area, so the "
            "comparison above is being satisfied by degenerate boxes alone"
        )
