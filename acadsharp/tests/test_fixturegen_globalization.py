"""The recording oracle and the shipped library agree about globalization (#125).

`native/Viprs.ACadSharp.Native.csproj` sets `InvariantGlobalization`, and ADR 0001
lists it under "load-bearing at run time". The fixture generator is the thing that
produces every expectation in `tests/expectations/`, and until #125 its csproj did
not mention the property at all, so the library that made the recordings ran with
ICU globalization and the library that ships runs without it.

ADR 0001 measured that this does not move DWG code-page text (code pages come from
`CodePagesEncodingProvider`, not from ICU). That is a measurement about one pair of
runs on one day, and it is the kind of fact that stops being true quietly, so the
two projects are held to the same setting here instead of to a sentence in an ADR.
Flipping `InvariantGlobalization` leaves `shim_sha256` byte-identical, which means
the digest cannot notice either: this is the only thing that would.
"""

import os
import re

ACAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
NATIVE = os.path.join(ACAD_DIR, "native", "Viprs.ACadSharp.Native.csproj")
FIXTUREGEN = os.path.join(ACAD_DIR, "tests", "fixtures", "gen", "Viprs.ACadSharp.FixtureGen.csproj")

SETTING = re.compile(r"<InvariantGlobalization>\s*([^<\s]*)\s*</InvariantGlobalization>")


def values(path):
    with open(path) as f:
        text = f.read()
    # Comments are not settings. The shipped csproj mentions the property in one.
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return SETTING.findall(text)


class TestTheOracleRunsTheWayTheLibraryShips:
    def test_the_shipped_project_sets_it_exactly_once(self):
        assert values(NATIVE) == ["true"], (
            "native/Viprs.ACadSharp.Native.csproj does not set InvariantGlobalization to "
            f"true exactly once ({values(NATIVE)}), and ADR 0001 calls it load-bearing"
        )

    def test_the_fixture_generator_sets_it_exactly_once(self):
        assert len(values(FIXTUREGEN)) == 1, (
            "Viprs.ACadSharp.FixtureGen.csproj has to say what globalization the recordings "
            f"were made under, once ({values(FIXTUREGEN)}). Silence is a third setting that "
            "looks like agreement"
        )

    def test_the_two_projects_agree(self):
        assert values(FIXTUREGEN) == values(NATIVE), (
            f"the oracle runs with InvariantGlobalization={values(FIXTUREGEN)} and the shipped "
            f"library with {values(NATIVE)}, so a recording cannot be authoritative about the "
            "text the library produces. Change both or neither"
        )
