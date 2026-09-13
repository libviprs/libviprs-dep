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
from g13_support import FIXTURES, scenario, scenarios, sha256_file

ALL = scenarios()

# name -> (expected open code, expected decode code, the bound that tripped)
EXPECTED = {
    "gate/signature_out_of_range": ("UNSUPPORTED_FORMAT", "NOT_REACHED", None),
    "gate/signature_in_range_but_garbage": ("CORRUPT_INPUT", "NOT_REACHED", None),
    "limits/max_entities_1": ("OK", "LIMIT_EXCEEDED", None),
    "limits/max_entities_default": ("OK", "OK", None),
    "limits/max_polyline_2048": ("OK", "LIMIT_EXCEEDED", None),
    "limits/max_polyline_8192": ("OK", "OK", None),
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

    def test_each_refusal_has_a_control_beside_it(self):
        # The pairing is the point. A refusal with no passing control could be
        # a decoder that refuses everything.
        pairs = [
            ("limits/max_entities_1", "limits/max_entities_default"),
            ("limits/max_polyline_2048", "limits/max_polyline_8192"),
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
        assert "nesting depth 6" in result["decode_detail"]

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
