"""Malformed input: derived here, refused there, and nothing leaked.

The derivatives are not committed. They are derived at test time from the
fixture in the tree, exactly the way the runner derived them before decoding
them, and the digests are compared. That is what makes a recorded refusal a
refusal of the bytes this test is holding rather than of whatever was on disk
the day someone ran the generator.
"""

import pytest
from g13_support import FLIP_COUNT, FLIP_SEED, MALFORMED_SOURCE, derived_inputs, scenario, scenarios

DERIVED = derived_inputs()
ALL = scenarios()

# A malformed input is allowed to come back corrupt, or to be refused on its
# version signature when a flip landed inside the six bytes that carry it, or
# to decode successfully with warnings. What it may never do is abort the
# process or leave a handle open.
ACCEPTABLE = ("CORRUPT_INPUT", "UNSUPPORTED_FORMAT", "OK")


class TestTheDerivationMatchesTheRecordedOne:
    def test_the_runner_used_the_same_recipe(self):
        recorded = ALL["malformed"]
        assert recorded["source"] == MALFORMED_SOURCE
        assert tuple(recorded["truncations"]) == (25, 50, 90)
        assert recorded["flip_count"] == FLIP_COUNT
        assert recorded["flip_seed"] == FLIP_SEED

    @pytest.mark.parametrize("case", DERIVED, ids=[c["name"] for c in DERIVED])
    def test_the_bytes_are_the_bytes_that_were_decoded(self, case):
        entry = scenario(case["scenario"])
        assert entry["derived"]["sha256"] == case["sha256"], (
            f"{case['name']} derived here is not the file the capture was produced "
            f"from. Either the fixture changed or the derivation did"
        )
        assert entry["derived"]["bytes"] == case["bytes"]

    def test_every_derivative_is_shorter_or_different(self):
        # A derivation that produced the original file would pass every test
        # below and test nothing.
        import os

        from g13_support import FIXTURES, sha256_file

        original = sha256_file(os.path.join(FIXTURES, MALFORMED_SOURCE))
        for case in DERIVED:
            assert case["sha256"] != original, f"{case['name']} is the original file"


class TestEveryDerivativeIsRefusedCleanly:
    @pytest.mark.parametrize("case", DERIVED, ids=[c["name"] for c in DERIVED])
    def test_the_code_is_one_a_consumer_can_act_on(self, case):
        result = scenario(case["scenario"])["result"]
        code = result["open_code"]
        assert code in ACCEPTABLE, (
            f"{case['name']} came back {code}. Untrusted input may be corrupt or may be "
            f"a format this build does not read, and neither is an internal error: "
            f"{result.get('open_detail')}"
        )

    @pytest.mark.parametrize("case", DERIVED, ids=[c["name"] for c in DERIVED])
    def test_the_process_did_not_abort(self, case):
        result = scenario(case["scenario"])["result"]
        assert result["exit_code"] == 0, (
            f"{case['name']} killed the process. A DWG reader that aborts takes the "
            "host down with it, and no try/catch on the boundary can contain a SIGABRT"
        )

    @pytest.mark.parametrize("case", DERIVED, ids=[c["name"] for c in DERIVED])
    def test_no_handle_survived_the_failure(self, case):
        result = scenario(case["scenario"])["result"]
        assert result["live_handles"] == 0, (
            f"{case['name']} left {result['live_handles']} handles alive. The count comes "
            "from the same table the exports use, so a failure path that skipped a close "
            "shows up here as a number"
        )

    @pytest.mark.parametrize("case", DERIVED, ids=[c["name"] for c in DERIVED])
    def test_a_decode_was_never_reached_on_a_refusal(self, case):
        result = scenario(case["scenario"])["result"]
        if result["open_code"] == "OK":
            assert result["decode_code"] in ("OK", "LIMIT_EXCEEDED", "CANCELED")
        else:
            assert result["decode_code"] == "NOT_REACHED"


class TestTheSignatureCaseIsAccountedFor:
    """A flip in the first six bytes is a different refusal, and the seed decides.

    64 flips in a ten-kilobyte file will land in the version signature about
    one run in thirty. The seed is fixed so the answer is knowable rather than
    lucky, and the capture records which way it went, so a future seed change
    that starts hitting the signature is visible instead of confusing.
    """

    def test_the_recorded_positions_agree_with_the_ones_derived_here(self):
        case = [c for c in DERIVED if c["name"] == "bitflip_64"][0]
        entry = scenario("malformed/bitflip_64")
        assert entry["derived"]["positions"] == case["positions"]
        assert entry["derived"]["touches_signature"] == case["touches_signature"]

    def test_the_code_matches_which_way_it_went(self):
        case = [c for c in DERIVED if c["name"] == "bitflip_64"][0]
        code = scenario("malformed/bitflip_64")["result"]["open_code"]
        if case["touches_signature"]:
            assert code in ("UNSUPPORTED_FORMAT", "CORRUPT_INPUT")
        else:
            assert code == "CORRUPT_INPUT", (
                "no flip landed in the version signature, so the file still declares a "
                "version this build reads and the refusal has to come from the parse"
            )


class TestTruncationIsNotJustTheSameFailureThreeTimes:
    def test_the_three_cuts_are_three_different_files(self):
        digests = {c["sha256"] for c in DERIVED if c["name"].startswith("truncated_")}
        assert len(digests) == 3

    def test_they_get_shorter(self):
        sizes = [c["bytes"] for c in DERIVED if c["name"].startswith("truncated_")]
        assert sizes == sorted(sizes)

    def test_the_shortest_is_still_long_enough_to_carry_a_signature(self):
        # A cut that took the header off would be refused on the signature and
        # would say nothing about the parser.
        shortest = min(c["bytes"] for c in DERIVED if c["name"].startswith("truncated_"))
        assert shortest > 6
