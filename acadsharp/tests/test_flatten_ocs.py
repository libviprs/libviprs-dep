"""Object coordinates, reflections, and the plane a record says it is in.

A DWG entity does not store world coordinates. It stores coordinates in the
object coordinate system its extrusion direction defines, and DXF's arbitrary
axis algorithm is what turns one into the other. Every fixture in this corpus
until ``g13_ocs_*`` has an extrusion of +Z, where that algorithm is exactly the
identity, so a shim that never ran it produced the right answer on every file
there was.

Three things follow from running it, and they are three faces of one bug:

* a point in a plane that is not the world's XY plane lands somewhere else,
* the normal a record carries is the plane the record's angles and bulges are
  measured in, so it has to follow the transform rather than be copied off the
  entity,
* and once the normal follows a reflection, an ``Arc``'s angles and an
  ``Ellipse``'s parameters have to follow it too, because both are
  counter-clockwise about that normal and a mirror reverses what
  counter-clockwise means.

Every probe below is chosen for what the defect moves. A point on the mirror's
own axis, an arc that starts where its reflection starts, a normal of +Z under
a transform that keeps the XY plane: all of those are fixed points, and a
fixture built out of them passes whether the code is right or wrong.
"""

import math

import pytest
from g13_support import (
    arbitrary_axis,
    arc_midpoint,
    arc_point,
    curve_record,
    distance,
    dot,
    ellipse_point,
    negate_x,
    normalize,
    records,
    vertex_record,
    warnings,
)

PLANE = "g13_ocs_plane.dwg"
MIRROR = "g13_ocs_mirror.dwg"
ROTATED = "g13_ocs_rotated.dwg"
SKEW = "g13_ocs_skew.dwg"

# The extrusion g13_ocs_plane.dwg is built on, and the one a real drawing
# produces constantly: mirroring an entity in AutoCAD flips the extrusion
# rather than the geometry.
FLIPPED = (0.0, 0.0, -1.0)

# The oblique one, as the fixture stores it, which is not a unit vector.
OBLIQUE = (1.0, 2.0, 2.0)
OBLIQUE_ELEVATION = 7.0

# A coordinate read straight off a dump.
TOL = 1e-6

# A point reconstructed through an angle. The dump prints six decimals, so an
# angle on it is only good to 5e-7, and the radius multiplies that error into
# the position: at r = 5 the reconstruction is a couple of parts in a million
# out however right the shim is. Tightening this back to TOL makes the arc
# assertions fail on the printing rather than on the geometry.
ANGLE_TOL = 1e-5


def geometry(fixture):
    return [r for r in records(fixture) if r["kind"] != "Warning"]


def of_kind(fixture, kind):
    return [r for r in geometry(fixture) if r["kind"] == kind]


def approx(point):
    return pytest.approx(point, abs=TOL)


def swept(point):
    return pytest.approx(point, abs=ANGLE_TOL)


def flat(points):
    """A sequence of triples as one list of numbers.

    ``pytest.approx`` does not reach inside a list of tuples: it compares each
    tuple with plain ``==``, so ``[(1.0, 2.0)] == approx([(1.0, 2.0000001)])``
    is False however wide the tolerance is. A nested comparison is therefore
    an exact one wearing approx's clothes, and it passes for as long as the
    numbers happen to round to the same six decimals. Flattening first is what
    makes the tolerance real, and it is why every multi-point comparison here
    goes through this.
    """
    return [x for p in points for x in p]


class TestTheHelperIsTheAlgorithm:
    """The positive control for everything below.

    Every assertion in this module that names a point goes through
    ``arbitrary_axis``. If that function were wrong in the same direction the
    shim is wrong, the two would agree and this file would pass over a
    defect, so the algorithm is pinned here against cases worked out by hand
    from its definition rather than from any code.
    """

    def test_the_world_z_axis_is_the_identity_case(self):
        assert arbitrary_axis((0, 0, 1)) == approx((1.0, 0.0, 0.0))

    def test_a_flipped_extrusion_turns_the_plane_over(self):
        # N = -Z is inside the 1/64 band, so the x axis is Wy x N, which is
        # (0,1,0) x (0,0,-1) = (-1, 0, 0).
        assert arbitrary_axis(FLIPPED) == approx((-1.0, 0.0, 0.0))

    def test_an_axis_normal_outside_the_band_uses_the_z_cross(self):
        # N = +Y has |Ny| = 1, so the else branch: Wz x N = (0,0,1) x (0,1,0)
        # = (-1, 0, 0).
        assert arbitrary_axis((0, 1, 0)) == approx((-1.0, 0.0, 0.0))

    def test_the_sixty_fourth_is_load_bearing(self):
        """A normal just inside the band and one just outside it.

        The threshold is the whole reason the algorithm has two branches, and
        a helper that dropped it would answer these two the same way. C# will
        also write `1 / 64` as integer division and get zero, which is the
        shape this probe catches: with the band gone, both of these take the
        z branch and the first comes out 90 degrees from where it belongs.
        """
        inside = arbitrary_axis((1.0 / 128.0, 0.0, 1.0))
        outside = arbitrary_axis((1.0 / 32.0, 0.0, 1.0))
        assert inside == approx(normalize((1.0, 0.0, -1.0 / 128.0)))
        assert outside == approx((0.0, 1.0, 0.0))
        assert distance(inside, outside) > 1.0, (
            "the two branches produced almost the same axis, so this probe cannot "
            "tell an implementation that keeps the 1/64 band from one that drops it"
        )


class TestAnEntitysPlaneIsLiftedIntoWorldSpace:
    """g13_ocs_plane.dwg: five entities, no block, extrusions that are not +Z.

    The failing state, recorded before the fix: every centre came out as the
    coordinates the file holds, so the arc sat at (4, 3, 0) instead of
    (-4, 3, 0) and the oblique circle at (5, 0, 7) instead of on its own
    plane.
    """

    def test_the_arc_centre_is_lifted(self):
        arc = curve_record(of_kind(PLANE, "Arc")[0]["rest"])
        # (4, 3, 0) in the entity's plane. The arbitrary axis for -Z is
        # x = (-1,0,0), y = (0,1,0), so x negates and y does not: the 4 is the
        # probe and the 3 is a fixed point that would pass either way.
        assert arc["c"] == approx((-4.0, 3.0, 0.0))
        assert arc["r"] == pytest.approx(2.0)

    def test_the_circles_elevation_is_lifted_too(self):
        # (6, -2, 1): a non-zero OCS z, which is the third column of the lift.
        # A shim that lifted only x and y would leave the 1 alone.
        circle = curve_record(of_kind(PLANE, "Circle")[0]["rest"])
        assert circle["c"] == approx((-6.0, -2.0, -1.0))

    def test_the_polyline_vertices_and_its_elevation_are_lifted(self):
        lw = [r for r in of_kind(PLANE, "Polyline") if r["handle"] == "4B"]
        assert len(lw) == 1, f"the lwpolyline came out as {len(lw)} records"
        pts, bulges, normal = vertex_record(lw[0]["rest"])
        assert flat(pts) == approx(
            flat([(0.0, 0.0, -2.0), (-10.0, 0.0, -2.0), (-10.0, 10.0, -2.0)])
        )
        assert normal == approx(FLIPPED)
        # Nothing here is a reflection: the placement is the identity and the
        # plane is the entity's own, so the bulge crosses exactly as read.
        assert bulges == approx([0.0, 0.5, 0.0])

    def test_the_bulged_span_lands_on_the_arc_the_drawing_has(self):
        # In its own plane the span (10,0) to (10,10) with bulge 0.5 has its
        # midpoint at (12.5, 5), which is g13_polyline.dwg's pinned number.
        # Lifted through the -Z frame that is (-12.5, 5, -2).
        lw = [r for r in of_kind(PLANE, "Polyline") if r["handle"] == "4B"][0]
        pts, bulges, normal = vertex_record(lw["rest"])
        point = arc_midpoint(pts[1], pts[2], bulges[1], normal)
        assert point == approx((-12.5, 5.0, -2.0))

    def test_the_oblique_centre_lands_on_the_entitys_own_plane(self):
        """The probe that does not depend on which x axis the algorithm picks.

        A point at OCS z = k lifts onto { p : p . N = k } whatever frame is
        chosen inside that plane, so this pins the lift without agreeing with
        the producer about anything else. Unlifted, the dot product is 19/3.
        """
        circle = curve_record(of_kind(PLANE, "Circle")[1]["rest"])
        n = normalize(OBLIQUE)
        assert dot(circle["c"], n) == pytest.approx(OBLIQUE_ELEVATION, abs=TOL)

    def test_the_lift_is_a_rotation_so_it_keeps_the_length(self):
        # The control for the test above, and deliberately a weak one: the
        # length of (5, 0, 7) is the same before and after, so this passes
        # whether the lift ran or not. It is here to say that the dot product
        # moving is not the centre being scaled or translated.
        circle = curve_record(of_kind(PLANE, "Circle")[1]["rest"])
        assert distance(circle["c"], (0.0, 0.0, 0.0)) == pytest.approx(
            (5.0**2 + OBLIQUE_ELEVATION**2) ** 0.5, abs=TOL
        )

    def test_the_normal_crosses_as_a_unit_vector(self):
        # The file stores (1, 2, 2), which is a legal extrusion and is not a
        # unit vector. docs/WIRE.md's midpoint formula takes the normal as
        # unit, so a consumer that trusts the record and does not renormalise
        # gets a bulge three times too big.
        circle = curve_record(of_kind(PLANE, "Circle")[1]["rest"])
        assert distance(circle["normal"], (0.0, 0.0, 0.0)) == pytest.approx(1.0, abs=TOL)
        assert circle["normal"] == approx(normalize(OBLIQUE))

    def test_a_two_dimensional_polyline_is_lifted_as_well(self):
        """The other half of the arm a 3D polyline goes through.

        An LWPOLYLINE and a POLYLINE reach the flattener through different
        cases, so the assertion above says nothing about this one. Without
        this record the lift on the shared arm is never run at all and the
        3D control below is a comparison against a branch that does nothing.

        The z is 0 rather than the elevation the fixture sets, because
        upstream's DwgWriter does not round-trip a 2D polyline's elevation.
        The x negating is what says the lift ran.
        """
        p2 = [r for r in of_kind(PLANE, "Polyline") if r["handle"] == "4D"]
        assert len(p2) == 1
        pts, bulges, normal = vertex_record(p2[0]["rest"])
        assert flat(pts) == approx(flat([(-20.0, 0.0, 0.0), (-30.0, 5.0, 0.0), (-40.0, 0.0, 0.0)]))
        assert bulges == []
        assert normal == approx(FLIPPED)

    def test_a_three_dimensional_polyline_is_not_lifted(self):
        """The control that keeps the fix from being "lift every polyline".

        POLYLINE's 3D flag is exactly the flag that says its vertices are
        world coordinates, and the shared arm takes its points from the plain
        placement for that reason.

        What this cannot show is the branch failing. The fixture asks for an
        extrusion of -Z here and the record comes back with +Z, because a DWG
        has nowhere to put a 3D polyline's extrusion and upstream reads it as
        the default. So the wrong behaviour, lifting by the entity's own
        normal, is the identity on any 3D polyline this format can hold, and
        the branch is a statement about DXF rather than something this corpus
        can watch fail. It is still what the specification says, and the 2D
        record above is what proves the other side of it runs.
        """
        p3 = [r for r in of_kind(PLANE, "Polyline") if r["handle"] == "52"]
        assert len(p3) == 1
        pts, bulges, normal = vertex_record(p3[0]["rest"])
        assert flat(pts) == approx(flat([(1.0, 2.0, 3.0), (4.0, 5.0, 6.0), (7.0, 8.0, 9.0)]))
        assert bulges == []
        assert normal == approx((0.0, 0.0, 1.0))


class TestAnArcUnderAMirroredInsertion:
    """g13_ocs_mirror.dwg: one block, inserted once as itself and once at
    XScale -1, so the mirrored records are the unmirrored ones with x negated
    and nothing else.

    The expected values are therefore the fixture's own, not numbers typed
    into this file: whatever the control says the curve is, the mirror of it
    is what the second insertion has to produce.

    The failing state, recorded before the fix: the arc came out at angles
    pi and 3*pi/2, which is the same centre and radius sweeping the other
    side of the circle, and the ellipse's two parameters crossed unchanged.
    """

    def curves(self, kind):
        rs = of_kind(MIRROR, kind)
        assert len(rs) == 2, f"{MIRROR} has {len(rs)} {kind} records, not two"
        return curve_record(rs[0]["rest"]), curve_record(rs[1]["rest"])

    def test_the_circle_is_the_plain_mirror_image(self):
        # The control inside the control. A circle has no angles, so nothing
        # in it follows a reflection and nothing needed to: it was right
        # before the fix and has to still be right after.
        plain, mirrored = self.curves("Circle")
        assert mirrored["c"] == approx(negate_x(plain["c"]))
        assert mirrored["r"] == pytest.approx(plain["r"])
        assert mirrored["normal"] == approx(plain["normal"])

    def test_the_arc_sweeps_the_mirror_of_what_the_block_holds(self):
        plain, mirrored = self.curves("Arc")
        assert mirrored["c"] == approx(negate_x(plain["c"]))
        assert mirrored["r"] == pytest.approx(plain["r"])

        # Three points along each arc, at the two ends and the middle. A
        # reflection reverses which end is the start, so the comparison is
        # between the two sets and not between start and start.
        def along(rec):
            a0, a1 = rec["a0"], rec["a1"]
            return [arc_point(rec, a0 + (a1 - a0) * t) for t in (0.0, 0.5, 1.0)]

        want = sorted(negate_x(p) for p in along(plain))
        got = sorted(along(mirrored))
        assert flat(got) == swept(flat(want))

    def test_the_arcs_midpoint_is_the_probe_and_its_start_is_not(self):
        """Named separately because the endpoints alone cannot fail.

        The block's arc is a quarter turn from (14, 0) to (10, 4) about
        (10, 0). Reflected that is (-14, 0) to (-10, 4), and the wrong answer
        the shim produced sweeps from (-14, 0) to (-10, -4): the point at
        (-14, 0) is on the mirror's own axis and comes out identical either
        way. The midpoint is at 45 degrees, off both axes, and it is what
        moves.
        """
        plain, mirrored = self.curves("Arc")
        mid = arc_point(plain, (plain["a0"] + plain["a1"]) / 2.0)
        want = negate_x(mid)
        got = arc_point(mirrored, (mirrored["a0"] + mirrored["a1"]) / 2.0)
        assert got == swept(want)
        assert abs(want[1]) > 1.0, (
            "the midpoint this test probes has come to sit on the mirror axis, so "
            "it is now a fixed point and cannot tell a correct arc from its "
            "reflection. The fixture's arc has stopped being a quarter turn"
        )

    def test_the_ellipse_sweeps_the_mirror_of_what_the_block_holds(self):
        plain, mirrored = self.curves("Ellipse")
        assert mirrored["c"] == approx(negate_x(plain["c"]))
        assert mirrored["major"] == approx(negate_x(plain["major"]))
        assert mirrored["ratio"] == pytest.approx(plain["ratio"])

        def along(rec):
            p0, p1 = rec["p0"], rec["p1"]
            return [ellipse_point(rec, p0 + (p1 - p0) * t) for t in (0.0, 0.5, 1.0)]

        want = sorted(negate_x(p) for p in along(plain))
        got = sorted(along(mirrored))
        assert flat(got) == swept(flat(want))

    def test_a_mirror_raises_no_warning_about_the_scale(self):
        # Both scale magnitudes are 1. A reflection preserves every shape
        # exactly; what it does not preserve is handedness, and with the
        # angles following it the record is exact.
        codes = [w["code"] for w in warnings(MIRROR)]
        assert "NON_UNIFORM_BLOCK_SCALE" not in codes, (
            f"a pure reflection raised {codes}. The warning says the transform does "
            "not preserve the shape, and a mirror does"
        )


class TestARecordsNormalFollowsTheTransform:
    """g13_ocs_rotated.dwg: an insertion whose own extrusion is +Y, which
    takes the block's XY plane to the world's XZ plane.

    The failing state, recorded before the fix: every record claimed a normal
    of (0, 0, 1) while its points sat in a plane at right angles to that, so
    the arc was drawn flat and the polyline's bulge was measured about a
    normal parallel to its own chord.
    """

    EXTRUSION = (0.0, 1.0, 0.0)

    def test_the_circle_is_in_the_plane_the_insertion_puts_it_in(self):
        circle = curve_record(of_kind(ROTATED, "Circle")[0]["rest"])
        assert circle["normal"] == approx(self.EXTRUSION)
        assert circle["c"] == approx((-3.0, 0.0, 0.0))
        assert circle["r"] == pytest.approx(2.0)

    def test_the_arc_is_in_that_plane_and_sweeps_the_right_quarter(self):
        arc = curve_record(of_kind(ROTATED, "Arc")[0]["rest"])
        assert arc["normal"] == approx(self.EXTRUSION)
        # The block's arc runs (5,0,0) to (0,5,0) about the origin. The
        # insertion sends x to -x and y to z, so those become (-5,0,0) and
        # (0,0,5) with the midpoint at (-3.5355, 0, 3.5355).
        assert arc_point(arc, arc["a0"]) == swept((-5.0, 0.0, 0.0))
        assert arc_point(arc, arc["a1"]) == swept((0.0, 0.0, 5.0))
        half = 5.0 * math.cos(math.pi / 4.0)
        assert arc_point(arc, (arc["a0"] + arc["a1"]) / 2.0) == swept((-half, 0.0, half))

    def test_the_bulge_is_measured_about_the_plane_the_polyline_is_in(self):
        rs = of_kind(ROTATED, "Polyline")
        assert len(rs) == 1
        pts, bulges, normal = vertex_record(rs[0]["rest"])
        assert normal == approx(self.EXTRUSION)
        assert flat(pts) == approx(flat([(0.0, 0.0, 0.0), (-10.0, 0.0, 0.0), (-10.0, 0.0, 10.0)]))
        # (12.5, 5, 0) in the block, which the insertion sends to
        # (-12.5, 0, 5). With the normal left at +Z the cross product in the
        # midpoint formula is parallel to the chord and collapses to zero, so
        # the point comes back as the chord's own midpoint, 2.5 away.
        point = arc_midpoint(pts[1], pts[2], bulges[1], normal)
        assert point == approx((-12.5, 0.0, 5.0))


class TestASkewIsNotAUniformScale:
    """g13_ocs_skew.dwg: a uniform scale composed with a rotation is not one.

    The outer insertion scales x by three and the inner one is turned 45
    degrees inside it. The two basis vectors come out the same length, sqrt(5)
    each, and stop being at right angles: the dot product between them is -4.
    A circle under that is an ellipse.

    The failing state, recorded before the fix: the gate compared the two
    lengths and nothing else, called the transform uniform, and emitted a
    circle of radius 8.944272 in the XY plane with no warning at all. That is
    the direction that does damage, because a consumer is told the parameters
    describe the shape.
    """

    def test_the_squashed_circle_is_warned_about(self):
        squashed = [w for w in warnings(SKEW) if w["code"] == "NON_UNIFORM_BLOCK_SCALE"]
        assert squashed, (
            f"{SKEW} produced no NON_UNIFORM_BLOCK_SCALE. Its transform turns a "
            "circle into an ellipse with axes 3 and 1/3 of the radius"
        )
        assert any("CIRCLE" in w["message"] for w in squashed)

    def test_the_squashed_bulge_is_warned_about_too(self):
        # A bulge under this transform is an elliptical arc for the same
        # reason, and the polyline arm has its own gate.
        squashed = [w for w in warnings(SKEW) if w["code"] == "NON_UNIFORM_BLOCK_SCALE"]
        assert any("POLYLINE" in w["message"] for w in squashed), (
            f"only {[w['message'] for w in squashed]} was raised, and the fixture "
            "carries a bulged polyline under the same transform"
        )

    def test_the_controls_beside_it(self):
        # Without these, "the skew warns" could be a warning everything
        # raises. A plain non-uniform scale still warns and a mirror does not.
        assert "NON_UNIFORM_BLOCK_SCALE" in [w["code"] for w in warnings("g13_nonuniform.dwg")]
        assert "NON_UNIFORM_BLOCK_SCALE" not in [w["code"] for w in warnings(MIRROR)]


class TestTheWarningSaysSomethingTrue:
    """Item 3 of the issue, which is a sentence rather than a number.

    The message read "crossed under a block transform that is not a
    similarity, so its parameters describe a shape the transform does not
    preserve". A reflection is not a similarity and preserves every shape
    exactly, so on the one input that wording was most likely to be read it
    was false.
    """

    def test_it_no_longer_turns_on_the_word_similarity(self):
        for w in warnings(SKEW) + warnings("g13_nonuniform.dwg"):
            if w["code"] != "NON_UNIFORM_BLOCK_SCALE":
                continue
            assert "similarity" not in w["message"], (
                f"the message still reads {w['message']!r}. A reflection is not a "
                "similarity either, and it is exact"
            )

    def test_it_names_what_is_actually_wrong(self):
        for w in warnings(SKEW) + warnings("g13_nonuniform.dwg"):
            if w["code"] != "NON_UNIFORM_BLOCK_SCALE":
                continue
            assert "uniform" in w["message"], (
                f"the message reads {w['message']!r}, which does not say that the "
                "in-plane scale is what is not uniform"
            )
