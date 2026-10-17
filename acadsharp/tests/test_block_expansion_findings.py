"""What block expansion leaves out and puts in, against a second reader.

Three findings from the parity run in libviprs/acadsharp-rs-tests, each one a
place where this wire and the schema's reference stream disagreed about a
drawing's contents rather than about how to spell it:

* an ATTDEF inside a block was emitted as text when the block was inserted,
  although it is the template for an attribute and the insertion's own ATTRIBs
  are what the instance shows (acadsharp-rs-tests#54). The ATTRIBs also came
  before the block's body where a viewer paints them after it;
* the inserts inside a dimension's generated block were skipped, and that is
  where the arrowheads are (acadsharp-rs-tests#55);
* an entity the drawing marks invisible was drawn, and so was everything an
  invisible INSERT would have expanded into (acadsharp-rs-tests#60).

Each fixture carries its own controls (the things that must still be there), so
a fix that drops too much fails as loudly as one that drops too little. The
values are worked out from the fixture's constructor arguments in
`tests/fixtures/gen/Corpus.cs`, not read back from the dump.
"""

import re

import pytest
from g13_support import records

ATTDEF = "g13_attdef_block.dwg"
ARROW = "g13_dimension_arrow.dwg"
INVISIBLE = "g13_invisible.dwg"

TOL = 1e-5


def drawn(fixture):
    """Geometry and text only: the reader's own notifications are not under test."""
    return [r for r in records(fixture) if r["kind"] != "Warning"]


def of_kind(fixture, kind):
    return [r for r in drawn(fixture) if r["kind"] == kind]


def text_value(rest):
    m = re.search(r' value="((?:[^"\\]|\\.)*)"', rest)
    assert m, rest
    return m.group(1)


def line_ends(rest):
    m = re.search(r" pts=\[\(([-0-9.]+),([-0-9.]+),([-0-9.]+)\);\(([-0-9.]+),([-0-9.]+),([-0-9.]+)\)\]", rest)
    assert m, rest
    v = [float(x) for x in m.groups()]
    return (v[0], v[1], v[2]), (v[3], v[4], v[5])


def radius(rest):
    m = re.search(r" r=([-0-9.]+)", rest)
    assert m, rest
    return float(m.group(1))


class TestAnAttdefInsideABlock:
    """acadsharp-rs-tests#54."""

    def test_the_template_is_not_drawn_when_the_block_is_inserted(self):
        values = [text_value(r["rest"]) for r in of_kind(ATTDEF, "Text")]
        assert "the default" not in values, values

    def test_the_insertions_own_attribute_is_what_the_instance_shows(self):
        texts = of_kind(ATTDEF, "Text")
        instance = [r for r in texts if text_value(r["rest"]) == "the instance value"]
        assert len(instance) == 1, [text_value(r["rest"]) for r in texts]
        assert instance[0]["flags"] == 0

    def test_a_definition_at_the_top_of_the_view_is_still_text(self):
        # The control for "skipped everywhere": only the ones inside a block go.
        top = [r for r in of_kind(ATTDEF, "Text") if text_value(r["rest"]) == "top level definition"]
        assert len(top) == 1
        assert top[0]["flags"] == 0

    def test_nothing_else_is_text(self):
        assert len(of_kind(ATTDEF, "Text")) == 2

    def test_the_attribute_comes_after_the_block_body(self):
        # The block's line is the one record with the from-block bit set.
        body = [r for r in drawn(ATTDEF) if r["kind"] == "Line" and r["flags"] == 1]
        assert len(body) == 1
        instance = [
            r
            for r in of_kind(ATTDEF, "Text")
            if text_value(r["rest"]) == "the instance value"
        ][0]
        assert body[0]["index"] < instance["index"], (body[0]["index"], instance["index"])


class TestTheInsertsInsideADimensionsBlock:
    """acadsharp-rs-tests#55."""

    def lines(self):
        return [line_ends(r["rest"]) for r in of_kind(ARROW, "Line")]

    def has_line(self, a, b):
        for p, q in self.lines():
            if all(abs(x - y) < TOL for x, y in zip(p + q, a + b)):
                return True
        return False

    def test_the_first_arrowhead_is_drawn_at_its_insertion_point(self):
        # Insert at (0, 3), unscaled: both strokes start there.
        assert self.has_line((0.0, 3.0, 0.0), (-1.0, 3.25, 0.0))
        assert self.has_line((0.0, 3.0, 0.0), (-1.0, 2.75, 0.0))

    def test_the_second_arrowhead_follows_its_inserts_transform(self):
        # Insert at (10, 3), rotated half a turn and scaled by two: the stroke
        # (-1, +-0.25) becomes (+2, -+0.5) from the insertion point.
        assert self.has_line((10.0, 3.0, 0.0), (12.0, 2.5, 0.0))
        assert self.has_line((10.0, 3.0, 0.0), (12.0, 3.5, 0.0))

    def test_the_arrowheads_are_marked_as_coming_from_a_block(self):
        starts = ((0.0, 3.0, 0.0), (10.0, 3.0, 0.0))
        arrows = [
            r
            for r in of_kind(ARROW, "Line")
            if any(
                all(abs(x - y) < TOL for x, y in zip(line_ends(r["rest"])[0], s))
                for s in starts
            )
        ]
        assert len(arrows) == 4, len(arrows)
        assert all(r["flags"] == 1 for r in arrows)

    def test_the_dimension_line_and_its_text_are_still_there(self):
        # Four strokes of arrowhead are additions to what the dimension already
        # drew, not a replacement for it.
        assert len(of_kind(ARROW, "Text")) >= 1
        assert len(of_kind(ARROW, "Line")) >= 4 + 1


class TestAnInvisibleEntity:
    """acadsharp-rs-tests#60."""

    def test_an_invisible_line_is_not_drawn(self):
        lines = of_kind(INVISIBLE, "Line")
        assert len(lines) == 1, [line_ends(r["rest"]) for r in lines]
        a, b = line_ends(lines[0]["rest"])
        assert a == pytest.approx((0.0, 0.0, 0.0), abs=TOL)

    def test_an_invisible_entity_inside_a_block_is_not_drawn(self):
        radii = sorted(radius(r["rest"]) for r in of_kind(INVISIBLE, "Circle"))
        assert 2.0 not in [round(x, 6) for x in radii], radii

    def test_the_visible_one_beside_it_is_drawn(self):
        radii = [round(radius(r["rest"]), 6) for r in of_kind(INVISIBLE, "Circle")]
        assert 1.0 in radii, radii

    def test_an_invisible_insert_takes_its_block_with_it(self):
        radii = [round(radius(r["rest"]), 6) for r in of_kind(INVISIBLE, "Circle")]
        assert 3.0 not in radii, radii

    def test_exactly_one_circle_survives(self):
        assert len(of_kind(INVISIBLE, "Circle")) == 1
