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
        hits = re.findall(r"\b(windows|win32|msvc|\.dll\b)", text, re.I)
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

    def test_the_header_and_the_document_point_at_each_other(self, abi):
        with open(HEADER) as f:
            header = f.read()
        assert "docs/ABI.md" in header and "docs/WIRE.md" in header
        assert "viprs_acadsharp.h" in abi
        assert "WIRE.md" in abi
