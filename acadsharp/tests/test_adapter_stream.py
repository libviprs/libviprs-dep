"""Every fixture's decoded stream, compared against its committed expectation.

The expectation is a canonical text dump of the records the adapter produced,
one per line, floats at six decimals. It is the test: if the flattener changes
what it emits for a fixture, the dump changes and the diff is readable.

pytest cannot run the decoder, so the failing state the issue asks for (change
a fixture, leave the expectation) is caught here by the digest the capture
carries rather than by rerunning the decode. A changed fixture with a stale
expectation is a digest mismatch that names the fixture, and rerunning
``fixturegen decode --check`` is what then names the first differing record.
"""

import os
import re

import pytest
from g13_support import (
    CURVE_KINDS,
    EXPECTATIONS,
    FIXTURES,
    RECORD_KINDS,
    committed_fixtures,
    expectation_path,
    first_difference,
    kinds,
    load_script,
    manifest,
    parse,
    read_expectation,
    recorded_fixtures,
    records,
    sha256_file,
)

MANIFEST = manifest()
FIXTURE_NAMES = sorted(MANIFEST["fixtures"])

RECORDED = recorded_fixtures()
COMMITTED = committed_fixtures()


# What the generator has been told to record an expectation for. This is the
# upstream control and the manifest is downstream of it: expectations() walks
# DUMPED and nothing else, so a fixture that is not in here can never get a
# manifest entry however often anybody regenerates. #94's seven were never
# added, which is the mechanism behind all of it.
DUMPED = tuple(load_script(os.path.join(FIXTURES, "gen", "regenerate.py"), "regenerate").DUMPED)

# A DWG in tests/fixtures that the generator has not been told to record and
# that no committed capture mentions. Nothing decodes it, nothing pins its
# bytes, and nothing notices when it changes: it is a file the repository
# carries and does not check.
#
# Seven came in with #94 ahead of the refusal work that would record them,
# which is a reasonable thing to do once and a bad thing to be able to do by
# accident. A name leaves this list the moment its fixture joins DUMPED, which
# is a one-line edit in the branch that adds it and does not wait for a
# regeneration.
#
# All seven left that way: g13_solid and g13_mesh when SOLID and MESH landed,
# g13_polyface_mesh and g13_polygon_mesh when the IPolyline arm stopped
# swallowing them, g13_ray_xline when RAY and XLINE became an explicit refusal,
# and g13_point and g13_tolerance when the two refusals they witness were
# recorded rather than implemented. Recording is not implementing: both kinds
# are still refused, and tests/test_refused_point_tolerance.py is what says so
# on an input that carries nothing else.
#
# So it is empty, and it stays here rather than going away. The guard that
# reads it is the one that catches the next DWG arriving without a DUMPED
# line, and empty is the state that guard is meant to be in: nothing in
# tests/fixtures is excused from being checked today. A name going back on is
# a decision somebody writes a reason beside, which is the whole shape of it.
CARRIED_NOT_RECORDED = ()

# Every fixture that has ever had a committed dump.
#
# CARRIED_NOT_RECORDED's mirror, and the direction nothing else in this
# repository looks in. That list catches a DWG that arrived and was never
# recorded; this one catches a DWG that was recorded and stopped being. They
# are different failures and only the second one is an attack: the first is
# somebody forgetting a line, the second is somebody removing the only witness
# to a behaviour and taking the red test with it.
#
# It closes a real hole, measured on this tree. Take g13_polygon_mesh.dwg out
# of DUMPED, delete its MANIFEST.json entry and its dump, and put the name on
# CARRIED_NOT_RECORDED, and pytest is 2954 passed, 0 failed while the replay
# drops to 37 fixtures and says "37 of 37 replay identical", exit 0. That
# fixture is the whole of the corpus's evidence that a POLYGON_MESH is refused
# rather than emitted as a polyline (libviprs-dep#82): neither real drawing
# carries one. Every guard in the chain gets easier to satisfy as you trim,
# because they are all parametrised over what is on disk, and
# test_the_guard_would_see_a_new_fixture asserts len(COMMITTED) > len(DUMPED),
# which a removal makes more true.
#
# Against this list the same trim is a one-line diff whose only purpose is
# deleting evidence, in a file a reviewer reads.
#
# Two edits keep it honest and both are one line, in the branch that does the
# thing rather than in a later cleanup:
#
#   - A fixture joining DUMPED joins this tuple in the same commit.
#     test_every_recorded_fixture_is_on_the_ratchet is what says so, and
#     without it the list goes stale and the next trim is invisible again.
#   - Retiring a fixture for real (the kind it witnesses is implemented, so
#     the refusal it records is gone) means deleting its name from here on
#     purpose, next to the change that made it obsolete. That is the edit this
#     is asking for: a deliberate one, with a reason beside it, rather than a
#     removal nothing notices.
ONCE_RECORDED = (
    "g11_codepage.dwg",
    "g11_shapes.dwg",
    "g13_arc.dwg",
    "g13_bad_extents.dwg",
    "g13_circle.dwg",
    "g13_deep_blocks.dwg",
    "g13_dimension.dwg",
    "g13_dimension_shallow.dwg",
    "g13_ellipse.dwg",
    "g13_empty_view.dwg",
    "g13_face3d.dwg",
    "g13_hatch.dwg",
    "g13_insert.dwg",
    "g13_line.dwg",
    "g13_long_text.dwg",
    "g13_mesh.dwg",
    "g13_mesh_bad_faces.dwg",
    "g13_mirrored_bulge.dwg",
    "g13_nan_bulge.dwg",
    "g13_nonuniform.dwg",
    "g13_ocs_mirror.dwg",
    "g13_ocs_plane.dwg",
    "g13_ocs_rotated.dwg",
    "g13_ocs_skew.dwg",
    "g13_point.dwg",
    "g13_polyface_mesh.dwg",
    "g13_polygon_mesh.dwg",
    "g13_polyline.dwg",
    "g13_ray_xline.dwg",
    "g13_scale_1x.dwg",
    "g13_slot.dwg",
    "g13_slot_block.dwg",
    "g13_solid.dwg",
    "g13_spline.dwg",
    "g13_text.dwg",
    "g13_tolerance.dwg",
    "g13_two_entities.dwg",
    "g13_unsupported.dwg",
    "g13_wide_polyline.dwg",
    "g13_xref.dwg",
    "real_AC1018.dwg",
    "real_AC1032.dwg",
)


def no_longer_recorded(once, dumped):
    """Every name that had a committed dump and does not have one now.

    A function rather than an expression inside the test, so the ratchet's own
    failure path can be run on input that is not the tree. A guard nobody has
    watched fail is the thing this file exists to stop.
    """
    return sorted(set(once) - set(dumped))


# Named by a capture that did not record which file it measured. The streaming
# benchmark decodes these two and test_adapter_benchmarks.py asserts on the
# numbers, including that the 16x point is sixteen times the 1x one, and the
# 1x fixture carries a digest because it also has a dump. These two have
# neither, so the assertions are about a run of whatever happens to be on
# disk. Fixing it means a `fixture_sha256` per streaming entry, which is a
# benchmark regeneration, and this campaign does not regenerate benchmarks.
NAMED_BUT_NOT_PINNED = (
    "g13_scale_16x.dwg",
    "g13_scale_4x.dwg",
)

QUOTED = re.compile(r'"(?:[^"\\]|\\.)*"')


def strip_quoted(line):
    """The line with every quoted run removed.

    A drawing's own text is data the adapter copies, not a number it formatted,
    so the precision rule is about everything outside the quotes.
    """
    return QUOTED.sub('""', line)


class TestTheExpectationsExist:
    def test_there_are_expectations_at_all(self):
        assert FIXTURE_NAMES, (
            "no fixture has a committed expectation, so nothing here checks anything"
        )

    @pytest.mark.parametrize("fixture", FIXTURE_NAMES)
    def test_each_fixture_has_a_dump(self, fixture):
        assert os.path.isfile(expectation_path(fixture)), (
            f"{fixture} is in the manifest with no dump beside it"
        )

    @pytest.mark.parametrize("fixture", FIXTURE_NAMES)
    def test_each_dump_has_a_fixture(self, fixture):
        assert os.path.isfile(os.path.join(FIXTURES, fixture))

    def test_no_dump_is_orphaned(self):
        dumps = {name[:-4] for name in os.listdir(EXPECTATIONS) if name.endswith(".txt")}
        known = {os.path.splitext(f)[0] for f in FIXTURE_NAMES}
        assert dumps <= known, f"{sorted(dumps - known)} has no fixture in the manifest"


class TestNoFixtureIsOutsideEveryCheck:
    """The direction test_no_dump_is_orphaned does not look in.

    That one catches a dump with no fixture. This catches the other way round,
    which is the one that actually happened: #94 landed seven DWGs and the
    parametrisation above is built from MANIFEST["fixtures"], so all seven sat
    in tests/fixtures outside every check in this repository. Nothing decoded
    them, nothing pinned their bytes, and editing one changed nothing any test
    could see.

    The manifest is the symptom and DUMPED is the cause. `expectations()`
    walks that tuple and nothing else, so a fixture missing from it cannot get
    a manifest entry however often anybody regenerates, and a guard reading
    only the manifest would tell the next person to rerun the generator when
    what they actually need is to add a name. So the first case below asks
    DUMPED, and the manifest's agreement with it is a case of its own.

    Neither allow-list skips, because a skip is the same colour as a pass and
    both lists are meant to shrink. An excused fixture is asserted to still
    need excusing, so the day one is recorded the test that goes red is the
    one telling you to take it off the list.
    """

    @pytest.mark.parametrize("fixture", COMMITTED)
    def test_every_fixture_is_one_the_generator_records(self, fixture):
        recorded = fixture in DUMPED or fixture in RECORDED
        if fixture in CARRIED_NOT_RECORDED:
            assert not recorded, (
                f"{fixture} is recorded now, so take it off CARRIED_NOT_RECORDED. An "
                "allow-list that does not shrink is a list of things nobody checks"
            )
            return
        assert recorded, (
            f"{fixture} is in tests/fixtures, is not in regenerate.py's DUMPED, and "
            "no scenario or benchmark names it, so nothing in this repository reads "
            "it and nothing notices when it changes. Add it to DUMPED and rerun "
            "tests/fixtures/gen/regenerate.py --only expectations, or add it to "
            "CARRIED_NOT_RECORDED with a reason"
        )

    def test_the_generator_and_the_manifest_agree_on_what_is_dumped(self):
        # The state a branch is in between adding a name to DUMPED and
        # regenerating. That is a legitimate state to push in and not a
        # legitimate state to merge in, which is what makes it a test rather
        # than a comment.
        added = sorted(set(DUMPED) - set(FIXTURE_NAMES))
        gone = sorted(set(FIXTURE_NAMES) - set(DUMPED))
        assert not added and not gone, (
            f"regenerate.py dumps {added} that MANIFEST.json has no entry for, and "
            f"the manifest carries {gone} the generator no longer dumps. Rerun "
            "tests/fixtures/gen/regenerate.py --only expectations and read the diff"
        )

    @pytest.mark.parametrize("fixture", COMMITTED)
    def test_every_fixture_a_capture_uses_is_pinned_by_its_digest(self, fixture):
        if fixture in CARRIED_NOT_RECORDED:
            return
        if fixture in DUMPED and fixture not in RECORDED:
            # Named for recording and not recorded yet. The case above is
            # where that is reported, and reporting it twice would read as two
            # problems.
            return
        pinned = any(RECORDED.get(fixture, ()))
        if fixture in NAMED_BUT_NOT_PINNED:
            assert not pinned, (
                f"{fixture} carries a recorded digest now, so take it off NAMED_BUT_NOT_PINNED"
            )
            return
        assert pinned, (
            f"a capture measures {fixture} and recorded no sha256 for it, so the "
            "numbers it asserts on are about whatever file is on disk"
        )

    @pytest.mark.parametrize("fixture", CARRIED_NOT_RECORDED + NAMED_BUT_NOT_PINNED)
    def test_the_allow_lists_name_files_that_are_here(self, fixture):
        assert os.path.isfile(os.path.join(FIXTURES, fixture)), (
            f"{fixture} is excused from a check and is not in the tree, so the "
            "exception outlived the file"
        )

    def test_every_dumped_fixture_is_a_file(self):
        # DUMPED is the list this guard trusts, so it gets a control of its
        # own: a name in it with no DWG beside it is a regeneration that
        # cannot run, reported here rather than as a RuntimeError halfway
        # through a container.
        missing = [name for name in DUMPED if not os.path.isfile(os.path.join(FIXTURES, name))]
        assert not missing, f"regenerate.py dumps {missing}, which are not in tests/fixtures"

    def test_the_guard_would_see_a_new_fixture(self):
        # The control. Everything above is parametrised over what is on disk,
        # so if that listing ever came back short every case would pass by
        # being absent.
        assert len(COMMITTED) > len(DUMPED), (
            "the fixture listing found no more files than the generator dumps, which "
            "is either true or a broken listing, and this guard cannot tell"
        )
        assert "a_fixture_nobody_committed.dwg" not in RECORDED


class TestNoFixtureStopsBeingEvidence:
    """The ratchet. Everything above is about a fixture that never got in.

    This is about one that was in and left, which is the direction that looks
    like housekeeping and reads green: fewer parametrised cases, all passing,
    a smaller replay that still says every fixture it ran is identical. The
    suite gets 17 tests shorter and nothing says so.
    """

    def test_no_fixture_stops_being_recorded(self):
        gone = no_longer_recorded(ONCE_RECORDED, DUMPED)
        assert not gone, (
            f"{gone} had a committed dump and no longer does, so the corpus replay "
            "has stopped covering it. Nothing else in this repository would notice. "
            "If the fixture is genuinely retired, take its name off ONCE_RECORDED in "
            "this commit and say beside it what made it obsolete"
        )

    def test_every_recorded_fixture_is_on_the_ratchet(self):
        # The other half, and the one that keeps the list from going stale. A
        # fixture recorded after this list was written and never added to it
        # is a fixture the ratchet cannot protect, and the day somebody trims
        # it we are back where we started.
        new = sorted(set(DUMPED) - set(ONCE_RECORDED))
        assert not new, (
            f"{new} is dumped and is not on ONCE_RECORDED, so removing it again later "
            "would be invisible. Add the name, in the commit that adds it to DUMPED"
        )

    def test_a_name_is_never_both_recorded_and_excused(self):
        # The signature of the trim: a name that had a dump, does not have one
        # now, and has acquired a reason for not having one. Caught by the
        # first case too, and named separately because this combination is not
        # a mistake anybody makes by accident.
        both = sorted(set(ONCE_RECORDED) & set(CARRIED_NOT_RECORDED))
        assert not both, (
            f"{both} is on ONCE_RECORDED and on CARRIED_NOT_RECORDED at once, which "
            "says a fixture that used to be recorded is now excused from being. That "
            "is what deleting evidence looks like as a diff"
        )

    def test_the_ratchet_catches_the_trim_it_was_written_for(self):
        # The positive control, and it pins the measured attack rather than a
        # generic one: g13_polygon_mesh.dwg is the only fixture in the corpus
        # that witnesses POLYGON_MESH being refused instead of emitted as a
        # polyline, so it is the single removal that costs the most and shows
        # the least.
        trimmed = [name for name in DUMPED if name != "g13_polygon_mesh.dwg"]
        assert no_longer_recorded(ONCE_RECORDED, trimmed) == ["g13_polygon_mesh.dwg"]

    def test_an_emptied_ratchet_would_say_nothing(self):
        # The other control, and the reason the length is pinned rather than
        # left implicit: `set() - set(anything)` is empty, so a ratchet that
        # lost its names passes every case above by having nothing to say. The
        # second line is that failure mode, spelled out; the first is what
        # stops it, since the list can never be shorter than DUMPED.
        assert len(ONCE_RECORDED) == len(set(ONCE_RECORDED)) >= len(DUMPED)
        assert no_longer_recorded((), DUMPED) == []


class TestTheExpectationIsOfTheFixtureInTheTree:
    """Half of what makes every other check in this file mean something.

    A committed dump and a committed fixture can disagree silently: the dump is
    text, the fixture is bytes, and nothing in a pytest run reads DWG. So the
    manifest records the sha256 the dump was produced from, and this recomputes
    it. Change a fixture without rerunning the generator and this is the test
    that goes red, naming the fixture.

    The other half is test_shim_digest.py, which does the same for the code
    that read the fixture. Either one alone leaves a way for a dump to be a
    recording of something that is no longer here.
    """

    @pytest.mark.parametrize("fixture", FIXTURE_NAMES)
    def test_the_digest_matches(self, fixture):
        entry = MANIFEST["fixtures"][fixture]
        actual = sha256_file(os.path.join(FIXTURES, fixture))
        assert actual == entry["sha256"], (
            f"{fixture} is not the file the expectation was produced from. Rerun "
            f"tests/fixtures/gen/regenerate.py and read the diff it makes to "
            f"{os.path.relpath(expectation_path(fixture), FIXTURES)}"
        )

    @pytest.mark.parametrize("fixture", FIXTURE_NAMES)
    def test_the_size_matches(self, fixture):
        entry = MANIFEST["fixtures"][fixture]
        assert os.path.getsize(os.path.join(FIXTURES, fixture)) == entry["bytes"]


class TestTheDumpsAreWellFormed:
    @pytest.mark.parametrize("fixture", FIXTURE_NAMES)
    def test_every_line_parses(self, fixture):
        for line in read_expectation(fixture):
            assert parse(line) is not None, f"{fixture}: {line!r} is not a record line"

    @pytest.mark.parametrize("fixture", FIXTURE_NAMES)
    def test_the_indices_run_from_zero(self, fixture):
        for i, r in enumerate(records(fixture)):
            assert r["index"] == i, f"{fixture}: record {i} is numbered {r['index']}"

    @pytest.mark.parametrize("fixture", FIXTURE_NAMES)
    def test_every_kind_is_one_the_wire_carries(self, fixture):
        for r in records(fixture):
            assert r["kind"] in RECORD_KINDS, (
                f"{fixture}: {r['kind']} is not a record type docs/WIRE.md names"
            )

    @pytest.mark.parametrize("fixture", FIXTURE_NAMES)
    def test_the_record_count_matches_the_manifest(self, fixture):
        assert len(records(fixture)) == MANIFEST["fixtures"][fixture]["records"]

    @pytest.mark.parametrize("fixture", FIXTURE_NAMES)
    def test_the_kind_counts_match_the_manifest(self, fixture):
        assert kinds(fixture) == MANIFEST["fixtures"][fixture]["kinds"]

    @pytest.mark.parametrize("fixture", FIXTURE_NAMES)
    def test_every_float_is_at_the_fixed_precision(self, fixture):
        # A dump written at full precision is a record of the machine that
        # wrote it, and the first rerun on another architecture turns a
        # committed file into a diff nobody can read.
        #
        # Quoted runs are skipped, because a drawing's own text can be a
        # number: real_AC1032.dwg carries a label reading 7.0711, which is not
        # a coordinate this layer formatted and not something it may reformat.
        for line in read_expectation(fixture):
            for value in re.findall(r"-?\d+\.\d+", strip_quoted(line)):
                decimals = value.split(".")[1]
                assert len(decimals) == 6, (
                    f"{fixture}: {value} is not at six decimals, so the dump is not canonical"
                )

    def test_the_precision_check_can_see_a_bad_number(self):
        # A scan that skips quoted text could skip everything. This is the
        # control: a line with a short float outside the quotes has to fail.
        bad = "00000 Line handle=1 flags=0 pts=[(1.5,0.000000,0.000000)]"
        assert re.findall(r"-?\d+\.\d+", strip_quoted(bad)) == ["1.5", "0.000000", "0.000000"]

    @pytest.mark.parametrize("fixture", FIXTURE_NAMES)
    def test_no_negative_zero_survived(self, fixture):
        for line in read_expectation(fixture):
            assert "-0.000000" not in strip_quoted(line), (
                f"{fixture}: a negative zero is the same point as a zero and compares "
                "unequal to it, so the dump has to normalise it"
            )


class TestTheDifferIsUsable:
    """The differ is what makes a stale expectation readable, so it is tested.

    A diff tool that only ever runs on identical input is a diff tool nobody
    has seen work.
    """

    def test_identical_streams_have_no_difference(self):
        lines = read_expectation("g13_arc.dwg")
        assert first_difference(lines, list(lines)) is None

    def test_it_names_the_first_differing_record(self):
        lines = read_expectation("g13_hatch.dwg")
        mutated = list(lines)
        mutated[2] = mutated[2].replace("flags=0", "flags=1")
        diff = first_difference(lines, mutated)
        assert diff is not None and diff.startswith("record 2 differs")

    def test_a_shorter_stream_is_a_difference_at_the_first_missing_record(self):
        lines = read_expectation("g13_hatch.dwg")
        diff = first_difference(lines, lines[:-1])
        assert diff is not None
        assert f"record {len(lines) - 1} differs" in diff
        assert "<end of stream>" in diff


class TestTheStreamsSayWhatTheCorpusWasBuiltToSay:
    def test_the_real_world_drawing_exercises_most_of_the_wire(self):
        # A round trip of our own writer tests the reader against the writer.
        # A file AutoCAD produced is what production sees, and it is the only
        # fixture here that reaches every record type at once.
        seen = kinds("real_AC1032.dwg")
        missing = [k for k in RECORD_KINDS if k not in seen]
        assert not missing, f"the real-world drawing produced no {missing} records"

    def test_the_real_world_drawing_is_not_trivial(self):
        assert MANIFEST["fixtures"]["real_AC1032.dwg"]["records"] > 300

    @pytest.mark.parametrize("fixture", FIXTURE_NAMES)
    def test_no_decode_left_a_handle_open(self, fixture):
        assert MANIFEST["fixtures"][fixture]["live_handles"] == 0

    @pytest.mark.parametrize("fixture", FIXTURE_NAMES)
    def test_every_dumped_fixture_decoded_cleanly(self, fixture):
        assert MANIFEST["fixtures"][fixture]["decode_code"] == "OK"

    def test_the_curve_kinds_appear_somewhere_in_the_corpus(self):
        seen = set()
        for fixture in FIXTURE_NAMES:
            seen |= set(kinds(fixture))
        for kind in CURVE_KINDS:
            assert kind in seen, f"no fixture in the corpus produces a {kind} record"
