"""What the reader allocates from sizes the drawing declares.

`max_input_bytes` is applied to the input before the read begins and nothing
after it looks at a length again, so every buffer `DwgReader` asks for is a
number the file chose. `HugeMemoryStream.Create` commits the whole length in
its constructor, before a byte has been decompressed, which makes one
four-byte page-header field an allocation request nothing checked.

Nothing in `viprs_acad_limits_v1` reaches that, and nothing in the shim can:
the allocating types are `internal` with no injection point, and a GC hard
limit kills the process, which is not a refusal. The bound is a patch to the
pinned upstream source, `acadsharp/patches/allocation_ceiling.py`, and these
are the recorded runs that say it is there.

The derivation is one rewritten field, recomputed here from the committed
fixture, so the capture is provably a run of the file this test describes and
not of whatever was on the machine that recorded it.
"""

import pytest
from g13_support import (
    DECLARED_SIZE_SOURCE,
    FIXTURES,
    declared_size_inputs,
    scenario,
    sha256_file,
)

CASES = declared_size_inputs()
NAMES = [c["name"] for c in CASES]


@pytest.fixture(scope="module")
def control():
    return scenario("declared/unmodified_control")


class TestTheDerivationIsWhatTheCaptureSaysItIs:
    def test_there_are_cases_at_all(self):
        # Every check below walks CASES. An empty list would make all of them
        # pass over nothing.
        assert len(CASES) >= 8, NAMES

    @pytest.mark.parametrize("case", CASES, ids=NAMES)
    def test_the_recorded_input_is_the_one_this_test_derives(self, case):
        recorded = scenario(case["scenario"])["derived"]
        assert recorded["sha256"] == case["sha256"], (
            f"{case['name']} was recorded against a different file from the one this "
            "test derives from the committed fixture, so the capture describes an "
            "input nobody here can reproduce"
        )
        assert recorded["declared"] == case["declared"]
        assert recorded["source"] == DECLARED_SIZE_SOURCE

    def test_the_fixture_it_is_derived_from_has_not_moved(self):
        recorded = scenario("declared/unmodified_control")["fixture_sha256"]
        assert recorded == sha256_file(f"{FIXTURES}/{DECLARED_SIZE_SOURCE}")


class TestADeclaredSizeIsRefusedRatherThanAllocated:
    @pytest.mark.parametrize("case", CASES, ids=NAMES)
    def test_the_open_refuses(self, case):
        result = scenario(case["scenario"])["result"]
        assert result["open_code"] == "CORRUPT_INPUT", (
            f"{case['name']} declares {case['declared']} bytes in a "
            f"{case['source_bytes']}-byte file and the open answered "
            f"{result['open_code']}. A file whose header describes something the file "
            "cannot contain is malformed, and anything other than a refusal here is "
            "an allocation a drawing asked for and got."
        )

    @pytest.mark.parametrize("case", CASES, ids=NAMES)
    def test_the_refusal_names_the_ceiling_and_the_input(self, case):
        detail = scenario(case["scenario"])["result"]["open_detail"] or ""
        assert "ceiling" in detail and str(case["declared"]) in detail, (
            f"{case['name']} was refused with {detail!r}, which does not say what was "
            "declared or what it was measured against. Whoever owns the drawing has "
            "to be able to tell this from any other corrupt file."
        )

    @pytest.mark.parametrize("case", CASES, ids=NAMES)
    def test_nothing_near_the_declared_size_was_allocated(self, case):
        result = scenario(case["scenario"])["result"]
        allocated = result["alloc_open_bytes"]
        assert allocated < case["declared"] // 100, (
            f"{case['name']} declared {case['declared']} bytes and the open allocated "
            f"{allocated}. The refusal has to come before the allocation, or it is a "
            "report of something that already happened."
        )

    @pytest.mark.parametrize("case", CASES, ids=NAMES)
    def test_the_allocation_stays_within_a_small_multiple_of_the_file(self, case):
        result = scenario(case["scenario"])["result"]
        ratio = result["alloc_open_bytes"] / case["source_bytes"]
        assert ratio < 16.0, (
            f"{case['name']} allocated {result['alloc_open_bytes']} bytes for a "
            f"{case['source_bytes']}-byte file, {ratio:.1f} times its size. A refusal "
            "that costs this much is not a bound on the declared size."
        )


class TestTheControlSaysTheCeilingIsNotBelowTheCorpus:
    """A ceiling set below what a real drawing needs would refuse everything
    and pass every check above. This is the pair that stops that."""

    def test_the_unmodified_fixture_still_opens(self, control):
        assert control["result"]["open_code"] == "OK", (
            "the same file with no page header rewritten was refused, so the ceiling "
            "is below what an ordinary drawing asks for"
        )

    def test_it_still_decodes(self, control):
        assert control["result"]["decode_code"] == "OK"

    def test_and_it_allocates_what_a_small_drawing_allocates(self, control):
        allocated = control["result"]["alloc_open_bytes"]
        assert 0 < allocated < 64 * 1024 * 1024
