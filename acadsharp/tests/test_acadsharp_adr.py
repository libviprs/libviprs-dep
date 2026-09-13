"""ADR 0001 is the deliverable of the spike, so its shape is a test.

The issue asks for a go/no-go in the first line and a specific list of
measurements. An ADR that quietly loses the static-library outcome or
the glibc floor is the one a later issue trips over.
"""

import os
import re

ADR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "docs", "adr", "0001-nativeaot-feasibility.md")
)

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
