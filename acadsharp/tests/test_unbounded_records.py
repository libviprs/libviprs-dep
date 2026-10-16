"""RAY and XLINE become record 15, `Unbounded` (J1.3 / #136).

A line that does not end has no bounded record on wire 2, which is why ADR 0002
refused both kinds. `g13_ray_xline.dwg` held them as two refusals at top level
and two in a block, and the clip was always going to be the consumer's: a
decoder that clipped to the drawing's stated extents would be trusting a number
a drawing can get wrong, and `g13_bad_extents.dwg` is in the corpus because one
does.

So what these assert is the base point and the unit direction, under the two
insertions every refused-kind fixture carries, and that the kinds are told
apart. A direction that is the zero vector has no line to carry and is a
warning, which `g13_nonfinite_records.dwg` also holds (a zero direction is not
a NaN, and the two must not be reported as each other).
"""

import math

import wire3_reference as ref
from g13_support import kinds, records

FIXTURE = "g13_ray_xline.dwg"
REAL = "real_AC1032.dwg"


def unbounded(fixture):
    out = []
    for r in records(fixture):
        if r["kind"] != "Unbounded":
            continue
        out.append(
            {
                "flags": r["flags"],
                "base": ref.triple(r["rest"], "base"),
                "dir": ref.triple(r["rest"], "dir"),
                "kind": r["rest"].split(" kind=")[1].strip(),
            }
        )
    return out


class TestTheFixtureBecameRecords:
    def test_a_line_and_six_unbounded_records_and_no_refusal(self):
        assert ref.entity_counts(FIXTURE) == {"Line": 1, "Unbounded": 6}, (
            f"{FIXTURE} holds {ref.entity_counts(FIXTURE)}: a control LINE, a RAY and an "
            "XLINE at top level, and the same pair in a block inserted twice"
        )

    def test_the_kinds_are_told_apart(self):
        assert [u["kind"] for u in unbounded(FIXTURE)] == ["ray", "xline"] * 3


class TestTheValues:
    def test_the_top_level_pair(self):
        ray, xline = unbounded(FIXTURE)[:2]
        assert ray["flags"] == 0 and xline["flags"] == 0
        assert ref.close(ray["base"], (1.0, 2.0, 0.0))
        assert ref.close(ray["dir"], ref.unit((3.0, 1.0, 0.0)))
        assert ref.close(xline["base"], (-2.0, 3.0, 0.0))
        assert ref.close(xline["dir"], ref.unit((1.0, 4.0, 0.0)))

    def test_the_block_pair_under_each_insertion(self):
        got = unbounded(FIXTURE)[2:]
        block = [((0.0, 1.0, 0.0), (2.0, 1.0, 0.0)), ((0.0, -1.0, 0.0), (1.0, 3.0, 0.0))]
        want = []
        for insertion in (ref.SHEARED, ref.MIRRORED):
            for base, direction in block:
                want.append(ref.unbounded_record(insertion, base, direction))
        assert len(got) == 4
        for g, (base, direction) in zip(got, want):
            assert g["flags"] == 1
            assert ref.close(g["base"], base), f"{g['base']} against {base}"
            assert ref.close(g["dir"], direction), f"{g['dir']} against {direction}"


class TestTheDirectionIsAUnitVector:
    def test_every_direction_in_every_dump_has_length_one(self):
        seen = 0
        for fixture in (FIXTURE, REAL):
            for u in unbounded(fixture):
                seen += 1
                assert abs(math.sqrt(sum(c * c for c in u["dir"])) - 1.0) < 1e-5, (fixture, u)
        assert seen >= 8


class TestAZeroDirectionHasNoLineToCarry:
    NONFINITE = "g13_nonfinite_records.dwg"

    def test_it_is_a_warning_naming_the_kind_and_no_record(self):
        zero = [
            r["rest"]
            for r in records(self.NONFINITE)
            if r["kind"] == "Warning" and "direction of length zero" in r["rest"]
        ]
        assert len(zero) == 2, zero
        assert any(' message="RAY ' in z for z in zero) and any(
            ' message="XLINE ' in z for z in zero
        )
        assert all("code=UNSUPPORTED_ENTITY" in z for z in zero)

    def test_it_is_not_reported_as_a_non_finite_value(self):
        for r in records(self.NONFINITE):
            if r["kind"] == "Warning" and "direction of length zero" in r["rest"]:
                assert "NON_FINITE" not in r["rest"]


class TestTheRealDrawing:
    def test_its_ray_and_its_xline_cross(self):
        assert [u["kind"] for u in unbounded(REAL)] == ["xline", "ray"], kinds(REAL)

    def test_neither_is_refused_any_more(self):
        left = [
            r
            for r in records(REAL)
            if r["kind"] == "Warning"
            and (' message="RAY ' in r["rest"] or ' message="XLINE ' in r["rest"])
        ]
        assert not left, left
