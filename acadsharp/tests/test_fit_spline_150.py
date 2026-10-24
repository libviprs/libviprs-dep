"""A spline AutoCAD stored as fit points still crosses with its control points (#150).

The AC1032 save of the sample drawing holds spline 434 as two fit points and
the two end tangents, with no control points and no knots in the file. The
AC1018 save of the same drawing holds the same curve as four control points and
eight knots. Both are the same cubic, and the DXF twins AutoCAD wrote hold the
four control points for both saves, because AutoCAD derives them on the way out.

The reader is not at fault: it reads what the file holds, and the dump of
``real_AC1032.dwg`` shows the shim passing that on, a Spline record with
``knots=[] ctrl=[] weights=[]`` that a consumer cannot draw. The wire's Spline
record has no field for fit points, so the shim has to send the control points
itself. It can do that without guessing, because for two fit points and two end
tangents a cubic has exactly one set of control points (a Bezier segment).

pytest here has no .NET (ADR 0001), so the dumps read here are recordings of
the shim, and ``test_shim_digest.py`` refuses a stale one.
"""

import re

import pytest
from g13_support import records

REAL_32 = "real_AC1032.dwg"
REAL_18 = "real_AC1018.dwg"
FIT_SPLINE = "434"
TOL = 1e-5

# What DwgReader returns for spline 434 in real_AC1032.dwg: two fit points and
# the two end tangents, nothing else. Read with ACadSharp 3.7.1 under the JIT.
FIT_POINTS = [(250.45879078322412, 17.426024184844767), (259.2245511100041, 27.28750455247221)]
START_TANGENT = (12.764940978905884, 32.4868715404406)
END_TANGENT = (-0.4902389308192596, 29.35314911115819)

NUM = r"[-0-9.]+"
KNOTS = re.compile(r"knots=\[([^\]]*)\]")
CTRL = re.compile(r"ctrl=\[([^\]]*)\]")


def spline(fixture, handle):
    found = [r for r in records(fixture) if r["kind"] == "Spline" and r["handle"] == handle]
    assert len(found) == 1, (
        f"{fixture}: expected one Spline with handle {handle}, found {len(found)}"
    )
    return found[0]


def knots(record):
    body = KNOTS.search(record["rest"]).group(1)
    return [float(x) for x in body.split(",")] if body else []


def ctrl(record):
    body = CTRL.search(record["rest"]).group(1)
    pts = re.findall(rf"\(({NUM}),({NUM}),({NUM})\)", body)
    return [tuple(float(c) for c in p) for p in pts]


def all_splines():
    out = []
    for fixture in ("real_AC1018.dwg", "real_AC1032.dwg", "g13_spline.dwg", "g13_hatch.dwg"):
        for r in records(fixture):
            if r["kind"] == "Spline":
                out.append((fixture, r))
    return out


class TestTheFitSplineCrossesWithItsControlPoints:
    def test_it_has_four_control_points(self):
        got = ctrl(spline(REAL_32, FIT_SPLINE))
        assert len(got) == 4, (
            f"spline {FIT_SPLINE} in {REAL_32} crossed with {len(got)} control points; AutoCAD's "
            "own DXF of the same save holds four"
        )

    def test_it_has_eight_knots(self):
        got = knots(spline(REAL_32, FIT_SPLINE))
        assert len(got) == 8, (
            f"spline {FIT_SPLINE} in {REAL_32} crossed with {len(got)} knots; "
            "the DXF twin holds eight"
        )

    def test_the_knots_are_the_clamped_cubic_vector(self):
        assert knots(spline(REAL_32, FIT_SPLINE)) == [0, 0, 0, 0, 1, 1, 1, 1]

    def test_the_degree_is_three(self):
        assert " degree=3 " in spline(REAL_32, FIT_SPLINE)["rest"] + " "


class TestTheTwoSavesAgree:
    """The same drawing saved as AC1018 and as AC1032 is the same curve."""

    def test_the_knots_match(self):
        assert knots(spline(REAL_32, FIT_SPLINE)) == knots(spline(REAL_18, FIT_SPLINE))

    def test_the_control_points_match(self):
        a = ctrl(spline(REAL_32, FIT_SPLINE))
        b = ctrl(spline(REAL_18, FIT_SPLINE))
        assert len(a) == len(b), f"{len(a)} control points in AC1032, {len(b)} in AC1018"
        for i, (p, q) in enumerate(zip(a, b)):
            assert max(abs(x - y) for x, y in zip(p, q)) < TOL, (
                f"control point {i}: {p} against {q}"
            )

    def test_the_neighbouring_spline_was_never_affected(self):
        # Spline 433 is defined by control points in both saves and already agrees.
        assert ctrl(spline(REAL_32, "433")) == ctrl(spline(REAL_18, "433"))
        assert knots(spline(REAL_32, "433")) == knots(spline(REAL_18, "433"))


class TestNoRecordedSplineIsEmpty:
    def test_every_spline_record_carries_control_points_and_knots(self):
        empty = [
            (fixture, r["handle"]) for fixture, r in all_splines() if not ctrl(r) or not knots(r)
        ]
        assert not empty, f"Spline records with no control points or no knots: {empty}"


class TestTheDerivationIsNotAGuess:
    """Why the shim may build the control points: two fit points and two end
    tangents fix a cubic, so the result is the AC1018 save's, not a plausible one.

    This is true before the fix and stays true after it. It is what separates
    deriving the points from the guess the issue warned about.
    """

    def test_the_stored_fit_data_gives_the_other_saves_control_points(self):
        (x0, y0), (x1, y1) = FIT_POINTS
        derived = [
            (x0, y0),
            (x0 + START_TANGENT[0] / 3, y0 + START_TANGENT[1] / 3),
            (x1 - END_TANGENT[0] / 3, y1 - END_TANGENT[1] / 3),
            (x1, y1),
        ]
        expected = ctrl(spline(REAL_18, FIT_SPLINE))
        assert len(expected) == 4
        for i, (p, q) in enumerate(zip(derived, expected)):
            assert abs(p[0] - q[0]) < TOL and abs(p[1] - q[1]) < TOL, (
                f"control point {i}: {p} against {q}"
            )


@pytest.mark.parametrize("fixture", [REAL_18, REAL_32])
def test_the_spline_is_recorded_in_both_dumps(fixture):
    assert spline(fixture, FIT_SPLINE)["kind"] == "Spline"
