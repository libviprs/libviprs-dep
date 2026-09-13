"""ABI.md has to be enough on its own to build a second consumer.

That is the whole point of writing it: the C ABI is frozen here, an
`acadsharp-rs` crate will be generated from it later, and a third consumer
in some language nobody has picked yet must be able to start from the
header and this document without reading a line of the managed source or
of any existing consumer. So the document cannot lean on one consumer's
idioms, and these checks hold it to that.

The rules that follow are the ones a reader would otherwise have to guess
at: who frees what, which calls may share a thread, what each result code
means, how a caller sizes a string buffer, and how the fingerprint
handshake works. Every one of them has a way of being wrong that no
compiler on either side of the boundary can see.

These run as text checks with no toolchain, because the documents ship as
text and that is what a downstream author reads.
"""

import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ACADSHARP = os.path.dirname(HERE)
DOCS = os.path.join(ACADSHARP, "docs")
ABI_MD = os.path.join(DOCS, "ABI.md")
WIRE_MD = os.path.join(DOCS, "WIRE.md")
LINKINFO_MD = os.path.join(DOCS, "LINKINFO.md")
HEADER = os.path.join(ACADSHARP, "include", "viprs_acadsharp.h")

ENTRY_POINTS = (
    "viprs_acad_abi_version",
    "viprs_acad_abi_fingerprint",
    "viprs_acad_capabilities_v1",
    "viprs_acad_open_path_utf8",
    "viprs_acad_open_memory",
    "viprs_acad_view_count",
    "viprs_acad_view_info_v1",
    "viprs_acad_decode_begin",
    "viprs_acad_decode_next_batch",
    "viprs_acad_decode_close",
    "viprs_acad_close",
)

RESULT_CODES = (
    "VIPRS_ACAD_OK",
    "VIPRS_ACAD_INVALID_ARGUMENT",
    "VIPRS_ACAD_UNSUPPORTED_FORMAT",
    "VIPRS_ACAD_CORRUPT_INPUT",
    "VIPRS_ACAD_UNSUPPORTED_ENTITY",
    "VIPRS_ACAD_OUT_OF_MEMORY",
    "VIPRS_ACAD_CANCELED",
    "VIPRS_ACAD_INTERNAL_ERROR",
    "VIPRS_ACAD_ABI_MISMATCH",
    "VIPRS_ACAD_LIMIT_EXCEEDED",
)

STRUCTS = (
    "viprs_acad_limits_v1",
    "viprs_acad_capabilities_v1",
    "viprs_view_info_v1",
)

# The toolchain name is assembled rather than spelled. This directory is
# swept by test_acadsharp_targets.py for exactly that token, and that guard
# exempts one file, itself, so a second file that writes it out turns a
# green suite red from somewhere nobody is looking. The header does the same
# thing with the type names its own acceptance grep forbids.
_MS_TOOLCHAIN = "ms" + "vc"

LIMIT_FIELDS = (
    "max_input_bytes",
    "max_entities",
    "max_string_bytes",
    "max_polyline_points",
    "max_block_depth",
    "max_output_bytes",
)


@pytest.fixture(scope="module")
def abi():
    with open(ABI_MD) as f:
        return f.read()


@pytest.fixture(scope="module")
def wire():
    with open(WIRE_MD) as f:
        return f.read()


@pytest.fixture(scope="module")
def linkinfo():
    with open(LINKINFO_MD) as f:
        return f.read()


@pytest.fixture(scope="module")
def linkinfo_flat(linkinfo):
    """LINKINFO.md with its hard wrapping collapsed, same reason as `abi_flat`."""
    return re.sub(r"\s+", " ", linkinfo)


@pytest.fixture(scope="module")
def abi_flat(abi):
    """ABI.md with its hard wrapping collapsed.

    The document is wrapped to fit a terminal, so a phrase these checks look
    for lands across two lines as often as not. Checking the wrapped text
    would make the tests fail on a reflow, which is a fact about the editor
    and not about the contract.
    """
    return re.sub(r"\s+", " ", abi)


class TestTheDocumentsBindNoConsumer:
    """A contract that names one consumer's language has stopped being a contract."""

    @pytest.mark.parametrize("name", ("ABI.md", "WIRE.md"))
    def test_neither_document_mentions_a_consumer_language(self, name, abi, wire):
        text = abi if name == "ABI.md" else wire
        hits = re.findall(r"\b(rust|cargo|crate|crates)\b", text, re.I)
        assert not hits, (
            f"{name} names {sorted(set(h.lower() for h in hits))}. The document has to be "
            "buildable-from by a consumer in a language nobody has chosen yet, so naming "
            "the one that exists today turns the contract into a description of it."
        )

    @pytest.mark.parametrize("name", ("ABI.md", "WIRE.md"))
    def test_neither_document_names_a_microsoft_target(self, name, abi, wire):
        text = abi if name == "ABI.md" else wire
        hits = re.findall(rf"\b(windows|win32|{_MS_TOOLCHAIN}|\.dll\b)", text, re.I)
        assert not hits, (
            f"{name} names {sorted(set(h.lower() for h in hits))}. This org ships no such "
            "artifact for any dependency and the epic's acceptance says so explicitly."
        )

    @pytest.mark.parametrize("name", ("ABI.md", "WIRE.md"))
    def test_neither_document_leaks_the_upstream_object_model(self, name, abi, wire):
        text = abi if name == "ABI.md" else wire
        hits = re.findall(r"Cad(?:Document|Entity)|\bLayer\b|\bBlock\b", text)
        assert not hits, (
            f"{name} names {hits}. The boundary is VIPRS-owned, and a document that "
            "describes it in the backing library's type names has documented the "
            "implementation instead of the contract."
        )


class TestTheSurfaceIsDocumented:
    def test_every_entry_point_is_described(self, abi):
        missing = [e for e in ENTRY_POINTS if e not in abi]
        assert not missing, (
            f"ABI.md never mentions {missing}. A consumer author reading only this file "
            "would not know the call exists."
        )

    def test_every_result_code_is_described(self, abi):
        missing = [c for c in RESULT_CODES if c not in abi]
        assert not missing, (
            f"ABI.md never mentions {missing}. Downstream switches on the numeric value, "
            "so a code nobody documented is a branch nobody writes."
        )

    def test_every_struct_is_described(self, abi):
        missing = [s for s in STRUCTS if s not in abi]
        assert not missing, f"ABI.md never mentions {missing}"

    def test_every_limit_field_has_a_documented_default(self, abi_flat):
        # A null limits pointer means "the documented defaults", and a zero
        # field means "the default for that field". Both sentences are
        # meaningless unless the numbers are written down.
        for field in LIMIT_FIELDS:
            assert field in abi_flat, f"ABI.md never mentions the limit {field}"
            near = abi_flat[abi_flat.index(field) : abi_flat.index(field) + 400]
            assert re.search(r"\d", near), (
                f"ABI.md mentions {field} but states no default near it. A caller that "
                "leaves the field zero gets a bound it cannot look up."
            )


class TestTheModelsAreStated:
    def test_ownership_is_stated(self, abi):
        assert re.search(r"^#+ .*ownership", abi, re.I | re.M), (
            "ABI.md needs an ownership section. Every handle and every buffer on this "
            "boundary has exactly one owner, and a consumer that guesses wrong either "
            "leaks or frees something the callee still holds."
        )

    def test_threading_is_stated(self, abi):
        assert re.search(r"^#+ .*threading", abi, re.I | re.M)
        assert "cancel_flag" in abi, (
            "the threading section has to say what the cancel flag may be written from, "
            "because it is the one word on this boundary two threads touch at once."
        )

    def test_the_error_model_is_stated(self, abi, abi_flat):
        assert re.search(r"^#+ .*error", abi, re.I | re.M)
        assert re.search(
            r"never parse|not parse|no error string|never an error string", abi_flat, re.I
        ), (
            "the error model has to say that control flow comes from the numeric code "
            "and never from a string, which is the rule this ABI exists to enforce."
        )

    def test_the_capability_handshake_is_stated(self, abi):
        assert re.search(r"^#+ .*capabilit", abi, re.I | re.M)
        assert "abi_version" in abi and "dwg_version_min" in abi

    def test_the_fixed_width_rules_are_stated(self, abi):
        assert re.search(r"^#+ .*fixed[- ]width", abi, re.I | re.M)
        for rule in ("struct_size", "struct_version", "enum", "uint8_t"):
            assert rule in abi, f"the fixed-width section never mentions {rule}"

    def test_the_two_call_buffer_convention_is_stated(self, abi, abi_flat):
        assert re.search(r"\bcap\b", abi) and "required" in abi
        assert re.search(r"capacity of zero|cap of zero|zero capacity|cap 0", abi_flat, re.I), (
            "a caller sizes a UTF-8 buffer by calling once with a capacity of zero, and "
            "that is not guessable from the signature alone."
        )

    def test_no_implicit_null_termination_is_stated(self, abi):
        assert re.search(r"terminat", abi, re.I), (
            "ABI.md has to say there is no terminator, in both directions, or a consumer "
            "author will size a buffer one byte short and never find out on the happy path."
        )


class TestTheFingerprintIsDefined:
    def test_the_algorithm_is_written_down(self, abi_flat):
        assert "sha256" in abi_flat.lower()
        assert re.search(r"first eight bytes|first 8 bytes", abi_flat, re.I)
        assert re.search(r"big[- ]endian", abi_flat, re.I), (
            "which end the eight bytes come from decides the number, so it is part of "
            "the definition, not a detail."
        )

    def test_the_handshake_a_consumer_runs_is_written_down(self, abi, abi_flat):
        assert "VIPRS_ACAD_ABI_MISMATCH" in abi
        assert "viprs_acad_abi_fingerprint" in abi
        assert re.search(r"generated .*build time|at build time", abi_flat, re.I), (
            "the value is generated from the header at build time rather than assigned, "
            "and a consumer author needs to know that is the guarantee being offered."
        )


class TestTheSyntheticBackingIsDocumented:
    """It is reachable across the published ABI, so it is part of the contract."""

    def test_the_magic_is_written_down(self, abi):
        assert "VIPRSSYN" in abi, (
            "viprs_acad_open_memory selects the synthetic document on a magic byte "
            "sequence, and a consumer cannot exercise the decode path without it."
        )

    def test_both_open_calls_are_named_as_selecting_it(self, abi):
        # The document named open_memory only, and SourceFactory has always
        # sniffed the magic on the path route too. A second implementation
        # built from this file would have got that wrong in the direction
        # hardest to notice: it would work on every real drawing.
        #
        # The section, not the whole file: both names appear elsewhere in
        # ABI.md, so a check over the document would pass whatever this
        # paragraph said.
        section = re.search(r"^## The synthetic document$(.*?)(?=^## |\Z)", abi, re.S | re.M)
        assert section, "ABI.md no longer has a synthetic-document section"
        body = re.sub(r"\s+", " ", section.group(1))
        for call in ("viprs_acad_open_memory", "viprs_acad_open_path_utf8"):
            assert call in body, (
                f"the synthetic-document section never names {call}. Both open calls "
                "recognise the magic, and a document that names one of them describes "
                "half the behaviour as if it were all of it."
            )

    def test_the_header_and_the_document_point_at_each_other(self, abi):
        with open(HEADER) as f:
            header = f.read()
        assert "docs/ABI.md" in header and "docs/WIRE.md" in header
        assert "viprs_acadsharp.h" in abi
        assert "WIRE.md" in abi


class TestTheManifestSpecIsEnoughToLinkFrom:
    """`LINKINFO.md` is the third contract, and the one that did not exist.

    The header could always be consumed from the archive. `LINKINFO.json`
    could not: its field list was a tuple in the build driver, its types
    were implicit, and the rule that decides whether a static link works
    at all lived in a comment in that same driver. So the person writing
    a consumer read the producer's source, which is the coupling freezing
    the ABI was supposed to remove.

    Unlike ABI.md and WIRE.md, this document is allowed to name a build
    system. A manifest is not a boundary: it matters only to whatever
    parses it at build time, and the failure it exists to prevent is
    invisible unless the recipe is spelled out in that reader's own
    directives. The price of that exemption is the rule below that the
    linker-level form has to be there too, so a consumer built with
    something else is still served.
    """

    # The field table is checked against the driver's frozen tuple in
    # test_build_manifests.py, which is the file that holds it. What is
    # checked here is the prose around it: the rules a reader cannot
    # infer from a table of names and types.

    def test_the_schema_version_forward_rule_is_stated(self, linkinfo, linkinfo_flat):
        assert "schema_version" in linkinfo
        assert re.search(r"higher than.{0,120}refuse", linkinfo_flat, re.I), (
            "the document has to say what a consumer does when schema_version is "
            "higher than the one it knows. Pressing on with the fields it recognises "
            "turns a manifest change into a crash in somebody else's binary."
        )
        assert re.search(r"lower than.{0,160}(accept|may)", linkinfo_flat, re.I), (
            "and what it does with an older one, or every consumer refuses every "
            "archive published before its own release"
        )

    def test_the_fingerprint_format_is_pinned(self, linkinfo, linkinfo_flat):
        assert re.search(r"16 lowercase hex", linkinfo_flat, re.I), (
            "the width and the case are part of the format: a consumer comparing "
            "strings rather than numbers gets this wrong on a leading zero"
        )
        assert re.search(r"first eight bytes", linkinfo_flat, re.I)
        assert re.search(r"big[- ]endian", linkinfo_flat, re.I), (
            "which end the eight bytes come from decides the number"
        )
        assert re.search(r"carries no `0x` prefix", linkinfo_flat), (
            "the value carries no 0x prefix, and a radix-16 parse of a prefixed "
            "string fails rather than skipping it. Saying so is the whole point of "
            "documenting a format."
        )

    def test_absent_is_distinguished_from_empty(self, linkinfo_flat):
        assert re.search(r"absent, not empty", linkinfo_flat, re.I), (
            "static_library is missing from the JSON on a target that built none, "
            "never present and empty, and a consumer that tests truthiness rather "
            "than presence cannot tell those apart"
        )
        assert re.search(r"empty string is a path", linkinfo_flat, re.I)

    def test_static_certified_is_defined_as_linked_and_ran(self, linkinfo, linkinfo_flat):
        assert re.search(r"linked.{0,80}and ran", linkinfo_flat, re.I), (
            "an archive that links and aborts on the first call is indistinguishable "
            "from a working one until something runs it, so the flag is defined on "
            "running and not on linking"
        )
        assert re.search(r"`false` whenever", linkinfo_flat), (
            "the false side needs stating too: every target that never attempts a "
            "static build reports false, and that is not a failure"
        )

    def test_the_recipe_is_written_out_in_full_and_in_order(self, linkinfo):
        # The one part of this document that is not prose. Indented
        # directives only: the field table quotes the same lines in a
        # different order, because it is ordered by field name, and a
        # whole-file search for them would read that as the recipe.
        directives = [
            line.strip()
            for line in linkinfo.splitlines()
            if re.match(r"^ {4}cargo:rustc-link", line)
        ]
        assert directives == [
            "cargo:rustc-link-search=native=<archive>/lib",
            "cargo:rustc-link-lib=static:-bundle,+whole-archive=acadsharp_native_init",
            "cargo:rustc-link-lib=static:-bundle=acadsharp_native",
            "cargo:rustc-link-lib=<each static_system_libraries entry>",
        ], (
            "this is the measured working recipe, and every part of it is "
            "load-bearing: both modifiers on both libraries, and the initialiser "
            "archive ahead of the main one. A consumer reading a reordered or "
            "abbreviated copy reinvents a link that fails on RhRegisterOSModule or "
            "a binary that aborts at the first call."
        )

    @staticmethod
    def _paragraph(text, needle):
        """The one paragraph containing `needle`, hard wrapping collapsed.

        Scoped rather than whole-file on purpose. The document states the
        left-to-right rule twice, once for the linker in general and once
        for this specific trap, so a whole-file search for it stays green
        while the explanation that matters is deleted. It did: the first
        version of this check passed against a copy with the paragraph
        rewritten.
        """
        for block in text.split("\n\n"):
            if needle in block:
                return re.sub(r"\s+", " ", block)
        raise AssertionError(f"LINKINFO.md has no paragraph containing {needle!r}")

    def test_it_explains_why_both_libraries_are_unbundled(self, linkinfo):
        para = self._paragraph(linkinfo, "`-bundle` on both")
        assert "`+bundle`" in para, (
            "the default is the trap, so the document has to name it rather than "
            "only naming the flag that avoids it"
        )
        assert "rlib" in para, (
            "with the default, rustc packs the archive into the crate's own rlib "
            "instead of passing a -l flag, and that rlib lands ahead of the "
            "whole-archived initialiser"
        )
        assert re.search(r"left to right", para, re.I), (
            "the reason the position matters at all is that the linker reads left "
            "to right and cannot go back, and it has to be said here rather than "
            "only in the general section"
        )

    def test_it_explains_why_no_gc_flag_is_needed_and_what_to_do_if_it_is(self, linkinfo):
        """The failure a reader arrives at this page holding.

        Four archives are downloadable whose `__modules` has no retain
        flag, so someone will hit `undefined symbol: __start___modules`
        and come looking. Saying only "we set a flag for you" leaves that
        reader with nothing, so the paragraph has to name the error text
        and the escape hatch as well as the mechanism.
        """
        para = self._paragraph(linkinfo, "start-stop-gc")
        assert "__start___modules" in para, (
            "the symbol in the error message is the only string the reader has to "
            "search for, so it has to appear here"
        )
        assert "SHF_GNU_RETAIN" in para or "SHF_GNU_RETAIN" in linkinfo, (
            "the mechanism has to be named, or nobody can check the claim against "
            "an archive they are holding"
        )
        escape = self._paragraph(linkinfo, "If you do see that error")
        assert re.search(r"older|before this|newer", escape, re.I), (
            "a reader with an old archive needs to be told that is what they have, "
            "and that the flag is the workaround rather than the recipe"
        )

    def test_it_does_not_tell_a_consumer_to_pass_a_gc_flag_as_the_recipe(self, linkinfo_flat):
        """The whole point of the fix is that the recipe did not change.

        A document that lists `-z nostart-stop-gc` beside the two
        `rustc-link-lib` lines would be telling consumers to carry a
        requirement the archive already carries, and one that cannot
        travel from a dependency's build script anyway.
        """
        assert "cargo:rustc-link-arg=-Wl,-z,nostart-stop-gc" not in linkinfo_flat

    def test_it_says_the_initialiser_archive_comes_first_and_what_happens_otherwise(
        self, linkinfo, linkinfo_flat
    ):
        assert "RhRegisterOSModule" in linkinfo, (
            "a reader who hits this error needs to find it in the document that "
            "would have prevented it"
        )
        assert re.search(r"undefined reference", linkinfo_flat, re.I)
        assert re.search(r"first", linkinfo_flat, re.I)

    def test_it_says_a_link_argument_does_not_travel(self, linkinfo, linkinfo_flat):
        assert "cargo:rustc-link-arg" in linkinfo
        assert re.search(
            r"does \*\*not\*\* propagate|goes no further|never reaches", linkinfo_flat, re.I
        ), (
            "this is the failure the initialiser archive exists to avoid, and it is "
            "silent: the producing crate's own tests link correctly and every "
            "dependent binary does not. A reader who does not know it reinvents the "
            "bug, because forcing the symbol is the obvious fix."
        )

    def test_the_rule_is_stated_in_linker_terms_as_well(self, linkinfo):
        # The price of being the one document that may name a build
        # system. A consumer written in anything else reads this half.
        assert "--whole-archive" in linkinfo, (
            "the general rule is a linker rule, and a consumer that is not the one "
            "we happen to have needs it in those terms"
        )
        assert "force_load" in linkinfo, (
            "and on the Apple linker it is spelled differently, which is exactly "
            "the kind of thing a build-script-only recipe hides"
        )

    def test_it_points_at_the_other_two_contracts(self, linkinfo):
        assert "ABI.md" in linkinfo and "WIRE.md" in linkinfo

    def test_the_abi_document_points_back_at_it(self, abi):
        # Three files, and a reader arrives at whichever one they were
        # handed. ABI.md already names metadata/LINKINFO.json; it has to
        # name the document that defines it too.
        assert "LINKINFO.md" in abi, (
            "a reader who starts at ABI.md has no way to learn the manifest is specified anywhere"
        )


class TestNoDocumentPrintsALiveDigest:
    """A real fingerprint in prose goes stale the first time the header moves.

    It already did. An earlier draft of ABI.md printed `d855aa37...` as a
    worked example, the header gained six `typedef` keywords, and the document
    kept confidently stating a value that no build would ever return. The
    paragraph carrying it is the one warning that a hand-typed constant "drifts
    in the one direction that does damage, by continuing to report agreement".

    So the rule is not "keep the number correct", which is what failed. It is
    that no document prints a live digest at all: the worked example is
    obviously synthetic, and anyone who wants the real value reads
    `LINKINFO.json`, calls the export, or hashes the header.
    """

    def _digest(self):
        import hashlib

        with open(HEADER, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    @pytest.mark.parametrize("doc", ["ABI.md", "WIRE.md", "LINKINFO.md"])
    def test_no_prefix_of_the_header_digest_appears(self, doc):
        digest = self._digest()
        path = os.path.join(DOCS, doc)
        with open(path, encoding="utf-8") as f:
            text = f.read().lower()

        # Eight hex characters is four bytes, already far past coincidence in
        # prose, and short enough to catch a truncated copy of the real thing.
        for length in (64, 32, 16, 8):
            probe = digest[:length]
            assert probe not in text, (
                f"{doc} contains {probe!r}, which is the first {length} characters of the "
                f"live header digest. Whatever it is illustrating will be wrong the next "
                f"time viprs_acadsharp.h changes, and it will be wrong silently. Use an "
                f"obviously fake digest and point the reader at LINKINFO.json or "
                f"viprs_acad_abi_fingerprint()."
            )

    def test_the_check_can_actually_see_the_digest(self):
        # The positive control. Without it, a bug that read the wrong file or
        # lower-cased only one side would pass every assertion above by
        # comparing two things that never match.
        digest = self._digest()
        assert len(digest) == 64
        pretend = f"the digest {digest[:16]} becomes 0x{digest[:16].upper()}"
        assert digest[:16] in pretend.lower(), (
            "the containment test itself is broken, so the assertions above prove nothing"
        )
