"""A fit-defined spline the shim cannot fit says so (#155).

#150 sends the control points of a cubic with exactly two fit points and both
end tangents, because that one is a Bezier segment and was checked against the
same drawing saved as AC1018. Every other fit-defined spline still crossed as a
Spline record with no knots and no control points, and nothing said so.

Three or more fit points need a parametrisation (chord, square root or uniform)
and a tangent scaling, and the corpus holds no drawing that carries both the fit
data and the control points AutoCAD derived from it for more than two points: the
only pair is spline 434 of the sample drawing, and with two fit points every
parametrisation gives the same Bezier segment, so it cannot tell them apart.
Inventing the fit would send a plausible wrong curve, so the shim leaves the
record as the reader gave it and adds warning 117 beside it, under the same
handle. These specs hold that, on `g13_fit_spline.dwg`:

 0  3 fit points, chord, both tangents          warns
 1  4 fit points, square root, both tangents    warns
 2  10 fit points, uniform, both tangents       warns
 3  5 fit points, chord, closed, no tangents    warns
 4  3 fit points, chord, no tangents            warns
 5  2 fit points, both tangents                 does not warn (#150's Bezier)
 6  2 fit points, no tangents                   warns (nothing to build it from)
 7  a control-point spline                      does not warn

pytest here has no .NET (ADR 0001), so the dump read here is a recording of the
shim and `test_shim_digest.py` refuses a stale one.
"""

import os
import re

import pytest
from g13_support import ACAD_ROOT, records, warnings

FIXTURE = "g13_fit_spline.dwg"
CODE = "FIT_SPLINE_NOT_FITTED"

# (fit points, parametrisation as the message spells it, warns)
EXPECTED = [
    (3, "chord", True),
    (4, "square root", True),
    (10, "uniform", True),
    (5, "chord", True),
    (3, "chord", True),
    (2, "uniform", False),
    (2, "uniform", True),
    (None, None, False),
]

WARNING_CODES_CS = os.path.join(ACAD_ROOT, "native", "Adapter", "WarningCodes.cs")
WIRE_MD = os.path.join(ACAD_ROOT, "docs", "WIRE.md")


def splines():
    return [r for r in records(FIXTURE) if r["kind"] == "Spline"]


def fit_warnings():
    return [w for w in warnings(FIXTURE) if w["code"] == CODE]


def read(path):
    with open(path) as f:
        return f.read()


class TestTheFixtureIsWhatTheSpecsAssume:
    def test_it_holds_eight_splines(self):
        assert len(splines()) == len(EXPECTED), (
            f"{FIXTURE} decoded to {len(splines())} Spline records, the specs are written for "
            f"{len(EXPECTED)}"
        )

    def test_the_unfitted_ones_still_cross_empty(self):
        # The record is left as the reader gave it. Dropping it would be a second
        # behaviour change under a warning that only claims the first.
        for i in (0, 1, 2, 3, 4, 6):
            rest = splines()[i]["rest"]
            assert "knots=[]" in rest and "ctrl=[]" in rest, f"spline {i}: {rest}"

    def test_the_two_point_cubic_with_tangents_is_still_fitted(self):
        rest = splines()[5]["rest"]
        assert "knots=[0.000000,0.000000,0.000000,0.000000,1.000000" in rest, rest

    def test_the_control_point_spline_is_untouched(self):
        assert "ctrl=[(" in splines()[7]["rest"]


class TestEachUnfittedSplineWarns:
    @pytest.mark.parametrize("i", [i for i, e in enumerate(EXPECTED) if e[2]])
    def test_there_is_a_warning_under_its_handle(self, i):
        handle = splines()[i]["handle"]
        found = [w for w in fit_warnings() if w["handle"] == handle]
        assert len(found) == 1, (
            f"spline {i} (handle {handle}) has {len(found)} {CODE} warnings, expected one. "
            "A fit spline that crosses empty vanishes without a word otherwise"
        )

    @pytest.mark.parametrize("i", [i for i, e in enumerate(EXPECTED) if e[2]])
    def test_the_message_says_how_many_fit_points_and_which_parametrisation(self, i):
        count, param, _ = EXPECTED[i]
        handle = splines()[i]["handle"]
        message = next(w["message"] for w in fit_warnings() if w["handle"] == handle)
        assert f"{count} fit points" in message, message
        assert param in message, message


class TestNothingElseWarns:
    @pytest.mark.parametrize("i", [i for i, e in enumerate(EXPECTED) if not e[2]])
    def test_a_spline_that_crossed_whole_has_no_warning(self, i):
        handle = splines()[i]["handle"]
        assert not [w for w in fit_warnings() if w["handle"] == handle], (
            f"spline {i} crosses with its control points and must not warn"
        )

    def test_the_total_is_exactly_the_unfitted_count(self):
        assert len(fit_warnings()) == sum(1 for e in EXPECTED if e[2])


class TestTheCodeIsDefinedAndDocumented:
    def test_the_shim_defines_117(self):
        assert re.search(r"FitSplineNotFitted = 117u;", read(WARNING_CODES_CS))

    def test_the_shim_names_it(self):
        assert re.search(
            r'case FitSplineNotFitted: return "FIT_SPLINE_NOT_FITTED";', read(WARNING_CODES_CS)
        )

    def test_wire_md_has_a_row_for_it(self):
        assert re.search(r"^\|\s*117\s*\|\s*`FIT_SPLINE_NOT_FITTED`\s*\|", read(WIRE_MD), re.M)

    def test_wire_md_says_what_reference_data_would_finish_it(self):
        text = read(WIRE_MD)
        assert "libviprs-dep#155" in text
        assert "AC1018" in text and "three or more fit points" in text, (
            "the Spline section has to say which drawing is missing, or the next reader "
            "starts from the same question"
        )
