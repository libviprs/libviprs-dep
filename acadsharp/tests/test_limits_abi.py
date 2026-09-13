"""Every bound viprs_acad_limits_v1 declares, and the cancel flag.

Each limit gets two scenarios: one where the bound is below what the fixture
needs, and one where it is above. The second is the important one. A bound that
has only ever been seen refusing is a bound that might be refusing everything,
and this repository has shipped that mistake before.

The scenarios are runs recorded by tests/fixtures/gen/regenerate.py in a
container, because pytest has no .NET (ADR 0001). Each one carries the sha256
of the fixture it read and the test recomputes it.
"""

import os

import pytest
from g13_support import FIXTURES, kinds, records, scenario, scenarios, sha256_file

ALL = scenarios()

# name -> (expected open code, expected decode code, the bound that tripped)
EXPECTED = {
    "gate/signature_out_of_range": ("UNSUPPORTED_FORMAT", "NOT_REACHED", None),
    "gate/signature_in_range_but_garbage": ("CORRUPT_INPUT", "NOT_REACHED", None),
    "limits/max_entities_1": ("OK", "LIMIT_EXCEEDED", None),
    "limits/max_entities_default": ("OK", "OK", None),
    "limits/max_polyline_2048": ("OK", "LIMIT_EXCEEDED", None),
    "limits/max_polyline_8192": ("OK", "OK", None),
    "limits/bulged_polyline_at_the_bound": ("OK", "OK", None),
    "limits/bulged_polyline_past_the_bound": ("OK", "LIMIT_EXCEEDED", None),
    "limits/max_string_4096": ("OK", "LIMIT_EXCEEDED", None),
    "limits/max_string_16384": ("OK", "OK", None),
    "limits/max_output_2048": ("OK", "LIMIT_EXCEEDED", None),
    "limits/max_block_depth_5": ("OK", "LIMIT_EXCEEDED", None),
    "limits/max_block_depth_7": ("OK", "OK", None),
    "limits/max_input_below_size": ("LIMIT_EXCEEDED", "NOT_REACHED", None),
    "limits/max_input_default": ("OK", "OK", None),
    "limits/max_input_below_size_unreadable": ("LIMIT_EXCEEDED", "NOT_REACHED", None),
    "limits/unreadable_control": ("CORRUPT_INPUT", "NOT_REACHED", None),
    "cancel/after_one_batch": ("OK", "CANCELED", None),
    "cancel/never": ("OK", "OK", None),
    "criticals/dimension_chain_deep": ("OK", "LIMIT_EXCEEDED", None),
    "criticals/dimension_chain_shallow": ("OK", "OK", None),
    "limits/spline_points_4096": ("OK", "LIMIT_EXCEEDED", None),
    "limits/spline_points_65536": ("OK", "OK", None),
}

NAMES = sorted(EXPECTED)


class TestTheCaptureIsOfTheTreeInFrontOfUs:
    @pytest.mark.parametrize(
        "name", sorted(s["name"] for s in ALL["scenarios"] if "fixture_sha256" in s)
    )
    def test_the_fixture_digest_matches(self, name):
        entry = scenario(name)
        fixture = os.path.basename(entry["input"])
        if not os.path.isfile(os.path.join(FIXTURES, fixture)):
            # The unreadable-copy scenarios name a copy rather than a path.
            fixture = "g13_line.dwg"
        assert sha256_file(os.path.join(FIXTURES, fixture)) == entry["fixture_sha256"], (
            f"{name} was recorded against a different {fixture} than the one in the tree"
        )

    def test_every_scenario_ran_to_completion(self):
        for s in ALL["scenarios"]:
            assert s["result"].get("exit_code") == 0, (
                f"{s['name']} did not exit cleanly, so whatever it reports is not a result"
            )


class TestEveryLimitRefusesAndEveryControlPasses:
    @pytest.mark.parametrize("name", NAMES)
    def test_the_codes_are_what_the_abi_says(self, name):
        want_open, want_decode, _ = EXPECTED[name]
        result = scenario(name)["result"]
        assert result["open_code"] == want_open, (
            f"{name}: open returned {result['open_code']}, expected {want_open}. "
            f"{result.get('open_detail')}"
        )
        assert result.get("decode_code") == want_decode, (
            f"{name}: decode returned {result.get('decode_code')}, expected {want_decode}. "
            f"{result.get('decode_detail')}"
        )

    @pytest.mark.parametrize("name", NAMES)
    def test_nothing_leaked_a_handle(self, name):
        assert scenario(name)["result"]["live_handles"] == 0, (
            f"{name} left a handle open. viprs_acad__test_live_handles() reads the same "
            "table the exports use, so this is the count a consumer would see"
        )

    def test_the_bulge_array_does_not_count_against_max_polyline_points(self):
        # The slot is four vertices and four bulges, so a bound that counted
        # both would see eight. It decodes at a bound of four, and the pair
        # above is the control: at three the same file is refused, so this is
        # not a bound that stopped applying.
        at = scenario("limits/bulged_polyline_at_the_bound")["result"]
        assert at["decode_code"] == "OK", (
            "a four-vertex polyline was refused at max_polyline_points 4, so the bound "
            "is counting the bulge array as well. It bounds vertices: the array is one "
            "f64 per vertex and adding it to the count silently halves the bound a "
            "caller thinks it set"
        )

    def test_each_refusal_has_a_control_beside_it(self):
        # The pairing is the point. A refusal with no passing control could be
        # a decoder that refuses everything.
        pairs = [
            ("limits/max_entities_1", "limits/max_entities_default"),
            ("limits/max_polyline_2048", "limits/max_polyline_8192"),
            ("limits/bulged_polyline_past_the_bound", "limits/bulged_polyline_at_the_bound"),
            ("limits/max_string_4096", "limits/max_string_16384"),
            ("limits/max_block_depth_5", "limits/max_block_depth_7"),
            ("limits/max_input_below_size", "limits/max_input_default"),
            ("cancel/after_one_batch", "cancel/never"),
        ]
        for refusal, control in pairs:
            r = scenario(refusal)["result"]
            c = scenario(control)["result"]
            assert (
                "LIMIT_EXCEEDED" in (r["open_code"], r.get("decode_code"))
                or r.get("decode_code") == "CANCELED"
            )
            assert c["open_code"] == "OK" and c["decode_code"] == "OK", (
                f"{control} is the control for {refusal} and it did not pass"
            )
            assert scenario(refusal)["input"] == scenario(control)["input"], (
                "a control on a different fixture controls nothing"
            )


class TestTheVersionGateRunsBeforeAnythingParses:
    """Two files, six good bytes each, and nothing after them.

    One declares a version this build reads and one does not. If the gate ran
    after the parse, both would come back CORRUPT_INPUT and be
    indistinguishable. They do not, which is what makes the gate a gate.
    """

    def test_a_version_outside_the_range_is_unsupported_format(self):
        result = scenario("gate/signature_out_of_range")["result"]
        assert result["open_code"] == "UNSUPPORTED_FORMAT"
        assert "AC1009" in result["open_detail"]
        assert "AC1014" in result["open_detail"] and "AC1032" in result["open_detail"]

    def test_the_same_file_with_a_version_in_range_is_corrupt_input(self):
        result = scenario("gate/signature_in_range_but_garbage")["result"]
        assert result["open_code"] == "CORRUPT_INPUT", (
            "the control file is 506 zero bytes behind a signature this build reads, so "
            "it has to fail in the parse. If it comes back UNSUPPORTED_FORMAT the gate "
            "is refusing on something other than the version"
        )

    def test_the_refused_file_really_is_an_ac1009_header(self):
        with open(os.path.join(FIXTURES, "g13_ac1009.dwg"), "rb") as f:
            head = f.read(6)
        assert head == b"AC1009"


class TestMaxInputIsAppliedBeforeTheFileIsRead:
    """The positive control that makes "without reading the file" measurable.

    A mode 000 copy of a fixture, made inside the container where Linux
    permissions are real, and the process running as a non-root user. Refusing
    on the size returns LIMIT_EXCEEDED because the length is a stat. Anything
    that opened it gets a permission failure, which is exactly what the control
    run shows.
    """

    def test_refusing_on_size_never_opens_it(self):
        result = scenario("limits/max_input_below_size_unreadable")["result"]
        assert result["open_code"] == "LIMIT_EXCEEDED"
        assert "max_input_bytes" in result["open_detail"]

    def test_the_control_proves_opening_it_would_have_failed(self):
        result = scenario("limits/unreadable_control")["result"]
        assert result["open_code"] == "CORRUPT_INPUT"
        assert "UnauthorizedAccess" in result["open_detail"], (
            "the control did not fail on permissions, so the file was readable and the "
            "scenario above proves nothing"
        )

    def test_the_control_was_checked_by_the_runner_too(self):
        for name in ("limits/max_input_below_size_unreadable", "limits/unreadable_control"):
            assert "read proven to fail" in scenario(name)["result"]["control"]


class TestTheBoundIsTheLimitAndNotACrash:
    """The mutation the issue asks for, run and recorded.

    The over-depth fixture nests six blocks deep. With max_block_depth at five
    it is LIMIT_EXCEEDED; with it at seven the same file decodes cleanly. If
    the refusal were a stack overflow or a parse failure, raising the bound
    would not fix it.
    """

    def test_below_the_depth_it_refuses(self):
        result = scenario("limits/max_block_depth_5")["result"]
        assert result["decode_code"] == "LIMIT_EXCEEDED"
        assert "max_block_depth" in result["decode_detail"]
        assert "expansion depth 6" in result["decode_detail"]

    def test_above_the_depth_the_same_file_decodes(self):
        result = scenario("limits/max_block_depth_7")["result"]
        assert result["decode_code"] == "OK"
        assert result["batches"] >= 1

    def test_both_runs_read_the_same_file(self):
        a = scenario("limits/max_block_depth_5")
        b = scenario("limits/max_block_depth_7")
        assert a["fixture_sha256"] == b["fixture_sha256"]


class TestCancellation:
    def test_the_flag_stops_the_decode_on_the_next_call(self):
        result = scenario("cancel/after_one_batch")["result"]
        assert result["decode_code"] == "CANCELED"
        assert result["batches"] == 1, (
            "the flag went up after the first batch, so exactly one batch should have "
            f"come back before the refusal, not {result['batches']}"
        )

    def test_the_same_decode_finishes_when_the_flag_is_left_alone(self):
        result = scenario("cancel/never")["result"]
        assert result["decode_code"] == "OK"
        assert result["batches"] > 1, (
            "the control has to take more than one batch, or setting a flag between two "
            "calls would never have had a second call to reach"
        )

    def test_the_cancelled_run_stopped_early(self):
        cancelled = scenario("cancel/after_one_batch")["result"]
        whole = scenario("cancel/never")["result"]
        assert cancelled["output_bytes"] < whole["output_bytes"]


class TestTheLimitsAreTheHeadersLimits:
    def test_every_field_in_the_header_has_a_scenario(self):
        header = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "include",
            "viprs_acadsharp.h",
        )
        with open(header) as f:
            text = f.read()
        fields = [
            "max_input_bytes",
            "max_entities",
            "max_string_bytes",
            "max_polyline_points",
            "max_block_depth",
            "max_output_bytes",
        ]
        for field in fields:
            assert field in text, f"{field} is not in the header any more"
        covered = " ".join(s["name"] for s in ALL["scenarios"])
        for field, token in (
            ("max_input_bytes", "max_input"),
            ("max_entities", "max_entities"),
            ("max_string_bytes", "max_string"),
            ("max_polyline_points", "max_polyline"),
            ("max_block_depth", "max_block_depth"),
            ("max_output_bytes", "max_output"),
        ):
            assert token in covered, f"{field} has no scenario that hits it"


class TestADimensionCannotRecurseTheProcessToDeath:
    """The first Critical: a DIMENSION's block can hold another DIMENSION.

    That was walked by calling Map again, so the nesting a file chose became
    recursion on the CLR stack, and nothing looked at max_block_depth on the
    way. A StackOverflowException cannot be caught in .NET, so the export's
    catch-all was never in the picture: the runtime calls FailFast, which is a
    SIGABRT in the consumer's address space from an untrusted file.

    The fixture is three thousand levels. The recursive walk died at roughly
    2686 stack frames and spent two or three of them per level, so it was gone
    somewhere around a thousand: a shallower fixture could pass against the
    broken build and prove nothing.
    """

    def test_the_process_came_back(self):
        # The assertion that matters. A return code can only be read from a
        # process that returned.
        result = scenario("criticals/dimension_chain_deep")["result"]
        assert result["exit_code"] == 0, (
            "the decode did not come back. A stack overflow is not a failure the "
            "boundary can report, it is the whole process going away"
        )

    def test_it_refused_on_the_depth_bound_and_said_so(self):
        result = scenario("criticals/dimension_chain_deep")["result"]
        assert result["decode_code"] == "LIMIT_EXCEEDED"
        detail = result["decode_detail"] or ""
        assert "max_block_depth" in detail, (
            f"the refusal did not come from the depth bound: {detail!r}. A decode that "
            "refused for some other reason looks identical from the code alone"
        )
        assert "DIMENSION" in detail, (
            "the refusal has to name the kind of expansion it stopped, or the bound "
            "reads as if it only ever applied to insertions"
        )

    def test_the_shallow_control_decodes_the_same_shape(self):
        result = scenario("criticals/dimension_chain_shallow")["result"]
        assert result["open_code"] == "OK" and result["decode_code"] == "OK", (
            "nested dimensions are refused outright, which would mean the fix is a "
            "decoder that cannot read a dimension rather than a bound"
        )
        assert result["batches"] >= 1

    def test_the_control_really_is_the_same_shape(self):
        # A control built differently controls nothing.
        shallow = kinds("g13_dimension_shallow.dwg")
        assert shallow.get("Line", 0) >= 4 and shallow.get("Text", 0) >= 2, (
            f"the shallow fixture does not look like a chain of dimensions: {shallow}"
        )

    def test_the_records_lifted_out_of_a_dimension_say_so(self):
        # Bit 0 of the prologue. They come out of an expansion now, and they
        # did not say so before, because the walk gave the dimension's block
        # the same depth as the drawing it sits in.
        for r in records("g13_dimension.dwg"):
            if r["kind"] == "Warning" and "READER_NOTIFICATION" in r["rest"]:
                continue
            assert r["flags"] & 1, (
                f"record {r['index']} came out of a dimension's block and is not marked "
                "as coming from an expansion"
            )


class TestAnExpansionThatEmitsNothingIsStillBounded:
    """The second Critical: max_entities counted records, and an INSERT emits none.

    A chain of block records each holding two insertions of the next expands
    exponentially, emits nothing, and keeps its nesting inside max_block_depth
    the whole way, so nothing counted it: not the record count, not
    max_output_bytes, not the depth. The counter the walk already kept was
    compared against nothing and read by nobody.

    The document is built in memory rather than read from a file. ACadSharp
    cannot store this shape: `new Insert(record)` deep-clones a record that
    belongs to a document, so assembling the chain and then pointing at it
    clones the whole expansion and never returns. The hole was never about a
    file format.
    """

    def test_the_entity_counter_is_what_stopped_it(self):
        result = scenario("criticals/fanout_bounded")["result"]
        assert result["code"] == "LIMIT_EXCEEDED"
        detail = result["detail"] or ""
        assert "max_entities" in detail, (
            f"the refusal did not come from the entity counter: {detail!r}"
        )

    def test_it_stopped_at_the_bound_rather_than_near_it(self):
        result = scenario("criticals/fanout_bounded")["result"]
        args = scenario("criticals/fanout_bounded")["args"]
        bound = int(args[args.index("--max-entities") + 1])
        assert result["entities_walked"] == bound + 1, (
            f"the walk visited {result['entities_walked']} entities against a bound of "
            f"{bound}. Stopping late is a counter that is checked somewhere other than "
            "where it is incremented"
        )

    def test_it_produced_no_records_at_all(self):
        # Which is the whole point: a bound on output would have seen nothing.
        result = scenario("criticals/fanout_bounded")["result"]
        assert result["records"] == 0

    def test_the_document_is_small_and_the_expansion_is_not(self):
        result = scenario("criticals/fanout_bounded")["result"]
        assert result["entities_in_document"] <= 64, (
            "the fan-out fixture stopped being a small document, which is what made it "
            "worth refusing"
        )
        assert result["entities_walked"] > result["entities_in_document"] * 1000

    def test_the_nesting_stayed_inside_the_depth_bound(self):
        # If the depth bound had caught it, the entity counter would not be
        # the thing under test.
        result = scenario("criticals/fanout_bounded")["result"]
        assert result["depth"] < result["max_block_depth"], (
            "the fan-out is deeper than max_block_depth, so this scenario is testing "
            "the depth bound and not the entity counter"
        )

    def test_the_shallow_control_walks_the_same_shape_to_the_end(self):
        result = scenario("criticals/fanout_shallow")["result"]
        assert result["code"] == "OK", (
            "the fan-out shape is refused outright, so the bound is not a bound"
        )
        assert result["records"] == 0
        assert result["entities_walked"] > 1

    def test_the_two_runs_are_the_same_shape_at_different_sizes(self):
        bounded = scenario("criticals/fanout_bounded")["result"]
        shallow = scenario("criticals/fanout_shallow")["result"]
        assert bounded["width"] == shallow["width"]
        assert bounded["depth"] > shallow["depth"]


class TestCancellationReachesTheWalk:
    """A decode that yields nothing never gives the export a chance to look.

    The flag was read once at the entry to decode_next_batch. On a document
    that produces no record, the call does not return, so a caller that sets
    the flag is waiting on a decode that will never read it. Polling inside
    the walk is the only place a poll helps.
    """

    def test_the_walk_noticed_a_flag_that_was_already_up(self):
        result = scenario("criticals/fanout_cancelled")["result"]
        assert result["code"] == "CANCELED", (
            f"the walk ran to {result.get('code')} with the cancel flag up the whole "
            "time, so nothing inside it is reading the flag"
        )
        assert result["cancel_polls"] >= 1, "the flag was never read"

    def test_it_stopped_early_rather_than_after_the_whole_expansion(self):
        cancelled = scenario("criticals/fanout_cancelled")["result"]
        bounded = scenario("criticals/fanout_bounded")["result"]
        assert cancelled["entities_walked"] < bounded["entities_walked"] / 10, (
            "cancelling saved almost nothing, which means the poll is happening after "
            "the work rather than during it"
        )

    def test_it_produced_no_record_to_return_on(self):
        # The condition that makes the between-batches read useless.
        assert scenario("criticals/fanout_cancelled")["result"]["records"] == 0


class TestASplinesPointsAreCounted:
    """max_polyline_points did not apply to splines.

    The encoder guarded Polyline and Polygon and nothing else, and it guarded
    after the list existed: the control points, the knots and the weights were
    already allocated by the time anything compared them to the caller's
    bound.
    """

    def test_a_wide_spline_is_refused(self):
        result = scenario("limits/spline_points_4096")["result"]
        assert result["decode_code"] == "LIMIT_EXCEEDED"
        detail = result["decode_detail"] or ""
        assert "max_polyline_points" in detail
        assert "Spline" in detail, (
            f"the refusal does not say it was a spline: {detail!r}. The field's own "
            "documentation used to say Polyline, which is how the arm was missed"
        )

    def test_the_same_spline_passes_when_the_bound_allows_it(self):
        assert scenario("limits/spline_points_65536")["result"]["decode_code"] == "OK"

    def test_the_refusal_happens_before_the_points_are_gathered(self):
        # The number in the message is the source entity's point count, not
        # the length of a list that was built and then measured.
        detail = scenario("limits/spline_points_4096")["result"]["decode_detail"]
        assert "20000 points" in detail


class TestTheHostileCorpusFailsInMoreThanOnePlace:
    """Four inputs that all die at the same line are one test, not four.

    Every malformed derivative in this suite is refused inside DwgReader with
    the same EndOfStreamException, so until the hostile-but-well-formed
    fixtures landed, the flattener, the encoder and the batch writer had never
    seen a byte of hostile input. Both Criticals live exactly in that gap:
    inputs that open cleanly and then break during the walk.
    """

    HOSTILE = (
        "malformed/truncated_25",
        "malformed/bitflip_64",
        "criticals/dimension_chain_deep",
        "limits/spline_points_4096",
        "limits/max_output_2048",
    )

    def test_they_do_not_all_fail_the_same_way(self):
        details = set()
        for name in self.HOSTILE:
            r = scenario(name)["result"]
            details.add((r.get("open_detail") or r.get("decode_detail") or "")[:60])
        assert len(details) >= 4, (
            f"the hostile corpus produced {len(details)} distinct failures across "
            f"{len(self.HOSTILE)} inputs. A corpus where every case dies at the same "
            "line is covering one line"
        )

    def test_some_of_them_get_past_the_reader(self):
        past_open = [name for name in self.HOSTILE if scenario(name)["result"]["open_code"] == "OK"]
        assert len(past_open) >= 3, (
            "every hostile input is refused before the walk starts, so the flattener is "
            "still untested against one"
        )

    def test_and_some_of_them_do_not(self):
        at_open = [name for name in self.HOSTILE if scenario(name)["result"]["open_code"] != "OK"]
        assert at_open, "nothing exercises the reader's own refusal any more"

    def test_none_of_them_took_the_process_with_it(self):
        for name in self.HOSTILE:
            assert scenario(name)["result"]["exit_code"] == 0, f"{name} did not come back"

    def test_none_of_them_leaked_a_handle(self):
        for name in self.HOSTILE:
            r = scenario(name)["result"]
            if "live_handles" in r:
                assert r["live_handles"] == 0, f"{name} left a handle open"


class TestMaxOutputBytesIsAnExactCeiling:
    """The bound is checked before the batch is committed, not after it.

    It used to be checked after `written` had been set and the batch framed, so
    a caller allowing 2 KiB of output got one whole 64 KiB batch handed to it
    and then a refusal. The effective ceiling was "max_output_bytes plus a
    batch", which nothing documented and no caller could plan around.
    """

    SCENARIO = "limits/max_output_2048"

    def allowed(self):
        args = scenario(self.SCENARIO)["args"]
        assert args[0] == "--max-output", args
        return int(args[1])

    def test_it_refused(self):
        assert scenario(self.SCENARIO)["result"]["decode_code"] == "LIMIT_EXCEEDED"

    def test_it_produced_something_before_refusing(self):
        # The positive control, and it is the whole reason this class exists.
        # The refusal used to arrive as a throw that left *written at zero, so
        # a test asserting only "no more than 2048 bytes" passed against a
        # decode that had really written 65,548 of them into the caller's
        # buffer. Zero output is the shape of that bug, not of a fix.
        produced = scenario(self.SCENARIO)["result"]["output_bytes"]
        assert produced > 0, (
            "the decode reported no output at all before refusing, which is what "
            "a refusal that discards its own byte count looks like"
        )

    def test_it_stopped_inside_the_bound(self):
        r = scenario(self.SCENARIO)["result"]
        allowed = self.allowed()
        assert r["output_bytes"] <= allowed, (
            f"the decode handed back {r['output_bytes']} bytes with max_output_bytes "
            f"set to {allowed}, so the ceiling is really the ceiling plus a batch"
        )


class TestARefusalNeverLeavesAFramedBatchBehind:
    """A call that refuses must not have written a batch into the buffer.

    The harness clears the twelve header bytes before every call, so the flag
    it records afterwards is about the call that just returned and nothing
    earlier. A framed batch beside a non-OK code and `*written` of zero is the
    one shape a caller cannot defend against: it has been told there is nothing
    there.
    """

    REFUSALS = sorted(
        s["name"]
        for s in ALL["scenarios"]
        if (s.get("result") or {}).get("decode_code") not in (None, "OK", "SKIPPED", "NOT_REACHED")
    )

    def test_there_are_refusals_to_look_at(self):
        assert len(self.REFUSALS) >= 5, (
            f"only {len(self.REFUSALS)} scenarios refuse during the decode, so the "
            "check below is about almost nothing"
        )

    @pytest.mark.parametrize("name", REFUSALS)
    def test_the_refusing_call_framed_nothing(self, name):
        r = scenario(name)["result"]
        assert "framed_batch_after_refusal" in r, (
            f"{name} was recorded by a harness that did not look, so this cannot fail"
        )
        assert r["framed_batch_after_refusal"] is False, (
            f"{name} returned {r['decode_code']} with a framed batch sitting in the caller's buffer"
        )
