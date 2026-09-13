"""What the AC1021 side of the reader allocates from sizes the drawing declares.

`test_limits_declared_sizes.py` covers the two sites an AC18 file reaches. The
other four are on the AC1021 path, and until these captures existed not one of
them had been watched refusing anything:

  * `Check(lenght, ...)` and `Check(totalSize, ...)` and
    `CheckUnsigned(uncompressedSize, ...)` in `getPageBuffer`, which allocates
    three arrays straight out of the compressed file header.
  * `CheckUnsigned(totalLength, ...)` in `getSectionBuffer21`, which is the sum
    of every page's declared decompressed size.

Nothing in `tests/fixtures` reaches them, and nothing derived from anything in
there can. `readFileHeader` sends AC1024, AC1027 and AC1032 to the AC18 reader,
so `real_AC1032.dwg` never touches this code, and upstream's `DwgWriter`
refuses to write AC1021, so the generator that produced the corpus cannot make
a file that does. The inputs are therefore built rather than derived, and
`ac21_forge.py` is where the building lives and why it is possible at all.

The control is the same forge with nothing inflated. It gets past all four
ceilings, the section buffer is assembled out of a real page, and what refuses
it is the header parser running off the end of 255 bytes of nothing. A ceiling
that refused every AC1021 file would pass every check above this one.
"""

import json
import os

import pytest
from ac21_forge import (
    SITES,
    WRAPPED_TOTAL_SIZE,
    compressed_total,
    control_case,
    inputs,
    page_block_length,
)
from g13_support import ACAD_ROOT, FIXTURES, scenario, sha256_file

PATCH = os.path.join(ACAD_ROOT, "patches", "allocation_ceiling.py")

CASES = inputs()
NAMES = [c["name"] for c in CASES]
CONTROL = control_case()

# The ceiling this build derives for a file this small: 16 MiB, the floor,
# because 64 times a 2 KB file is far below it. Every refusal below has to
# cost less than the bound it is enforcing.
FLOOR_BYTES = 16 * 1024 * 1024


def by_site(site):
    return [c for c in CASES if c.get("site") == site]


@pytest.fixture(scope="module")
def control():
    return scenario(CONTROL["scenario"])["result"]


@pytest.fixture(scope="module")
def real_ac1032():
    return scenario("declared/real_ac1032_control")


class TestTheInputsAreTheOnesTheCapturesRecord:
    def test_there_are_cases_at_all(self):
        # Every check below walks CASES. An empty list would make all of them
        # pass over nothing.
        assert len(CASES) >= 7, NAMES

    def test_every_site_is_covered(self):
        assert sorted({c["site"] for c in CASES}) == sorted(SITES), (
            "one of the four AC21 allocation sites has no input, so this file would "
            "report a guard proven that nothing here reaches"
        )

    @pytest.mark.parametrize("case", CASES, ids=NAMES)
    def test_the_recorded_input_is_the_one_this_test_builds(self, case):
        recorded = scenario(case["scenario"])["derived"]
        assert recorded["sha256"] == case["sha256"], (
            f"{case['name']} was recorded against a different file from the one "
            "ac21_forge builds, so the capture describes an input nobody here can "
            "reproduce"
        )
        assert recorded["declared"] == case["declared"]
        assert recorded["site"] == case["site"]

    def test_the_control_is_the_one_this_test_builds(self):
        recorded = scenario(CONTROL["scenario"])["derived"]
        assert recorded["sha256"] == CONTROL["sha256"]

    @pytest.mark.parametrize("case", CASES, ids=NAMES)
    def test_every_input_is_an_ac1021_file(self, case):
        assert case["blob"][:6] == b"AC1021", (
            f"{case['name']} does not open with the one version signature that "
            "reaches readFileHeaderAC21, so whatever it exercises is not these guards"
        )

    def test_the_sites_are_the_ones_the_patch_names(self):
        """What ties a capture's detail line to a guard in the patch.

        `open_detail` carrying "AC21 section buffer" is only evidence that the
        section-buffer guard refused if that string is what the patch hands
        `CheckUnsigned` there. Renaming a site without renaming it here would
        otherwise leave every check below asserting on a string nothing emits.
        """
        with open(PATCH) as f:
            text = f.read()
        for site in SITES:
            assert f'"{site}"' in text, (
                f"the patch passes no guard a site named {site!r}, so nothing in the "
                "reader can produce the refusal this file reads back"
            )

    def test_the_corpus_has_no_file_that_could_have_done_this(self):
        """The reason the inputs are forged, stated as a check.

        AC1024, AC1027 and AC1032 all go to `readFileHeaderAC18`, so the one
        real 2018 drawing in the corpus is on the other path entirely. If a
        genuine AC1021 fixture ever lands, this is the test that says so and
        the forge stops being the only way in.
        """
        signatures = {}
        for name in sorted(os.listdir(FIXTURES)):
            if not name.endswith(".dwg"):
                continue
            with open(os.path.join(FIXTURES, name), "rb") as f:
                signatures[name] = f.read(6)
        assert signatures, FIXTURES
        assert b"AC1021" not in signatures.values(), (
            "there is now an AC1021 file in the corpus: "
            f"{[n for n, s in signatures.items() if s == b'AC1021']}. These guards can "
            "be reached from a fixture and no longer need ac21_forge."
        )
        assert signatures["real_AC1032.dwg"] == b"AC1032"


class TestADeclaredSizeIsRefusedRatherThanAllocated:
    @pytest.mark.parametrize("case", CASES, ids=NAMES)
    def test_the_open_refuses(self, case):
        result = scenario(case["scenario"])["result"]
        assert result["open_code"] == "CORRUPT_INPUT", (
            f"{case['name']} declares {case['declared']} bytes for its "
            f"{case['site']} in a {case['bytes']}-byte file and the open answered "
            f"{result['open_code']}"
        )

    @pytest.mark.parametrize("case", CASES, ids=NAMES)
    def test_the_refusal_names_the_site_the_ceiling_and_the_input(self, case):
        detail = scenario(case["scenario"])["result"]["open_detail"] or ""
        assert case["site"] in detail, (
            f"{case['name']} was refused with {detail!r}, which does not name the "
            "allocation site, so this capture cannot say which of the four guards "
            "stopped it"
        )
        assert "ceiling" in detail and str(case["declared"]) in detail

    @pytest.mark.parametrize("case", CASES, ids=NAMES)
    def test_the_refusal_costs_less_than_the_ceiling_it_enforces(self, case):
        allocated = scenario(case["scenario"])["result"]["alloc_open_bytes"]
        assert 0 < allocated < FLOOR_BYTES, (
            f"{case['name']} allocated {allocated} bytes refusing an allocation it "
            f"holds to {FLOOR_BYTES}"
        )

    @pytest.mark.parametrize("case", CASES, ids=NAMES)
    def test_nothing_near_the_declared_size_was_allocated(self, case):
        allocated = scenario(case["scenario"])["result"]["alloc_open_bytes"]
        assert allocated < case["declared"] // 10, (
            f"{case['name']} declared {case['declared']} bytes and the open allocated "
            f"{allocated}. The refusal has to come before the allocation, or it is a "
            "report of something that already happened."
        )

    @pytest.mark.parametrize("site", [s for s in SITES if len(by_site(s)) > 1])
    def test_the_allocation_does_not_track_the_declared_size(self, site):
        """The claim a single ratio cannot make.

        Getting to the AC21 section buffer costs a few megabytes of managed
        churn whatever the file declares, so "the allocation is small" is a
        statement about the walk rather than about the bound. This is the one
        that is about the bound: the same site at two declared sizes a
        gigabyte apart has to allocate the same amount either way.
        """
        cases = sorted(by_site(site), key=lambda c: c["declared"])
        low, high = cases[0], cases[-1]
        allocations = [scenario(c["scenario"])["result"]["alloc_open_bytes"] for c in (low, high)]
        declared_gap = high["declared"] - low["declared"]
        assert declared_gap > 0, site
        assert abs(allocations[1] - allocations[0]) < 65536, (
            f"{site} allocated {allocations[0]} and {allocations[1]} for declared "
            f"sizes {declared_gap} bytes apart. An allocation that moves with the "
            "declared field is an allocation the declared field is still driving."
        )


class TestWhyTheCompressedPageGuardTakesAWrappedSize:
    """The reason that one input looks unlike the other six.

    getPageBuffer allocates `lenght` before `totalSize`, and `lenght` is
    `ceil(totalSize / 239) * 255`, which is larger than `totalSize` for every
    value that does not wrap. So the first guard refuses before the second
    ever sees anything, and a declared size that reaches the second one has to
    be one where `(int)(totalSize + 238)` narrows to something small. That is
    worth pinning rather than leaving as a remark: if upstream widens that
    cast, this input stops reaching the guard it is named after and every
    check above would still pass.
    """

    @pytest.mark.parametrize("total", [8, 239, 240, 65536, 1 << 20, 1 << 26, 1 << 30, 0x7FFFFF00])
    def test_the_block_buffer_is_always_larger_than_the_compressed_page(self, total):
        assert page_block_length(total) > total

    def test_so_the_only_way_to_the_second_guard_is_the_int_cast_wrapping(self):
        total = compressed_total(WRAPPED_TOTAL_SIZE, 1)
        assert total == WRAPPED_TOTAL_SIZE
        assert page_block_length(total) == 0, (
            "the block buffer no longer comes out at zero for a wrapped total size, "
            "so the compressed-page input now trips the guard before it and this "
            "file's claim to cover that site is wrong"
        )


class TestTheControlSaysTheCeilingIsNotRefusingEveryAc1021File:
    """Four refusals on forged files prove nothing on their own: a reader that
    threw on the version signature would produce the same shape of capture.
    These are the pair that stops that."""

    def test_it_gets_past_every_ceiling(self, control):
        detail = control["open_detail"] or ""
        assert "ceiling" not in detail, (
            "the same forge with nothing inflated was refused by the ceiling, so the "
            f"bound is below what an ordinary AC1021 file asks for: {detail!r}"
        )

    def test_and_it_is_the_parser_that_refuses_it_instead(self, control):
        # The section buffer is built, handed to DwgHeaderReader, and the 255
        # bytes behind it are not a header. That is a different refusal from a
        # declared size, and it is the one that says the guards let the file
        # through.
        assert control["open_code"] == "CORRUPT_INPUT"
        assert "EndOfStream" in (control["open_detail"] or ""), control["open_detail"]

    def test_it_costs_what_the_refusals_cost(self, control):
        # Within a factor of two of the largest refusal above, which is what
        # says the megabytes in those numbers are the cost of walking an AC21
        # header rather than anything the declared size bought.
        worst = max(scenario(c["scenario"])["result"]["alloc_open_bytes"] for c in CASES)
        assert control["alloc_open_bytes"] < 2 * worst


class TestTheBenignAc18ControlStillOpens:
    """The real 2018 drawing, which is on the other path and has to stay
    readable. It was taken for this repository's AC21 fixture, and recording
    it is what says it is not one: it opens, it decodes, and not one of the
    four sites appears anywhere in its capture."""

    def test_it_opens(self, real_ac1032):
        assert real_ac1032["result"]["open_code"] == "OK"

    def test_it_decodes(self, real_ac1032):
        assert real_ac1032["result"]["decode_code"] == "OK"

    def test_the_capture_is_of_the_committed_fixture(self, real_ac1032):
        assert real_ac1032["fixture_sha256"] == sha256_file(f"{FIXTURES}/real_AC1032.dwg")

    def test_it_allocates_what_a_real_drawing_allocates(self, real_ac1032):
        allocated = real_ac1032["result"]["alloc_open_bytes"]
        assert 0 < allocated < 64 * 1024 * 1024

    def test_and_it_never_went_near_an_ac21_allocation_site(self, real_ac1032):
        recorded = json.dumps(real_ac1032)
        for site in SITES:
            assert site not in recorded, (
                f"{site} turns up in the capture of an AC1032 file, so this file's "
                "account of which version reaches which reader is wrong"
            )
