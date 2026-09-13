"""`Warning.code` is on the frozen wire, so the wire document owns the numbers.

A consumer is told to switch on the code and never to parse the message, and
until now `docs/WIRE.md` gave the field's offset and width and said nothing
about a single value. The enumeration lived in `native/Adapter/WarningCodes.cs`,
which is the shim's source and not something a second implementation ever
reads, so the only thing a consumer could honestly do with a warning was count
it.

These checks hold the specification and the shim to the same table: every code
the shim emits is written down with the same number and the same name, the
document knows no code nothing emits, and the ranges say who may allocate what.
"""

import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ACADSHARP = os.path.dirname(HERE)
WIRE_MD = os.path.join(ACADSHARP, "docs", "WIRE.md")
WARNING_CODES_CS = os.path.join(ACADSHARP, "native", "Adapter", "WarningCodes.cs")
SYNTHETIC_CS = os.path.join(ACADSHARP, "native", "Sources", "SyntheticSource.cs")

# Where the document draws the line. Below it the codes are VIPRS-defined and
# every one of them is in WIRE.md; at or above it they belong to whichever
# backend produced the stream and a consumer is free to know none of them.
BACKEND_RANGE_START = 1000


def read(path):
    with open(path) as f:
        return f.read()


@pytest.fixture(scope="module")
def wire():
    return read(WIRE_MD)


@pytest.fixture(scope="module")
def documented(wire):
    """``{code: NAME}`` out of WIRE.md's warning-code table."""
    return {
        int(m.group(1)): m.group(2)
        for m in re.finditer(r"^\|\s*(\d+)\s*\|\s*`([A-Z0-9_]+)`\s*\|", wire, re.M)
    }


@pytest.fixture(scope="module")
def emitted():
    """``{code: NAME}`` out of the shim, by pairing the two halves of the file.

    `WarningCodes.cs` carries the numbers as C# constants and the names in a
    switch, which is exactly the drift this pairing catches: a constant with
    no case, or a case naming a constant that no longer exists.
    """
    code = read(WARNING_CODES_CS)
    numbers = {
        m.group(1): int(m.group(2)) for m in re.finditer(r"public const uint (\w+) = (\d+)u;", code)
    }
    names = {
        m.group(1): m.group(2) for m in re.finditer(r'case (\w+): return "([A-Z0-9_]+)";', code)
    }
    return {numbers[csharp]: wire_name for csharp, wire_name in names.items()}


class TestTheTableIsReadable:
    """The positive controls. Every assertion below compares two dictionaries,
    and two empty ones agree about everything."""

    def test_the_document_carries_a_table(self, documented):
        assert len(documented) >= 7, (
            f"WIRE.md's warning-code table parsed to {documented}. Every check in this "
            "module compares it against the shim, so an unparsed table would make all "
            "of them pass over nothing."
        )

    def test_the_shim_carries_an_enumeration(self, emitted):
        assert len(emitted) >= 7, (
            f"WarningCodes.cs parsed to {emitted}, which is not the enumeration this "
            "module is here to compare against"
        )


class TestTheSpecificationAndTheShimAgree:
    def test_every_code_the_shim_emits_is_documented(self, documented, emitted):
        missing = sorted(c for c in emitted if c not in documented)
        assert not missing, (
            f"the shim emits warning codes {missing} and WIRE.md defines none of them. "
            "A consumer switches on this number and is forbidden from reading the "
            "message, so an undocumented code is a warning it can only count."
        )

    def test_every_documented_code_has_the_name_the_shim_uses(self, documented, emitted):
        for code, name in sorted(emitted.items()):
            assert documented.get(code) == name, (
                f"code {code} is {name!r} in the shim and {documented.get(code)!r} in "
                "WIRE.md. The name is how a consumer author writes the branch."
            )

    def test_the_document_defines_no_code_nothing_emits(self, documented, emitted):
        extra = sorted(set(documented) - set(emitted))
        assert not extra, (
            f"WIRE.md defines {extra}, which nothing in this library produces. A "
            "specified code with no producer is a branch a consumer writes and never "
            "reaches, and it stops anyone noticing when the real one is missing."
        )


class TestTheRangesSayWhoOwnsWhat:
    def test_every_viprs_defined_code_is_below_the_backend_range(self, documented):
        for code in sorted(documented):
            assert 1 <= code < BACKEND_RANGE_START, (
                f"WIRE.md's table defines {code}, which is outside the VIPRS-defined "
                f"range of 1 to {BACKEND_RANGE_START - 1}. The split is the whole "
                "reason a consumer can tell a code it should have known from one it "
                "was never going to."
            )

    def test_the_ranges_are_written_down(self, wire):
        flat = re.sub(r"\s+", " ", wire)
        assert re.search(r"1 to 999", flat), (
            "WIRE.md has to name the VIPRS-defined range, or a second backend has no "
            "way to know which numbers it may allocate"
        )
        assert re.search(r"1000", flat), "WIRE.md has to name where the backend range starts"

    def test_the_forward_rule_is_written_down(self, wire):
        flat = re.sub(r"\s+", " ", wire)
        assert re.search(r"skips? (a |any )?code it does not know", flat, re.I), (
            "WIRE.md has to say that an unknown warning code is skipped rather than "
            "refused. Without that sentence a careful consumer refuses the stream the "
            "first time this library adds a code, which is the opposite of what the "
            "numbering is for."
        )
        assert re.search(r"does not refuse|rather than refus|never refus", flat, re.I), (
            "and it has to say so as a rule about refusing, not only as a rule about "
            "skipping: the failure being prevented is a consumer that stops"
        )

    def test_the_synthetic_probe_code_is_in_the_backend_range(self, documented):
        code = read(SYNTHETIC_CS)
        m = re.search(r"public const uint ProbeWarningCode = (\d+)u;", code)
        assert m, "SyntheticSource no longer declares a probe warning code"
        probe = int(m.group(1))
        assert probe >= BACKEND_RANGE_START, (
            f"the synthetic document's probe warning is {probe}, inside the "
            "VIPRS-defined range. It is a backing-source detail and belongs above "
            f"{BACKEND_RANGE_START}, where a consumer is entitled to ignore it."
        )
        assert probe not in documented, (
            f"WIRE.md defines {probe}, which is the synthetic document's own probe. "
            "The table is the VIPRS-defined set and nothing else."
        )


class TestTheShimPointsAtTheSpecification:
    def test_the_enumeration_says_where_the_contract_lives(self):
        code = read(WARNING_CODES_CS)
        assert "WIRE.md" in code, (
            "WarningCodes.cs has to name the document that owns these numbers, or the "
            "next code gets added here and nowhere else"
        )

    def test_it_no_longer_claims_the_synthetic_source_uses_one(self):
        # It says so today and it has not been true since the synthetic probe
        # moved to the backend range. A comment justifying a numbering scheme
        # with a fact that stopped holding is worse than no comment: it is the
        # reason the next person keeps the scheme.
        code = read(WARNING_CODES_CS)
        assert "SyntheticSource already uses 1" not in code
