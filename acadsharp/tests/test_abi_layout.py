"""Struct layout is the part of an ABI that fails silently.

A field in the wrong place does not fail to compile on either side and does
not throw. It reads the neighbouring field's bytes, and a `uint64_t` read
where a `double` lives comes back as a perfectly plausible number. That is
why the epic asks for `size_of` and `offset_of` on every struct in the
consumers rather than trusting a shared definition.

This module is the third statement of the same fact, and the only one that
runs without a compiler. It computes the layout the header implies, by the C
rules, and compares it with the tables the two conformance consumers assert
against. Those tables are written out by hand on purpose: if they were
generated from the header alongside the struct definitions, reordering two
fields would move both and the test would pass.

It also checks that the managed structs carry the same fields in the same
order under the same names, because the shim is the one consumer that is not
generated from anything.
"""

import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ACADSHARP = os.path.dirname(HERE)
HEADER = os.path.join(ACADSHARP, "include", "viprs_acadsharp.h")
ABI_CS = os.path.join(ACADSHARP, "native", "Abi.cs")
C_TABLE = os.path.join(ACADSHARP, "tests", "conformance", "c", "layout_table.h")
RUST_TABLE = os.path.join(ACADSHARP, "tests", "conformance", "rust", "tests", "layout.rs")

STRUCTS = ("viprs_acad_limits_v1", "viprs_acad_capabilities_v1", "viprs_acad_view_info_v1")

# Width and alignment of every scalar the header is allowed to use. There are
# only four, which is the point of the fixed-width rule.
SCALARS = {"uint8_t": 1, "uint32_t": 4, "uint64_t": 8, "double": 8}

# The managed side spells the same fields in the same order. It uses the C
# names verbatim rather than the usual C# casing, so the two lists can be
# compared as written instead of through a naming convention nobody enforces.
CS_TYPES = {"uint8_t": "byte", "uint32_t": "uint", "uint64_t": "ulong", "double": "double"}


def header_text():
    with open(HEADER) as f:
        return f.read()


def strip_comments(text):
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", text, flags=re.S))


def header_fields(name):
    """[(ctype, field), ...] in declaration order."""
    code = strip_comments(header_text())
    m = re.search(rf"struct {name} \{{(.*?)\n\}};", code, re.S)
    assert m, f"{name} is not declared as a struct in the header"
    fields = []
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line:
            continue
        fm = re.fullmatch(r"(\w+)\s+(\w+);", line)
        assert fm, f"{name} has a member this parser does not understand: {line!r}"
        fields.append((fm.group(1), fm.group(2)))
    return fields


def c_layout(fields):
    """(size, align, [(field, offset, size), ...]) by the C rules.

    Natural alignment, tail padding to the struct's own alignment. Every
    scalar the header permits has alignment equal to its size on every target
    this ships to, which is what keeps this five lines long.
    """
    offset, align, out = 0, 1, []
    for ctype, field in fields:
        size = SCALARS[ctype]
        offset += (-offset) % size
        out.append((field, offset, size))
        offset += size
        align = max(align, size)
    return offset + (-offset) % align, align, out


@pytest.fixture(scope="module")
def computed():
    return {name: c_layout(header_fields(name)) for name in STRUCTS}


def parse_table(path, struct_re, field_re):
    structs, fields = {}, {}
    with open(path) as f:
        text = f.read()
    for m in re.finditer(struct_re, text):
        structs[m.group(1)] = (int(m.group(2)), int(m.group(3)))
    for m in re.finditer(field_re, text):
        fields.setdefault(m.group(1), []).append((m.group(2), int(m.group(3)), int(m.group(4))))
    return structs, fields


@pytest.fixture(scope="module")
def c_table():
    return parse_table(
        C_TABLE,
        r"VIPRS_LAYOUT_STRUCT\((\w+),\s*(\d+),\s*(\d+)\)",
        r"VIPRS_LAYOUT_FIELD\((\w+),\s*(\w+),\s*(\d+),\s*(\d+)\)",
    )


@pytest.fixture(scope="module")
def rust_table():
    return parse_table(
        RUST_TABLE,
        r"layout_struct!\((\w+),\s*(\d+),\s*(\d+)\)",
        r"layout_field!\((\w+),\s*(\w+),\s*(\d+),\s*(\d+)\)",
    )


class TestTheHeaderObeysItsOwnFixedWidthRule:
    def test_every_member_is_one_of_four_scalars(self):
        for name in STRUCTS:
            for ctype, field in header_fields(name):
                assert ctype in SCALARS, (
                    f"{name}.{field} is a {ctype}. Only {sorted(SCALARS)} cross this "
                    "boundary, because those are the only widths every target agrees on."
                )

    def test_no_struct_needs_internal_padding_the_header_did_not_ask_for(self, computed):
        # Implicit padding is not wrong, but it is a byte range neither side
        # writes and both sides read, so the header declares it as a reserved
        # field instead. This catches the one that was forgotten.
        for name in STRUCTS:
            expected = 0
            for field, offset, size in computed[name][2]:
                assert offset == expected, (
                    f"{name} has {offset - expected} implicit padding bytes before "
                    f"{field}. Declare them as a reserved member so both sides know "
                    "they are there and neither reads them by accident."
                )
                expected = offset + size


class TestTheCheckedInTablesMatchTheHeader:
    @pytest.mark.parametrize("name", STRUCTS)
    def test_the_c_table_agrees(self, name, computed, c_table):
        size, align, fields = computed[name]
        structs, table_fields = c_table
        assert name in structs, f"the C layout table says nothing about {name}"
        assert structs[name] == (size, align), (
            f"{name} is {size} bytes aligned {align} by the header, and the C table "
            f"records {structs[name]}. Either the header moved or the table is stale, "
            "and the two disagreeing is the whole failure mode this catches."
        )
        assert table_fields.get(name) == fields, (
            f"{name} field layout disagrees.\n  header: {fields}\n  C table: "
            f"{table_fields.get(name)}"
        )

    @pytest.mark.parametrize("name", STRUCTS)
    def test_the_rust_table_agrees(self, name, computed, rust_table):
        size, align, fields = computed[name]
        structs, table_fields = rust_table
        assert name in structs, f"the generated-bindings layout table says nothing about {name}"
        assert structs[name] == (size, align), (
            f"{name} is {size} bytes aligned {align} by the header, and the consumer's "
            f"table records {structs[name]}"
        )
        assert table_fields.get(name) == fields, (
            f"{name} field layout disagrees.\n  header: {fields}\n  table: {table_fields.get(name)}"
        )

    def test_both_tables_cover_every_struct_and_nothing_else(self, c_table, rust_table):
        for label, (structs, _fields) in (("C", c_table), ("generated", rust_table)):
            assert sorted(structs) == sorted(STRUCTS), (
                f"the {label} layout table covers {sorted(structs)}, not {sorted(STRUCTS)}. "
                "A struct with no assertion is a struct nobody would notice moving."
            )


class TestTheManagedStructsMatchTheHeader:
    @pytest.mark.parametrize("name", STRUCTS)
    def test_the_shim_declares_the_same_fields_in_the_same_order(self, name):
        with open(ABI_CS) as f:
            cs = f.read()
        m = re.search(rf"struct {name}\s*\{{(.*?)\n\t\}}", cs, re.S)
        assert m, f"native/Abi.cs declares no struct named {name}"
        declared = re.findall(r"public (\w+) (\w+);", m.group(1))
        expected = [(CS_TYPES[t], f) for t, f in header_fields(name)]
        assert declared == expected, (
            f"{name} in the shim is\n  {declared}\nand the header says\n  {expected}\n"
            "The shim is the one consumer nothing generates, so this is the only place "
            "a divergence shows up before a caller reads the wrong bytes."
        )

    @pytest.mark.parametrize("name", STRUCTS)
    def test_the_shim_pins_sequential_layout(self, name):
        with open(ABI_CS) as f:
            cs = f.read()
        before = cs[: cs.index(f"struct {name}")]
        attr = before.rsplit("[", 1)[-1]
        assert "StructLayout(LayoutKind.Sequential" in "[" + attr, (
            f"{name} has no explicit sequential layout attribute. The runtime is free to "
            "reorder a struct it was not told to keep in order, and it does not have to "
            "tell anyone."
        )
