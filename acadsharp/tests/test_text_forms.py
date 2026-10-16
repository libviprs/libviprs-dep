"""The text a record 10 carries is the text the drawing shows (#111 and #112).

Two gaps, found by saving one AutoCAD drawing twice and decoding both. They are
here together because both are about the same thing: a Text record whose bytes
are not what a viewer would draw, with no warning beside it.

* `\\U+XXXX` (#111). A pre-2007 DWG spells a character its code page cannot hold
  as a MIF escape, and ACadSharp resolves none. `real_AC1018.dwg` and
  `real_AC1032.dwg` are the same drawing saved as R2004 and as R2018, so they
  are an oracle: AutoCAD's own conversion to R2018 resolved the escape the code
  page could not hold (handle 64B, a diameter sign) and the decoder has to get to
  the same string from the R2004 save.
* A multiline attribute (#112). On R2018 the text of one lives in an embedded
  MTEXT and the legacy field is empty, and the adapter read only the legacy
  field, so handle 79D came back as the empty string from the R2018 save and as
  its sentence from the R2004 one.

Both oracles are the recordings, so these tests need no .NET: they read what a
decode somebody ran produced, and `fixturegen decode --check` is what keeps the
recordings honest about the library.

`g13_text_forms.dwg` holds the cases the real pair does not: the rules for an
escape that is not a character, an MTEXT with an escaped backslash in front of
one, and an attribute whose MTEXT and legacy field disagree.
"""

import re

import pytest
from g13_support import records

R2004 = "real_AC1018.dwg"
R2018 = "real_AC1032.dwg"
FORMS = "g13_text_forms.dwg"

VALUE_RE = re.compile(r' value="((?:[^"\\]|\\.)*)"$')
ESCAPES = {"\\": "\\", '"': '"', "n": "\n", "r": "\r", "t": "\t"}


def unquote(raw):
    """The dump's quoting undone: `\\\\` is one backslash, `\\u0000` a NUL."""
    out = []
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch != "\\":
            out.append(ch)
            i += 1
        elif raw[i + 1] == "u":
            out.append(chr(int(raw[i + 2 : i + 6], 16)))
            i += 6
        else:
            out.append(ESCAPES[raw[i + 1]])
            i += 2
    return "".join(out)


def texts(fixture):
    """Every Text record in a dump as (handle, value), in stream order."""
    found = []
    for r in records(fixture):
        if r["kind"] != "Text":
            continue
        m = VALUE_RE.search(r["rest"])
        assert m, f"{fixture}: a Text record with no value: {r['rest']!r}"
        found.append((r["handle"], unquote(m.group(1))))
    return found


def by_handle(fixture):
    out = {}
    for handle, value in texts(fixture):
        out.setdefault(handle, []).append(value)
    return out


class TestTheTwoSavesOfOneDrawingAgree:
    def test_every_handle_in_both_dumps_carries_the_same_text(self):
        old, new = by_handle(R2004), by_handle(R2018)
        common = sorted(set(old) & set(new))
        assert len(common) >= 70, (
            f"only {len(common)} handles are in both dumps, so this is comparing too little "
            "to be an oracle"
        )
        differ = {h: (old[h], new[h]) for h in common if old[h] != new[h]}
        assert not differ, (
            "the same handle reads differently out of the R2004 and the R2018 save of one "
            f"drawing: {differ}. AutoCAD wrote the same text into both"
        )


class TestMifEscapesAreResolved:
    @pytest.mark.parametrize("fixture", [R2004, R2018])
    def test_the_diameter_callout_is_a_diameter_sign(self, fixture):
        assert by_handle(fixture)["64B"] == ["\u220545,6"], (
            f"{fixture} handle 64B is {by_handle(fixture)['64B']!r}. The drawing says a "
            "diameter sign and 45,6, and U+2205 is the sign"
        )

    @pytest.mark.parametrize("fixture", [R2004, R2018])
    @pytest.mark.parametrize("handle", ["634", "63E"])
    def test_a_degree_sign_the_code_page_could_hold_is_still_a_degree_sign(self, fixture, handle):
        assert by_handle(fixture)[handle] == ["94\u00b0"], (
            f"{fixture} handle {handle} is {by_handle(fixture)[handle]!r}"
        )

    @pytest.mark.parametrize("fixture", [R2004, R2018])
    def test_no_dump_carries_a_resolvable_escape_any_more(self, fixture):
        left = [(h, v) for h, v in texts(fixture) if re.search(r"\\U\+[0-9A-Fa-f]{4}", v)]
        assert not left, f"{fixture} still carries MIF escapes in {left}"


class TestAMultilineAttributeKeepsItsText:
    def test_the_r2018_save_holds_what_the_r2004_save_holds(self):
        assert by_handle(R2018)["79D"] == ["my multi line text for the attrrib"], (
            f"{R2018} handle 79D is {by_handle(R2018)['79D']!r}. It is a multiline "
            "attribute, its text is in the embedded MTEXT, and the R2004 save of the same "
            "drawing holds the sentence"
        )


class TestTheCasesTheRealPairDoesNotHave:
    EXPECTED = [
        # TEXT. The escape is resolved wherever it sits, with either case of hex.
        "\u220545,6",
        "94\u00b0",
        "smile \U0001f600",
        # Not characters, so they stay what the drawing spells.
        "lone \\U+D83D high",
        "short \\U+22 end",
        "nul \\U+0000 inside",
        "plain text with a \\ in it",
        # MTEXT. Format codes stay for the consumer, an escaped backslash in
        # front of the letters is not an escape, and one in front of a real
        # escape leaves the escape resolved.
        "Dia \u2205 45\\P2nd line",
        "\\\\U+2205 is literal here",
        "\\\\\u2205 is an escaped backslash then a diameter sign",
        # Attributes. The embedded MTEXT wins when it has text, the legacy
        # field is the fallback, and a single line attribute is untouched.
        "single line",
        "the multiline text\\Pof an attribute",
        "fallback field",
        "attribute \u2205 escape",
    ]

    def test_the_forms_fixture_is_dumped_in_the_order_it_was_written(self):
        got = [v for _, v in texts(FORMS)]
        assert got == self.EXPECTED, "\n".join(
            f"{'ok ' if a == b else 'BAD'} want {b!r} got {a!r}"
            for a, b in zip(got + [None] * len(self.EXPECTED), self.EXPECTED)
        )
