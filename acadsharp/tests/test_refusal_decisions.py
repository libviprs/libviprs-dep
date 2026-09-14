"""A refusal this build decided on is written down in three places, and they agree.

`docs/adr/0002-what-this-decoder-refuses.md` is the decision, with a row per
entity kind saying what happens to it and what would reopen it.
`native/Adapter/RefusedKinds.cs` is the table the flattener actually consults.
`docs/WIRE.md` is what a consumer reads. Nothing in a compiler or a consumer
can tell you those three have drifted: a kind dropped from the C# table goes
back to warning 100 and every expectation still parses, and a row added to the
ADR with no code behind it reads exactly like a decision that shipped.

So this compares them the way `test_warning_codes.py` compares the shim's
enumeration against the specification, and for the same reason. It needs no
.NET and no fixture: all three are text.
"""

import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ACADSHARP = os.path.dirname(HERE)
ADR = os.path.join(ACADSHARP, "docs", "adr", "0002-what-this-decoder-refuses.md")
WIRE_MD = os.path.join(ACADSHARP, "docs", "WIRE.md")
REFUSED_CS = os.path.join(ACADSHARP, "native", "Adapter", "RefusedKinds.cs")
WARNING_CODES_CS = os.path.join(ACADSHARP, "native", "Adapter", "WarningCodes.cs")
FLATTENER_CS = os.path.join(ACADSHARP, "native", "Adapter", "Flattener.cs")
ABI_CS = os.path.join(ACADSHARP, "native", "Abi.cs")

# Which warning code each disposition has to carry. "refused" is a decision
# that will not expire and "deferred" is work nobody has done yet, and the
# whole point of code 109 is that a consumer can tell those apart without
# reading the message.
DISPOSITION_CODE = {
    "refused": "ENTITY_REFUSED_BY_DESIGN",
    "deferred": "UNSUPPORTED_ENTITY",
}


def read(path):
    if not os.path.isfile(path):
        return None
    with open(path) as f:
        return f.read()


@pytest.fixture(scope="module")
def adr():
    text = read(ADR)
    assert text is not None, f"{ADR} does not exist, and it is the decision itself"
    return text


@pytest.fixture(scope="module")
def decided(adr):
    """``{KIND: (disposition, code name)}`` out of the ADR's disposition table."""
    return {
        m.group(1): (m.group(2), m.group(3))
        for m in re.finditer(r"^\|\s*`([A-Z0-9]+)`\s*\|\s*(\w+)\s*\|\s*`([A-Z_]+)`\s*\|", adr, re.M)
    }


@pytest.fixture(scope="module")
def revisits(adr):
    """``{KIND: what would reopen it}``, the last cell of each disposition row."""
    out = {}
    for line in adr.splitlines():
        m = re.match(r"^\|\s*`([A-Z0-9]+)`\s*\|", line)
        if not m:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        out[m.group(1)] = cells[-1]
    return out


@pytest.fixture(scope="module")
def code_numbers():
    """``{C# constant: number}`` out of WarningCodes.cs."""
    source = read(WARNING_CODES_CS)
    return {m.group(1): int(m.group(2)) for m in re.finditer(r"(\w+) = (\d+)u;", source)}


@pytest.fixture(scope="module")
def code_names():
    """``{C# constant: wire name}`` out of WarningCodes.cs."""
    source = read(WARNING_CODES_CS)
    pattern = r'case (\w+): return "([A-Z0-9_]+)";'
    return {m.group(1): m.group(2) for m in re.finditer(pattern, source)}


@pytest.fixture(scope="module")
def refused_cs():
    text = read(REFUSED_CS)
    assert text is not None, (
        f"{REFUSED_CS} does not exist. The ADR without it is a decision nothing applies."
    )
    return text


@pytest.fixture(scope="module")
def table(refused_cs, code_names):
    """``{KIND: code name}`` out of the C# table the flattener consults."""
    out = {}
    for m in re.finditer(r'case "([A-Z0-9]+)":\s*\n\s*code = WarningCodes\.(\w+);', refused_cs):
        kind, constant = m.group(1), m.group(2)
        assert constant in code_names, (
            f"RefusedKinds sends {kind} to WarningCodes.{constant}, which WarningCodes.cs "
            "gives no name. A code with no name in Name() is 'WARNING_<n>' on the wire."
        )
        out[kind] = code_names[constant]
    return out


@pytest.fixture(scope="module")
def messages(refused_cs):
    """``{KIND: the sentence that goes on the wire}``."""
    out = {}
    pattern = (
        r'case "([A-Z0-9]+)":\s*\n\s*code = WarningCodes\.\w+;'
        r"\s*\n\s*reason =(.*?);\s*\n\s*return true;"
    )
    for m in re.finditer(pattern, refused_cs, re.S):
        out[m.group(1)] = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(2)))
    return out


class TestThereIsSomethingToCompare:
    """The positive controls. Every check below compares two mappings, and two
    empty ones agree about everything."""

    def test_the_adr_decides_a_set_of_kinds(self, decided):
        assert len(decided) >= 8, (
            f"ADR 0002's disposition table parsed to {sorted(decided)}. A table this "
            "reader cannot see makes every comparison in this file pass over nothing."
        )

    def test_the_shim_carries_the_same_kind_of_table(self, table):
        assert len(table) >= 8, f"RefusedKinds.cs parsed to {sorted(table)}"


class TestTheDecisionAndTheCodeAgree:
    def test_they_name_the_same_kinds(self, decided, table):
        assert set(decided) == set(table), (
            f"ADR 0002 decides {sorted(set(decided) - set(table))} that RefusedKinds.cs "
            f"does not carry, and RefusedKinds.cs carries {sorted(set(table) - set(decided))} "
            "that the ADR never decided. Either half alone is a refusal nobody can audit."
        )

    def test_every_kind_gets_the_code_the_adr_says(self, decided, table):
        for kind, (_disposition, code) in sorted(decided.items()):
            assert table[kind] == code, (
                f"{kind} is {code} in ADR 0002 and {table[kind]} in RefusedKinds.cs. The "
                "code is the only part of this a consumer can branch on."
            )

    def test_the_disposition_and_the_code_say_the_same_thing(self, decided):
        for kind, (disposition, code) in sorted(decided.items()):
            assert disposition in DISPOSITION_CODE, (
                f"{kind} is {disposition!r}, which is neither {sorted(DISPOSITION_CODE)}"
            )
            assert DISPOSITION_CODE[disposition] == code, (
                f"{kind} is {disposition!r} and carries {code}. A kind somebody decided "
                "against emits ENTITY_REFUSED_BY_DESIGN and a kind nobody has got to "
                "emits UNSUPPORTED_ENTITY, and swapping them is the exact confusion "
                "code 109 was added to end."
            )

    def test_every_kind_says_what_would_reopen_it(self, revisits, decided):
        for kind in sorted(decided):
            cell = revisits.get(kind, "")
            assert len(cell) >= 10 and cell not in {"-", "n/a", "none"}, (
                f"{kind}'s revisit cell is {cell!r}. A refusal with no condition on it "
                "is one the next reader has to re-derive from scratch to challenge."
            )


class TestTheSpecificationCarriesTheCodes:
    def test_every_code_used_is_in_the_wire_table(self, table, code_numbers, code_names):
        wire = read(WIRE_MD)
        documented = {
            m.group(2): int(m.group(1))
            for m in re.finditer(r"^\|\s*(\d+)\s*\|\s*`([A-Z0-9_]+)`\s*\|", wire, re.M)
        }
        constant_for = {name: c for c, name in code_names.items()}
        for kind, code in sorted(table.items()):
            assert code in documented, (
                f"{kind} is refused with {code} and WIRE.md's table does not define it, "
                "so a consumer is handed a number with no meaning"
            )
            assert documented[code] == code_numbers[constant_for[code]], (
                f"{code} is {documented[code]} in WIRE.md and "
                f"{code_numbers[constant_for[code]]} in the shim"
            )


class TestTheMessagesAreUsable:
    def test_each_one_names_its_kind_first(self, messages, table):
        assert set(messages) == set(table), (
            "a row in RefusedKinds.cs has a code and no reason, or the reader above "
            f"lost one: codes {sorted(table)}, reasons {sorted(messages)}"
        )
        for kind, message in sorted(messages.items()):
            assert message.split(" ")[0] == kind, (
                f"{kind}'s message starts {message.split(' ')[0]!r}. Every other "
                "unsupported-entity message on this wire starts with the DXF type, and "
                "the corpus tests read the type back by splitting on the first space."
            )

    def test_each_one_says_more_than_the_default_arm_does(self, messages):
        for kind, message in sorted(messages.items()):
            assert len(message) > 60, (
                f"{kind}'s message is {message!r}. The whole reason for a second code is "
                "that the reader of a log gets told why, so a sentence no longer than "
                "the fall-through's is a row that earned nothing."
            )


class TestTheFlattenerActuallyConsultsIt:
    """A table nothing reads is a table that drifts silently."""

    def test_the_default_arm_looks_the_kind_up(self):
        source = read(FLATTENER_CS)
        assert "RefusedKinds" in source, (
            "Flattener.cs never mentions RefusedKinds, so every kind in the table is "
            "still falling through to warning 100 and the ADR describes nothing"
        )

    def test_the_fall_through_is_still_there(self):
        # The control. A flattener that refused everything by design would pass
        # the check above, and 100 is what a kind nobody has decided on gets.
        source = read(FLATTENER_CS)
        assert "WarningCodes.UnsupportedEntity" in source


class TestTheThingsTheNextCampaignInherits:
    """The ADR's job is not only to record eight rows. These are the three
    facts a reader six months from now cannot re-derive cheaply."""

    def test_the_verdict_is_the_first_line_after_the_title(self, adr):
        lines = [ln.strip() for ln in adr.splitlines() if ln.strip()]
        assert lines[0].startswith("# ")
        assert re.search(r"\b(REFUSED|DEFERRED|GO|NO-GO)\b", lines[1]), (
            f"the second line has to carry the verdict, it says {lines[1]!r}"
        )

    def test_it_names_what_is_not_decided(self, adr):
        assert re.search(r"^#+ .*not decided", adr, re.M | re.I), (
            "ADR 0002 has no 'not decided' section. The kinds that are refused are the "
            "easy half; the record that a wire 3 would need is the part nobody has."
        )

    def test_it_records_the_frozen_geometry_range_trap(self, adr):
        flat = re.sub(r"\s+", " ", adr)
        assert "IsGeometry" in flat, "the trap is about WireFormat.IsGeometry and has to name it"
        assert re.search(r"\b3 to 10\b", flat), (
            "ADR 0002 does not say the geometry range is 3 to 10. A new geometry record "
            "numbered past 13 falls outside Flattener.Finite() and WIRE.md's finiteness "
            "guarantee stops covering it, silently, and that is the single thing a wire 3 "
            "campaign most needs handed to it."
        )
        assert "Finite" in flat

    def test_it_records_the_version_floor_finding(self, adr):
        flat = re.sub(r"\s+", " ", adr)
        for token in ("1014", "1012", "AC1013"):
            assert token in flat, (
                f"ADR 0002 never mentions {token}, and libviprs-dep#91 is about exactly "
                "which versions this decoder refuses and why"
            )


class TestTheVersionFloorCitesSomethingReal:
    """`DwgVersionMin` said ADR 0001 took the range from upstream's own reader
    table. ADR 0001 has no reader table, no 1014 and no 1012: the citation
    pointed at nothing, which is worse than no comment because it stops the
    next reader looking."""

    def test_the_constant_no_longer_cites_adr_0001_for_it(self):
        source = read(ABI_CS)
        m = re.search(r"((?:^\t*//.*\n)+)\t*public const uint DwgVersionMin", source, re.M)
        assert m, "DwgVersionMin has no comment at all, which is what #91 filed"
        comment = m.group(1)
        assert "ADR 0001" not in comment, (
            "the comment on DwgVersionMin still cites ADR 0001 for the AC10xx range. "
            "ADR 0001 contains no reader table, no 1014 and no 1012."
        )

    def test_adr_0001_really_does_not_carry_it(self):
        # The control for the assertion above, so it cannot be satisfied by
        # deleting a citation that was in fact correct.
        text = read(os.path.join(ACADSHARP, "docs", "adr", "0001-nativeaot-feasibility.md"))
        assert "1014" not in text and "1012" not in text
        assert not re.search(r"reader table", text, re.I)

    def test_the_comment_says_what_was_tried(self):
        source = read(ABI_CS)
        m = re.search(r"((?:^\t*//.*\n)+)\t*public const uint DwgVersionMin", source, re.M)
        comment = re.sub(r"\s+", " ", m.group(1))
        assert "AC1012" in comment or "R13" in comment, (
            "#91 asks the constant to say why it is what it is, and the whole question is R13"
        )
        assert "0002" in comment, "and to point at where the working is"
