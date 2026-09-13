"""What the AC18 descriptor loop allocates from counts and offsets a drawing declares.

`test_limits_declared_sizes.py` covers the two AC18 sinks a four-byte edit
reaches, and `test_limits_ac21_declared_sizes.py` the four on the AC1021 path.
These are the last two, and they are the pair #77 shipped as defence with
nothing reaching them:

  * `CheckDescriptor(PageCount, DecompressedSize, ...)`, which runs before the
    loop over a descriptor's pages and before the buffer that loop sizes.
  * `CheckPageOffset(Offset, DecompressedSize, ...)`, which runs before the gap
    fill that adds one empty page per `DecompressedSize` until it reaches a
    declared offset.

Neither field is in plaintext. Both sit inside the LZ77 stream the
data-section map is stored as, and the page header in front of that stream is
the only part of the page in the clear, which is the part the `section_map_*`
derivations rewrite. So the inputs are forged rather than derived, and
`ac18_forge.py` is where the writing lives and why it is possible.

The control is the same forge with nothing inflated. It reaches both guards
and passes both, its section buffer is assembled out of one real page and one
zero-filled gap, and what refuses it is a header variable the buffer does not
contain. A ceiling that refused every forged AC18 map would pass every check
above this one.

One case is recorded patched and has never been run without the guards. A zero
`DecompressedSize` does not over-allocate, it hangs: `decompressSizeCounter`
advances by `DecompressedSize` and the gap fill has nothing else to reach the
offset with, so `LocalSections` grows until the process is killed. The recorder
runs each decode with no timeout, so recording that would be a recorder that
never comes back. The refusal is recorded; the hang is cited.
"""

import json
import os

import pytest
from ac18_forge import (
    BRANCHES,
    CEILING_FACTOR,
    COUNT_PAGE_SIZE,
    COUNT_PAGES,
    FLOOR_BYTES,
    GAP_OFFSET,
    GAP_PAGE_SIZE,
    MAX_SECTION_PAGES,
    OFFSET_PAGE_SIZE,
    PRODUCT_PAGE_SIZE,
    PRODUCT_PAGES,
    SECTION_NAME,
    control_case,
    inputs,
    read_back,
    section_map,
)
from g13_support import (
    ACAD_ROOT,
    DECLARED_SIZE_HEADERS,
    DECLARED_SIZE_SOURCE,
    FIXTURES,
    declared_size_offset,
    scenario,
)

PATCH = os.path.join(ACAD_ROOT, "patches", "allocation_ceiling.py")

CASES = inputs()
NAMES = [c["name"] for c in CASES]
CONTROL = control_case()

# Only the cases whose declared number is big enough for "the allocation did
# not track it" to be a claim. The zero case is the other kind of evidence.
SIZED = [c for c in CASES if c["declared"] >= (1 << 20)]
SIZED_NAMES = [c["name"] for c in SIZED]

# The branches an input reaches at two declared numbers, which are the only
# ones a low-against-high comparison can be made on.
PAIRED_SITES = sorted(
    {c["site"] for c in CASES if len([x for x in CASES if x["site"] == c["site"]]) > 1}
)


def by_site(site):
    return [c for c in CASES if c["site"] == site]


@pytest.fixture(scope="module")
def control():
    return scenario(CONTROL["scenario"])["result"]


@pytest.fixture(scope="module")
def corpus_control():
    return scenario("declared/unmodified_control")


class TestTheInputsAreTheOnesTheCapturesRecord:
    def test_there_are_cases_at_all(self):
        # Every check below walks CASES. An empty list would make all of them
        # pass over nothing.
        assert len(CASES) >= 7, NAMES

    def test_every_branch_of_both_guards_is_covered(self):
        covered = {c["site"] for c in CASES}
        assert covered == {site for site, _ in BRANCHES}, (
            "a branch of CheckDescriptor or CheckPageOffset has no input, so this "
            "file would report a guard proven that nothing here reaches: "
            f"{sorted(covered)}"
        )

    def test_both_guards_are_reached(self):
        assert {c["guard"] for c in CASES} == {"CheckDescriptor", "CheckPageOffset"}

    @pytest.mark.parametrize("case", CASES, ids=NAMES)
    def test_the_recorded_input_is_the_one_this_test_builds(self, case):
        recorded = scenario(case["scenario"])["derived"]
        assert recorded["sha256"] == case["sha256"], (
            f"{case['name']} was recorded against a different file from the one "
            "ac18_forge builds, so the capture describes an input nobody here can "
            "reproduce"
        )
        assert recorded["declared"] == case["declared"]
        assert recorded["declared_text"] == case["declared_text"]
        assert recorded["site"] == case["site"]
        assert recorded["guard"] == case["guard"]

    @pytest.mark.parametrize("case", CASES, ids=NAMES)
    def test_the_bytes_declare_what_the_case_says_they_declare(self, case):
        """The forge read back through its own walk of the reader's path.

        A capture names a number, and the only thing tying that number to the
        file is that both come out of `ac18_forge`. This is the other tie: the
        bytes are decoded again, through the header block, the page map and the
        section map in the order `readFileHeaderAC18` walks them, and the
        descriptor that comes out has to be the one the case describes.
        """
        descriptors = read_back(case["blob"])
        assert len(descriptors) == 1
        descriptor = descriptors[0]
        assert descriptor["name"] == SECTION_NAME
        if case["guard"] == "CheckDescriptor":
            if case["site"].endswith("page size"):
                assert descriptor["decompressed_size"] == case["declared"]
            elif case["site"].endswith("page count"):
                assert descriptor["page_count"] == COUNT_PAGES
                assert descriptor["decompressed_size"] == COUNT_PAGE_SIZE
            else:
                assert descriptor["page_count"] == PRODUCT_PAGES
                assert descriptor["decompressed_size"] == PRODUCT_PAGE_SIZE
        else:
            assert descriptor["pages_declared_but_absent"] == 0
            assert descriptor["pages"][0]["offset"] == case["declared"]
            expected = GAP_PAGE_SIZE if case["site"].endswith("gap") else OFFSET_PAGE_SIZE
            assert descriptor["decompressed_size"] == expected

    def test_the_control_reads_back_as_a_drawing_sized_section(self):
        descriptor = read_back(CONTROL["blob"])[0]
        assert descriptor["page_count"] == 1
        assert descriptor["decompressed_size"] == CONTROL["declared"]
        # One page of gap in front of the real one, so the fill loop runs and
        # stops, and getSectionBuffer18 zero-fills a page before it writes one.
        assert descriptor["pages"][0]["offset"] == descriptor["decompressed_size"]
        assert descriptor["pages"][0]["page_number"] in descriptor["records"]

    def test_the_control_is_the_one_this_test_builds(self):
        recorded = scenario(CONTROL["scenario"])["derived"]
        assert recorded["sha256"] == CONTROL["sha256"]

    @pytest.mark.parametrize("case", CASES, ids=NAMES)
    def test_every_input_is_an_ac1018_file(self, case):
        assert case["blob"][:6] == b"AC1018", (
            f"{case['name']} does not open with a version signature readFileHeader "
            "sends to readFileHeaderAC18, so whatever it exercises is not these guards"
        )

    @pytest.mark.parametrize("case", CASES + [CONTROL], ids=NAMES + [CONTROL["name"]])
    def test_the_ceiling_these_numbers_were_chosen_against_is_the_floor(self, case):
        # Every declared value in ac18_forge is picked against 16 MiB. A file
        # big enough that 64 times its length is larger would be measured
        # against a different number, and half the cases would stop refusing.
        assert CEILING_FACTOR * case["bytes"] < FLOOR_BYTES, case["name"]

    def test_the_sites_are_the_ones_the_patch_composes(self):
        """What ties a capture's detail line to a guard in the patch.

        `open_detail` carrying "section descriptor 'AcDb:Header' page size" is
        only evidence that CheckDescriptor refused if the patch is what builds
        that string. It builds it by concatenation rather than as a literal, so
        what is checked here is the pieces.
        """
        with open(PATCH) as f:
            text = f.read()
        for fragment in (
            '"section descriptor \'"',
            '" page size"',
            '" page count"',
            '"section \'"',
            '"\' page offset"',
            '" gap"',
            '" pages of "',
        ):
            assert fragment in text, (
                f"the patch no longer builds a site string out of {fragment}, so "
                "nothing in the reader produces the refusals this file reads back"
            )
        for guard in ("CheckDescriptor", "CheckPageOffset"):
            assert f"internal static void {guard}(" in text

    def test_the_fields_these_reach_are_not_in_plaintext(self):
        """The reason the inputs are forged, stated as a check.

        The `section_map_*` derivations rewrite four bytes of the section map's
        page header, which is in the clear. Everything this file is about is
        behind that header, inside the LZ77 stream, and the header itself says
        so: compression type 2, and a compressed size that is not the
        decompressed one.
        """
        with open(os.path.join(FIXTURES, DECLARED_SIZE_SOURCE), "rb") as f:
            data = f.read()
        magic = dict(DECLARED_SIZE_HEADERS)["section_map"]
        at = declared_size_offset(data, magic)
        decompressed = int.from_bytes(data[at : at + 4], "little")
        compressed = int.from_bytes(data[at + 4 : at + 8], "little")
        compression = int.from_bytes(data[at + 8 : at + 12], "little")
        assert compression == 2, (
            f"{DECLARED_SIZE_SOURCE} now stores its data-section map uncompressed, so "
            "the descriptor fields are reachable by a byte edit and the forge is no "
            "longer the only way in"
        )
        assert 0 < compressed != decompressed

    def test_the_map_this_forge_writes_is_the_shape_the_reader_reads(self):
        """The two sizes the brief for this work was checked against.

        The section map is a 20-byte preamble, a 96-byte descriptor, and 16
        bytes per page entry. Both numbers are inside the one-byte literal run,
        which is why a whole section map stores in the short form and only the
        control's data page needs the continuation form.
        """
        empty = section_map([{"page_count": 0, "decompressed_size": 1, "compressed_size": 0}])
        one = section_map(
            [
                {
                    "page_count": 1,
                    "decompressed_size": 1,
                    "compressed_size": 0,
                    "pages": [{"page_number": 1, "compressed_size": 0, "offset": 0}],
                }
            ]
        )
        assert len(empty) == 116
        assert len(one) == 132


class TestADeclaredCountIsRefusedRatherThanWalked:
    @pytest.mark.parametrize("case", CASES, ids=NAMES)
    def test_the_open_refuses(self, case):
        result = scenario(case["scenario"])["result"]
        assert result["open_code"] == "CORRUPT_INPUT", (
            f"{case['name']} declares {case['declared_text']} for its "
            f"{case['site']} in a {case['bytes']}-byte file and the open answered "
            f"{result['open_code']}"
        )

    @pytest.mark.parametrize("case", CASES, ids=NAMES)
    def test_the_refusal_names_the_site_the_ceiling_and_the_input(self, case):
        detail = scenario(case["scenario"])["result"]["open_detail"] or ""
        assert case["site"] in detail, (
            f"{case['name']} was refused with {detail!r}, which does not name the "
            "site, so this capture cannot say which branch of which guard stopped it"
        )
        assert "ceiling" in detail and case["declared_text"] in detail

    @pytest.mark.parametrize("case", CASES, ids=NAMES)
    def test_the_refusal_costs_less_than_the_ceiling_it_enforces(self, case):
        allocated = scenario(case["scenario"])["result"]["alloc_open_bytes"]
        assert 0 < allocated < FLOOR_BYTES, (
            f"{case['name']} allocated {allocated} bytes refusing an allocation it "
            f"holds to {FLOOR_BYTES}"
        )

    @pytest.mark.parametrize("case", SIZED, ids=SIZED_NAMES)
    def test_nothing_near_the_declared_number_was_allocated(self, case):
        allocated = scenario(case["scenario"])["result"]["alloc_open_bytes"]
        assert allocated < case["declared"] // 10, (
            f"{case['name']} declares {case['declared_text']} and the open allocated "
            f"{allocated}. The refusal has to come before the allocation, or it is a "
            "report of something that already happened."
        )

    @pytest.mark.parametrize("site", PAIRED_SITES)
    def test_the_allocation_does_not_track_the_declared_number(self, site):
        """The claim a single number cannot make.

        Reaching either guard costs the same walk whatever the file declares,
        so "the allocation is small" is a statement about that walk. This is
        the one about the bound: the same branch at two declared values a
        gigabyte apart has to cost the same either way.
        """
        cases = sorted(by_site(site), key=lambda c: c["declared"])
        low, high = cases[0], cases[-1]
        allocations = [scenario(c["scenario"])["result"]["alloc_open_bytes"] for c in (low, high)]
        assert high["declared"] - low["declared"] > 0, site
        assert abs(allocations[1] - allocations[0]) < 65536, (
            f"{site} allocated {allocations[0]} and {allocations[1]} for declared "
            f"numbers {high['declared'] - low['declared']} apart. An allocation that "
            "moves with the declared field is one the declared field still drives."
        )

    def test_the_product_case_has_both_factors_inside_their_own_bounds(self):
        """Why that case needs a guard on the product at all.

        Each factor passes the branch in front of it, so nothing but the
        multiplication refuses this file. If either factor ever exceeded its
        own bound, the case would still be refused and would stop saying
        anything about the product.
        """
        assert PRODUCT_PAGE_SIZE < FLOOR_BYTES
        assert PRODUCT_PAGES < MAX_SECTION_PAGES
        assert PRODUCT_PAGES * PRODUCT_PAGE_SIZE > FLOOR_BYTES

    def test_the_page_count_case_is_only_about_the_count(self):
        """Why that case declares a one-byte page.

        The branch before it takes the page size and the branch after it takes
        the product, and both let this file through: one byte is far under the
        ceiling and two million of them still are. So the only thing that can
        refuse it is the count, which is what the case is for.

        It is also the weakest of the seven, and worth saying so rather than
        letting the table imply otherwise. The file declares two million pages
        and carries one, because CheckDescriptor runs before the loop that
        would read them. Without the guard that loop throws
        EndOfStreamException on its second iteration rather than allocating,
        and a file that really carried two million entries would be thirty-two
        megabytes, which is past the ceiling it is trying to get around. This
        branch is defence in depth and a better message, in the same way #80
        found the AC21 compressed-page guard to be.
        """
        assert COUNT_PAGE_SIZE < FLOOR_BYTES
        assert COUNT_PAGES > MAX_SECTION_PAGES
        assert COUNT_PAGES * COUNT_PAGE_SIZE < FLOOR_BYTES
        case = [c for c in CASES if c["name"] == "ac18_descriptor_page_count"][0]
        assert read_back(case["blob"])[0]["pages_declared_but_absent"] > 0

    def test_the_gap_case_is_a_count_the_byte_ceiling_would_have_allowed(self):
        """Why MaxSectionPages is not the byte ceiling written twice.

        The offset is inside the ceiling and so is the buffer the pages it
        creates add up to. What is not inside anything is the number of
        objects, which is the quantity this branch bounds and the byte ceiling
        never sees.
        """
        assert GAP_OFFSET < FLOOR_BYTES
        assert GAP_OFFSET // GAP_PAGE_SIZE > MAX_SECTION_PAGES
        assert GAP_PAGE_SIZE * (GAP_OFFSET // GAP_PAGE_SIZE) < FLOOR_BYTES


class TestTheZeroPageSizeIsTheOneThatHangs:
    """The case recorded patched and never run without the guard.

    `while (decompressSizeCounter < localmap.Offset)` at DwgReader.cs:929 adds
    an empty page and then advances the counter by `descriptor.DecompressedSize`,
    which is the only thing that advances it. At zero the condition never
    changes and `LocalSections` grows until the process dies, so the absence of
    this guard is a hang rather than an allocation and there is no unpatched
    capture of it to hold beside the patched one.
    """

    def test_the_forged_input_declares_an_offset_the_counter_cannot_reach(self):
        # A zero page size with a zero offset does not hang, it divides by zero
        # further down where sizeLeft is taken. The offset above zero is what
        # makes this the hanging case rather than a different refusal.
        case = [c for c in CASES if c["name"] == "ac18_descriptor_page_size_zero"][0]
        descriptor = read_back(case["blob"])[0]
        assert descriptor["decompressed_size"] == 0
        assert descriptor["pages"][0]["offset"] > 0, (
            "the zero-page-size input no longer declares an offset the gap fill has "
            "to reach, so it is not the input the hang is an argument about"
        )

    def test_the_patched_capture_refuses_it_at_the_page_size(self):
        case = [c for c in CASES if c["name"] == "ac18_descriptor_page_size_zero"][0]
        result = scenario(case["scenario"])["result"]
        assert result["open_code"] == "CORRUPT_INPUT"
        assert "page size" in (result["open_detail"] or "")
        assert result["exit_code"] == 0, (
            "the recorder came back, which is the whole difference between this case "
            "with the guard and without it"
        )


class TestTheControlSaysTheCeilingIsNotRefusingEveryForgedMap:
    """Seven refusals on forged files prove nothing on their own: a reader that
    threw on any hand-written section map would produce the same captures.
    These are the pair that stops that."""

    def test_it_gets_past_every_ceiling(self, control):
        detail = control["open_detail"] or ""
        assert "ceiling" not in detail, (
            "the same forge with nothing inflated was refused by the ceiling, so the "
            f"bound is below what an ordinary AC18 section map asks for: {detail!r}"
        )

    def test_and_it_is_a_header_variable_that_refuses_it_instead(self, control):
        # Both guards passed, the descriptor loop finished, getSectionBuffer18
        # built the buffer out of one zero-filled gap and one real page, and
        # DwgHeaderReader read a text height out of it. That is a refusal about
        # what the file contains rather than about what it declares.
        assert control["open_code"] == "CORRUPT_INPUT"
        assert "TextHeight" in (control["open_detail"] or ""), control["open_detail"]

    def test_it_walks_further_than_any_of_the_refusals(self, control):
        worst = max(scenario(c["scenario"])["result"]["alloc_open_bytes"] for c in CASES)
        assert control["alloc_open_bytes"] > worst, (
            "the control allocated no more than a refusal did, so there is no "
            "evidence it went past the guards rather than being stopped by one"
        )
        assert control["alloc_open_bytes"] < FLOOR_BYTES


class TestTheDrawingsInTheCorpusStillOpen:
    """The guards run on every AC1018 and later file, including the real ones.
    A bound that refused a drawing AutoCAD wrote would show up here first."""

    def test_the_unmodified_fixture_opens(self, corpus_control):
        assert corpus_control["result"]["open_code"] == "OK"
        assert corpus_control["result"]["decode_code"] == "OK"

    def test_and_no_descriptor_guard_appears_in_its_capture(self, corpus_control):
        recorded = json.dumps(corpus_control)
        for site, _ in BRANCHES:
            assert site not in recorded, (
                f"{site} turns up in the capture of an unmodified drawing, so the "
                "guard is refusing something a real file declares"
            )

    def test_the_real_2018_drawing_opens_too(self):
        real = scenario("declared/real_ac1032_control")
        assert real["result"]["open_code"] == "OK"
        assert real["result"]["decode_code"] == "OK"
