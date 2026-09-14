"""LEADER, the annotation composite whose geometry is already geometry.

A LEADER is a run of world coordinates and a reference to whatever it points
at. The vertex run is the whole of what this version emits: it lowers onto
record 4 as one open `Polyline`, no new record, no wire change, nothing
synthesised. The hook line, where the drawing recorded one, is already the
last vertex in that run.

Two of the assertions below are about what the arm does NOT do, and they are
the reason this file is not four lines counting records.

A LEADER's vertices are world coordinates while almost every other oriented
entity in this corpus stores its own in the plane its normal names, so the
plausible wrong arm lifts them through the arbitrary axis algorithm the way
the SOLID and TEXT arms lift theirs. That produces a two-point run that still
looks like a leader and is nowhere near where the drawing put it, which is why
the fourth leader carries a non-Z extrusion and why `TestTheLiftWouldBeVisible`
recomputes the lifted answer off the record's own points and shows the two are
far apart.

A spline-fit LEADER stores fit points and never the curve, so threading a
polyline through them is a tessellation this layer is not allowed to make
(docs/WIRE.md, and the same rule that keeps an ARC an ARC). The third leader is
spline-fit with fit points that are not collinear, so an arm that straightened
it would emit a four-vertex run rather than something indistinguishable from
a correct answer.

`TestTheArmExpandsNothing` reads the arm as text. #86's first acceptance box is
that expansion goes through `Walk`'s own stack and never recurses inside `Map`,
because a composite expanded from inside `Map` is a file-controlled recursion
on the CLR stack, and a `StackOverflowException` cannot be caught. LEADER
expands nothing at all, which is a claim about the arm's shape rather than
about any output, so the geometry above cannot check it: an arm that walked
`AssociatedAnnotation` by calling `Map` again would emit exactly the records
this file asserts on and take the process down on a drawing that nests one
leader's annotation inside another's.
"""

import os
import re

from g13_support import arbitrary_axis, cross, kinds, normalize, records, warnings

LEADER = "g13_leader.dwg"

HERE = os.path.dirname(os.path.abspath(__file__))
NATIVE = os.path.join(os.path.dirname(HERE), "native")
ARM = os.path.join(NATIVE, "Adapter", "Flatten.Leaders.cs")

# The dump prints six decimals, so this is the floor of what any comparison
# here can mean.
TOL = 1e-6

# The fourth leader's extrusion, as Corpus.cs writes it. Not a unit vector on
# purpose, so an arm that forgot to normalise it shows up in the record.
SKEW = (1.0, 2.0, 2.0)


def read(path):
    with open(path) as f:
        return f.read()


def without_comments(code):
    """`code` with its whole-line `//` comments dropped.

    The checks at the bottom are about what the arm does, and the arm's header
    is prose about what it deliberately does not do, so a rule that read the
    prose would fail on the paragraph explaining why it passes.
    """
    return "\n".join(line for line in code.splitlines() if not line.lstrip().startswith("//"))


def body_of(code, signature):
    """The brace-matched body of the first member whose declaration matches.

    The same reader `test_shim_source_rules.py` uses, and it is here for the
    same reason: a declaration is not a call, so a rule looking for a recursive
    call has to start after the signature or it finds the method's own name
    every time.
    """
    m = re.search(signature, code)
    assert m, f"nothing in this source matches {signature!r}"
    start = code.find("{", m.end())
    assert start >= 0, "the declaration is not followed by a body"
    depth, i = 0, start
    while i < len(code):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                return code[start : i + 1]
        i += 1
    raise AssertionError("the body's braces do not balance")


def arm_body():
    return body_of(without_comments(read(ARM)), r"private IEnumerable<Primitive> LeaderPolyline\(")


def close(a, b):
    return all(abs(a[i] - b[i]) <= TOL for i in range(3))


TRIPLE_RE = re.compile(r"\(([-0-9.]+),([-0-9.]+),([-0-9.]+)\)")


def polylines(fixture):
    """Every Polyline in one dump, as a dict, in stream order.

    A local reader rather than `g13_support.vertex_record`, because the two
    things this file asks about a record are its `closed` flag and its handle,
    and that helper returns neither.
    """
    out = []
    for i, r in enumerate(records(fixture)):
        if r["kind"] != "Polyline":
            continue
        pts = {}
        for name in ("pts", "normal"):
            m = re.search(rf" {name}=\[([^\]]*)\]", r["rest"])
            assert m, f"no {name}= in {r['rest']!r}"
            pts[name] = [
                (float(a), float(b), float(c)) for a, b, c in TRIPLE_RE.findall(m.group(1))
            ]
        m = re.search(r" bulges=\[([^\]]*)\]", r["rest"])
        assert m, f"no bulges= in {r['rest']!r}"
        body = m.group(1).strip()
        closed = re.search(r" closed=(\d+)", r["rest"])
        assert closed, f"no closed= in {r['rest']!r}"
        out.append(
            {
                "index": i,
                "handle": r["handle"],
                "flags": r["flags"],
                "pts": pts["pts"],
                "normal": pts["normal"][0],
                "bulges": [float(x) for x in body.split(",")] if body else [],
                "closed": int(closed.group(1)),
            }
        )
    return out


def warning_records(fixture, code):
    """Every Warning of one code, with the stream index it sits at."""
    out = []
    for i, r in enumerate(records(fixture)):
        if r["kind"] != "Warning":
            continue
        m = re.search(r' code=(\S+) message="((?:[^"\\]|\\.)*)"', r["rest"])
        assert m, f"a Warning with no code and message: {r['rest']!r}"
        if m.group(1) == code:
            out.append(
                {"index": i, "handle": r["handle"], "flags": r["flags"], "message": m.group(2)}
            )
    return out


def top_level(items):
    return [x for x in items if x["flags"] == 0]


class TestAStraightLeaderIsOneOpenPolyline:
    """The vertices the file holds, in the order it holds them, and nothing
    else. A leader is a path rather than a boundary, so it is open."""

    def test_a_straight_leader_is_one_open_polyline_through_its_vertices(self):
        first = top_level(polylines(LEADER))[0]
        expected = [(0.0, 0.0, 0.0), (5.0, 3.0, 0.0), (9.0, 3.0, 0.0)]
        assert len(first["pts"]) == 3, f"the first leader emitted {len(first['pts'])} vertices"
        for i, (got, want) in enumerate(zip(first["pts"], expected)):
            assert close(got, want), f"vertex {i} is {got} and the file holds {want}"
        assert first["closed"] == 0, (
            "the leader came out closed, which draws a line from its tip back to its "
            "tail. A leader is a path and record 4's closed flag is what says so."
        )
        assert first["bulges"] == [], (
            "a leader's spans are straight, so record 4 leaves the bulge array out "
            "entirely rather than carrying a run of zeroes"
        )

    def test_it_is_one_record_and_not_a_run_of_lines(self):
        # The other plausible wrong arm: one Line per span. That loses the
        # entity's identity (three records under one handle with no delimiter)
        # and it loses the plane, because record 3 carries no normal.
        counts = kinds(LEADER)
        assert counts.get("Polyline", 0) == 5, (
            f"{counts.get('Polyline', 0)} Polyline records, and five leaders crossed. "
            "Without this the assertion below is satisfied by a dump holding no "
            "geometry at all."
        )
        assert counts.get("Line", 0) == 0, (
            f"{counts.get('Line')} Line records in this dump. A leader is one Polyline, "
            "and a run of Lines has nowhere to say where the entity ended."
        )

    def test_the_two_vertex_leader_crosses_whole(self):
        second = top_level(polylines(LEADER))[1]
        expected = [(20.0, 0.0, 0.0), (24.0, 5.0, 0.0)]
        assert len(second["pts"]) == 2
        for got, want in zip(second["pts"], expected):
            assert close(got, want), f"{got} is not {want}"

    def test_every_top_level_leader_that_crossed_is_its_own_record(self):
        # Three of the four top-level leaders are straight and one is
        # spline-fit, so three records and three handles. A count alone would
        # be satisfied by one leader emitted three times.
        found = top_level(polylines(LEADER))
        assert len(found) == 3, f"{len(found)} top-level polylines, and the fixture has three"
        handles = {p["handle"] for p in found}
        assert len(handles) == 3, f"the three records carry {sorted(handles)}"


class TestTheArrowhead:
    """An arrowhead is a glyph the dimension style names, not geometry the
    drawing holds, so this version draws none and says so."""

    def test_an_enabled_arrowhead_is_said_to_be_missing(self):
        said = top_level(warning_records(LEADER, "ARROWHEAD_NOT_DRAWN"))
        assert len(said) == 1, (
            f"{len(said)} arrowhead warnings at top level. The fixture has one leader "
            "with the flag on and two straight ones with it off."
        )
        first = top_level(polylines(LEADER))[0]
        assert said[0]["handle"] == first["handle"], (
            f"the warning names {said[0]['handle']} and the leader whose arrowhead is on "
            f"is {first['handle']}"
        )
        assert said[0]["index"] < first["index"], (
            "the warning follows the polyline it is about. A consumer reading the stream "
            "in order has already drawn the leader by then."
        )

    def test_a_leader_with_the_flag_off_says_nothing(self):
        # The control, and the half that says this warning is about the flag
        # rather than about LEADER. Without it a producer that raised 114 for
        # every leader would pass the assertion above.
        said = {w["handle"] for w in warning_records(LEADER, "ARROWHEAD_NOT_DRAWN")}
        quiet = top_level(polylines(LEADER))[1:]
        assert len(quiet) == 2, (
            f"{len(quiet)} top-level leaders after the first, and the fixture has two "
            "with the arrowhead off. An empty list agrees with everything."
        )
        for p in quiet:
            assert p["handle"] not in said, (
                f"leader {p['handle']} has its arrowhead off and still got a warning "
                "saying the arrowhead was not drawn"
            )

    def test_the_message_says_why_there_is_no_arrowhead(self):
        said = warning_records(LEADER, "ARROWHEAD_NOT_DRAWN")[0]
        assert said["message"].startswith("LEADER "), (
            f"the message opens {said['message'][:20]!r}. The corpus tests read the kind "
            "back by splitting on the first space, so the first word is the DXF kind."
        )
        assert "dimension style" in said["message"], (
            "the message does not say where the arrowhead lives, which is the one thing "
            "a consumer could act on"
        )


class TestASplineFitLeader:
    """Its fit points are not the curve, and this layer tessellates nothing."""

    def test_a_spline_leader_is_refused_not_straightened(self):
        refusals = [
            w
            for w in top_level(warning_records(LEADER, "UNSUPPORTED_ENTITY"))
            if w["message"].startswith("LEADER ")
        ]
        assert len(refusals) == 1, (
            f"{len(refusals)} LEADER refusals at top level, and the fixture has one "
            "spline-fit leader and three straight ones"
        )
        handle = refusals[0]["handle"]
        for p in polylines(LEADER):
            assert p["handle"] != handle, (
                f"the spline-fit leader {handle} was refused AND emitted a polyline of "
                f"{len(p['pts'])} vertices, so the refusal is not what happened to it"
            )
        assert "fit points" in refusals[0]["message"], (
            "the message does not say the file holds fit points rather than a curve, "
            "which is the whole reason this one is refused and the others are not"
        )

    def test_its_fit_points_are_nowhere_in_the_stream(self):
        # The control for the handle check above: an arm that straightened the
        # curve under some other handle would satisfy it. These are the four
        # fit points Corpus.cs writes, and they are not collinear, so a
        # polyline through them is a shape rather than a degenerate run.
        fit = [(50.0, 0.0, 0.0), (52.0, 4.0, 0.0), (56.0, 4.0, 0.0), (58.0, 0.0, 0.0)]
        found = polylines(LEADER)
        assert len(found) == 5, (
            f"{len(found)} polylines in this dump, and the fixture has three straight "
            "leaders at top level and two block copies. A dump with none in it agrees "
            "that the fit points are absent for the wrong reason."
        )
        for p in found:
            for v in p["pts"]:
                assert not any(close(v, f) for f in fit), (
                    f"{v} is one of the spline-fit leader's fit points and it reached "
                    f"the stream on the record at index {p['index']}"
                )


class TestThePlaneIsNamedAndTheVerticesAreNot:
    """A LEADER is `Entity, IOrientable`: it carries a normal at DXF 210 and
    its vertices at DXF 10 are already world coordinates. Record 4 carries a
    normal, so the plane is still named, and the points are not touched."""

    def test_vertices_are_not_lifted_but_the_plane_is_named(self):
        skew = top_level(polylines(LEADER))[2]
        expected = [(30.0, 0.0, 0.0), (34.0, 2.0, 1.0)]
        assert len(skew["pts"]) == 2
        for i, (got, want) in enumerate(zip(skew["pts"], expected)):
            assert close(got, want), (
                f"vertex {i} is {got} and the file holds {want}. A LEADER's vertices are "
                "world coordinates, so lifting one through the arbitrary axis algorithm "
                "moves it out of the drawing."
            )
        assert close(skew["normal"], normalize(SKEW)), (
            f"the record names the plane {skew['normal']} and the entity's normal is "
            f"{SKEW}, which normalises to {normalize(SKEW)}"
        )

    def test_the_flat_leaders_still_name_a_plane(self):
        # The control from the other side. A producer that emitted no normal
        # at all, or the world's, would pass the +Z records and fail the skew
        # one, and this says the +Z records are a real answer rather than a
        # default nobody set.
        flat = top_level(polylines(LEADER))[:2]
        assert len(flat) == 2, f"{len(flat)} flat leaders crossed, and the fixture has two"
        for p in flat:
            assert close(p["normal"], (0.0, 0.0, 1.0)), (
                f"a leader drawn in the world's own plane names {p['normal']}"
            )


class TestTheLiftWouldBeVisible:
    """The probe for the assertion above, and the reason the fourth leader has
    a non-Z extrusion at all.

    It recomputes, from the record's own points, where the wrong arm would have
    put them: through `ObjectToWorld(normal)`, which is the arbitrary axis
    algorithm docs/WIRE.md states and `g13_support.arbitrary_axis` implements.
    If the two answers were close, every assertion about the unlifted points
    would be measuring a fixed point of the transform, which is exactly what a
    leader drawn on +Z is.
    """

    def lifted(self, p, normal):
        n = normalize(normal)
        ax = arbitrary_axis(n)
        ay = cross(n, ax)
        return tuple(p[0] * ax[i] + p[1] * ay[i] + p[2] * n[i] for i in range(3))

    def test_the_two_answers_are_far_apart_on_the_skew_leader(self):
        skew = top_level(polylines(LEADER))[2]
        for v in skew["pts"]:
            moved = self.lifted(v, SKEW)
            gap = sum((moved[i] - v[i]) ** 2 for i in range(3)) ** 0.5
            assert gap > 1.0, (
                f"lifting {v} through the extrusion puts it at {moved}, which is {gap} "
                "away. If that were zero this fixture could not tell a lifted arm from "
                "an unlifted one."
            )

    def test_a_flat_leader_is_a_fixed_point_of_it(self):
        # And the reason the assertion has to be made on the skew leader: for
        # a normal of +Z the algorithm is exactly the identity, so the first
        # three leaders in this fixture agree with the wrong arm.
        flat = top_level(polylines(LEADER))[0]
        for v in flat["pts"]:
            assert close(self.lifted(v, (0.0, 0.0, 1.0)), v)


class TestTheBlockCopies:
    """The half a fixture without a block lets through: the kind is
    implemented, the top-level case works, and instances inside an INSERT are
    dropped or emitted untransformed."""

    def test_block_copies_cross_and_are_labelled(self):
        found = [p for p in polylines(LEADER) if p["flags"] == 1]
        assert len(found) == 2, (
            f"{len(found)} leaders came out of the two insertions. Bit 0 of flags is "
            "'came from expanding a nested insertion'."
        )
        # The block's leader is (0,0,0) (3,2,0) (6,2,0). The first insertion is
        # at (40,0,0) scaled 3 by 1.5, the second at (80,0,0) scaled -2 by 2,
        # so the second is the mirrored one and runs the other way in x.
        expected = [
            [(40.0, 0.0, 0.0), (49.0, 3.0, 0.0), (58.0, 3.0, 0.0)],
            [(80.0, 0.0, 0.0), (74.0, 4.0, 0.0), (68.0, 4.0, 0.0)],
        ]
        for i, p in enumerate(found):
            assert len(p["pts"]) == 3, f"insertion {i} emitted {len(p['pts'])} vertices"
            assert p["closed"] == 0
            for j, (got, want) in enumerate(zip(p["pts"], expected[i])):
                assert close(got, want), f"insertion {i} vertex {j} is {got} and not {want}"

    def test_the_two_insertions_are_not_the_same_record_twice(self):
        # The control for the pair above. Two records that happened to be one
        # instance emitted twice would satisfy a count, and one of these two
        # insertions is mirrored so their first spans run opposite ways in x.
        found = [p for p in polylines(LEADER) if p["flags"] == 1]
        dx = [p["pts"][1][0] - p["pts"][0][0] for p in found]
        assert dx[0] * dx[1] < 0, (
            f"the two insertions run {dx} in x, and one of them is a mirror of the "
            "other, so the signs cannot agree"
        )

    def test_the_arrowhead_code_is_evidenced_under_a_transform(self):
        said = [w for w in warning_records(LEADER, "ARROWHEAD_NOT_DRAWN") if w["flags"] == 1]
        assert len(said) == 2, (
            f"{len(said)} arrowhead warnings came out of the two insertions. The block's "
            "leader has its arrowhead on, so the code is produced under a transform as "
            "well as at the identity."
        )


class TestNothingStillRefusesAStraightLeader:
    def test_nothing_still_refuses_a_straight_leader(self):
        refused = [
            w["message"]
            for w in warnings(LEADER)
            if w["code"] == "UNSUPPORTED_ENTITY" and w["message"].startswith("LEADER ")
        ]
        assert len(refused) == 1, (
            f"{len(refused)} LEADER refusals in this dump: {refused}. Only the spline-fit "
            "one is refused, and the generic 'not a primitive this version flattens' "
            "sentence should not be among them."
        )
        assert "not a primitive this version flattens" not in refused[0], (
            "a straight LEADER is still reaching the default arm"
        )


# A call that hands control back to the walk, or to this arm itself. `Map` and
# `Walk` are the two methods on the flattener that dispatch an entity, and a
# `Pending` is the item type the walk's own stack holds: an arm that built one
# would be expanding something.
_REENTRY = re.compile(r"\b(Map|Walk|LeaderPolyline|Pending)\s*[({<]")


class TestTheArmExpandsNothing:
    """#86's first acceptance box, read off the arm rather than off a dump.

    A composite expanded from inside `Map` is a file-controlled recursion on
    the CLR stack, which is how a drawing took the process down before
    DIMENSION and HATCH moved onto `Walk`'s explicit stack; the comment above
    `case Dimension` says so and the depth bound `CheckDepth` applies is the
    other half. LEADER expands nothing, so it needs neither, and that is a
    claim about the arm's shape that no assertion on the records can make: an
    arm that walked `AssociatedAnnotation` by calling `Map` again emits exactly
    the records the rest of this file asserts on.

    So this reads the arm as text, the way `test_shim_source_rules.py` reads
    the entity switch, and fails on the arrangement that makes the recursion
    possible rather than on the recursion.
    """

    def test_the_arm_is_the_one_this_reads(self):
        # The extraction control. A rule that silently found nothing to check
        # is the same colour as a rule that checked everything.
        body = arm_body()
        assert "Primitive.Polyline(" in body and "ArrowheadNotDrawn" in body, (
            f"the body this parsed out of {ARM} is not the leader arm: {body[:200]!r}"
        )

    def test_it_never_hands_an_entity_back_to_the_walk_or_to_itself(self):
        found = sorted({m.group(1) for m in _REENTRY.finditer(arm_body())})
        assert not found, (
            f"the leader arm names {found}. Expanding anything from inside `Map` is a "
            "recursion a file controls, and a leader arm has nothing to expand: its "
            "vertices are numbers, not entities."
        )

    def test_it_never_reads_the_annotation_the_leader_points_at(self):
        # `Leader.AssociatedAnnotation` is an MTEXT, a TOLERANCE or an INSERT,
        # and it is the one field on this entity that is another entity. It is
        # the field an arm would reach for to draw the label, and reaching for
        # it from here is the recursion above. The annotation is a root of the
        # document in its own right, so the walk visits it anyway.
        assert "AssociatedAnnotation" not in without_comments(read(ARM)), (
            "the leader arm reads AssociatedAnnotation, which is another entity. "
            "Whatever it does with it is done from inside Map."
        )

    def test_the_rule_would_see_the_call_it_is_looking_for(self):
        # The positive control. Without it a regex that matched nothing would
        # pass the check above on any file at all, including an empty one.
        recursive = "foreach (Primitive p in Map(leader.AssociatedAnnotation, place, depth))"
        assert sorted({m.group(1) for m in _REENTRY.finditer(recursive)}) == ["Map"]
        onto_the_stack = "yield return new Pending { Entity = e, Depth = item.Depth + 1 };"
        assert sorted({m.group(1) for m in _REENTRY.finditer(onto_the_stack)}) == ["Pending"]
