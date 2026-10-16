"""IMAGE and PDFUNDERLAY become record 16, `Placement` (J1.4 / #137).

Both name something that is not in the drawing, and ADR 0002 refused them
because this decoder opens no path a drawing names. That policy has not moved
and this file holds it: every name in `g13_placement.dwg` is an absolute path
into a directory that does not exist, and each comes out of the dump exactly as
the drawing spelled it, non-ASCII character included, with nothing having gone
looking. What the refusal also threw away was the drawing's own geometry, the
frame and its clip, and that is what these assert.

The frames are shaped for the wrong implementations. The polygonal clip is
asymmetric both ways because pixel rows run the opposite way from V, so a clip
symmetric about the middle cannot tell a flipped row from an unflipped one. The
rectangular clip stores two corners and means four. The underlays are rotated
and scaled differently along each axis, and the block half is inserted
mirrored.
"""

import math
import os
import re

import pytest
import wire3_reference as ref
from g13_support import kinds, records

FIXTURE = "g13_placement.dwg"
NONFINITE = "g13_nonfinite_records.dwg"
REAL = "real_AC1032.dwg"
ACAD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
ARM = os.path.join(ACAD, "native", "Adapter", "Flatten.Placements.cs")

RASTER = "/viprs-g13-never-opened/dräwing raster.png"
SHEET = "/viprs-g13-never-opened/sheet.pdf"


def unquote(raw):
    out, i = [], 0
    while i < len(raw):
        if raw[i] != "\\":
            out.append(raw[i])
            i += 1
        elif raw[i + 1] == "u":
            out.append(chr(int(raw[i + 2 : i + 6], 16)))
            i += 6
        else:
            out.append({"\\": "\\", '"': '"', "n": "\n", "r": "\r", "t": "\t"}[raw[i + 1]])
            i += 2
    return "".join(out)


def placements(fixture):
    out = []
    for r in records(fixture):
        if r["kind"] != "Placement":
            continue
        rest = r["rest"]
        out.append(
            {
                "flags": r["flags"],
                "i": ref.triple(rest, "i"),
                "u": ref.triple(rest, "u"),
                "v": ref.triple(rest, "v"),
                "size": ref.plain_pair(rest, "size"),
                "kind": re.search(r" kind=(\w+)", rest).group(1),
                "clip": ref.pairs(rest, "clip"),
                "name": unquote(re.search(r' name="((?:[^"\\]|\\.)*)"$', rest).group(1)),
            }
        )
    return out


class TestTheFixtureBecameRecords:
    def test_ten_frames_at_top_level_and_ten_from_the_insertions(self):
        got = placements(FIXTURE)
        assert kinds(FIXTURE).get("Placement") == 15, kinds(FIXTURE)
        assert [p["flags"] for p in got] == [0] * 5 + [1] * 10

    def test_three_images_and_two_underlays_in_each_half(self):
        assert [p["kind"] for p in placements(FIXTURE)[:5]] == ["image"] * 3 + ["pdf"] * 2


class TestNothingWasOpened:
    def test_the_names_are_exactly_what_the_drawing_spells(self):
        names = [p["name"] for p in placements(FIXTURE)[:5]]
        assert names == [RASTER] * 3 + [SHEET] * 2, names

    def test_the_arm_cannot_open_a_file(self):
        with open(ARM) as f:
            code = re.sub(r"//[^\n]*", "", f.read())
        for token in ("System.IO", "File.", "FileInfo", "Directory", "Path.", "Process", "Stream"):
            assert token not in code, (
                f"Flatten.Placements.cs mentions {token}. The name is an opaque string and "
                "nothing on this boundary opens it"
            )


class TestAnImageFrame:
    def test_the_unclipped_control(self):
        p = placements(FIXTURE)[0]
        assert ref.close(p["i"], (0.0, 0.0, 0.0))
        assert ref.close(p["u"], (50.0, 0.0, 0.0)), "100 pixels of 0.5 span 50 units"
        assert ref.close(p["v"], (0.0, 30.0, 0.0)), "60 pixels of 0.5 span 30 units"
        assert p["size"] == (100.0, 60.0)
        assert p["clip"] == []

    def test_a_turned_frame_with_a_polygonal_clip(self):
        p = placements(FIXTURE)[1]
        assert ref.close(p["i"], (100.0, 0.0, 0.0))
        assert ref.close(p["u"], (24.0, 32.0, 0.0))
        assert ref.close(p["v"], (-16.0, 12.0, 0.0))
        assert p["size"] == (80.0, 40.0)
        # x = (px + 0.5) / 80, y = (40 - py - 0.5) / 40.
        want = [(0.0, 1.0), (0.7625, 0.73125), (0.259375, 0.0)]
        assert ref.close(p["clip"], want), f"{p['clip']} against {want}"

    def test_a_rectangular_clip_means_four_corners(self):
        p = placements(FIXTURE)[2]
        assert p["size"] == (80.0, 40.0)
        want = [(0.0, 1.0), (0.5125, 1.0), (0.5125, 0.5), (0.0, 0.5)]
        assert ref.close(p["clip"], want), f"{p['clip']} against {want}"

    def test_the_clip_of_a_whole_frame_stays_inside_it(self):
        for p in placements(FIXTURE):
            if p["kind"] != "image":
                continue
            for x, y in p["clip"]:
                assert -1e-9 <= x <= 1 + 1e-9 and -1e-9 <= y <= 1 + 1e-9, p


class TestAnUnderlayFrame:
    def test_the_size_is_unknown_and_the_axes_are_per_unit(self):
        p = placements(FIXTURE)[3]
        assert p["size"] == (0.0, 0.0)
        c, s = math.cos(0.5), math.sin(0.5)
        assert ref.close(p["i"], (300.0, 0.0, 0.0))
        assert ref.close(p["u"], (2 * c, 2 * s, 0.0)), p["u"]
        assert ref.close(p["v"], (-3 * s, 3 * c, 0.0)), p["v"]
        assert p["clip"] == [(0.0, 0.0), (100.0, 0.0), (50.0, 80.0)]

    def test_a_tilted_plane_is_turned_by_its_own_axes(self):
        p = placements(FIXTURE)[4]
        ox = ref.arbitrary_axis_x((1.0, 2.0, 2.0))
        oy = ref.cross(ref.unit((1.0, 2.0, 2.0)), ox)
        assert ref.close(p["i"], (400.0, 0.0, 2.0))
        assert ref.close(p["u"], ox), p["u"]
        assert ref.close(p["v"], oy), p["v"]
        assert p["clip"] == []


class TestTheInsertionsAreFollowed:
    @pytest.mark.parametrize(
        "insertion,start", [(ref.SHEARED, 5), (ref.MIRRORED, 10)], ids=["non-uniform", "mirrored"]
    )
    def test_each_expanded_frame_is_the_top_level_one_moved(self, insertion, start):
        top = placements(FIXTURE)[:5]
        got = placements(FIXTURE)[start : start + 5]
        for t, g in zip(top, got):
            # The block copy sits 1000 up from the top-level one and is then
            # inserted at (40, 500) or (80, 500), so compare the frame vectors,
            # which do not depend on where it is.
            assert g["kind"] == t["kind"] and g["name"] == t["name"] and g["size"] == t["size"]
            assert g["clip"] == t["clip"], "a clip is in the frame's own coordinates"
            assert ref.close(g["u"], insertion.vector(t["u"])), f"{g['u']} for {t['u']}"
            assert ref.close(g["v"], insertion.vector(t["v"])), f"{g['v']} for {t['v']}"


class TestAValueThatIsNotANumber:
    def test_the_frame_is_a_warning_and_the_good_one_crosses(self):
        got = placements(NONFINITE)
        assert [p["kind"] for p in got] == ["image", "pdf"], got
        assert [p["name"] for p in got] == [
            "/viprs-g13-never-opened/nonfinite.png",
            "/viprs-g13-never-opened/nonfinite.pdf",
        ]


class TestTheRealDrawing:
    def test_its_image_and_its_underlay_cross(self):
        assert [p["kind"] for p in placements(REAL)] == ["image", "pdf"], kinds(REAL)

    def test_neither_is_refused_any_more(self):
        left = [
            r
            for r in records(REAL)
            if r["kind"] == "Warning"
            and (' message="IMAGE ' in r["rest"] or ' message="PDFUNDERLAY ' in r["rest"])
        ]
        assert not left, left
