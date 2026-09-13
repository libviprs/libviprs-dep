"""No curve is tessellated, asserted where tessellating one would show up.

This is the rule the whole lane exists to hold. Arc, circle, ellipse and spline
cross the boundary carrying the parameters that define them, because libviprs
owns zoom and precision aware tessellation and is the only layer that knows
either. A shim that turned any of them into a polyline would have thrown away
the information its consumer needs, and a polyline of a thousand points does
not tell you it was a circle.

The failing state: tessellate one in the shim and the count assertions below go
red, because the curve record disappears and Polyline records appear in its
place on a fixture that has no polyline in it.
"""

import re

import pytest
from g13_support import (
    CURVE_KINDS,
    arc_midpoint,
    closed_flag,
    distance,
    kinds,
    manifest,
    records,
    vertex_record,
    warnings,
)

MANIFEST = manifest()

# fixture -> the one curve record it is built to produce.
ONE_CURVE_EACH = {
    "g13_arc.dwg": "Arc",
    "g13_circle.dwg": "Circle",
    "g13_ellipse.dwg": "Ellipse",
    "g13_spline.dwg": "Spline",
}


class TestOneCurveFixtureProducesOneCurve:
    @pytest.mark.parametrize("fixture,kind", sorted(ONE_CURVE_EACH.items()))
    def test_exactly_one_record_of_that_type(self, fixture, kind):
        assert kinds(fixture).get(kind, 0) == 1, (
            f"{fixture} is one {kind} and has to decode to exactly one {kind} record"
        )

    @pytest.mark.parametrize("fixture,kind", sorted(ONE_CURVE_EACH.items()))
    def test_no_polyline_anywhere_in_it(self, fixture, kind):
        counts = kinds(fixture)
        assert counts.get("Polyline", 0) == 0, (
            f"{fixture} produced {counts.get('Polyline')} Polyline records. A polyline "
            f"where a {kind} should be is a curve that was tessellated in the shim, "
            "which is the one thing this layer must never do"
        )

    @pytest.mark.parametrize("fixture,kind", sorted(ONE_CURVE_EACH.items()))
    def test_no_other_curve_type_appears(self, fixture, kind):
        counts = kinds(fixture)
        others = [k for k in CURVE_KINDS if k != kind and counts.get(k, 0)]
        assert not others, f"{fixture} also produced {others}"


class TestTheCurveRecordsCarryTheirParameters:
    """A record of the right type with no parameters in it is still a loss."""

    def test_the_arc_carries_a_centre_a_radius_and_two_angles(self):
        line = [r for r in records("g13_arc.dwg") if r["kind"] == "Arc"][0]["rest"]
        assert " r=7.125000" in line
        assert " a0=0.250000" in line and " a1=2.750000" in line
        assert "c=[(3.250000,4.500000,0.000000)]" in line

    def test_the_circle_carries_its_radius(self):
        line = [r for r in records("g13_circle.dwg") if r["kind"] == "Circle"][0]["rest"]
        assert " r=9.875000" in line

    def test_the_ellipse_carries_a_major_axis_a_ratio_and_two_parameters(self):
        line = [r for r in records("g13_ellipse.dwg") if r["kind"] == "Ellipse"][0]["rest"]
        assert "major=[(8.000000,0.000000,0.000000)]" in line
        assert " ratio=0.375000" in line
        assert " p0=0.500000" in line and " p1=4.250000" in line

    def test_the_spline_carries_its_degree_knots_and_control_points(self):
        line = [r for r in records("g13_spline.dwg") if r["kind"] == "Spline"][0]["rest"]
        assert " degree=3" in line
        knots = re.search(r"knots=\[([^\]]*)\]", line).group(1).split(",")
        ctrl = re.search(r"ctrl=\[([^\]]*)\]", line).group(1).split(";")
        assert len(knots) == 8, f"the spline came back with {len(knots)} knots"
        assert len(ctrl) == 4, f"the spline came back with {len(ctrl)} control points"


class TestABulgeCrossesAsABulge:
    """Wire version 2's Polyline has a slot per vertex, so nothing is decomposed.

    Until this landed, a bulged polyline went out as its straight runs plus one
    Arc per bulged span, every record under the source handle. That lost
    closed-ness, lost which instance of a block a record came from, and turned
    an exact number into a centre and two angles that drift apart from the
    vertices either side of them as the bulge gets small.
    """

    def test_the_bulge_is_on_the_record_exactly_as_the_drawing_holds_it(self):
        # The fixture's LWPOLYLINE is (0,0), (10,0) with bulge 0.5, (10,10).
        # A literal, because deriving it from the record would be deriving the
        # expected value from the thing it is checking.
        lw = [r for r in records("g13_polyline.dwg") if r["handle"] == "49"]
        assert len(lw) == 1, f"the polyline came out as {len(lw)} records, not one"
        assert " bulges=[0.000000,0.500000,0.000000]" in lw[0]["rest"]

    def test_the_polyline_fixture_produces_no_arc_at_all(self):
        counts = kinds("g13_polyline.dwg")
        assert counts.get("Arc", 0) == 0, (
            f"a bulged span came out as an Arc record: {counts}. The bulge has a field "
            "of its own now, and decomposing one is the tessellation decision this "
            "layer does not own"
        )
        assert counts.get("Polyline", 0) == 3

    def test_a_polyline_with_no_bulge_carries_no_bulge_array(self):
        # bulges=[] is not the same statement as three zeroes. The array is
        # left out when every span is straight, which keeps a plain polyline
        # the size wire version 1 had it.
        rest = [r for r in records("g13_wide_polyline.dwg") if r["kind"] == "Polyline"][0]["rest"]
        _pts, bulges, _normal = vertex_record(rest)
        assert bulges == []

    def test_leaving_the_array_out_keeps_a_plain_polyline_the_size_it_was(self):
        # 4096 vertices, no bulge anywhere. The number is a literal, read off a
        # decode: emitting the array unconditionally makes it 131,980, which is
        # 32,768 bytes of zeroes for a polyline that is entirely straight.
        assert MANIFEST["fixtures"]["g13_wide_polyline.dwg"]["output_bytes"] == 99212

    def test_the_closing_span_of_a_closed_polyline_survives(self):
        rs = [r for r in records("g13_slot.dwg") if r["kind"] == "Polyline"]
        assert len(rs) == 1, f"the slot came out as {len(rs)} records, not one"
        assert closed_flag(rs[0]["rest"]) == 1, (
            "the slot is a closed polyline and the record says it is open. A consumer "
            "cannot recover that by comparing the first and last point, because an "
            "open polyline that happens to return home looks identical"
        )
        _pts, bulges, _normal = vertex_record(rs[0]["rest"])
        assert bulges == [0.0, 1.0, 0.0, 1.0]

    def test_three_instances_of_one_block_are_three_records(self):
        # This is the defect that ruled the decomposition out on its own.
        # Every instance emits the block entity's own handle, so the twelve
        # records the decomposition produced were twelve records under one
        # handle with no delimiter: grouping by handle merged three slots into
        # one path and grouping by contiguity could not find the seams.
        counts = kinds("g13_slot_block.dwg")
        polylines = counts.get("Polyline", 0)
        assert polylines == 3, f"three insertions of one slot produced {polylines} records"
        assert counts.get("Arc", 0) == 0
        handles = {r["handle"] for r in records("g13_slot_block.dwg") if r["kind"] == "Polyline"}
        assert len(handles) == 1, (
            "the three instances no longer share a handle, so this fixture has stopped "
            "demonstrating the ambiguity it exists for"
        )

    def test_the_midpoint_formula_lands_on_the_arc_the_bulge_names(self):
        # The span is (10,0) to (10,10) with bulge 0.5. Wire version 1 recorded
        # that arc as centre (6.25, 5, 0) with radius 6.25, and those two
        # numbers are literals here: they came out of the decomposition this
        # branch deleted, so they are an independent record of the same arc and
        # not something derived from the bytes under test.
        rest = [r for r in records("g13_polyline.dwg") if r["handle"] == "49"][0]["rest"]
        pts, bulges, normal = vertex_record(rest)
        point = arc_midpoint(pts[1], pts[2], bulges[1], normal)
        assert distance(point, (6.25, 5.0, 0.0)) == pytest.approx(6.25, abs=1e-9), (
            f"the arc midpoint came out at {point}, which is not on the circle the "
            "bulge names. The cross product in the formula is the whole of the sign "
            "convention: taking it the other way round puts the point at (7.5, 5, 0), "
            "1.25 from the centre instead of 6.25"
        )

    def test_a_reflection_negates_every_bulge(self):
        # The block holds the same polyline as g13_polyline.dwg and the
        # insertion has XScale -1, so the arc's midpoint has to be the mirror
        # image of (12.5, 5, 0). Both scale magnitudes are 1, so nothing here
        # is a non-uniform scale and nothing should warn about one.
        rs = [r for r in records("g13_mirrored_bulge.dwg") if r["kind"] == "Polyline"]
        assert len(rs) == 1
        pts, bulges, normal = vertex_record(rs[0]["rest"])
        assert bulges == [0.0, -0.5, 0.0], (
            f"the mirrored polyline carries {bulges}. A reflection flips which side of "
            "the chord the arc bulges to, and the vertices follow the transform while "
            "the bulge does not, so it has to be negated"
        )
        point = arc_midpoint(pts[1], pts[2], bulges[1], normal)
        assert point == pytest.approx((-12.5, 5.0, 0.0), abs=1e-9)

    def test_a_mirror_is_not_a_non_uniform_scale(self):
        codes = [w["code"] for w in warnings("g13_mirrored_bulge.dwg")]
        assert "NON_UNIFORM_BLOCK_SCALE" not in codes, (
            "a pure reflection raised NON_UNIFORM_BLOCK_SCALE, whose message says the "
            "transform does not preserve the shape. It does: both scale magnitudes are "
            "1, and with the bulge negated the record is exact"
        )

    def test_a_genuinely_non_uniform_scale_still_warns(self):
        # The control for the test above. Without it, "a mirror does not warn"
        # could be a warning nothing raises any more.
        codes = [w["code"] for w in warnings("g13_nonuniform.dwg")]
        assert "NON_UNIFORM_BLOCK_SCALE" in codes


class TestBlockExpansionKeepsCurvesAsCurves:
    def test_a_circle_inside_a_block_is_still_a_circle(self):
        counts = kinds("g13_insert.dwg")
        assert counts.get("Circle", 0) == 1
        assert counts.get("Polyline", 0) == 0

    def test_the_expanded_records_are_marked_as_coming_from_a_block(self):
        geometry = [r for r in records("g13_insert.dwg") if r["kind"] != "Warning"]
        assert geometry, "the insert fixture produced no geometry at all"
        assert all(r["flags"] & 1 for r in geometry), (
            "a record lifted out of a block has to say so in its flags, which is what "
            "docs/WIRE.md's geometry prologue is for"
        )

    def test_the_scaled_circle_kept_its_radius_scaled_and_not_its_shape_lost(self):
        # The block holds a circle of radius 1.5 and the insertion scales by 2.
        circle = [r for r in records("g13_insert.dwg") if r["kind"] == "Circle"][0]["rest"]
        assert " r=3.000000" in circle


class TestTheHatchBoundaryIsNotStraightened:
    """A loop of lines and circular arcs is one Polygon, curves and all.

    The fixture holds four hatches: a square, a loop with a counter-clockwise
    arc, the same shape with the arc traversed clockwise, and a loop with a
    spline edge. The first three are polygons now, because record 9 carries a
    bulge per vertex and a bulge is exactly a circular arc. The fourth still
    goes out as its own edges, because a spline is not one.
    """

    def test_three_of_the_four_loops_are_polygons(self):
        counts = kinds("g13_hatch.dwg")
        assert counts.get("Polygon", 0) == 3
        assert counts.get("Arc", 0) == 0, (
            "an arc edge came out as its own Arc record, so the loop it belongs to was "
            "broken up rather than carried"
        )

    def test_the_arc_loop_is_one_polygon_with_a_bulge_and_no_warning(self):
        rs = [r for r in records("g13_hatch.dwg") if r["handle"] == "4A"]
        assert [r["kind"] for r in rs] == ["Polygon"], (
            f"handle 4A produced {[r['kind'] for r in rs]}, not one Polygon and nothing "
            "else. A warning here would mean the loop was refused"
        )
        pts, bulges, normal = vertex_record(rs[0]["rest"])
        assert bulges == [0.0, 1.0, 0.0, 0.0], (
            "the semicircular edge is a half turn counter-clockwise, which is a bulge "
            f"of exactly 1, and the record says {bulges}"
        )
        # The arc bulges away from the rectangle, so its midpoint is outside it.
        point = arc_midpoint(pts[1], pts[2], bulges[1], normal)
        assert point == pytest.approx((35.0, 5.0, 0.0), abs=1e-9)

    def test_the_clockwise_loop_is_the_same_arc_with_the_sign_flipped(self):
        # A boundary arc's direction is a flag, not a sign on the sweep. With
        # only counter-clockwise loops in the corpus, a converter that ignored
        # the flag produced the right answer on every fixture there was.
        rs = [r for r in records("g13_hatch.dwg") if r["handle"] == "4B"]
        assert [r["kind"] for r in rs] == ["Polygon"]
        pts, bulges, normal = vertex_record(rs[0]["rest"])
        assert bulges == [0.0, -1.0, 0.0, 0.0]
        point = arc_midpoint(pts[1], pts[2], bulges[1], normal)
        assert point == pytest.approx((65.0, 5.0, 0.0), abs=1e-9), (
            f"the clockwise arc's midpoint is at {point}. It bulges into the rectangle, "
            "so a converter that dropped the flag puts it at (75, 5), outside"
        )

    def test_a_loop_with_a_spline_still_keeps_the_spline(self):
        counts = kinds("g13_hatch.dwg")
        assert counts.get("Spline", 0) == 1
        codes = [w["code"] for w in warnings("g13_hatch.dwg")]
        assert "HATCH_LOOP_NOT_POLYGON" in codes, (
            "nothing refused the spline loop, so either it is being approximated or the "
            "fixture has stopped carrying one"
        )

    def test_the_real_world_drawing_produces_polygons_and_curves_together(self):
        counts = kinds("real_AC1032.dwg")
        assert counts.get("Polygon", 0) >= 1
        assert counts.get("Arc", 0) >= 1
        assert counts.get("Spline", 0) >= 1


class TestNoFixtureQuietlyTessellated:
    """The corpus-wide version of the rule.

    A per-fixture count can be satisfied by a fixture that never had a curve in
    it. This one asks the opposite question: across every expectation, does any
    curve-bearing entity's handle also carry a Polyline record? That is what a
    tessellating shim would produce, and no fixture in this corpus has an entity
    that legitimately emits both.

    It ran over the four one-curve fixtures until wire version 2, which is four
    files with one entity each, and the shapes it is looking for need a bulged
    polyline to appear at all. The two real drawings carry three of them
    between them, including one in a block inserted twice, and neither was in
    range. Every dumped fixture now is.
    """

    @pytest.mark.parametrize("fixture", sorted(MANIFEST["fixtures"]))
    def test_no_handle_carries_both_a_curve_and_a_polyline(self, fixture):
        by_handle = {}
        for r in records(fixture):
            by_handle.setdefault(r["handle"], set()).add(r["kind"])
        for handle, ks in by_handle.items():
            assert not (ks & set(CURVE_KINDS) and "Polyline" in ks), (
                f"{fixture}: handle {handle} produced both {sorted(ks & set(CURVE_KINDS))} "
                "and a Polyline, which is what a tessellated curve looks like"
            )
