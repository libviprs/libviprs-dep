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

Two more questions live here because they are questions about a filled face.
`g13_mesh_bad_faces.dwg` is the only input in the corpus that reaches the
flattener's own hostile-input handling, warning 111, and a degenerate SOLID is
a record that is not a shape: both are decisions, so both are asserted rather
than left to whatever the code happens to do.
"""

import pytest
from g13_support import kinds, manifest, records, vertex_record, warnings

SOLID = "g13_solid.dwg"
MESH = "g13_mesh.dwg"
BAD_FACES = "g13_mesh_bad_faces.dwg"
# The degenerate SOLID is here rather than in g13_solid.dwg: this fixture's is
# default constructed, which is the only way to get one out of the generator,
# and it is where flattening SOLID first showed the question up.
DEGENERATE = "g13_unsupported.dwg"

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

    def test_each_block_copy_is_its_own_insertion(self):
        # A count of two is satisfied by the same instance twice, and the two
        # insertions are 3x/1.5 and a mirrored -2x/2 of one asymmetric quad:
        # exactly the pair where a corner-order defect and a winding defect
        # compound, which Corpus.cs says in as many words is why the block
        # member has no symmetry. Counting them proves neither.
        #
        # The block's corners are (0,0), (6,1), (1,4) and (7,5), so 1, 2, 4, 3
        # traces (0,0) (6,1) (7,5) (1,4) and each insertion is that scaled and
        # moved.
        found = [p for p in polygons(SOLID) if p[0] == 1]
        assert len(found) == 2
        expected = [
            [(40.0, 0.0, 0.0), (58.0, 1.5, 0.0), (61.0, 7.5, 0.0), (43.0, 6.0, 0.0)],
            [(80.0, 0.0, 0.0), (68.0, 2.0, 0.0), (66.0, 10.0, 0.0), (78.0, 8.0, 0.0)],
        ]
        for i, (_flags, pts, _bulges, _normal) in enumerate(found):
            assert len(pts) == 4, f"insertion {i} emitted {len(pts)} vertices"
            for j, (got, want) in enumerate(zip(pts, expected[i])):
                assert close(got, want), (
                    f"insertion {i} vertex {j} is {got} and not {want}. The second "
                    "insertion is the mirrored one, so a winding that was not "
                    "reversed and a corner order that was not swapped both land here."
                )

    def test_the_mirrored_copy_is_not_the_other_one(self):
        # The control for the pair above. Two records that happened to be the
        # same instance emitted twice would satisfy a count and a zip of one
        # expectation, and the shoelace of these two has opposite signs.
        found = [p for p in polygons(SOLID) if p[0] == 1]
        first = shoelace(found[0][1])
        second = shoelace(found[1][1])
        assert first * second < 0, (
            f"the two insertions enclose {first} and {second}, and one of them is a "
            "mirror of the other, so their signed areas cannot have the same sign"
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

    def test_the_mirrored_insertion_reverses_the_winding(self):
        # What the mirrored insertion is for, and the assertion Corpus.cs asks
        # for: "a mesh whose faces face the wrong way renders inside out rather
        # than obviously wrong". A count of four says nothing about it, and
        # neither does anything else in this file, because a normal is the only
        # place a reversed traversal shows up in a record.
        #
        # The second insertion is -2x/2, so its faces traverse the other way
        # and the normals measured off the emitted points follow them. The
        # first face is flat and comes out -Z where the unmirrored copy is +Z;
        # the second is not flat, which is why its normal is a number rather
        # than a sign.
        found = [p for p in polygons(MESH) if p[0] == 1]
        assert len(found) == 4
        mirrored = [found[2][3], found[3][3]]
        expected = [(0.0, 0.0, -1.0), (0.192450, 0.192450, -0.962250)]
        for i, (got, want) in enumerate(zip(mirrored, expected)):
            assert close(got, want), (
                f"the mirrored insertion's face {i} names the plane {got} and not "
                f"{want}. A winding that survived the mirror puts the sign back."
            )

    def test_the_two_insertions_do_not_agree_about_which_way_the_faces_face(self):
        # The control. Both halves of the pair above are assertions about one
        # insertion, and a build that emitted the same instance twice would
        # satisfy them by putting the mirrored copy in both slots. The
        # unmirrored insertion is 3x/1.5, a pure scale, so its faces keep the
        # winding the file stores and its flat face is +Z.
        found = [p for p in polygons(MESH) if p[0] == 1]
        assert close(found[0][3], (0.0, 0.0, 1.0)), (
            f"the unmirrored insertion's flat face names {found[0][3]}"
        )
        assert not close(found[0][3], found[2][3]), (
            "both insertions' flat faces name the same plane, so either nothing was "
            "mirrored or the same instance came out twice"
        )

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


class TestAFaceThatIsNotAPolygon:
    """Warning 111, and the only hostile input the flattener's own C# handles.

    Everything else malformed in this corpus is refused by the reader or by a
    bound before the walk sees it. A MESH is different because the file
    controls the face list and the vertex list independently, so the two can
    contradict each other while both parse, and the decision is that one
    entity's worth of bad data drops a face rather than failing the decode.

    `g13_mesh_bad_faces.dwg` holds one MESH of three vertices: a good face
    first, then an index past the end, a face of two vertices, an index below
    zero and a face of none. Both branches of `Unreadable` and both ends of
    each, and the good face is what separates "the rest of the mesh crosses"
    from "the decoder stopped".
    """

    def test_every_bad_face_is_named_with_its_index_and_its_fault(self):
        found = [w for w in warnings(BAD_FACES) if w["code"] == "MESH_FACE_UNREADABLE"]
        assert len(found) == 4, (
            f"{len(found)} faces were called unreadable and the fixture holds four"
        )
        expected = [
            "face 1 of this MESH indexes vertex 99 of a list that holds 3, so it is not emitted",
            "face 2 of this MESH lists 2 vertices and a closed polygon needs three, "
            "so it is not emitted",
            "face 3 of this MESH indexes vertex -1 of a list that holds 3, so it is not emitted",
            "face 4 of this MESH lists 0 vertices and a closed polygon needs three, "
            "so it is not emitted",
        ]
        assert [w["message"] for w in found] == expected

    def test_a_negative_index_is_one_of_them(self):
        # The end a bounds check written as `>= vertices.Count` misses, and it
        # does not degrade to a warning: it is an IndexOutOfRangeException out
        # of the middle of the walk. Asserted on its own because the list above
        # would still pass with three of the four right.
        messages = [w["message"] for w in warnings(BAD_FACES)]
        assert any("vertex -1" in m for m in messages), (
            "nothing reported the negative index, so the lower bound is untested"
        )

    def test_each_one_carries_the_handle_of_the_mesh(self):
        found = [w for w in warnings(BAD_FACES) if w["code"] == "MESH_FACE_UNREADABLE"]
        assert {w["handle"] for w in found} == {"49"}, (
            "a warning about one face of one entity has to name that entity, or the "
            f"drawing's owner cannot find it: {[w['handle'] for w in found]}"
        )

    def test_the_good_face_still_crosses(self):
        found = polygons(BAD_FACES)
        assert len(found) == 1, (
            f"{len(found)} polygons. Four faces are unreadable and one is not, and a "
            "mesh that lost its good face is a decoder that gave up rather than one "
            "that dropped what it could not read."
        )
        _flags, pts, _bulges, normal = found[0]
        expected = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 4.0)]
        for i, (got, want) in enumerate(zip(pts, expected)):
            assert close(got, want), f"vertex {i} is {got} and not {want}"
        assert not close(normal, (0.0, 0.0, 1.0)), (
            f"the good face names {normal}. It is deliberately off the +Z plane, so a "
            "normal that was fabricated rather than measured off the face lands here."
        )

    def test_the_decode_still_succeeds(self):
        # The whole point of a warning rather than a refusal: a file with a
        # contradictory face list is a file with four faces missing, not a
        # failed decode.
        assert manifest()["fixtures"][BAD_FACES]["decode_code"] == "OK"


class TestADegenerateSolidIsStillASolid:
    """Three coincident vertices and a `+Z` normal nothing measured.

    `MeshPolygons` reasons about degenerate faces at length and `SolidPolygon`
    said nothing about the same question, so what a default-constructed SOLID
    does was an accident that nobody had looked at. It is a decision now and
    this is where it is written down: the record goes out, because the corners
    are what the file holds and refusing real geometry is worse than emitting a
    shape with no area.

    A mesh face is the other answer for a reason that is not taste. Its face
    list indexes a vertex list the same file controls, so a face naming vertex
    99 of 3 is data contradicting itself and there is nothing to emit; a
    SOLID's four corners agree with each other and happen to coincide.

    It is not free, and the cost is recorded here too. `g13_unsupported.dwg`
    used to carry EMPTY_VIEW, code 108, which is a branch docs/WIRE.md tells
    consumers to take. Flattening this SOLID put geometry in that view and the
    warning went away.
    """

    def test_it_is_emitted_rather_than_refused(self):
        found = [r for r in records(DEGENERATE) if r["kind"] == "Polygon"]
        assert len(found) == 1, (
            f"{len(found)} polygons came out of a fixture holding one SOLID. A "
            "degenerate SOLID is emitted on purpose: the corners are in the file."
        )

    def test_it_is_three_coincident_vertices(self):
        pts, bulges, _normal = vertex_record(
            [r for r in records(DEGENERATE) if r["kind"] == "Polygon"][0]["rest"]
        )
        assert len(pts) == 3, (
            f"the default-constructed SOLID emitted {len(pts)} vertices. Its fourth "
            "corner equals its third, which is the format's way of saying triangle."
        )
        for i, p in enumerate(pts):
            assert close(p, (0.0, 0.0, 0.0)), f"vertex {i} is {p} and not the origin"
        assert bulges == []

    def test_its_normal_is_a_name_rather_than_a_measurement(self):
        # A shape with no area has no plane, and record 9 has a slot for one
        # anyway. +Z is what goes in it, which is a statement about the format
        # and not about the drawing, and a consumer reading this record's
        # normal as a measured plane is reading something nobody measured.
        _pts, _bulges, normal = vertex_record(
            [r for r in records(DEGENERATE) if r["kind"] == "Polygon"][0]["rest"]
        )
        assert close(normal, (0.0, 0.0, 1.0)), f"the degenerate solid names {normal}"

    def test_the_view_it_occupies_no_longer_reports_itself_empty(self):
        # The cost, asserted rather than described. EMPTY_VIEW is about a view
        # that produced no geometry record at all, and this view produces one,
        # so its absence is correct. It is here so that the day somebody
        # decides a degenerate SOLID should be refused, this goes red beside
        # the tests above rather than quietly coming back.
        assert "EMPTY_VIEW" not in {w["code"] for w in warnings(DEGENERATE)}


class TestNeitherFixtureProducesAnythingElse:
    @pytest.mark.parametrize("fixture", (SOLID, MESH, BAD_FACES))
    def test_only_polygons_and_warnings(self, fixture):
        assert set(kinds(fixture)) <= {"Polygon", "Warning"}, (
            f"{fixture} produced {sorted(kinds(fixture))}, and a filled face is a "
            "Polygon or it is a refusal"
        )
