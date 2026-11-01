"""Layer, layer state, block name and instance identity on the wire (#156).

A geometry record used to say where it was and which handle made it. It did not
say which layer it sat on, whether that layer was on or frozen, which block it
came out of or which expansion of that block it was, and the last of those is
the one a handle cannot stand in for: every instance of a block repeats the
leaf's own handle. libviprs's Epic N (libviprs/libviprs#1239) has to apply the
drawing's own visibility rules, so the information has to cross.

The shape these tests pin down, all inside wire 3 and none of it a reshuffle of
a layout that already exists:

* record 17 `Layer`, one per layer of the document, written once per view just
  after `ViewBegin`: an id (1-based), a state word (bit 0 on, bit 1 frozen) and
  the name;
* the geometry prologue's `reserved0` becomes `layer_id`, an id into that table
  (0 when the producer does not know);
* record 18 `InstanceBegin` and record 19 `InstanceEnd` bracket the records one
  expansion of an INSERT produces. Begin carries the instance index (1-based,
  counted per view and per array cell, in expansion order), the INSERT's own
  layer id and the block's name. Nesting is the chain, so a record's block and
  its instance are the innermost open bracket.

`g13_layers.dwg` is the fixture: an off layer, a frozen layer, one block
inserted three times (twice at top level, once through another block), so the
same leaf handle shows up under three different instances.
"""

import os
import re

import pytest
from g13_support import records as _records

ACAD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
WIRE_FORMAT = os.path.join(ACAD, "native", "Wire", "WireFormat.cs")
WIRE_MD = os.path.join(ACAD, "docs", "WIRE.md")
C_CONSUMER = os.path.join(ACAD, "tests", "conformance", "c", "conformance.c")
RUST_WIRE = os.path.join(ACAD, "tests", "conformance", "rust", "src", "wire.rs")
RUST_PAYLOAD = os.path.join(ACAD, "tests", "conformance", "rust", "src", "payload.rs")

LAYERS = "g13_layers.dwg"
REAL = "real_AC1032.dwg"

ON = 1
FROZEN = 2

GEOMETRY = {
    "Line",
    "Polyline",
    "Arc",
    "Circle",
    "Ellipse",
    "Spline",
    "Polygon",
    "Text",
    "Point",
    "Unbounded",
    "Placement",
}


def records(fixture):
    """The whole stream, view context included."""
    return _records(fixture, context=True)


def read(path):
    with open(path) as f:
        return f.read()


def field(rest, name):
    m = re.search(rf"\b{name}=(\d+)", rest)
    assert m, f"no {name}= in {rest!r}"
    return int(m.group(1))


def quoted(rest, name):
    m = re.search(rf'\b{name}="((?:[^"\\]|\\.)*)"', rest)
    assert m, f"no {name}= in {rest!r}"
    return m.group(1)


def layer_table(fixture):
    table = {}
    for r in records(fixture):
        if r["kind"] == "Layer":
            table[field(r["rest"], "id")] = (field(r["rest"], "lflags"), quoted(r["rest"], "name"))
    return table


class TestTheTypesExist:
    def test_the_numbers_are_17_18_and_19(self):
        src = read(WIRE_FORMAT)
        for name, number in (("Layer", 17), ("InstanceBegin", 18), ("InstanceEnd", 19)):
            assert re.search(
                rf"public const ushort Type{name}\s*=\s*{number};", src
            ), f"WireFormat.cs has no Type{name} = {number}"

    def test_none_of_them_is_geometry(self):
        src = read(WIRE_FORMAT)
        m = re.search(r"static bool IsGeometry\(ushort type\)\s*\{(.*?)\n\t\t\}", src, re.S)
        assert m
        for name in ("Layer", "InstanceBegin", "InstanceEnd"):
            assert f"Type{name}" not in m.group(1), (
                f"{name} is a table or a bracket and carries no coordinates, so the finiteness "
                "guarantee has nothing to say about it"
            )

    def test_the_document_defines_them(self):
        doc = read(WIRE_MD)
        for number, name in ((17, "Layer"), (18, "InstanceBegin"), (19, "InstanceEnd")):
            assert re.search(rf"\|\s*{number}\s*\|\s*`{name}`", doc), (
                f"docs/WIRE.md's record table has no row for {number} `{name}`"
            )
        assert "`layer_id`" in doc, "WIRE.md does not name the prologue's layer_id"

    def test_both_conformance_consumers_know_them(self):
        c = read(C_CONSUMER)
        for const in ("VACB_TYPE_LAYER", "VACB_TYPE_INSTANCE_BEGIN", "VACB_TYPE_INSTANCE_END"):
            assert const in c, f"conformance.c has no {const}"
        rust = read(RUST_WIRE)
        for const in ("TYPE_LAYER", "TYPE_INSTANCE_BEGIN", "TYPE_INSTANCE_END"):
            assert const in rust, f"the Rust consumer's wire.rs has no {const}"
        assert "TYPE_LAYER" in read(RUST_PAYLOAD), "payload.rs never reads a Layer record"


class TestTheFixtureStatesTheLayers:
    def test_the_layer_table_carries_the_four_states(self):
        by_name = {name: flags for flags, name in layer_table(LAYERS).values()}
        assert by_name["0"] == ON, by_name
        assert by_name["WALLS"] == ON, by_name
        assert by_name["HIDDEN"] == 0, f"an off layer is neither on nor frozen: {by_name}"
        assert by_name["FROZEN"] & FROZEN, "a frozen layer has bit 1 set"

    def test_ids_are_one_based_and_distinct(self):
        ids = sorted(layer_table(LAYERS))
        assert ids and ids[0] == 1 and ids == list(range(1, len(ids) + 1)), ids

    def test_the_table_comes_before_any_record_that_uses_it(self):
        seen_geometry = False
        for r in records(LAYERS):
            if r["kind"] in GEOMETRY:
                seen_geometry = True
            if r["kind"] == "Layer":
                assert not seen_geometry, "a Layer record arrived after geometry"


class TestEveryGeometryRecordNamesItsLayer:
    def geometry(self, fixture):
        return [r for r in records(fixture) if r["kind"] in GEOMETRY]

    def test_the_top_level_records_sit_on_the_layers_the_file_gave_them(self):
        table = layer_table(LAYERS)
        top = [r for r in self.geometry(LAYERS) if r["flags"] & 1 == 0]
        names = [table[field(r["rest"], "layer")][1] for r in top]
        assert names == ["HIDDEN", "FROZEN"], names

    def test_a_leaf_keeps_its_own_layer_not_its_inserts(self):
        table = layer_table(LAYERS)
        leaves = [r for r in self.geometry(LAYERS) if r["flags"] & 1]
        names = [table[field(r["rest"], "layer")][1] for r in leaves]
        # three expansions of a block holding a line on WALLS and a circle on 0
        assert names == ["WALLS", "0"] * 3, names

    @pytest.mark.parametrize("fixture", [LAYERS, REAL, "g13_insert.dwg", "g13_text.dwg"])
    def test_every_layer_id_resolves_to_a_layer_record(self, fixture):
        table = layer_table(fixture)
        assert table, f"{fixture} has no Layer records"
        for r in self.geometry(fixture):
            lid = field(r["rest"], "layer")
            assert lid in table, (
                f"{r['kind']} {r['handle']} names layer {lid}, which is not in {sorted(table)}"
            )


class TestInstancesAreToldApart:
    def stream(self):
        return [
            r for r in records(LAYERS) if r["kind"] in GEOMETRY | {"InstanceBegin", "InstanceEnd"}
        ]

    def test_the_brackets_balance_and_nest(self):
        stack = []
        for r in self.stream():
            if r["kind"] == "InstanceBegin":
                stack.append(field(r["rest"], "inst"))
            elif r["kind"] == "InstanceEnd":
                assert stack and stack.pop() == field(r["rest"], "inst"), r
        assert stack == [], f"instances left open: {stack}"

    def test_indices_count_expansions_from_one_in_order(self):
        begins = [field(r["rest"], "inst") for r in self.stream() if r["kind"] == "InstanceBegin"]
        assert begins == [1, 2, 3, 4], begins

    def test_every_begin_names_its_block_and_the_inserts_own_layer(self):
        table = layer_table(LAYERS)
        got = [
            (quoted(r["rest"], "block"), table[field(r["rest"], "layer")][1])
            for r in self.stream()
            if r["kind"] == "InstanceBegin"
        ]
        assert got == [
            ("VIPRS_G13_UNIT", "HIDDEN"),
            ("VIPRS_G13_UNIT", "WALLS"),
            ("VIPRS_G13_OUTER", "WALLS"),
            ("VIPRS_G13_UNIT", "0"),
        ], got

    def test_the_same_leaf_handle_lands_under_three_different_instances(self):
        owner = []
        stack = []
        for r in self.stream():
            if r["kind"] == "InstanceBegin":
                stack.append(field(r["rest"], "inst"))
            elif r["kind"] == "InstanceEnd":
                stack.pop()
            elif r["kind"] == "Line" and stack:
                owner.append((r["handle"], stack[-1]))
        handles = {h for h, _ in owner}
        assert len(handles) == 1, f"one block, one line, one handle: {owner}"
        assert [i for _, i in owner] == [1, 2, 4], (
            f"{owner}: the handle repeats, the innermost instance is what tells the three apart"
        )

    def test_a_nested_expansion_sits_inside_its_parent(self):
        depth = 0
        max_depth = 0
        for r in self.stream():
            if r["kind"] == "InstanceBegin":
                depth += 1
                max_depth = max(max_depth, depth)
            elif r["kind"] == "InstanceEnd":
                depth -= 1
        assert max_depth == 2, "OUTER holds an INSERT of UNIT, so the brackets nest two deep"

    def test_top_level_records_are_outside_every_bracket(self):
        depth = 0
        for r in self.stream():
            if r["kind"] == "InstanceBegin":
                depth += 1
            elif r["kind"] == "InstanceEnd":
                depth -= 1
            elif r["kind"] in GEOMETRY:
                assert (depth > 0) == bool(r["flags"] & 1), (
                    f"flags bit 0 and the brackets disagree about {r}"
                )


class TestARealDrawing:
    def test_it_has_a_layer_table_with_the_drawings_own_layers(self):
        names = {name for _, name in layer_table(REAL).values()}
        assert "0" in names and len(names) > 1, names
