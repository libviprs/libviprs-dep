"""Rules about the shim's source that no compiler and no consumer can see.

Each one is a shape, not a behaviour. A bound enforced in three places with
three different error mappings compiles, runs, and returns whichever code the
layer that noticed first happened to use; a decode handle that is tracked and
never untracked leaks eight bytes per decode and every test stays green. So
these read `native/` as text, the way `test_abi_exports.py` does, and fail on
the arrangement that makes the bug possible rather than on the bug.

They run with no .NET present, which is the only way they run at all here
(ADR 0001).
"""

import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ACADSHARP = os.path.dirname(HERE)
NATIVE = os.path.join(ACADSHARP, "native")

EXPORTS = os.path.join(NATIVE, "Exports.cs")
DECODE_SESSION = os.path.join(NATIVE, "Wire", "DecodeSession.cs")
SOURCE_FACTORY = os.path.join(NATIVE, "Sources", "SourceFactory.cs")
ACAD_SOURCE = os.path.join(NATIVE, "Sources", "AcadSharpSource.cs")


def read(path):
    with open(path) as f:
        return f.read()


def shim_sources():
    """Every C# file in the shim, by path relative to `native/`."""
    out = {}
    for root, _dirs, files in os.walk(NATIVE):
        for name in sorted(files):
            if name.endswith(".cs"):
                full = os.path.join(root, name)
                out[os.path.relpath(full, NATIVE)] = read(full)
    return out


def body_of(code, signature):
    """The brace-matched body of the first member whose declaration matches."""
    m = re.search(signature, code)
    if m is None:
        return None
    start = code.find("{", m.end())
    if start < 0:
        return None
    depth, i = 0, start
    while i < len(code):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                return code[start : i + 1]
        i += 1
    return None


# ---------------------------------------------------------------------------
# The decode list a document keeps
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def decode_session():
    return read(DECODE_SESSION)


class TestEveryTrackedDecodeCanBeUntracked:
    """`_decodes` is how closing a document closes the decodes still open on
    it. `decode_begin` appends to it and `decode_close` used to remove the
    handle from the global table, dispose the session and leave the entry
    behind, so the list only ever emptied when the document did. A consumer
    re-decoding one view in a loop grows it without bound, and
    `viprs_acad__test_live_handles` cannot see it: that counts the handle
    table, which is the one thing `decode_close` did clean up."""

    def test_the_document_can_untrack_a_decode(self, decode_session):
        assert "public void Untrack(IntPtr" in decode_session, (
            "DocumentHandle has Track and no Untrack, so the list of open decodes only "
            "shrinks when the document closes"
        )

    def test_untrack_removes_under_the_same_lock_as_track(self, decode_session):
        track = body_of(decode_session, r"public void Track\(IntPtr \w+\)")
        untrack = body_of(decode_session, r"public void Untrack\(IntPtr \w+\)")
        assert track and untrack, "Track or Untrack is not a method with a body"
        for label, body in (("Track", track), ("Untrack", untrack)):
            assert "lock (_decodes)" in body, (
                f"{label} touches the decode list without holding the lock. Dispose "
                "walks the same list from whatever thread closed the document."
            )
        assert re.search(r"_decodes\.Remove\(", untrack), (
            "Untrack does not remove anything from _decodes, so it is a method that "
            "reads like a fix and is not one"
        )

    def test_decode_close_untracks_what_it_closes(self):
        body = body_of(read(EXPORTS), r'EntryPoint = "viprs_acad_decode_close"\)\]\s*\n\s*[^\n]+')
        assert body, "viprs_acad_decode_close is not an export with a body"
        assert "Untrack" in body, (
            "viprs_acad_decode_close removes the handle from the table and disposes the "
            "session without untracking it, so the document keeps a dead entry for every "
            "decode the caller opened and closed"
        )

    def test_the_session_still_knows_its_document(self, decode_session):
        # The control. Untracking from decode_close is only reachable because
        # DecodeSession exposes the document it belongs to; drop that and the
        # export has nothing to call.
        assert "public DocumentHandle Document" in decode_session


# ---------------------------------------------------------------------------
# max_input_bytes
# ---------------------------------------------------------------------------

# A comparison of anything against a resolved max_input_bytes. Not a mention:
# ResolvedLimits declares the field and fills it in, and neither is enforcement.
_ENFORCEMENT = re.compile(r"[<>]=?\s*\w+\.MaxInputBytes|\w+\.MaxInputBytes\s*[<>]=?")

# The one call that turns a caller's path into a size, which is also the one
# place that has to decide what a failed stat means.
_STAT = re.compile(r"new FileInfo\(")


class TestMaxInputBytesIsEnforcedInOnePlace:
    """It was three, with three mappings. `SourceFactory.FileLength` called a
    failed stat CORRUPT_INPUT, `AcadSharpSource.OpenPath` and
    `Exports.OpenPathUtf8` called it INVALID_ARGUMENT, and which one a caller
    saw depended on which layer noticed first. A caller cannot write a branch
    for a code that depends on a race."""

    def test_only_the_source_factory_compares_against_it(self):
        offenders = sorted(
            name for name, code in shim_sources().items() if _ENFORCEMENT.search(code)
        )
        assert offenders == [os.path.join("Sources", "SourceFactory.cs")], (
            f"max_input_bytes is compared against in {offenders}. One bound with two "
            "spellings is a bound whose refusal code depends on which layer noticed "
            "first, and the ordering comment in SourceFactory is the argument for "
            "where it belongs."
        )

    def test_the_source_factory_still_enforces_it(self):
        code = read(SOURCE_FACTORY)
        assert _ENFORCEMENT.search(code), (
            "nothing compares against max_input_bytes any more, so the bound the header "
            "promises is not applied at all. This is the positive control for the check "
            "above, which an empty shim would also pass."
        )
        assert "LimitExceeded" in code

    @pytest.mark.parametrize("route", ["OpenPath", "OpenMemory"])
    def test_both_open_routes_reach_it(self, route):
        body = body_of(read(SOURCE_FACTORY), rf"public static IDocumentSource {route}\(")
        assert body, f"SourceFactory has no {route}"
        assert "CheckInputBytes" in body or "FileLength" in body, (
            f"SourceFactory.{route} never applies max_input_bytes, so a too-large input "
            "is refused through one open call and not the other"
        )

    def test_only_one_place_stats_a_path(self):
        offenders = sorted(name for name, code in shim_sources().items() if _STAT.search(code))
        assert offenders == [os.path.join("Sources", "SourceFactory.cs")], (
            f"{offenders} each stat the caller's path. Two stats are two chances to map "
            "the same failure to two codes, which is exactly what happened."
        )
        assert len(_STAT.findall(read(SOURCE_FACTORY))) == 1

    def test_the_failed_stat_has_one_mapping(self):
        body = body_of(read(SOURCE_FACTORY), r"private static long FileLength\(")
        assert body, "SourceFactory no longer has FileLength"
        codes = set(re.findall(r"Result\.(\w+)", body))
        assert codes == {"InvalidArgument", "CorruptInput"}, (
            f"FileLength reports {sorted(codes)}. A missing path is the caller's "
            "argument and a path that exists and cannot be stat'ed is the input's "
            "problem, and those are the only two things this decides. The bound "
            "itself is CheckInputBytes."
        )

    def test_the_bound_reports_one_code(self):
        body = body_of(read(SOURCE_FACTORY), r"public static void CheckInputBytes\(")
        assert body, "SourceFactory no longer has CheckInputBytes"
        codes = set(re.findall(r"Result\.(\w+)", body))
        assert codes == {"LimitExceeded"}, (
            f"the bound reports {sorted(codes)}. The header says exceeding a limit is "
            "VIPRS_ACAD_LIMIT_EXCEEDED, and nothing else."
        )

    def test_the_memory_route_is_bounded_before_the_copy(self):
        # open_memory duplicates the caller's buffer, and a bound applied
        # after the copy has already paid for the thing it was refusing. This
        # is the one call outside SourceFactory that may know the number, and
        # it asks SourceFactory rather than deciding for itself.
        body = body_of(read(EXPORTS), r'EntryPoint = "viprs_acad_open_memory"\)\]\s*\n\s*[^\n]+')
        assert body, "viprs_acad_open_memory is not an export with a body"
        assert "CheckInputBytes" in body, (
            "open_memory copies the caller's bytes before anything applies "
            "max_input_bytes, so a buffer past the bound is duplicated and then refused"
        )
        assert body.index("CheckInputBytes") < body.index("Marshal.Copy"), (
            "the bound is applied after the copy, which is the allocation it exists to prevent"
        )


# ---------------------------------------------------------------------------
# The synthetic backing on the path route
# ---------------------------------------------------------------------------


class TestTheSyntheticDocumentIsReachableBothWays:
    """`docs/ABI.md` documented it for `open_memory` only, and
    `SourceFactory.OpenPath` has always sniffed it too. A second implementation
    built from the specification would have got that wrong in the direction
    that is hardest to notice: it would work on every real drawing."""

    def test_the_path_route_sniffs_it(self):
        body = body_of(read(SOURCE_FACTORY), r"public static IDocumentSource OpenPath\(")
        assert body, "SourceFactory has no OpenPath"
        assert "SyntheticSource.Matches" in body, (
            "the path route no longer recognises the synthetic magic. If that is "
            "deliberate, ABI.md and the header have to stop saying it does."
        )

    def test_the_memory_route_sniffs_it(self):
        body = body_of(read(SOURCE_FACTORY), r"public static IDocumentSource OpenMemory\(")
        assert body and "SyntheticSource.Matches" in body


# ---------------------------------------------------------------------------
# The test-only exports
# ---------------------------------------------------------------------------


class TestTheTestConfigurationCompiles:
    """`viprs_acad__test_live_handles` was declared twice inside the same
    `#if VIPRS_ACAD_TEST_EXPORTS` block. Two members with one signature is a
    build error, so the AbiTest configuration could not be published at all,
    and the AbiTest configuration is the one both conformance consumers run
    against. Nothing noticed because every check here keys exports by name
    into a dictionary, where the second declaration silently replaces the
    first."""

    def test_no_entry_point_is_declared_twice(self):
        code = read(EXPORTS)
        names = re.findall(r'\[UnmanagedCallersOnly\(EntryPoint = "(\w+)"\)\]', code)
        duplicates = sorted({n for n in names if names.count(n) > 1})
        assert not duplicates, (
            f"{duplicates} is declared more than once in Exports.cs. The C# compiler "
            "refuses a type with two members of the same signature, so whichever "
            "configuration compiles both of them does not build."
        )

    def test_no_method_name_is_declared_twice(self):
        code = read(EXPORTS)
        names = re.findall(r"public static (?:unsafe )?\S+ (\w+)\(", code)
        duplicates = sorted({n for n in names if names.count(n) > 1})
        assert not duplicates, f"Exports.cs declares {duplicates} more than once"
