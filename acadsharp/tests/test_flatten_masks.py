"""WIPEOUT, which lands on record 9 and is a mask rather than a face.

A wipeout is an image with no image. What it carries is a frame (an insertion
point and two per-pixel vectors, U and V) and a clip boundary given in the
image's own pixel space, and the whole of what an implementation can get wrong
here is the mapping out of that space:

    wcs(px, py) = insert + u * (px + 0.5) + v * (size_y - py - 0.5)

Pixel rows run the opposite way from V and the pixel origin sits half a pixel
outside the first pixel, so the plausible wrong implementation is the one that
drops the flip and writes ``v * (py + 0.5)``. It produces a boundary with the
right corners, reflected about the middle of the frame, which on a rectangle is
the same rectangle. So every entity in ``g13_wipeout.dwg`` that this file
asserts a shape on is asymmetric about that axis, and ``TestTheFixtureCanFail``
below is what shows the two answers are different points rather than the same
ones in a different order.

The mapping is a convention, and this fixture cannot prove a convention: the
same generator writes the numbers and reads them back, so a round trip agrees
with itself whichever way the flip goes. The coordinates below are pinned to
what ezdxf 1.4.4 answers when it is handed the same frame and the same pixel
boundary, asked both ways: ``boundary_path_wcs()`` on the fixture's own
attributes, and ``add_wipeout()`` on a known world triangle, which came back
with exactly the insert point, vectors, size and pixel boundary the second
entity here is written with. The run is in the pull request that added this.

The other half of this file is that the boundary is said to be a mask.
Record 9 has no slot for "this one covers", so warning 112 carries it beside
the record under the same handle, which is what code 110 already does for a
MESH's subdivision level. A wipeout whose Polygon crossed silently would be
drawn as a filled face by a consumer that had no way to know better, and that
is the thing the code is for.
"""

from g13_support import kinds, records, vertex_record, warnings

WIPEOUT = "g13_wipeout.dwg"

# The dump prints six decimals, so this is the floor of what any comparison
# here can mean.
TOL = 1e-6

# The frame and the pixel-space boundary of each entity in the fixture, copied
# from Corpus.WriteWipeout. They are here so the two candidate mappings can
# both be computed from the same input, which is what makes the control below
# a control rather than a second copy of the answer.
FRAMES = {
    "rectangular": ((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 4.0, 0.0), 1.0),
    "polygonal": ((20.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 6.0, 0.0), 1.0),
    "rotated": ((40.0, 0.0, 0.0), (6.0, 8.0, 0.0), (-4.0, 3.0, 0.0), 1.0),
}
POLYGONAL_PIXELS = [(-0.5, -0.5), (0.5, -0.5), (0.1, 0.5)]
# The two opposite corners a rectangular clip stores, and the four the reader
# of one has to make out of them: (a.x, a.y), (b.x, a.y), (b.x, b.y), (a.x, b.y).
RECT_PIXELS = [(-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)]


def flipped(frame, px, py):
    """The mapping this build ships."""
    insert, u, v, size_y = frame
    return tuple(insert[i] + u[i] * (px + 0.5) + v[i] * (size_y - py - 0.5) for i in range(3))


def unflipped(frame, px, py):
    """The same thing with the row direction dropped, which is the defect."""
    insert, u, v, _size_y = frame
    return tuple(insert[i] + u[i] * (px + 0.5) + v[i] * (py + 0.5) for i in range(3))


def polygons(fixture):
    """Every Polygon in one dump, as (index, handle, flags, points, normal)."""
    out = []
    for i, r in enumerate(records(fixture)):
        if r["kind"] != "Polygon":
            continue
        pts, bulges, normal = vertex_record(r["rest"])
        assert bulges == [], "a mask boundary has straight edges and carries no bulge array"
        out.append((i, r["handle"], r["flags"], pts, normal))
    return out


def masks(fixture):
    """Every POLYGON_MASKS warning, as (index, handle, flags)."""
    out = []
    for i, r in enumerate(records(fixture)):
        if r["kind"] != "Warning":
            continue
        if "code=POLYGON_MASKS" not in r["rest"]:
            continue
        out.append((i, r["handle"], r["flags"]))
    return out


def close(a, b):
    return all(abs(a[i] - b[i]) <= TOL for i in range(3))


def same(got, want, what):
    assert len(got) == len(want), f"{what}: {len(got)} vertices, expected {len(want)}"
    for i, (g, w) in enumerate(zip(got, want)):
        assert close(g, w), f"{what}: vertex {i} is {g} and the mapping puts {w} there"


def shoelace(points):
    """Twice the signed area of a closed polygon projected on the XY plane."""
    total = 0.0
    n = len(points)
    for i in range(n):
        ax, ay = points[i][0], points[i][1]
        bx, by = points[(i + 1) % n][0], points[(i + 1) % n][1]
        total += ax * by - bx * ay
    return total / 2.0


class TestThePixelMapping:
    def test_the_polygonal_mask_lands_where_the_pixel_mapping_puts_it(self):
        # The entity this fixture exists for. Its third vertex is off-centre in
        # both directions, so the two mappings put it in two different places
        # and neither is a permutation of the other.
        _i, _h, flags, pts, _normal = polygons(WIPEOUT)[1]
        assert flags == 0, "the second Polygon should be the second top-level wipeout"
        want = [flipped(FRAMES["polygonal"], px, py) for px, py in POLYGONAL_PIXELS]
        same(pts, want, "the polygonal mask")

    def test_a_rectangular_clip_is_four_corners(self):
        # A rectangular clip stores two opposite corners and means four, in the
        # order (a.x, a.y), (b.x, a.y), (b.x, b.y), (a.x, b.y). An
        # implementation that emitted the two stored corners would put a
        # two-vertex "polygon" on the wire.
        _i, _h, flags, pts, _normal = polygons(WIPEOUT)[0]
        assert flags == 0
        want = [flipped(FRAMES["rectangular"], px, py) for px, py in RECT_PIXELS]
        same(pts, want, "the rectangular mask")

    def test_the_rectangular_mask_covers_the_area_it_should(self):
        # The control for the case above. Four corners in the wrong order is a
        # bow-tie, and the frame is 10 by 4.
        _i, _h, _flags, pts, _normal = polygons(WIPEOUT)[0]
        assert abs(abs(shoelace(pts)) - 40.0) <= TOL, (
            f"the rectangular mask encloses {shoelace(pts)} and the frame is 10 by 4"
        )

    def test_a_rotated_frame_follows_u_and_v(self):
        # U and V are vectors, not a width and a height. This one is neither
        # axis-aligned nor uniform, so an implementation that read the two
        # lengths and drew an upright box is right about the first two entities
        # and wrong here.
        _i, _h, flags, pts, _normal = polygons(WIPEOUT)[2]
        assert flags == 0
        insert, u, v, _size_y = FRAMES["rotated"]
        want = [
            tuple(insert[k] + u[k] * fu + v[k] * fv for k in range(3))
            for fu, fv in ((0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0))
        ]
        same(pts, want, "the rotated mask")

    def test_no_mask_carries_a_bulge_array(self):
        # Asserted inside polygons(), and named here so a reader of this file
        # can see it is asserted at all.
        assert polygons(WIPEOUT), "no Polygon in the dump, so nothing above checked anything"


class TestTheFixtureCanFail:
    """A probe that cannot tell the two mappings apart is not a probe.

    The rectangular entities cannot: a rectangle reflected about the middle of
    its own frame is the same rectangle, so both mappings emit the same four
    points there and only the order moves. That is exactly why the entity the
    mapping case above asserts on is the polygonal one.
    """

    def test_the_two_mappings_disagree_about_the_polygonal_boundary(self):
        mine = [flipped(FRAMES["polygonal"], px, py) for px, py in POLYGONAL_PIXELS]
        theirs = [unflipped(FRAMES["polygonal"], px, py) for px, py in POLYGONAL_PIXELS]
        worst = max(abs(a[k] - b[k]) for a, b in zip(mine, theirs) for k in range(3))
        assert worst >= 1.0, (
            f"the flipped and un-flipped mappings put this boundary within {worst} of "
            "each other, so the assertion above cannot tell them apart"
        )
        assert sorted(mine) != sorted(theirs), (
            "the two mappings emit the same set of points in a different order, so an "
            "assertion on the set would pass either way"
        )

    def test_the_rectangle_is_the_fixed_point_it_looks_like(self):
        # The other half, and the reason the fixture is not four rectangles.
        mine = sorted(flipped(FRAMES["rectangular"], px, py) for px, py in RECT_PIXELS)
        theirs = sorted(unflipped(FRAMES["rectangular"], px, py) for px, py in RECT_PIXELS)
        assert mine == theirs, (
            "the rectangular clip is no longer symmetric about the middle of its frame, "
            "so this control has stopped being one"
        )


class TestTheMaskIsSaidToBeAMask:
    def test_every_mask_polygon_has_a_112_beside_it_with_the_same_handle(self):
        found = polygons(WIPEOUT)
        said = masks(WIPEOUT)
        assert len(said) == len(found), (
            f"{len(found)} Polygon records and {len(said)} POLYGON_MASKS warnings. One "
            "warning per drawing, or none at all, is a boundary a consumer fills like "
            "any other face."
        )
        for (pi, ph, pflags, _pts, _normal), (wi, wh, wflags) in zip(found, said):
            assert wh == ph, f"the warning at record {wi} names handle {wh} and the polygon {ph}"
            assert wflags == pflags, "the warning and the record it is about disagree about flags"
            assert wi == pi - 1, (
                f"the warning is at record {wi} and its polygon at {pi}. The warning comes "
                "immediately before, so a consumer reading forward knows before it draws"
            )

    def test_the_code_says_what_to_do_with_it(self):
        messages = {w["message"] for w in warnings(WIPEOUT) if w["code"] == "POLYGON_MASKS"}
        assert len(messages) == 1, f"{len(messages)} different sentences on one code"
        message = messages.pop()
        assert message.startswith("WIPEOUT"), (
            f"the sentence starts {message.split(' ')[0]!r}, and the corpus tests read "
            "the entity type back by splitting on the first space"
        )
        assert "mask" in message


class TestBlockCopies:
    def test_the_block_copies_cross_and_the_mirror_flips_the_measured_normal(self):
        expanded = [p for p in polygons(WIPEOUT) if p[2] == 1]
        assert len(expanded) == 2, (
            f"{len(expanded)} expanded masks. AddBlockInstances inserts one block twice, "
            "so one wipeout in it is two records"
        )
        (_i0, h0, _f0, pts0, n0), (_i1, h1, _f1, pts1, n1) = expanded
        assert h0 == h1, "both insertions carry the block entity's own handle"
        same(pts0, [(61.0, 0.0, 0.0), (70.0, 6.0, 0.0), (40.0, 7.5, 0.0)], "the upright copy")
        same(pts1, [(66.0, 0.0, 0.0), (60.0, 8.0, 0.0), (80.0, 10.0, 0.0)], "the mirrored copy")
        assert close(n0, (0.0, 0.0, 1.0)), f"the upright copy reports {n0}"
        assert close(n1, (0.0, 0.0, -1.0)), (
            f"the mirrored copy reports {n1}. A wipeout carries no normal of its own, so "
            "record 9's normal is measured off the points as emitted, and a mirror "
            "reverses the traversal and the normal with it. This is the MESH rule, and "
            "it is the opposite of what SOLID does"
        )


class TestWipeoutIsNoLongerRefused:
    def test_nothing_in_this_fixture_refuses_a_wipeout(self):
        refused = [
            w["message"]
            for w in warnings(WIPEOUT)
            if w["code"] in ("UNSUPPORTED_ENTITY", "ENTITY_REFUSED_BY_DESIGN")
        ]
        assert not refused, f"a wipeout is still being refused: {refused}"

    def test_the_fixture_is_geometry_and_warnings_and_nothing_else(self):
        counts = kinds(WIPEOUT)
        assert set(counts) == {"Warning", "Polygon"}, (
            f"{counts}. A wipeout lowers to record 9 and nothing else, and the warnings "
            "are the reader's notifications plus one 112 per boundary"
        )
