"""An ADR in this directory states numbers, so the numbers are a test.

ADR 0001 is the deliverable of the spike. The issue asks for a go/no-go
in the first line and a specific list of measurements, and an ADR that
quietly loses the static-library outcome or the glibc floor is the one a
later issue trips over.

ADR 0002's census is the sharper case, and it is the one that actually
went wrong. It states what this adapter emits on `real_AC1032.dwg`
"so this document does not depend on an issue staying open", which is
the right instinct and leaves the number depending on a campaign instead:
it said 222 geometry records and 88 refusals on the day it merged into a
tree that emitted 371 and refused 73. In a repository whose whole
discipline is recomputing the numbers that appear in prose, a census is
computed rather than typed. `TestTheCensusIsRecomputed` reads the counts
back out of the ADR and recomputes each one from the committed
expectation, which needs no .NET: the expectation is a dump of a real
decode of that drawing, bound to the shim by `MANIFEST.json`.
"""

import collections
import os
import re

from g13_support import kinds, records, warnings

DOCS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "docs", "adr"))
ADR = os.path.join(DOCS, "0001-nativeaot-feasibility.md")
ADR_0002 = os.path.join(DOCS, "0002-what-this-decoder-refuses.md")

# The drawing the census is taken on, and the two codes an entity refusal
# can carry. A READER_NOTIFICATION is not a refusal: it is the backing
# reader talking about the document, with item_handle 0.
CENSUS_FIXTURE = "real_AC1032.dwg"
REFUSAL_CODES = ("UNSUPPORTED_ENTITY", "ENTITY_REFUSED_BY_DESIGN")

REQUIRED_SECTIONS = (
    "Source or NuGet",
    "Trimmer roots",
    "InvariantGlobalization",
    "glibc floor",
    "Static library",
    "JIT versus AOT",
    "Build hosts",
    "C# coverage",
)


def adr_text():
    with open(ADR) as f:
        return f.read()


class TestAdrShape:
    def test_it_exists(self):
        assert os.path.isfile(ADR)

    def test_the_verdict_is_the_first_line_after_the_title(self):
        lines = [ln.strip() for ln in adr_text().splitlines() if ln.strip()]
        assert lines[0].startswith("# ")
        assert re.search(r"\b(GO|NO-GO)\b", lines[1]), (
            f"the second line has to carry the verdict, it says {lines[1]!r}"
        )

    def test_every_required_section_is_present(self):
        text = adr_text()
        for section in REQUIRED_SECTIONS:
            assert section in text, f"ADR 0001 never decides {section!r}"

    def test_the_measurements_are_numbers_and_not_prose(self):
        text = adr_text()
        # A size, a count and a duration each have to appear with units.
        assert re.search(r"\d+(\.\d+)?\s?MB", text), "no library size recorded"
        assert re.search(r"\d+\s?s\b", text), "no build or parse time recorded"
        assert re.search(r"GLIBC_2\.\d+", text), "no glibc version recorded"


def adr_0002_flat():
    """ADR 0002 with its hard wrapping collapsed, so a reflow is not a failure."""
    with open(ADR_0002) as f:
        return re.sub(r"\s+", " ", f.read())


def census():
    """What the committed expectation for `real_AC1032.dwg` actually holds."""
    counts = kinds(CENSUS_FIXTURE)
    by_code = collections.Counter(w["code"] for w in warnings(CENSUS_FIXTURE))
    return {
        "geometry": sum(n for kind, n in counts.items() if kind != "Warning"),
        "warnings": counts.get("Warning", 0),
        "refusals": sum(by_code[c] for c in REFUSAL_CODES),
        "code_100": by_code["UNSUPPORTED_ENTITY"],
        "code_109": by_code["ENTITY_REFUSED_BY_DESIGN"],
        "point": sum(
            1
            for w in warnings(CENSUS_FIXTURE)
            if w["code"] in REFUSAL_CODES and w["message"].split(" ")[0] == "POINT"
        ),
    }


class TestTheCensusIsRecomputed:
    """ADR 0002's headline counts, against the decode they describe.

    Nothing here restates a number. Both sides come from somewhere else:
    the claim is parsed out of the ADR's own sentence and the fact is
    counted off the committed expectation, so editing one without the
    other fails rather than agreeing with itself.
    """

    def test_the_expectation_is_there_to_count(self):
        # The positive control. Every check below counts a dump, and a
        # dump this reader could not find counts zero of everything,
        # which an ADR claiming zero would match.
        assert len(records(CENSUS_FIXTURE)) > 100, (
            f"{CENSUS_FIXTURE}'s expectation parsed to almost nothing, so the census "
            "below is comparing the ADR against an empty file"
        )

    def test_the_adr_still_states_its_census_in_a_readable_shape(self):
        assert self.stated(), (
            "ADR 0002 no longer states the census as a sentence this can read. It is "
            "the one paragraph in the document that goes stale on its own, so it is "
            "the one that has to stay machine-checkable."
        )

    @staticmethod
    def stated():
        m = re.search(
            r"On `real_AC1032\.dwg` this adapter emits \*\*(\d+)\*\* geometry records "
            r"and \*\*(\d+)\*\* warnings, of which \*\*(\d+)\*\* are entity refusals: "
            r"\*\*(\d+)\*\* on code 100 and \*\*(\d+)\*\* on code 109",
            adr_0002_flat(),
        )
        if not m:
            return None
        names = ("geometry", "warnings", "refusals", "code_100", "code_109")
        return dict(zip(names, (int(g) for g in m.groups())))

    def test_every_stated_count_is_the_one_in_the_corpus(self):
        stated, actual = self.stated(), census()
        wrong = {k: (v, actual[k]) for k, v in stated.items() if v != actual[k]}
        assert not wrong, (
            "ADR 0002's census disagrees with the committed expectation for "
            f"{CENSUS_FIXTURE}, as {{claim: (adr, corpus)}}: {wrong}. The census is a "
            "count of this adapter's own output, so the flattener moving is what moves "
            "it. Recompute the sentence rather than the other way round."
        )

    def test_the_two_codes_add_up_to_the_refusals(self):
        # The ADR states a total and its two parts, and a total that is
        # not the sum is the shape a reader cannot catch by looking.
        stated = self.stated()
        assert stated["code_100"] + stated["code_109"] == stated["refusals"], (
            f"ADR 0002 says {stated['refusals']} refusals split "
            f"{stated['code_100']} and {stated['code_109']}"
        )

    def test_the_point_bullet_counts_the_same_refusals(self):
        # The wire-3 section leans on POINT being the biggest single gap,
        # and it quotes the same denominator from the other end of the
        # document. Two places holding one number is two places to drift.
        m = re.search(
            r"\*\*(\d+)\*\* of the \*\*(\d+)\*\* refusals on `real_AC1032\.dwg` are POINT",
            adr_0002_flat(),
        )
        assert m, (
            "ADR 0002's Point bullet no longer states how many of the refusals are "
            "POINT, which is the whole argument for putting it on a wire 3 first"
        )
        point, total = int(m.group(1)), int(m.group(2))
        actual = census()
        assert (point, total) == (actual["point"], actual["refusals"]), (
            f"ADR 0002 says {point} of {total} refusals are POINT and the expectation "
            f"for {CENSUS_FIXTURE} holds {actual['point']} of {actual['refusals']}"
        )
