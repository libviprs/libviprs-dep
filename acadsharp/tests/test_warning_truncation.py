"""A warning this library writes about a drawing is shortened, never dropped.

`max_string_bytes` is a bound on one string, and until now breaching it ended
the whole decode wherever the string came from. That is the right answer for a
drawing's own text: shortening a label or a view name and saying nothing turns
a bound into silent data loss, and a consumer cannot tell a name the file
carries from one this library cut. It is the wrong answer for a `Warning`,
because that message is this library's own sentence about the file, and losing
every record after it to protect a sentence nobody depends on is a trade that
was never worth making. The reader's `Unlisted object with DXF name ...`
notification carries the file's own class name, so the string that ends the
decode can be entirely the input's choice.

So a `Warning` message is cut at a character boundary and marked, and
`limits/max_string_4096` beside it still records `LIMIT_EXCEEDED` on
`g13_long_text.dwg`, which is the control that `Text` is still refused.

What this reads is a run recorded in a container by
tests/fixtures/gen/regenerate.py, because pytest here has no .NET (ADR 0001).
The two long-warning scenarios are measured on the bytes the caller would have
received rather than on the canonical dump, for the same reason the finiteness
scan is: the truncation happens inside the encoder, and the dump is the layer
above it, so the dump cannot say what actually left.
"""

import os

import pytest
from g13_support import FIXTURES, scenario, sha256_file

# What the fixture's reference path is made of, and what the encoder appends.
# Both are facts about `g13_xref_long.dwg` and `RecordEncoder`, restated here
# so a scenario that stopped measuring them fails rather than passes quietly.
PATH_BYTES = 8192
PATH_PREFIX = "../not-resolved/"
PATH_SUFFIX = ".dwg"
FILLER_BYTES = 2
MARKER = " [truncated]"

# The part of the message that survives any cut inside the filler run: the
# sentence the flattener opens with, then the path's own ASCII prefix.
MESSAGE_PREFIX = "external reference " + PATH_PREFIX

TRUNCATED = "warnings/long_warning_truncated"
FITS = "warnings/long_warning_fits"
BOUND = 4096


def result(name):
    return scenario(name)["result"]


class TestTheCaptureIsOfTheFixtureInTheTree:
    @pytest.mark.parametrize("name", (TRUNCATED, FITS))
    def test_the_fixture_digest_matches(self, name):
        entry = scenario(name)
        fixture = os.path.basename(entry["input"])
        assert sha256_file(os.path.join(FIXTURES, fixture)) == entry["fixture_sha256"], (
            f"{name} was recorded against a different {fixture} than the one in the tree"
        )

    @pytest.mark.parametrize("name", (TRUNCATED, FITS))
    def test_the_scanners_find_what_they_look_for(self, name):
        # The positive controls. Every assertion below is a number a scan over
        # the raw stream produced, and a scan that found nothing anywhere would
        # satisfy "no dangling lead byte" and "no marker" for free.
        r = result(name)
        assert r["longest_filler_run_in_control"] > 0
        assert r["dangling_lead_bytes_in_control"] > 0
        assert r["truncation_markers_in_control"] > 0


class TestTheBoundNoLongerEndsTheDecode:
    def test_the_decode_completes(self):
        r = result(TRUNCATED)
        assert r["open_code"] == "OK"
        assert r["decode_code"] == "OK", (
            "an 8192-byte reference path still ends the decode at max_string_bytes "
            f"{BOUND}. The message is this library's own sentence about the drawing, "
            "and refusing it costs every record after it. "
            f"{r.get('decode_detail')}"
        )

    def test_the_message_is_marked(self):
        r = result(TRUNCATED)
        assert r["truncation_markers"] == 1, (
            f"the stream carries {r['truncation_markers']} copies of {MARKER!r}. A "
            "shortened message that does not say so is a message a consumer reads as "
            "the whole thing."
        )

    def test_the_marker_is_at_the_end_of_the_message(self):
        # What follows the filler run, read straight out of the bytes. The
        # marker sits between the last whole filler character and the record's
        # zero padding, which is the only place it can be and still be the end
        # of the message.
        tail = bytes.fromhex(result(TRUNCATED)["after_longest_filler_run"])
        assert tail.startswith(MARKER.encode()), (
            f"the bytes after the filler run are {tail!r}, which does not open with {MARKER!r}"
        )

    def test_the_cut_lands_on_a_character_boundary(self):
        assert result(TRUNCATED)["dangling_lead_bytes"] == 0, (
            "the stream carries a lead byte of the filler character with no "
            "continuation byte after it, so the message was cut by bytes rather than "
            "by characters and message_len names bytes that are not UTF-8"
        )

    def test_the_message_is_as_long_as_the_bound_allows(self):
        # Derived from the bound and the record's own layout rather than copied
        # out of the recording, so a truncation that started cutting twice as
        # much as it needed to would fail here rather than be re-recorded.
        run = result(TRUNCATED)["longest_filler_run"]
        length = len(MESSAGE_PREFIX) + FILLER_BYTES * run + len(MARKER)
        assert length <= BOUND, f"the message came to {length} bytes against a bound of {BOUND}"
        assert length + FILLER_BYTES > BOUND, (
            f"the message came to {length} bytes and one more character of the path "
            f"would still have fitted inside {BOUND}. The cut is supposed to keep "
            "everything the bound allows."
        )


class TestTheControlKeepsTheWholeString:
    def test_the_decode_completes(self):
        r = result(FITS)
        assert r["open_code"] == "OK" and r["decode_code"] == "OK"

    def test_nothing_was_cut(self):
        r = result(FITS)
        assert r["truncation_markers"] == 0, (
            "the same fixture under a bound above its length came back marked, so the "
            "encoder is shortening a message that fits"
        )
        assert r["dangling_lead_bytes"] == 0

    def test_the_whole_path_crossed(self):
        # The fixture's path is PATH_BYTES of UTF-8: an ASCII prefix and
        # suffix, and two-byte filler characters in between. A run of exactly
        # that many is the whole path and nothing less.
        run = result(FITS)["longest_filler_run"]
        fixed = len(PATH_PREFIX) + len(PATH_SUFFIX)
        assert FILLER_BYTES * run + fixed == PATH_BYTES, (
            f"the path that reached the wire is {FILLER_BYTES * run + fixed} bytes "
            f"and the fixture writes {PATH_BYTES}"
        )

    def test_it_is_the_same_fixture_as_the_refusal(self):
        assert scenario(TRUNCATED)["input"] == scenario(FITS)["input"], (
            "a control on a different fixture controls nothing"
        )


class TestTheDrawingsOwnTextIsStillRefused:
    """The other half, and the reason this is not just a loosened bound.

    `g13_long_text.dwg` is an 8192-byte MTEXT value, which becomes a `Text`
    record. Under the same 4096-byte bound it is refused, and it has to stay
    refused: shortening it would drop characters out of the drawing and tell
    nobody.
    """

    def test_a_long_text_record_still_ends_the_decode(self):
        r = result("limits/max_string_4096")
        assert r["decode_code"] == "LIMIT_EXCEEDED", (
            "an over-long Text is no longer refused. The truncation is for the "
            "messages this library writes itself, not for the drawing's own strings."
        )


# ----------------------------------------------------------------- the source
#
# The rest of this module reads code rather than a recorded run, which the
# container cannot help with: there is no fixture for a notification stored at
# open, because producing one means a drawing that makes ACadSharp's reader say
# something 100 KB long, and the reader's own messages are short. What there is
# instead is one helper, used by both paths, and these are the checks that it
# stays one.

ENCODER_CS = os.path.join(os.path.dirname(FIXTURES), "..", "native", "Wire", "RecordEncoder.cs")
SOURCE_CS = os.path.join(os.path.dirname(FIXTURES), "..", "native", "Sources", "AcadSharpSource.cs")
HELPER = "TruncateMessage"


def read_source(path):
    with open(os.path.normpath(path), encoding="utf-8") as f:
        return f.read()


def switch_arm(text, record_type):
    """One `case WireFormat.Type...:` arm of the encoder's record walk."""
    at = text.index(f"case WireFormat.Type{record_type}:")
    rest = text[at:]
    end = rest.index("break;")
    return rest[:end]


class TestOneHelperCutsBothWays:
    def test_the_encoder_defines_it(self):
        text = read_source(ENCODER_CS)
        assert f"string {HELPER}(" in text, (
            f"RecordEncoder no longer defines {HELPER}, which is the one place the cut is decided"
        )
        assert f'"{MARKER}"' in text, (
            f"the encoder no longer appends {MARKER!r}, which is what tells a consumer "
            "the message it is reading is not the whole one"
        )

    def test_the_stored_notification_goes_through_it(self):
        text = read_source(SOURCE_CS)
        at = text.index("private void OnNotification(")
        body = text[at : text.index("\n\t\t}", at)]
        assert HELPER in body, (
            "a reader notification is stored whole and cut later, so a document whose "
            "reader says something enormous carries it in the list from the moment the "
            "file is opened, before any bound has been consulted. The reader's "
            "unlisted-object message carries the file's own class name, so that length "
            "is the input's choice."
        )

    def test_the_warning_record_truncates(self):
        assert "ref c, true)" in switch_arm(read_source(ENCODER_CS), "Warning")

    @pytest.mark.parametrize("record_type", ("Text", "ViewBegin"))
    def test_the_drawings_own_strings_do_not(self, record_type):
        arm = switch_arm(read_source(ENCODER_CS), record_type)
        assert "ref c, false)" in arm, (
            f"the {record_type} arm shortens a string the drawing owns. A consumer "
            "cannot tell a short one from one this library cut, so the refusal is the "
            "honest answer there."
        )
