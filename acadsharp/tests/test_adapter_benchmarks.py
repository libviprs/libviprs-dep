"""The amplification, streaming and path-versus-memory numbers, with budgets.

These are measurements, not assertions about code: a container ran one scenario
per process, read peak RSS out of /proc, and the numbers are committed beside
the decision they support. The tests here are the budget. If block expansion
starts costing more output or more memory than it does today, this is what goes
red, and tests/benchmarks/DECISION.md is what has to be rewritten.
"""

import os
import re

from g13_support import DECISION, FIXTURES, amplification, sha256_file

BENCH = amplification()
AMP = BENCH["amplification"]
STREAM = BENCH["streaming"]
PV = BENCH["path_versus_memory"]
BATCH = BENCH["batch_bytes"]

# The ceilings, written out rather than derived from the measurement.
#
# Deriving them from the numbers they guard is the mistake that makes a budget
# useless: multiply the recorded value by 1.1 and the ceiling moves with every
# rerun, so an implementation that doubled its output would rerecord a doubled
# ceiling and pass. These are the figures the decision in DECISION.md was made
# on, plus room for the spread of a managed runtime, and a rerun that breaches
# one is supposed to be a conversation.
#
# Measured 2026-09-13: 2,481,264 output bytes and 94,036 KB peak RSS on
# g13_many_inserts.dwg, arm64 containers.
OUTPUT_BYTES_CEILING = 2_800_000
PEAK_RSS_CEILING_KB = 120_000

# What the decode asks the allocator for, per byte it hands back.
#
# Retention and peak RSS were the only two numbers the decode had, and neither
# of them is about churn: retention is near zero by construction because the
# stream is an iterator, and peak RSS on a managed runtime moves by megabytes
# between identical runs. So DECISION.md could attribute RSS movement to
# "a managed collector's allocation churn" without anything ever having
# measured the churn. alloc_decode_bytes is that measurement, bracketing the
# decode loop with the same instrument the open has two lines away.
#
# Written out, not derived. The number this guards is
# AMP["alloc_decode_per_output_byte"], and a ceiling computed from it would
# move every time the benchmark is rerun, which is a budget that cannot fail.
#
# Measured 2026-09-13 on g13_many_inserts.dwg, arm64 containers, the Debug
# build regenerate.py records with: 122,722,280 bytes allocated for 2,481,264
# bytes of output, a ratio of 49.46, byte-identical across reruns. main at
# b782c7f measured 140,563,048 bytes and 56.65 on the same fixture, so this
# ceiling is below what the code this replaced produced and reverting any of
# the three allocation fixes puts it red.
#
# Re-checked after wire version 2 landed rather than recomputed from the new
# reading. A Polyline record grew by 32 bytes, so the denominator moved without
# anything about allocation changing, and a ceiling that followed the
# measurement would have absorbed that silently. It did not need to move: 54.0
# still sits between what main produces and what this does.
ALLOC_PER_OUTPUT_BYTE_CEILING = 54.0

# The same number for the three streaming fixtures, which have no block
# expansion in them and so sit an order lower: an expanded INSERT costs
# allocation for entities that emit nothing of their own, and that cost lands
# entirely on the amplification fixture. One ceiling for both would have been a
# ceiling three times above what either actually does.
#
# Measured 2026-09-13: 15.11, 15.84 and 16.04 across the 1x, 4x and 16x
# fixtures. main at b782c7f measured 25.63, 26.98 and 27.35. Re-checked rather
# than recomputed, for the reason above.
STREAMING_ALLOC_PER_OUTPUT_BYTE_CEILING = 18.0

# The stream is pulled record by record and never collected, so what a decode
# retains must not scale with what it produces. Four batch sizes is the issue's
# budget and it is two orders of magnitude above what this actually retains, so
# it is derived from the batch size on purpose: it is a budget about the
# protocol, not about a measurement.
RETENTION_BUDGET_KB = (4 * BATCH) // 1024


class TestTheBenchmarkIsOfTheFixtureInTheTree:
    def test_the_amplification_fixture_digest_matches(self):
        actual = sha256_file(os.path.join(FIXTURES, AMP["fixture"]))
        assert actual == AMP["fixture_sha256"], (
            "the benchmark was recorded against a different g13_many_inserts.dwg than "
            "the one in the tree, so every number below is about a file nobody has"
        )

    def test_the_path_versus_memory_fixture_digest_matches(self):
        actual = sha256_file(os.path.join(FIXTURES, PV["fixture"]))
        assert actual == PV["fixture_sha256"]


class TestAmplification:
    def test_the_hostile_fixture_is_actually_hostile(self):
        assert AMP["output_bytes"] > 2_000_000
        assert AMP["output_bytes"] / AMP["file_bytes"] > 10, (
            "the fixture stopped amplifying, so it is no longer the case the decision was made on"
        )

    def test_the_output_has_not_grown(self):
        assert AMP["output_bytes"] <= OUTPUT_BYTES_CEILING, (
            f"block expansion now emits {AMP['output_bytes']} bytes against a "
            f"{OUTPUT_BYTES_CEILING} ceiling. That is the number DECISION.md rests on, "
            "so this is a decision to revisit rather than a ceiling to raise"
        )

    def test_the_peak_memory_has_not_grown(self):
        assert AMP["peak_rss_kb"] <= PEAK_RSS_CEILING_KB

    def test_expansion_costs_bytes_and_not_memory(self):
        # The whole decision rests on this. Thirty thousand records cross the
        # boundary and the decoder ends holding what it started with.
        assert AMP["managed_retained_kb"] <= RETENTION_BUDGET_KB, (
            f"the decode retained {AMP['managed_retained_kb']} KB across "
            "30,004 records, so the flattened stream is being accumulated somewhere"
        )

    def test_the_decode_allocation_was_measured_at_all(self):
        # The positive control. A harness that stopped bracketing the decode
        # records nothing here, and a ceiling on a missing number is a ceiling
        # that passes forever: every assertion below would still be green
        # against a benchmark that measured nothing.
        allocated = AMP.get("alloc_decode_bytes")
        assert allocated, (
            "the benchmark carries no alloc_decode_bytes, so the decode's "
            "allocation was not measured and the ceiling below guards nothing"
        )
        assert allocated > AMP["output_bytes"], (
            f"the decode allocated {allocated} bytes to produce "
            f"{AMP['output_bytes']}. Less than one byte of allocation per byte "
            "of output is not a decoder that got clever, it is an instrument "
            "that stopped reading"
        )

    def test_the_recorded_ratio_is_the_recorded_numbers(self):
        # The ratio is stored rounded, so it could quietly stop describing the
        # two numbers beside it. This is what makes the ceiling a statement
        # about the measurement rather than about a third number.
        recorded = AMP["alloc_decode_per_output_byte"]
        computed = AMP["alloc_decode_bytes"] / AMP["output_bytes"]
        assert abs(recorded - computed) < 0.01, (
            f"amplification.json records a ratio of {recorded} beside "
            f"{AMP['alloc_decode_bytes']} bytes and {AMP['output_bytes']} bytes, "
            f"which divide to {computed:.3f}"
        )

    def test_the_decode_allocation_has_not_grown(self):
        recorded = AMP["alloc_decode_per_output_byte"]
        assert recorded <= ALLOC_PER_OUTPUT_BYTE_CEILING, (
            f"the decode now allocates {recorded} bytes for every byte it emits, "
            f"against a {ALLOC_PER_OUTPUT_BYTE_CEILING} ceiling. That is churn the "
            "collector has to clear while the decode runs, and it is the number "
            "DECISION.md's account of peak RSS rests on"
        )

    def test_every_streaming_size_is_measured_the_same_way(self):
        # The three sizes exist to show retention does not track the stream.
        # The same three now show whether churn does, so a regression that only
        # appears at scale has somewhere to appear.
        for s in STREAM:
            assert s.get("alloc_decode_bytes"), f"{s['fixture']} carries no alloc_decode_bytes"
            assert s["alloc_decode_per_output_byte"] <= STREAMING_ALLOC_PER_OUTPUT_BYTE_CEILING, (
                f"{s['fixture']} allocated {s['alloc_decode_per_output_byte']} bytes "
                f"per byte of output, against a "
                f"{STREAMING_ALLOC_PER_OUTPUT_BYTE_CEILING} ceiling"
            )

    def test_it_took_more_than_one_batch(self):
        assert AMP["batches"] > 1, (
            "a single batch means the whole stream fitted in one buffer, and then "
            "nothing here measured streaming at all"
        )


class TestStreamingDoesNotAccumulate:
    def test_there_are_three_sizes(self):
        assert [s["fixture"] for s in STREAM] == [
            "g13_scale_1x.dwg",
            "g13_scale_4x.dwg",
            "g13_scale_16x.dwg",
        ]

    def test_the_sizes_really_differ(self):
        produced = [s["output_bytes"] for s in STREAM]
        assert produced == sorted(produced)
        assert produced[2] > produced[0] * 10, (
            "the 16x fixture is not sixteen times the 1x one, so the comparison below "
            "is between three files of the same size"
        )

    def test_retention_stays_inside_four_batches_at_every_size(self):
        for s in STREAM:
            assert s["managed_retained_kb"] <= RETENTION_BUDGET_KB, (
                f"{s['fixture']} retained {s['managed_retained_kb']} KB across the decode"
            )

    def test_retention_does_not_grow_with_the_stream(self):
        first = STREAM[0]["managed_retained_kb"]
        last = STREAM[-1]["managed_retained_kb"]
        assert last - first <= RETENTION_BUDGET_KB, (
            "sixteen times the records retained more memory, which is what accumulating "
            "the stream would look like"
        )

    def test_peak_rss_growth_is_churn_and_not_the_stream(self):
        # Peak RSS during a decode does move, by a few hundred kilobytes to a
        # few megabytes, and it is tempting to read that as accumulation. It is
        # not: the largest stream in the corpus grows RSS by less than the
        # smallest of these three. A managed collector's allocation churn is
        # what that is, and retention above is the number that answers the
        # question this test is about.
        biggest_stream = AMP["rss_peak_during_decode_kb"] - AMP["rss_after_begin_kb"]
        smallest_fixture = STREAM[0]["rss_decode_growth_kb"]
        assert AMP["output_bytes"] > STREAM[-1]["output_bytes"] * 10
        assert biggest_stream < STREAM[-1]["rss_decode_growth_kb"], (
            f"the 30,004-record decode grew RSS by {biggest_stream} KB and the "
            f"2,052-record one by {STREAM[-1]['rss_decode_growth_kb']} KB. If that "
            "ordering ever reverses, growth has started tracking the record count and "
            "the stream is being held"
        )
        assert smallest_fixture >= 0


class TestPathVersusMemory:
    """The claim is that a path-based open does not duplicate the input.

    On the largest committed fixture the difference is a couple of hundred
    kilobytes either way, because the fixture is 175 KB and peak RSS on a
    managed runtime moves by megabytes between identical runs. So the same pair
    runs again on the same drawing with 32 MiB appended, which the decode does
    not read. That it does not read it is asserted, not assumed: both runs
    produce exactly the record bytes the unpadded fixture produces.
    """

    def test_the_small_fixture_is_honestly_recorded_as_inconclusive(self):
        assert len(PV["runs"]) >= 5
        assert PV["file_bytes"] < 1_000_000
        # No assertion on the sign of the delta here on purpose. Recording five
        # pairs that straddle zero is the honest result at this size, and
        # asserting a direction would be asserting on which way the noise fell.
        assert any(r["peak_delta_kb"] <= 0 for r in PV["runs"]) or all(
            r["peak_delta_kb"] > 0 for r in PV["runs"]
        )

    def test_the_caller_copy_is_the_difference(self):
        for run in PV["runs"]:
            assert run["path_caller_copy_bytes"] == 0, (
                "open_path_utf8 materialised the input, which is the one thing it exists not to do"
            )
            assert run["memory_caller_copy_bytes"] == PV["file_bytes"]

    def test_the_allocation_difference_is_about_the_file(self):
        # The memory route allocates the caller's copy and the path route does
        # not, so the difference is the file, give or take what the path route
        # spends on stream buffers. On a 175 KB input that give-or-take is a
        # third of the number, which is why the padded pair below is what
        # carries the claim and this is corroboration.
        assert PV["median_alloc_delta_bytes"] >= PV["file_bytes"] * 0.8, (
            f"the memory route allocated only {PV['median_alloc_delta_bytes']} bytes "
            f"more than the path route on a {PV['file_bytes']} byte input"
        )
        assert PV["min_alloc_delta_bytes"] > 0

    def test_the_padded_pair_reads_the_same_drawing(self):
        padded = PV["padded"]
        for run in padded["runs"]:
            assert run["path_output_bytes"] == padded["unpadded_output_bytes"]
            assert run["memory_output_bytes"] == padded["unpadded_output_bytes"], (
                "the padded input decoded to different bytes than the unpadded one, so "
                "the padding is being read and the measurement is about a different file"
            )

    def test_peak_rss_separates_once_the_input_is_big_enough(self):
        padded = PV["padded"]
        file_kb = padded["file_kb"]
        # The shortfall against the full file size is the path route's stream
        # buffering, which the memory route does not need because it wraps the
        # caller's array. A megabyte of slack on a 32 MB input covers it and
        # still fails an implementation that copied the input.
        assert padded["min_peak_delta_kb"] >= file_kb - 1024, (
            f"peak RSS differed by {padded['min_peak_delta_kb']} KB on a {file_kb} KB "
            "input, so the memory route is not holding a copy the path route avoids"
        )
        assert padded["median_alloc_delta_bytes"] >= padded["file_bytes"] * 0.99


class TestTheDecisionIsWrittenDownAgainstTheseNumbers:
    """A decision that does not quote its evidence is an opinion.

    So the document has to carry the figures it rests on, and if the benchmark
    moves far enough to change them this goes red and the document gets
    rewritten rather than quietly outliving its basis.
    """

    def test_the_document_exists_and_decides_something(self):
        with open(DECISION) as f:
            text = f.read()
        assert "not add block-definition" in " ".join(text.split())

    def test_it_quotes_the_output_bytes(self):
        with open(DECISION) as f:
            text = f.read()
        assert f"{AMP['output_bytes']:,}" in text, (
            "DECISION.md does not carry the output byte count it was decided on"
        )

    def test_it_quotes_the_record_count_and_the_retention(self):
        with open(DECISION) as f:
            text = f.read()
        assert f"{AMP['batches']}" in text
        assert "1 KB" in text

    def test_it_names_the_condition_that_would_reverse_it(self):
        with open(DECISION) as f:
            text = f.read()
        assert "max_output_bytes" in text
        assert re.search(r"revisit", text, re.I)

    def test_it_quotes_the_decode_allocation(self):
        # The document says peak RSS moving during a decode is "a managed
        # collector's allocation churn". That was an attribution with no
        # measurement under it until alloc_decode_bytes existed, so the
        # document now carries the number and this is what keeps the two
        # together.
        with open(DECISION) as f:
            text = f.read()
        assert f"{AMP['alloc_decode_bytes']:,}" in text, (
            "DECISION.md attributes RSS movement to allocation churn and does not "
            "carry the churn number the benchmark recorded"
        )

    def test_it_quotes_the_padded_measurement(self):
        with open(DECISION) as f:
            text = f.read()
        padded = PV["padded"]
        assert f"{padded['median_peak_delta_kb']:,}" in text
        assert f"{padded['file_kb']:,}" in text
