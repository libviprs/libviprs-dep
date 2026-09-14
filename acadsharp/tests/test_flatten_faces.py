"""SOLID and MESH, the two filled-face kinds that lower to record 9.

Both were refused until now and both are a `Polygon` with no bulge array, so
they share a file the way they share an arm file in the shim.

The assertions here are about ORDER, and that is the whole reason this module
is not three lines checking a record count. DXF stores a SOLID's third and
fourth corners swapped relative to traversal order, so the plausible wrong
implementation emits corners 1, 2, 3, 4 and draws a bow-tie. Over a rectangle
that is invisible: both orders cover a plausible area and every assertion on
an area or a bounding box passes either way. `g13_solid.dwg`'s first solid is
asymmetric on purpose, and `TestTheFixtureCanFail` below is what proves the
assertion could go red, by reordering the same emitted points into the wrong
order and showing the number moves.

A MESH is the same shape of claim from the other side: its faces are
deliberately not coplanar, so an implementation that collapsed the mesh to one
polygon, or that emitted the vertex list as a path, fails on the normals
rather than on a count.
"""

import pytest
from g13_support import kinds, records, vertex_record, warnings

SOLID = "g13_solid.dwg"
MESH = "g13_mesh.dwg"

# The dump prints six decimals, so this is the floor of what any comparison
# here can mean.
TOL = 1e-6


def polygons(fixture):
    """Every Polygon in one dump, as (flags, points, bulges, normal)."""
    out = []
    for r in records(fixture):
        if r["kind"] != "Polygon":
            continue
        pts, bulges, normal = vertex_record(r["rest"])
        out.append((r["flags"], pts, bulges, normal))
    return out


def shoelace(points):
    """Twice the signed area of a closed polygon projected on the XY plane.

    Written out rather than imported, and projected rather than measured in
    the polygon's own plane, because the whole job here is to separate two
    orderings of the same four flat points. A bow-tie's two lobes cancel and a
    simple quad's do not.
    """
    total = 0.0
    n = len(points)
    for i in range(n):
        ax, ay = points[i][0], points[i][1]
        bx, by = points[(i + 1) % n][0], points[(i + 1) % n][1]
        total += ax * by - bx * ay
    return total / 2.0


def close(a, b):
    return all(abs(a[i] - b[i]) <= TOL for i in range(3))


class TestSolidCornerOrder:
    """`Solid` exposes FirstCorner, SecondCorner, ThirdCorner and FourthCorner
    at DXF group codes 10, 11, 12 and 13 verbatim, so the swap is the format's
    and ACadSharp passes it through. The emitted order is 1, 2, 4, 3."""

    def test_the_first_solid_is_emitted_in_1_2_4_3_order(self):
        flags, pts, _bulges, _normal = polygons(SOLID)[0]
        assert flags == 0, "the first Polygon should be the first top-level solid"
        expected = [(0.0, 0.0, 0.0), (10.0, 1.0, 0.0), (11.0, 7.0, 0.0), (2.0, 5.0, 0.0)]
        assert len(pts) == 4, f"the asymmetric quad emitted {len(pts)} vertices"
        for i, (got, want) in enumerate(zip(pts, expected)):
            assert close(got, want), (
                f"vertex {i} is {got} and the 1, 2, 4, 3 order puts {want} there. "
                "1, 2, 3, 4 is the bow-tie this fixture exists to catch."
            )

    def test_it_carries_no_bulge_array(self):
        _flags, _pts, bulges, _normal = polygons(SOLID)[0]
        assert bulges == [], (
            "a solid's edges are straight, so record 9 leaves the array out entirely "
            "rather than carrying a run of zeroes"
        )

    def test_a_fourth_corner_equal_to_the_third_is_a_triangle(self):
        _flags, pts, _bulges, _normal = polygons(SOLID)[1]
        assert len(pts) == 3, (
            f"the three-cornered solid emitted {len(pts)} vertices. The format says a "
            "SOLID with three corners repeats the third as the fourth, and emitting "
            "both puts a coincident vertex on the wire."
        )
        expected = [(20.0, 0.0, 0.0), (26.0, 2.0, 0.0), (22.0, 6.0, 0.0)]
        for got, want in zip(pts, expected):
            assert close(got, want), f"{got} is not {want}"

    def test_a_non_z_extrusion_is_lifted_and_names_its_own_plane(self):
        # Solid is IOrientable and carries its normal at DXF 210, so its
        # corners are object coordinates. The fixture's normal is (1, 2, 2),
        # which is not a unit vector as written, so a producer that forgot to
        # normalise it is visible in the record rather than only in the shape.
        _flags, pts, _bulges, normal = polygons(SOLID)[2]
        assert close(normal, (1.0 / 3.0, 2.0 / 3.0, 2.0 / 3.0)), (
            f"the skew-normal solid names the plane {normal}"
        )
        assert not all(abs(p[2]) <= TOL for p in pts), (
            "every emitted z is zero, so the corners were never lifted out of the "
            "plane the normal defines"
        )

    def test_the_block_copies_cross_and_are_labelled(self):
        # The half a fixture without a block lets through: the kind is
        # implemented, the top-level case works, and instances inside an
        # INSERT are dropped or emitted untransformed.
        found = [p for p in polygons(SOLID) if p[0] == 1]
        assert len(found) == 2, (
            f"{len(found)} solids came out of the two insertions. Bit 0 of flags is "
            "'came from expanding a nested insertion'."
        )

    def test_nothing_still_refuses_a_solid(self):
        named = {w["message"].split(" ")[0] for w in warnings(SOLID)}
        assert "SOLID" not in named, "a SOLID is still reaching the unsupported arm"


class TestTheFixtureCanFail:
    """A probe that cannot distinguish the two answers is not a probe.

    This reorders the record's own points into the order the defect would have
    emitted and shows the two are far apart. Without it, every assertion above
    could be passing on a shape where both orders agree, which is exactly what
    a rectangle is: the third solid in this fixture is `(0,0) (4,0) (0,3)
    (4,3)`, whose bow-tie has signed area exactly 0, so a control written
    there would be measuring a fixed point.
    """

    def test_the_two_orders_of_the_first_solid_are_different_polygons(self):
        _flags, pts, _bulges, _normal = polygons(SOLID)[0]
        assert len(pts) == 4
        simple = shoelace(pts)
        # Back to 1, 2, 3, 4 from the emitted 1, 2, 4, 3.
        bowtie = shoelace([pts[0], pts[1], pts[3], pts[2]])
        assert abs(simple - 50.0) <= 1e-6, (
            f"the emitted order encloses {simple}, and the correct quad is 50. This "
            "test is the control for the order assertions above."
        )
        assert abs(bowtie - 3.5) <= 1e-6, (
            f"the other order encloses {bowtie}, and the bow-tie is 3.5. If these two "
            "numbers were equal this fixture could not tell the orders apart."
        )


class TestMeshFaces:
    """A MESH is an explicit vertex list plus face indices, so there is no
    proprietary format and no evaluator: the faces are in the file."""

    def test_one_polygon_per_face_of_every_mesh(self):
        found = polygons(MESH)
        assert len(found) == 6, (
            f"{len(found)} polygons. The fixture holds one mesh of two faces at top "
            "level and two insertions of a block holding another, which is six."
        )
        for flags, pts, _bulges, _normal in found:
            assert len(pts) == 3, f"a face came out with {len(pts)} vertices"
            assert flags in (0, 1)

    def test_the_top_level_faces_are_the_ones_the_file_holds(self):
        found = [p for p in polygons(MESH) if p[0] == 0]
        assert len(found) == 2
        expected = [
            [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0)],
            [(0.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 4.0)],
        ]
        for f, (_flags, pts, _bulges, _normal) in enumerate(found):
            for i, (got, want) in enumerate(zip(pts, expected[f])):
                assert close(got, want), f"face {f} vertex {i} is {got} and not {want}"

    def test_the_faces_did_not_collapse_onto_one_plane(self):
        # The reason the fixture's faces are deliberately not coplanar. An
        # implementation that emitted the vertex list once, or that took a
        # single normal for the whole mesh, agrees with this file on a flat
        # mesh and not on this one.
        found = [p for p in polygons(MESH) if p[0] == 0]
        first = found[0][3]
        second = found[1][3]
        assert not close(first, second), (
            f"both faces name the plane {first}, and this mesh has two that do not share one"
        )
        assert close(first, (0.0, 0.0, 1.0)), f"the flat face names {first}"

    def test_the_block_copies_cross_and_are_labelled(self):
        found = [p for p in polygons(MESH) if p[0] == 1]
        assert len(found) == 4, f"{len(found)} faces came out of the two insertions"

    def test_the_subdivision_level_is_ignored_out_loud(self):
        found = [w for w in warnings(MESH) if w["code"] == "MESH_SUBDIVISION_IGNORED"]
        assert len(found) == 3, (
            f"{len(found)} meshes said the level was ignored, and the fixture holds "
            "three at subdivision level 2. Emitting the base mesh silently is the "
            "thing this warning exists to stop."
        )
        assert "smoothing" in found[0]["message"], (
            "the message has to say what was not done, not only that something was"
        )

    def test_nothing_still_refuses_a_mesh(self):
        named = {w["message"].split(" ")[0] for w in warnings(MESH)}
        assert "MESH" not in named or not any(
            w["code"] == "UNSUPPORTED_ENTITY" for w in warnings(MESH)
        ), "a MESH is still reaching the unsupported arm"


class TestNeitherFixtureProducesAnythingElse:
    @pytest.mark.parametrize("fixture", (SOLID, MESH))
    def test_only_polygons_and_warnings(self, fixture):
        assert set(kinds(fixture)) <= {"Polygon", "Warning"}, (
            f"{fixture} produced {sorted(kinds(fixture))}, and a filled face is a "
            "Polygon or it is a refusal"
        )
