"""POINT becomes record 14, `Point` (#83, J1.2 / #135).

POINT was 40 of the 88 refused entities on `real_AC1032.dwg`. It is a position
and nothing else, so no shape wire version 2 defines can hold one, and
`g13_point.dwg` sat in the corpus as eight refusals that
`test_refused_point_tolerance.py` was written to see go red the day a record
replaced them. This is that day, and these are the assertions that replace
them.

Three things the fixture is shaped to catch.

* The location is world. ADR 0003 said it was lifted out of the entity's plane
  and DXF says group 10 on a POINT is WCS, which ACadSharp's own comment on the
  field repeats. The fourth top-level point carries a non-Z extrusion and a Z
  that a lift would move a long way, so an arm that lifted it is wrong here and
  right about the other three.
* The plane and the marker's rotation follow the insertion. The block half is
  inserted once non-uniformly and once mirrored, and the mirrored copy is where
  the rotation changes sign and the angle becomes pi, which an arm that only
  adds the turn gets wrong.
* A value that is not a number is a warning and no record. Every record here is
  numbered past 13, which is the case `IsGeometry` used to leave uncovered
  (J1.1, #134). `g13_nonfinite_records.dwg` holds the bad ones and a good one
  beside each as the control.
"""

import math
import re

import pytest
import wire3_reference as ref
from g13_support import kinds, records

POINT = "g13_point.dwg"
NONFINITE = "g13_nonfinite_records.dwg"
REAL = "real_AC1032.dwg"

SKEW = (1.0, 2.0, 2.0)
Z = (0.0, 0.0, 1.0)


def points(fixture):
    out = []
    for r in records(fixture):
        if r["kind"] != "Point":
            continue
        out.append(
            {
                "flags": r["flags"],
                "handle": r["handle"],
                "p": ref.triple(r["rest"], "p"),
                "normal": ref.triple(r["rest"], "normal"),
                "rot": ref.scalar(r["rest"], "rot"),
            }
        )
    return out


class TestTheFixtureBecameRecords:
    def test_it_holds_eight_points_and_nothing_else(self):
        assert kinds(POINT) == {"Point": 8}, (
            f"{POINT} now holds {kinds(POINT)}. Four POINTs at top level and two in a block "
            "inserted twice are eight records, and a view with geometry in it has no EMPTY_VIEW"
        )

    def test_four_are_top_level_and_four_came_from_the_insertions(self):
        flags = [p["flags"] for p in points(POINT)]
        assert flags == [0, 0, 0, 0, 1, 1, 1, 1], (
            f"{flags}: the block half is expanded under two INSERTs, so bit 0 is set on four"
        )


class TestPositionIsWorld:
    def test_the_top_level_points_are_where_the_file_put_them(self):
        got = [p["p"] for p in points(POINT)[:4]]
        assert got == [
            (0.0, 0.0, 0.0),
            (3.25, -7.5, 0.0),
            (-11.75, 4.5, 2.25),
            (2.0, 3.0, 5.0),
        ], (
            f"{got}. The fourth carries a skewed extrusion, and a POINT's location is world "
            "whatever its extrusion is: lifting it through the arbitrary axis algorithm "
            "puts it somewhere the drawing does not"
        )

    def test_the_plane_is_the_extrusion_normalised(self):
        got = [p["normal"] for p in points(POINT)[:4]]
        third = 1.0 / 3.0
        want = [Z, Z, Z, (third, 2 * third, 2 * third)]
        for g, w in zip(got, want):
            assert ref.close(g, w), f"{got} against {want}"

    def test_the_rotation_of_an_upright_marker_is_zero(self):
        assert [p["rot"] for p in points(POINT)[:4]] == [0.0, 0.0, 0.0, 0.0]


class TestTheInsertionsAreFollowed:
    # Two points in the block, inserted twice: (1,2,0) upright and (2,3,5) on the
    # skewed plane. Expected values come from wire3_reference, which shares no
    # code with the decoder.
    BLOCK = [((1.0, 2.0, 0.0), Z), ((2.0, 3.0, 5.0), SKEW)]

    @pytest.mark.parametrize(
        "insertion,slot",
        [(ref.SHEARED, slice(4, 6)), (ref.MIRRORED, slice(6, 8))],
        ids=["non-uniform", "mirrored"],
    )
    def test_each_expanded_point_matches_the_reference(self, insertion, slot):
        got = points(POINT)[slot]
        assert len(got) == 2
        for g, (location, normal) in zip(got, self.BLOCK):
            p, n, rot = ref.point_record(insertion, location, normal, 0.0)
            assert ref.close(g["p"], p), f"position {g['p']} against {p}"
            assert ref.close(g["normal"], n), f"normal {g['normal']} against {n}"
            assert ref.close(ref.dump_angle(g["rot"]), ref.dump_angle(rot)), (
                f"rotation {g['rot']} against {rot}"
            )

    def test_a_mirror_turns_the_marker_half_way_round(self):
        # Hand-checked rather than computed: x -> -2x sends the marker's x axis
        # to -x, whose angle about +z is pi.
        got = points(POINT)[6]
        assert ref.close(got["p"], (78.0, 4.0, 0.0)), got
        assert ref.close(got["normal"], Z), got
        assert ref.close(got["rot"], math.pi), got


class TestAValueThatIsNotANumberIsAWarning:
    def counts(self):
        names = {}
        for r in records(NONFINITE):
            if r["kind"] != "Warning":
                continue
            m = re.search(r' code=(\S+) message="(\w+) ', r["rest"])
            assert m, r
            names.setdefault((m.group(1), m.group(2)), 0)
            names[(m.group(1), m.group(2))] += 1
        return names

    def test_every_bad_point_is_warning_107_and_no_record(self):
        assert self.counts().get(("NON_FINITE_GEOMETRY", "Point")) == 3, self.counts()

    def test_the_good_point_beside_them_still_crosses(self):
        got = points(NONFINITE)
        assert len(got) == 1 and got[0]["p"] == (3.0, 3.0, 3.0), got

    def test_a_non_finite_record_past_13_is_covered_for_all_three_kinds(self):
        c = self.counts()
        assert c.get(("NON_FINITE_GEOMETRY", "Unbounded")) == 2, c
        assert c.get(("NON_FINITE_GEOMETRY", "Placement")) == 2, c


class TestTheRealDrawing:
    def test_all_forty_points_cross(self):
        assert len(points(REAL)) == 40, kinds(REAL)

    def test_no_point_is_refused_any_more(self):
        refused = [
            r for r in records(REAL) if r["kind"] == "Warning" and 'message="POINT ' in r["rest"]
        ]
        assert not refused, f"{len(refused)} POINTs are still refused on {REAL}"
