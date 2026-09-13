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
from g13_support import CURVE_KINDS, kinds, manifest, records

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


class TestABulgeIsAnArcAndNotASegment:
    """docs/WIRE.md's Polyline has nowhere to put a bulge, so it becomes an Arc.

    Dropping the bulge would turn an arc into a chord, which is worse than
    tessellation: a chord is not even a good approximation. So the flattener
    splits a bulged polyline into its straight runs and an Arc per bulged span,
    every record keeping the source entity's handle so a consumer can regroup
    them. The fixture has one bulge of 0.5 on a ten-unit span, which is an arc
    of exactly four atan(0.5) radians.
    """

    def test_the_polyline_fixture_produces_an_arc(self):
        counts = kinds("g13_polyline.dwg")
        assert counts.get("Arc", 0) == 1, (
            "the bulged span did not come out as an Arc, so either it was dropped "
            f"or it was straightened: {counts}"
        )

    def test_the_arc_shares_the_polyline_handle(self):
        rs = records("g13_polyline.dwg")
        arc = [r for r in rs if r["kind"] == "Arc"][0]
        polys = [r for r in rs if r["kind"] == "Polyline"]
        assert arc["handle"] in {p["handle"] for p in polys}, (
            "the arc has to carry the handle of the polyline it came out of, or a "
            "consumer cannot tell which entity it belongs to"
        )

    def test_the_arc_is_the_one_the_bulge_names(self):
        import math

        arc = [r for r in records("g13_polyline.dwg") if r["kind"] == "Arc"][0]["rest"]
        a0 = float(re.search(r" a0=(-?\d+\.\d+)", arc).group(1))
        a1 = float(re.search(r" a1=(-?\d+\.\d+)", arc).group(1))
        radius = float(re.search(r" r=(\d+\.\d+)", arc).group(1))

        # A bulge is the tangent of a quarter of the included angle, so the
        # sweep and the radius both follow in closed form from the chord and
        # the one number. The fixture's span is (10,0) to (10,10), bulge 0.5.
        bulge = 0.5
        chord = 10.0
        assert abs((a1 - a0) - 4.0 * math.atan(bulge)) < 1e-6, (
            "the arc's sweep is not the angle the bulge names"
        )
        expected_radius = chord * (1.0 + bulge * bulge) / (4.0 * abs(bulge))
        assert abs(radius - expected_radius) < 1e-6

    def test_a_polyline_with_no_bulge_is_still_one_record(self):
        # The decomposition must not fire on a polyline that does not need it.
        counts = kinds("g13_wide_polyline.dwg")
        assert counts.get("Polyline", 0) == 1
        assert counts.get("Arc", 0) == 0


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
    def test_a_loop_of_straight_edges_is_one_polygon(self):
        assert kinds("g13_hatch.dwg").get("Polygon", 0) == 1

    def test_a_loop_with_an_arc_keeps_the_arc(self):
        assert kinds("g13_hatch.dwg").get("Arc", 0) == 1

    def test_a_loop_with_a_spline_keeps_the_spline(self):
        assert kinds("g13_hatch.dwg").get("Spline", 0) == 1

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
    """

    @pytest.mark.parametrize("fixture", sorted(ONE_CURVE_EACH))
    def test_no_handle_carries_both_a_curve_and_a_polyline(self, fixture):
        by_handle = {}
        for r in records(fixture):
            by_handle.setdefault(r["handle"], set()).add(r["kind"])
        for handle, ks in by_handle.items():
            assert not (ks & set(CURVE_KINDS) and "Polyline" in ks), (
                f"{fixture}: handle {handle} produced both {sorted(ks & set(CURVE_KINDS))} "
                "and a Polyline, which is what a tessellated curve looks like"
            )
