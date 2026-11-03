"""A Polygon says whether the region inside it is filled (libviprs-dep#165).

libviprs cannot fill every record 9: a patterned hatch would black out, and a
face or a mask is not a fill at all. The fill word on the record tells the three
apart, and loops that share a handle are one even-odd region.

pytest here has no .NET (ADR 0001), so the dumps read here are recordings of the
shim, and ``test_shim_digest.py`` refuses a stale one.
"""

import os
import re

import pytest
from g13_support import ACAD_ROOT, records

FILL = re.compile(r" fill=(\S+) ")


def polygons(fixture):
    return [r for r in records(fixture) if r["kind"] == "Polygon"]


def fill_of(record):
    m = FILL.search(record["rest"])
    assert m, f"a Polygon with no fill word: {record['rest']!r}"
    return m.group(1)


class TestEveryPolygonNamesItsFill:
    @pytest.mark.parametrize(
        "fixture",
        ("g13_hatch.dwg", "g13_hatch_fill.dwg", "g13_solid.dwg", "g13_face3d.dwg", "g13_mesh.dwg"),
    )
    def test_the_dump_carries_a_fill_on_every_polygon(self, fixture):
        found = polygons(fixture)
        assert found, f"{fixture} has no Polygon to read a fill from"
        for r in found:
            assert fill_of(r) in ("none", "solid", "pattern")


class TestSolidHatchesAndSolidEntities:
    def test_a_solid_hatch_loop_is_solid(self):
        assert {fill_of(r) for r in polygons("g13_hatch.dwg")} == {"solid"}

    def test_a_2d_solid_entity_is_solid(self):
        assert {fill_of(r) for r in polygons("g13_solid.dwg")} == {"solid"}


class TestFacesAreNotFills:
    @pytest.mark.parametrize("fixture", ("g13_face3d.dwg", "g13_mesh.dwg"))
    def test_a_face_has_no_fill(self, fixture):
        assert {fill_of(r) for r in polygons(fixture)} == {"none"}


class TestPatternAndIsland:
    def test_a_patterned_hatch_loop_is_pattern(self):
        fills = [fill_of(r) for r in polygons("g13_hatch_fill.dwg")]
        assert fills.count("pattern") == 1

    def test_the_island_and_its_outer_loop_share_a_handle_and_a_fill(self):
        found = polygons("g13_hatch_fill.dwg")
        solid_hatch = [r for r in found if fill_of(r) == "solid"][:2]
        assert len(solid_hatch) == 2
        assert solid_hatch[0]["handle"] == solid_hatch[1]["handle"]

    def test_the_patterned_hatch_is_not_the_islands_handle(self):
        found = polygons("g13_hatch_fill.dwg")
        by_fill = {fill_of(r): r["handle"] for r in found}
        assert by_fill["pattern"] != by_fill["solid"]


class TestTheDocumentSaysSo:
    def test_wire_md_names_the_fill_word_and_the_even_odd_rule(self):
        with open(os.path.join(ACAD_ROOT, "docs", "WIRE.md")) as f:
            text = f.read()
        assert "The fill word" in text
        assert "even-odd" in text
        for name in ("none", "solid", "pattern"):
            assert f"| {name} |" in text
