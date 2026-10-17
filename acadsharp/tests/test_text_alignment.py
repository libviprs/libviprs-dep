"""A Text record carries its alignment, its width factor and its style (wire 3).

libviprs/acadsharp-rs-tests#66. Wire 2's record 10 had a position, a height, a
rotation and the string. Schema 1's text has the alignment pair, the style name
and the width factor, and anchors at the alignment point for anything but left
on the baseline, which is where a viewer puts it. The parity run found the
anchor 1.72 units out in y on a top-aligned multiline attribute, and eleven
texts out on a second drawing, because the wire always sent the insertion point.

``g13_text_aligned.dwg`` is built so each of those is separately visible: every
aligned text has an alignment point that is not its insertion point, one of each
half of the pair moves the anchor on its own, MTEXT carries its attachment point
as a pair, and a text inside a stretched block has its width factor follow the
stretch.

pytest here has no .NET (ADR 0001), so the dump read here is a recording of the
shim, and ``test_shim_digest.py`` refuses a stale one.
"""

import re

import pytest
from g13_support import records

FIXTURE = "g13_text_aligned.dwg"
TOL = 1e-6

FIELDS = re.compile(
    r"p=\[\(([-0-9.]+),([-0-9.]+),([-0-9.]+)\)\] h=([-0-9.]+) rot=([-0-9.]+)"
    r' halign=(\S+) valign=(\S+) wf=([-0-9.]+) style="((?:[^"\\]|\\.)*)"'
    r' value="((?:[^"\\]|\\.)*)"$'
)


def texts():
    found = {}
    for r in records(FIXTURE):
        if r["kind"] != "Text":
            continue
        m = FIELDS.search(r["rest"])
        assert m, f"a Text record with no alignment, width factor and style: {r['rest']!r}"
        found[m.group(10)] = {
            "p": tuple(float(m.group(i)) for i in (1, 2, 3)),
            "h": float(m.group(4)),
            "halign": m.group(6),
            "valign": m.group(7),
            "wf": float(m.group(8)),
            "style": m.group(9),
        }
    return found


class TestTheAnchorIsWhereAViewerPutsTheText:
    def test_left_on_the_baseline_anchors_at_the_insertion_point(self):
        t = texts()["left baseline"]
        assert t["p"] == pytest.approx((10.0, 10.0, 0.0), abs=TOL)
        assert (t["halign"], t["valign"]) == ("left", "baseline")

    def test_centre_bottom_anchors_at_the_alignment_point(self):
        t = texts()["centre bottom"]
        assert t["p"] == pytest.approx((26.0, 23.0, 0.0), abs=TOL), (
            "the insertion point is (20, 20), and DXF keeps the drawn position in the "
            "alignment point for this alignment"
        )
        assert (t["halign"], t["valign"]) == ("center", "bottom")

    def test_right_top_anchors_at_the_alignment_point(self):
        t = texts()["right top"]
        assert t["p"] == pytest.approx((31.0, 41.0, 0.0), abs=TOL)
        assert (t["halign"], t["valign"]) == ("right", "top")

    def test_either_half_of_the_pair_alone_moves_the_anchor(self):
        left_middle = texts()["left middle"]
        assert left_middle["p"] == pytest.approx((52.0, 54.0, 0.0), abs=TOL)
        assert (left_middle["halign"], left_middle["valign"]) == ("left", "middle")

        middle_baseline = texts()["middle baseline"]
        assert middle_baseline["p"] == pytest.approx((65.0, 61.0, 0.0), abs=TOL)
        assert (middle_baseline["halign"], middle_baseline["valign"]) == ("middle", "baseline")

    def test_mtext_carries_its_attachment_point_as_a_pair(self):
        t = texts()["mtext bottom right"]
        assert t["p"] == pytest.approx((40.0, 40.0, 0.0), abs=TOL)
        assert (t["halign"], t["valign"]) == ("right", "bottom")
        assert t["wf"] == pytest.approx(1.0, abs=TOL), "MTEXT has no width factor of its own"


class TestTheWidthFactorAndTheStyleCross:
    def test_the_stored_width_factor_and_style_name_cross(self):
        t = texts()["centre bottom"]
        assert t["wf"] == pytest.approx(0.8, abs=TOL)
        assert t["style"] == "VIPRS_G13_NARROW"

    def test_a_text_with_no_choices_made_has_the_defaults(self):
        t = texts()["left baseline"]
        assert t["wf"] == pytest.approx(1.0, abs=TOL)
        assert t["style"] == "Standard"

    def test_a_stretch_moves_the_width_factor_with_the_baseline(self):
        # The block holds a text of height 2 and width factor 0.5, inserted
        # with X scaled by 3 and Y by 1. The baseline came out three times
        # longer than the up direction, so the factor leaves as 1.5 and the
        # height is the Y scale's.
        t = texts()["stretched"]
        assert t["wf"] == pytest.approx(1.5, abs=TOL)
        assert t["h"] == pytest.approx(2.0, abs=TOL)
