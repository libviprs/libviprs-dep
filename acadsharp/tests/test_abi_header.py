"""The ABI header is the contract, so its rules are enforced not just written.

Every check here maps to a rule in the header's own preamble, and each rule is
there because its absence has broken a C ABI somewhere before. A C enum whose
width the compiler chooses, a `bool` whose size C and C# disagree on, a
`size_t` that is not the same on two targets: each one compiles cleanly on both
sides and produces garbage at run time, which is the worst failure mode
available.

These run with no .NET and no compiler. They read the header as text, because
the header is the artifact that ships and the thing a consumer is generated
from.
"""

import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ACADSHARP = os.path.dirname(HERE)
HEADER = os.path.join(ACADSHARP, "include", "viprs_acadsharp.h")

# Every entry point the epic freezes. Written out rather than scraped from the
# header, so deleting a declaration fails here instead of shrinking the
# expectation along with it.
ENTRY_POINTS = (
    "viprs_acad_abi_version",
    "viprs_acad_abi_fingerprint",
    "viprs_acad_get_capabilities_v1",
    "viprs_acad_open_path_utf8",
    "viprs_acad_open_memory",
    "viprs_acad_view_count",
    "viprs_acad_get_view_info_v1",
    "viprs_acad_decode_begin",
    "viprs_acad_decode_next_batch",
    "viprs_acad_decode_close",
    "viprs_acad_close",
)

RESULT_CODES = {
    "VIPRS_ACAD_OK": 0,
    "VIPRS_ACAD_INVALID_ARGUMENT": 1,
    "VIPRS_ACAD_UNSUPPORTED_FORMAT": 2,
    "VIPRS_ACAD_CORRUPT_INPUT": 3,
    "VIPRS_ACAD_UNSUPPORTED_ENTITY": 4,
    "VIPRS_ACAD_OUT_OF_MEMORY": 5,
    "VIPRS_ACAD_CANCELED": 6,
    "VIPRS_ACAD_INTERNAL_ERROR": 7,
    "VIPRS_ACAD_ABI_MISMATCH": 8,
    "VIPRS_ACAD_LIMIT_EXCEEDED": 9,
    "VIPRS_ACAD_BUFFER_TOO_SMALL": 10,
}

STRUCTS = (
    "viprs_acad_limits_v1",
    "viprs_acad_capabilities_v1",
    "viprs_acad_view_info_v1",
)

HANDLES = ("viprs_acad_handle", "viprs_acad_decode_handle")


@pytest.fixture(scope="module")
def header():
    with open(HEADER) as f:
        return f.read()


@pytest.fixture(scope="module")
def code(header):
    """The header with comments stripped.

    A rule stated in prose must not satisfy the check for that rule, and it
    must not trip it either. `zstd/tests/test_ci_coverage.py` grew
    `without_comments()` after a guard fired on the paragraph explaining it.
    """
    no_block = re.sub(r"/\*.*?\*/", "", header, flags=re.S)
    return re.sub(r"//[^\n]*", "", no_block)


class TestTheSurfaceIsComplete:
    def test_every_entry_point_is_declared(self, code):
        missing = [e for e in ENTRY_POINTS if f"{e}(" not in code]
        assert not missing, (
            f"the header declares no {missing}. Downstream is generated from this file, "
            "so a missing declaration is a missing function on every consumer."
        )

    def test_no_undeclared_entry_point_sneaks_in(self, code):
        found = set(re.findall(r"\b(viprs_acad_[a-z0-9_]+)\s*\(", code))
        extra = found - set(ENTRY_POINTS)
        assert not extra, (
            f"the header declares {sorted(extra)}, which the epic does not freeze. "
            "Adding an entry point is an ABI change and goes through the fingerprint gate."
        )

    def test_every_result_code_has_its_frozen_value(self, code):
        for name, value in RESULT_CODES.items():
            m = re.search(rf"#define\s+{name}\s+(\d+)u", code)
            assert m, f"{name} is not defined"
            assert int(m.group(1)) == value, (
                f"{name} is {m.group(1)}, frozen at {value}. Downstream switches on the "
                "number, so renumbering silently changes what every consumer believes."
            )

    def test_the_abi_and_wire_versions_are_declared(self, code):
        assert re.search(r"#define\s+VIPRS_ACAD_ABI_VERSION\s+2u", code)
        assert re.search(r"#define\s+VIPRS_ACAD_WIRE_VERSION\s+2u", code)

    def test_a_buffer_too_small_has_a_code_of_its_own(self, code):
        # The two outcomes used to share VIPRS_ACAD_LIMIT_EXCEEDED and were
        # told apart by whether *written came back larger than the caller's
        # cap, which no document stated and no consumer could be expected to
        # infer. One of them is retryable and the other ends the decode, so
        # they are different numbers now.
        assert re.search(r"#define\s+VIPRS_ACAD_BUFFER_TOO_SMALL\s+10u", code)

    def test_the_limit_code_no_longer_describes_a_buffer(self, header):
        # In the prose as well as in the constant. A header that still tells a
        # consumer author to expect LIMIT_EXCEEDED for a short buffer has
        # documented the bug rather than the fix.
        decode = header[header.index("uint32_t viprs_acad_decode_next_batch") - 3000 :]
        decode = decode[: decode.index("uint32_t viprs_acad_decode_next_batch")]
        assert "VIPRS_ACAD_BUFFER_TOO_SMALL" in decode, (
            "the decode_next_batch comment never names the buffer code, so a consumer "
            "author reading the header still writes the LIMIT_EXCEEDED branch"
        )
        assert "VIPRS_ACAD_LIMIT_EXCEEDED" not in decode, (
            "the decode_next_batch comment still promises LIMIT_EXCEEDED for a short "
            "buffer, which is the conflation this change removes"
        )

    def test_the_header_says_a_refusal_is_terminal(self, header):
        flat = re.sub(r"\s+", " ", header)
        assert re.search(r"terminal|latch", flat, re.I), (
            "nothing in the header says what a second call after a refusal does. A C# "
            "iterator that threw returns false forever after, so the documented "
            "grow-and-retry produced a well-framed FLAG_LAST stream missing every "
            "record after the breach, and reported OK."
        )


class TestTheLayoutRules:
    def test_no_acadsharp_type_crosses_the_boundary(self, header):
        # The acceptance check, run as written, against the whole file. It is a
        # bare grep on purpose: the header is what ships, and a consumer reads
        # all of it.
        hits = re.findall(r"Cad(?:Document|Entity)|\bLayer\b|\bBlock\b", header)
        assert not hits, (
            f"the header names {hits}. The boundary is VIPRS-owned; upstream's object "
            "model must not appear in it, in code or in prose."
        )

    def test_every_struct_opens_with_size_and_version(self, code):
        for name in STRUCTS:
            m = re.search(rf"struct {name} \{{(.*?)\n\}};", code, re.S)
            assert m, f"{name} is not defined as a struct"
            body = m.group(1).strip()
            first_two = [ln.strip() for ln in body.splitlines() if ln.strip()][:2]
            assert first_two == ["uint32_t struct_size;", "uint32_t struct_version;"], (
                f"{name} opens with {first_two}. Without both, a callee cannot tell which "
                "version of the struct it was handed and reads past what was allocated."
            )

    def test_no_struct_shares_a_name_with_a_call(self, code):
        # This one cost a compile to find, and the fix is now the rename
        # rather than the missing keyword. `viprs_acad_capabilities_v1` named
        # both a struct and an entry point, and in C a typedef name and a
        # function name are the same kind of identifier, so a header that
        # typedef'd the struct to its own name did not compile as C at all.
        # It compiled as C++, where the function hides the class name, which
        # is how a header ships broken: every reader who tried it tried it
        # the wrong way. The calls carry `get_` now, so the two namespaces no
        # longer touch, and this checks the collision has not come back.
        tags = set(re.findall(r"struct (viprs_[a-z0-9_]+) \{", code))
        calls = set(re.findall(r"\b(viprs_[a-z0-9_]+)\s*\(", code))
        assert not (tags & calls), (
            f"{sorted(tags & calls)} names both a struct and a call. That is legal C "
            "only while nobody typedefs the struct, which is a rule somebody gets "
            "wrong, so the boundary does not have the collision at all."
        )

    def test_every_type_on_the_boundary_carries_the_same_prefix(self, code):
        # `viprs_cad_handle` and `viprs_decode_handle` were the two names that
        # did not. One prefix for everything means a consumer generating
        # bindings can select the surface with one pattern.
        names = set(re.findall(r"struct (viprs_[a-z0-9_]+)", code))
        stray = sorted(n for n in names if not n.startswith("viprs_acad_"))
        assert not stray, (
            f"{stray} sits on the boundary without the viprs_acad_ prefix everything "
            "else carries."
        )

    def test_no_abi_struct_is_typedefed_to_its_own_name(self, code):
        # Uniformly, rather than only the one that collides: a contract where
        # you have to remember which struct needs the keyword is a contract
        # somebody gets wrong.
        for name in STRUCTS:
            assert f"typedef struct {name}" not in code, (
                f"{name} is typedef'd. The reason is no longer the collision, which the "
                "rename removed: the conformance generator reads every "
                "`typedef struct X X;` as an opaque handle, so a typedef of a struct "
                "that also has a body emits the type twice and the consumer stops "
                "compiling. Structs on this boundary are tags."
            )

    def test_the_opaque_handles_are_still_typedefed(self, code):
        # They have no fields and no call shares their name, so the idiomatic
        # opaque-pointer typedef costs nothing and saves every consumer a word.
        for name in HANDLES:
            assert f"typedef struct {name} {name};" in code

    def test_no_enum_is_used_as_abi(self, code):
        assert "enum" not in code, (
            "a C enum's width is implementation defined, so it is not an ABI type. "
            "Result codes and kinds are uint32_t constants."
        )

    def test_no_bool_crosses_the_boundary(self, code):
        assert not re.search(r"\bbool\b|\b_Bool\b", code), (
            "C bool and C# bool do not agree on width and neither is fixed by its "
            "language's ABI. Flags are uint8_t, 0 or 1."
        )

    def test_no_pointer_sized_integer_except_handles(self, code):
        for banned in ("size_t", "ssize_t", "intptr_t", "uintptr_t", "long "):
            assert banned not in code, (
                f"{banned!r} is in the header. Its width differs across the targets this "
                "ships to; lengths are uint64_t."
            )

    def test_every_string_carries_an_explicit_length(self, code):
        # Any UTF-8 buffer parameter must be accompanied by a length or a
        # capacity. Implicit null termination is the rule this catches.
        for m in re.finditer(r"uint32_t (viprs_acad_[a-z0-9_]+)\((.*?)\);", code, re.S):
            name, params = m.group(1), m.group(2)
            if "utf8" not in params:
                continue
            assert re.search(r"_len|cap|required", params), (
                f"{name} takes a UTF-8 buffer with no length, capacity or required-length "
                "parameter, which means it relies on a terminator that is not there."
            )


class TestItIsUsableFromC:
    def test_it_is_include_guarded(self, code):
        assert "#ifndef VIPRS_ACADSHARP_H" in code
        assert "#define VIPRS_ACADSHARP_H" in code

    def test_it_includes_stdint_for_the_types_it_uses(self, code):
        assert "#include <stdint.h>" in code, (
            "every type in this header is a fixed-width one from stdint.h"
        )

    def test_it_is_callable_from_cplusplus(self, code):
        assert 'extern "C"' in code
        assert code.count("#ifdef __cplusplus") == 2, (
            "the extern block needs opening and closing guards"
        )
