"""MLINE, which is a path plus a style and lowers to one Polyline per element.

The whole of this file is about the thing that is NOT in the entity. An MLINE
carries a centre path and a handle to an MLINESTYLE, and the lines a drawing
actually shows sit at the offsets that style holds, scaled by the entity's own
scale factor and measured along each vertex's miter. Emitting the path alone
would be a `Polyline` record with a plausible point count and real coordinates
that no consumer could tell from a real polyline, which is the defect
libviprs-dep#87 exists to prevent, so every assertion below is written to go
red against exactly that.

Three plausible wrong implementations and where each one shows up:

* the bare path as one polyline: the counts are wrong everywhere, and
  `TestZeroJustification` asserts three records rather than one.
* the offsets applied without `ScaleFactor`: every y is 1.0 where it should be
  2.5, which is what `test_the_scale_factor_is_in_the_offset` is for.
* the offset taken along the segment's own perpendicular rather than along the
  vertex miter: right at both ends of a straight run and wrong at every bend,
  so the bend vertex lands at (10, 2.5) instead of (7.5, 2.5).

`TestTheFixtureCanFail` at the bottom is the control: it recomputes the three
wrong answers from the record's own numbers and shows they are far from the
emitted ones, because an assertion that both answers satisfy is not an
assertion.

The conventions the arm uses (positive offsets to the left of the direction
about the entity's normal, the Top and Bottom references being the largest and
smallest offset, `t = offset / dot(miter, side)`) are not mine. I confirmed
them against `real_AC1032.dwg`, which AutoCAD wrote, in two independent ways:
ezdxf's `MLine.virtual_entities()` on a DXF export of it, and the per-vertex
per-element `Segments[i].Parameters[0]` AutoCAD baked into the file, which is
that same `t`. Both agreed to every digit the dump prints on all three of its
MLINEs. The product code computes the offsets and never reads the baked
parameters, because two code paths for one shape is how the two drift.
"""

import math
import os

import pytest
from g13_support import FIXTURES, manifest, records, sha256_file, vertex_record, warnings

MLINE = "g13_mline.dwg"

# The dump prints six decimals, so this is the floor of what any comparison
# here can mean.
TOL = 1e-6

# The fixture's style VIPRS_G13_MLS carries these three offsets in this order,
# and every expected coordinate below is derived from them rather than typed.
OFFSETS = (1.0, 0.0, -1.5)
SCALE = 2.5

# The open path all three justification MLINEs share: a right-angle bend so
# the miter at the middle vertex is not the segment perpendicular.
PATH = ((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 8.0, 0.0))

# The unit miter at each of its vertices, in the file. The two ends of an open
# path carry the segment perpendicular; the bend carries the bisector.
ROOT2 = math.sqrt(2.0)
PATH_MITERS = ((0.0, 1.0, 0.0), (-1.0 / ROOT2, 1.0 / ROOT2, 0.0), (-1.0, 0.0, 0.0))
# dot(miter, side) at each vertex, which is what the offset is divided by.
PATH_DENOMS = (1.0, 1.0 / ROOT2, 1.0)


def offset_path(o_eff):
    """`PATH` displaced by one effective offset, along each vertex's miter."""
    out = []
    for p, m, denom in zip(PATH, PATH_MITERS, PATH_DENOMS):
        t = o_eff / denom
        out.append((p[0] + m[0] * t, p[1] + m[1] * t, p[2] + m[2] * t))
    return out


def polylines(fixture):
    """Every Polyline in one dump, in order, as a dict of what it carries."""
    out = []
    for r in records(fixture):
        if r["kind"] != "Polyline":
            continue
        pts, bulges, normal = vertex_record(r["rest"])
        closed = " closed=1 " in r["rest"]
        out.append(
            {
                "handle": r["handle"],
                "flags": r["flags"],
                "pts": pts,
                "bulges": bulges,
                "normal": normal,
                "closed": closed,
            }
        )
    return out


def groups(fixture):
    """The polylines of one dump, grouped into consecutive runs of one handle.

    One MLINE emits several records under its own handle, which is MESH's
    precedent, so at top level a run of one handle is one entity.

    Under block expansion that stops being true, and it is worth being precise
    about why rather than papering over it. Every instance of a block carries
    the block entity's own handle, so two insertions of a block holding one
    three-element MLINE arrive as six consecutive records under one handle with
    nothing between them saying where one insertion ends. That is the same
    ambiguity a MESH already has and it is not new here; `block_runs` below is
    how this file deals with it, by chunking on the element count the arm
    promises rather than pretending the dump delimits them.
    """
    out = []
    for p in polylines(fixture):
        if out and out[-1][0]["handle"] == p["handle"] and out[-1][0]["flags"] == p["flags"]:
            out[-1].append(p)
        else:
            out.append([p])
    return out


def top_level(fixture):
    return [g for g in groups(fixture) if g[0]["flags"] == 0]


def block_runs(fixture, elements):
    """The `flags == 1` polylines chunked into one run per insertion.

    The chunk size is the element count of the block MLINE's style, which is
    what the arm promises and what the fixture sets, because the dump itself
    carries no delimiter between two insertions of one block.
    """
    flat = [p for p in polylines(fixture) if p["flags"] == 1]
    assert len(flat) % elements == 0, (
        f"{len(flat)} block polylines do not divide into runs of {elements}, so either "
        "an element went missing or an insertion did"
    )
    return [flat[i : i + elements] for i in range(0, len(flat), elements)]


def close(a, b, tol=TOL):
    return all(abs(a[i] - b[i]) <= tol for i in range(3))


def same_path(got, want, tol=TOL):
    if len(got) != len(want):
        return False
    return all(close(g, w, tol) for g, w in zip(got, want))


def direction(a, b):
    v = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    n = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    assert n > 0.0, f"a zero-length span between {a} and {b} has no direction"
    return (v[0] / n, v[1] / n, v[2] / n)


def cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


class TestZeroJustification:
    """Zero puts the path where the file's coordinates are and every element
    at its own offset either side of it. The fixture's offsets are +1, 0 and
    -1.5 at a scale factor of 2.5, so the effective offsets are 2.5, 0 and
    -3.75 and the middle element is the path itself."""

    def test_zero_justification_emits_three_offset_polylines(self):
        group = top_level(MLINE)[0]
        assert len(group) == 3, (
            f"the first MLINE emitted {len(group)} polylines and its style carries three "
            "elements. One record here is the bare centre path, which is the defect "
            "libviprs-dep#87 exists to prevent: it is type-compatible with a real "
            "polyline and no consumer could tell them apart."
        )
        for i, o in enumerate(OFFSETS):
            want = offset_path((o - 0.0) * SCALE)
            assert same_path(group[i]["pts"], want), (
                f"element {i} (offset {o}) is {group[i]['pts']} and the style order puts "
                f"{want} there"
            )

    def test_the_scale_factor_is_in_the_offset(self):
        # The named wrong answer: offsets applied as the style writes them. The
        # first element would sit at y = 1.0 rather than y = 2.5 at the first
        # vertex, which is a drawing that looks right and is 2.5 times too
        # narrow.
        first = top_level(MLINE)[0][0]["pts"][0]
        assert abs(first[1] - 2.5) <= TOL, (
            f"the first element starts at {first}. The style offset is 1.0 and the "
            "entity's scale factor is 2.5, so 1.0 here means ScaleFactor was dropped."
        )

    def test_the_bend_follows_the_miter_and_not_the_perpendicular(self):
        # At a bend the miter is the bisector of the two segments' sides, so
        # the offset line moves along the bisector and meets its neighbour.
        # Offsetting along each segment's own perpendicular instead leaves the
        # bend at x = 10 and puts a gap in the drawn line.
        bend = top_level(MLINE)[0][0]["pts"][1]
        assert close(bend, (7.5, 2.5, 0.0)), (
            f"the bend vertex of the first element is {bend}. The miter at that vertex "
            "is (-1,1,0)/sqrt(2) and dot(miter, side) is 1/sqrt(2), so t is 2.5*sqrt(2) "
            "and the point is (7.5, 2.5, 0). (10, 2.5, 0) is the segment perpendicular, "
            "which is right at the ends of a straight run and wrong at every bend."
        )

    def test_the_elements_are_open_and_carry_no_bulges(self):
        for i, p in enumerate(top_level(MLINE)[0]):
            assert not p["closed"], f"element {i} of an open MLINE came out closed"
            assert p["bulges"] == [], (
                f"element {i} carries a bulge array, and an MLINE's elements are "
                "straight between its vertices: record 4 leaves the array out entirely "
                "rather than carrying a run of zeroes"
            )

    def test_every_element_is_under_the_entity_handle(self):
        group = top_level(MLINE)[0]
        assert len({p["handle"] for p in group}) == 1, (
            "the three elements of one MLINE carry three handles, and they are one "
            "entity. Several records under one handle is record 9's MESH precedent."
        )

    def test_the_record_names_the_entity_plane(self):
        for p in top_level(MLINE)[0]:
            assert close(p["normal"], (0.0, 0.0, 1.0)), (
                f"the record names the plane {p['normal']} and the entity's normal is +Z"
            )


class TestTopAndBottomJustification:
    """Justification says which of the style's offsets lands on the path the
    file holds. Top is the largest offset, Bottom the smallest, and Zero is
    literally zero whether or not any element sits there."""

    def test_top_justification_puts_the_largest_offset_on_the_path(self):
        group = top_level(MLINE)[1]
        assert len(group) == 3
        reference = max(OFFSETS)
        for i, o in enumerate(OFFSETS):
            want = offset_path((o - reference) * SCALE)
            assert same_path(group[i]["pts"], want), (
                f"element {i} (offset {o}) is {group[i]['pts']} and Top's reference is "
                f"{reference}, which puts {want} there"
            )
        assert same_path(group[0]["pts"], list(PATH)), (
            "Top's largest offset is the one that lies on the path the file holds, so "
            f"the first element should be {list(PATH)} and it is {group[0]['pts']}"
        )

    def test_bottom_justification_puts_the_smallest_offset_on_the_path(self):
        group = top_level(MLINE)[2]
        assert len(group) == 3
        reference = min(OFFSETS)
        for i, o in enumerate(OFFSETS):
            want = offset_path((o - reference) * SCALE)
            assert same_path(group[i]["pts"], want), (
                f"element {i} (offset {o}) is {group[i]['pts']} and Bottom's reference "
                f"is {reference}, which puts {want} there"
            )
        assert same_path(group[2]["pts"], list(PATH)), (
            "Bottom's smallest offset lies on the path the file holds, so the third "
            f"element should be {list(PATH)} and it is {group[2]['pts']}"
        )

    def test_the_three_justifications_are_three_different_drawings(self):
        # The control. All three MLINEs share one path, one style and one scale
        # factor, so an arm that ignored Justification entirely would emit the
        # same three records three times and every assertion above would still
        # be about real coordinates.
        zero = top_level(MLINE)[0][0]["pts"]
        top = top_level(MLINE)[1][0]["pts"]
        bottom = top_level(MLINE)[2][0]["pts"]
        assert not same_path(zero, top), "Zero and Top emitted the same first element"
        assert not same_path(zero, bottom), "Zero and Bottom emitted the same first element"
        assert not same_path(top, bottom), "Top and Bottom emitted the same first element"


class TestAClosedMLine:
    """A closed MLINE has no ends, so its first vertex is a joint like every
    other one and carries a bisector rather than a perpendicular.

    The square is (30,0) (40,0) (40,10) (30,10) traversed counter-clockwise,
    so at (30,0) the incoming segment runs -Y and the outgoing runs +X. Their
    two sides are (1,0,0) and (0,1,0), the miter is their sum normalised,
    (1,1,0)/sqrt(2), and dot(miter, side) is 1/sqrt(2). An effective offset of
    2.5 is therefore t = 2.5*sqrt(2) along that miter, which displaces the
    point by (2.5, 2.5) and lands on (32.5, 2.5).

    I recomputed those numbers rather than taking the ones I was handed: the
    plan for this lane wrote the same square with the displacement of a
    clockwise traversal, and the vertex list it gives is counter-clockwise.
    The claim the test makes is unchanged, which is that the first vertex is
    mitered and not squared off.
    """

    def test_a_closed_mline_closes_every_element(self):
        group = top_level(MLINE)[3]
        assert len(group) == 3
        for i, p in enumerate(group):
            assert p["closed"], (
                f"element {i} of the closed MLINE came out open. Record 4 closes "
                "implicitly, so a run of four vertices that does not say closed is a "
                "three-sided path."
            )
            assert len(p["pts"]) == 4, (
                f"element {i} carries {len(p['pts'])} vertices and the square has four. "
                "Repeating the first vertex at the end is what record 4 does not do."
            )

    def test_the_first_vertex_of_a_closed_path_is_mitered(self):
        inner = top_level(MLINE)[3][0]["pts"][0]
        outer = top_level(MLINE)[3][2]["pts"][0]
        assert close(inner, (32.5, 2.5, 0.0)), (
            f"the closed square's first element starts at {inner}. Treating the first "
            "vertex of a closed path as an open end gives (30, 2.5, 0), which squares "
            "off the corner the path actually turns."
        )
        assert close(outer, (26.25, -3.75, 0.0)), (
            f"the outer element starts at {outer}, and the open-end treatment would "
            "give (30, -3.75, 0)"
        )

    def test_the_closed_square_is_three_nested_squares(self):
        # The control for the miter assertion: each element is a square of its
        # own side length, so an arm that offset only the straight spans and
        # left the corners alone would not produce these three areas.
        group = top_level(MLINE)[3]
        sides = []
        for p in group:
            xs = [v[0] for v in p["pts"]]
            ys = [v[1] for v in p["pts"]]
            sides.append((max(xs) - min(xs), max(ys) - min(ys)))
        assert sides[0] == pytest.approx((5.0, 5.0), abs=TOL), (
            f"the inner element measures {sides[0]} and a 10 by 10 square inset by 2.5 "
            "on every side is 5 by 5"
        )
        assert sides[1] == pytest.approx((10.0, 10.0), abs=TOL)
        assert sides[2] == pytest.approx((17.5, 17.5), abs=TOL), (
            f"the outer element measures {sides[2]} and a 10 by 10 square grown by 3.75 "
            "on every side is 17.5 by 17.5"
        )


class TestAStyleWithNoElements:
    """The one unresolvable state this layer can actually see.

    A dangling style handle is not it. ACadSharp's `CadMLineTemplate.build`
    assigns `Style` only when the handle resolves and otherwise leaves the
    entity holding `MLineStyle.Default`, and `AssignDocument` then runs
    `TryAdd`, which hands back the document's own "Standard". So a file whose
    style is missing decodes as though its style were Standard with offsets
    +0.5 and -0.5, and no arm here can tell that from a file that really says
    Standard. What is left, and what this fixture carries, is a style that
    resolves and holds no elements: there is no offset to put a line at, and
    the path alone is not the entity.
    """

    def test_an_empty_style_warns_and_emits_nothing(self):
        empty = [
            w
            for w in warnings(MLINE)
            if w["code"] == "UNSUPPORTED_ENTITY" and w["message"].startswith("MLINE")
        ]
        assert len(empty) == 1, (
            f"the fixture produced {len(empty)} MLINE refusals and it carries exactly "
            f"one MLINE that cannot be placed: {[w['message'] for w in empty]}"
        )
        assert "carries no elements" in empty[0]["message"], (
            f"the refusal reads {empty[0]['message']!r} and the reason is the style "
            "holding no elements"
        )
        handle = empty[0]["handle"]
        assert not [p for p in polylines(MLINE) if p["handle"] == handle], (
            "the MLINE whose style is empty also emitted geometry. Falling back to "
            "Standard's +/-0.5 would put two polylines here, and inventing a style is "
            "the one thing docs/WIRE.md's lookup rule forbids."
        )

    def test_the_refusal_names_the_dxf_kind_first(self):
        # The corpus tests read the refused type back by splitting the message
        # on its first space, so the first word is load-bearing.
        empty = [w for w in warnings(MLINE) if w["message"].startswith("MLINE")]
        assert empty, "no refusal in this dump names MLINE at all"
        assert empty[0]["message"].split(" ")[0] == "MLINE"


class TestStyleFeaturesThisVersionDoesNotDraw:
    """Fill, joint lines and caps are things the style asks for and this
    version does not draw. Warning 115 says so, once, beside the entity that
    asked, and the control is that the plain styles produce none."""

    def test_style_features_are_said_to_be_ignored_only_when_asked_for(self):
        said = [w for w in warnings(MLINE) if w["code"] == "MLINE_STYLE_FEATURES_IGNORED"]
        assert len(said) == 1, (
            f"the dump carries {len(said)} MLINE_STYLE_FEATURES_IGNORED warnings and "
            "exactly one of the fixture's styles sets FillOn and a cap bit. More than "
            "one means the plain styles are raising it too, which would make the code "
            "mean nothing."
        )
        handle = said[0]["handle"]
        group = [g for g in top_level(MLINE) if g[0]["handle"] == handle]
        assert group, f"warning 115 names handle {handle} and no polyline carries it"
        assert len(group[0]) == 3, (
            "the entity that asked for fill and caps still emits its element lines; "
            "115 is a statement about what is missing beside them, not a refusal"
        )

    def test_the_warning_says_what_it_did_not_draw(self):
        said = [w for w in warnings(MLINE) if w["code"] == "MLINE_STYLE_FEATURES_IGNORED"]
        message = said[0]["message"]
        assert "fill" in message and "caps" in message, (
            f"the warning reads {message!r} and the style sets FillOn and a start cap"
        )


class TestBlockCopies:
    """Two insertions of one block, neither the identity, so the records carry
    flags bit 0 and the block entity's own handle."""

    def test_both_insertions_emit_every_element(self):
        flat = [p for p in polylines(MLINE) if p["flags"] == 1]
        assert len(flat) == 6, (
            f"{len(flat)} polylines crossed at flags 1. AddBlockInstances makes two "
            "insertions of a block holding one MLINE whose style carries three "
            "elements, so three is one insertion losing its copy and two is an arm "
            "that emitted the centre line per instance."
        )
        assert len({p["handle"] for p in flat}) == 1, (
            "the block copies carry more than one handle, and every instance of a "
            "block carries the block entity's own"
        )

    def test_block_copies_cross_and_stay_parallel(self):
        for run in block_runs(MLINE, 3):
            spans = [direction(p["pts"][0], p["pts"][1]) for p in run]
            for i in range(1, len(spans)):
                c = cross(spans[0], spans[i])
                assert max(abs(v) for v in c) <= 1e-5, (
                    f"element {i} of a block copy runs {spans[i]} and element 0 runs "
                    f"{spans[0]}. An affine map takes parallel lines to parallel lines, "
                    "so an arm that applied the offsets after the transform, or that "
                    "recomputed a miter in world space, shows up here."
                )

    def test_the_two_insertions_are_different_insertions(self):
        first, second = block_runs(MLINE, 3)
        a = direction(first[0]["pts"][0], first[0]["pts"][1])
        b = direction(second[0]["pts"][0], second[0]["pts"][1])
        assert not close(a, b, 1e-5), (
            f"both insertions run {a}, and the second is mirrored in X, so an arm that "
            "emitted one insertion twice would look exactly like this"
        )

    def test_the_block_path_is_not_axis_aligned(self):
        # The control. Three horizontal lines are parallel whatever the arm
        # does with them, so the block MLINE's path is slanted on purpose and
        # this is what says so.
        for run in block_runs(MLINE, 3):
            span = direction(run[0]["pts"][0], run[0]["pts"][1])
            assert min(abs(span[0]), abs(span[1])) > 0.1, (
                f"a block copy's span runs {span}, which is close enough to an axis "
                "that the parallelism assertion above could not fail"
            )


class TestNothingStillRefusesAWellFormedMLine:
    def test_the_kind_left_the_refusal_table(self):
        refusals = [
            w["message"]
            for w in warnings(MLINE)
            if w["code"] in ("UNSUPPORTED_ENTITY", "ENTITY_REFUSED_BY_DESIGN")
            and w["message"].startswith("MLINE is not a primitive")
        ]
        assert refusals == [], (
            f"MLINE is still refused as a whole kind: {refusals}. The only MLINE this "
            "build refuses is one it cannot place, and that refusal names the reason."
        )

    def test_the_fixture_is_mostly_geometry(self):
        assert len(polylines(MLINE)) == 21, (
            "six top-level MLINEs of which five emit three elements, plus two block "
            f"copies of three, is 21 polylines and the dump carries {len(polylines(MLINE))}"
        )


class TestTheFixtureCanFail:
    """A probe that cannot tell the two answers apart is not a probe.

    Each of the three named defects is recomputed here from the record's own
    numbers and shown to be far from what was emitted. Without this, every
    assertion above could be passing on a shape where the right answer and the
    wrong one coincide, which is what a straight two-vertex MLINE at scale 1
    would be for all three of them at once.
    """

    def test_the_bare_path_is_a_different_record_from_every_element(self):
        group = top_level(MLINE)[0]
        assert len(group) == 3
        # The centre path is element 1 here only because the fixture's style
        # carries a zero offset. The defect emits it ALONE, so what this shows
        # is that the other two are somewhere else entirely.
        for i in (0, 2):
            assert not same_path(group[i]["pts"], list(PATH)), (
                f"element {i} is the bare centre path, so emitting the path alone would "
                "satisfy the assertions above"
            )

    def test_dropping_the_scale_factor_moves_the_first_element(self):
        scaled = offset_path(OFFSETS[0] * SCALE)
        unscaled = offset_path(OFFSETS[0])
        assert not same_path(scaled, unscaled), (
            "the scaled and unscaled first elements are the same path, so "
            "test_the_scale_factor_is_in_the_offset could not fail"
        )
        assert abs(scaled[0][1] - unscaled[0][1]) == pytest.approx(1.5, abs=TOL)

    def test_the_miter_and_the_perpendicular_disagree_at_the_bend(self):
        o_eff = OFFSETS[0] * SCALE
        # Along the miter, which is what the arm does.
        mitered = offset_path(o_eff)[1]
        # The two perpendicular answers, one per segment meeting at the bend.
        # Either is what an arm that never looked at Vertex.Miter would emit,
        # and both leave a gap in the drawn line.
        incoming = (PATH[1][0], PATH[1][1] + o_eff, PATH[1][2])
        outgoing = (PATH[1][0] - o_eff, PATH[1][1], PATH[1][2])
        assert close(mitered, (7.5, 2.5, 0.0)), (
            f"the bend of the first element is at {mitered} by this file's own numbers"
        )
        assert not close(mitered, incoming), (
            f"the miter and the incoming segment's perpendicular both put the bend at "
            f"{mitered}, so this fixture's path has no bend worth the name"
        )
        assert not close(mitered, outgoing), (
            "the miter and the outgoing segment's perpendicular agree at the bend"
        )


class TestTheManifestCoversThisFixture:
    def test_the_dump_is_of_the_committed_dwg(self):
        entry = manifest()["fixtures"][MLINE]
        assert entry["sha256"] == sha256_file(os.path.join(FIXTURES, MLINE)), (
            "the committed dump was recorded against a different g13_mline.dwg"
        )
