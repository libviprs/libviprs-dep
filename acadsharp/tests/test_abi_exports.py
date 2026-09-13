"""Every export is a wall, and these checks are what keep it one.

An `[UnmanagedCallersOnly]` method is called from C with no runtime between
the two. Whatever it does with a bad argument, it does to the caller's
process: a null dereference is a segfault in the consumer's address space,
and a managed exception that reaches the boundary is not an exception any
more, it is an abort with no stack the consumer can read.

So each export has to do the same three things, and none of them is visible
in a signature: validate every pointer and length before touching one, turn
every failure into a numeric result code, and catch everything on the way
out. A wrapper that does two of the three looks exactly like one that does
all three until the day a consumer passes a null.

These are text checks over `native/Exports.cs`. They cannot prove the
behaviour, which is what the C conformance consumer is for, but they fail on
the shape that makes the behaviour impossible, and they fail with no .NET
present.
"""

import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ACADSHARP = os.path.dirname(HERE)
NATIVE = os.path.join(ACADSHARP, "native")
EXPORTS = os.path.join(NATIVE, "Exports.cs")
CSPROJ = os.path.join(NATIVE, "Viprs.ACadSharp.Native.csproj")
HEADER = os.path.join(ACADSHARP, "include", "viprs_acadsharp.h")

TEST_DEFINE = "VIPRS_ACAD_TEST_EXPORTS"

# Not "Test". The SDK turns the configuration name into a preprocessor symbol,
# and the vendored upstream source carries `#if TEST` regions that do not
# compile on their own, so building this project with -c Test fails inside a
# dependency with an error that says nothing about configurations.
TEST_CONFIGURATION = "AbiTest"
TEST_EXPORT = "viprs_acad__test_throw"

# Both test-only exports. The second one reports the live handle count,
# because the interesting handle bugs are invisible from outside: a close
# called with the wrong handle type used to orphan the object, and from the
# caller's side that looked exactly like a close that worked.
TEST_EXPORTS = (TEST_EXPORT, "viprs_acad__test_live_handles")

# It answers a question rather than performing an operation, so it reports a
# count and not a result code. The catch-all still applies.
COUNTING_EXPORTS = ("viprs_acad__test_live_handles",)

# G1.1's spike exports. They are not on the frozen ABI, the smoke programs
# under tests/smoke/ still resolve them, and they go when the adapter lands.
SPIKE_EXPORTS = ("viprs_acad_describe", "viprs_acad_entity_count")

# Exports that cannot fail and so return void or a plain value.
INFALLIBLE = ("viprs_acad_abi_version", "viprs_acad_abi_fingerprint")
VOID_EXPORTS = ("viprs_acad_decode_close", "viprs_acad_close")

# The spike exports predate the frozen ABI and report through negative int
# codes of their own. They are held to the catch-all and to nothing else,
# because the result-code contract is not theirs.
OWN_ERROR_PROTOCOL = SPIKE_EXPORTS


def _bodies(code):
    """Map export name to its method body, by matching braces."""
    out = {}
    for m in re.finditer(r'\[UnmanagedCallersOnly\(EntryPoint = "(\w+)"\)\]', code):
        name = m.group(1)
        start = code.find("{", m.end())
        arrow = code.find(";", m.end())
        if start < 0 or (0 <= arrow < start):
            out[name] = code[m.end() : arrow + 1]  # expression bodied
            continue
        depth, i = 0, start
        while i < len(code):
            if code[i] == "{":
                depth += 1
            elif code[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        out[name] = code[m.end() : i + 1]
    return out


@pytest.fixture(scope="module")
def code():
    with open(EXPORTS) as f:
        return f.read()


@pytest.fixture(scope="module")
def bodies(code):
    return _bodies(code)


@pytest.fixture(scope="module")
def header_entry_points():
    with open(HEADER) as f:
        header = f.read()
    no_comments = re.sub(r"/\*.*?\*/", "", header, flags=re.S)
    return sorted(set(re.findall(r"\b(viprs_acad_[a-z0-9_]+)\s*\(", no_comments)))


class TestEveryEntryPointIsDeclaredOnce:
    """A duplicate entry point does not compile, and nothing here noticed.

    `_bodies` above is keyed by entry-point name, so a second method
    carrying the same `EntryPoint` silently replaced the first and every
    check in this file went on passing against whichever body came last.
    `main` carried two `viprs_acad__test_live_handles` for exactly that
    reason, which means the AbiTest configuration had not compiled since the
    adapter merged, which means neither conformance consumer could be built,
    which nothing in CI would have said. The names are counted from the
    attributes rather than from the map, because the map is where the
    duplicate goes missing.
    """

    def test_no_two_exports_share_an_entry_point(self, code):
        names = re.findall(r'\[UnmanagedCallersOnly\(EntryPoint = "(\w+)"\)\]', code)
        duplicated = sorted({n for n in names if names.count(n) > 1})
        assert not duplicated, (
            f"{duplicated} is declared more than once in Exports.cs. The project does "
            "not compile in any configuration that defines it, and the checks in this "
            "file keep passing because they read a map that only holds the last one."
        )

    def test_no_two_methods_share_a_name(self, code):
        names = re.findall(r"public static \w+ (\w+)\(", code)
        duplicated = sorted({n for n in names if names.count(n) > 1})
        assert not duplicated, f"Exports.cs defines {duplicated} twice, so it will not compile"


class TestTheHeaderAndTheShimAgree:
    def test_every_declared_entry_point_is_exported(self, bodies, header_entry_points):
        missing = [e for e in header_entry_points if e not in bodies]
        assert not missing, (
            f"the header declares {missing} and the shim exports no such symbol. A "
            "consumer generated from the header links, loads and fails at the first "
            "call with an unresolved symbol."
        )

    def test_the_shim_exports_nothing_the_epic_has_not_accounted_for(
        self, bodies, header_entry_points
    ):
        allowed = set(header_entry_points) | set(SPIKE_EXPORTS) | set(TEST_EXPORTS)
        extra = sorted(set(bodies) - allowed)
        assert not extra, (
            f"{extra} is exported but is neither in the header nor one of the spike "
            "exports. An export the header does not declare is a surface nothing is "
            "generated from and nothing checks."
        )

    def test_the_spike_exports_are_still_the_only_two(self, bodies):
        # They are kept because tests/smoke/ resolves them by bare name. If
        # one grows a third friend, the ABI has two surfaces instead of one.
        present = [e for e in SPIKE_EXPORTS if e in bodies]
        assert present == list(SPIKE_EXPORTS), f"the spike exports changed: {present}"


class TestNothingEscapes:
    def test_every_fallible_export_catches_everything(self, bodies):
        for name, body in bodies.items():
            if name in INFALLIBLE:
                continue
            assert re.search(r"catch\s*\(\s*Exception", body), (
                f"{name} has no catch-all. A managed exception that reaches unmanaged "
                "code is not an exception, it is an abort in the caller's process, and "
                "the caller has no way to turn it back into a result code."
            )

    def test_every_fallible_export_reports_internal_error(self, bodies):
        for name, body in bodies.items():
            if (
                name in INFALLIBLE
                or name in VOID_EXPORTS
                or name in OWN_ERROR_PROTOCOL
                or name in COUNTING_EXPORTS
            ):
                continue
            assert "InternalError" in body or "INTERNAL_ERROR" in body, (
                f"{name} catches but does not return INTERNAL_ERROR, so the consumer "
                "sees a success code for a call that did not happen."
            )

    def test_the_void_exports_swallow_rather_than_throw(self, bodies):
        for name in VOID_EXPORTS:
            assert re.search(r"catch\s*\(\s*Exception", bodies[name]), (
                f"{name} is documented as never failing, which it can only be if it "
                "catches. A close that throws leaves the caller with no way to release "
                "the handle and no code to look at."
            )

    def test_no_export_returns_a_managed_reference(self, code):
        decls = re.findall(
            r'\[UnmanagedCallersOnly\(EntryPoint = "\w+"\)\]\s*\n\s*'
            r"public static (?:unsafe )?(\S+)",
            code,
        )
        assert decls
        for ret in decls:
            assert ret in ("uint", "ulong", "int", "void"), (
                f"an export returns {ret!r}. Only blittable primitives cross this "
                "boundary; a managed reference has no meaning on the other side of it."
            )


class TestArgumentsAreValidated:
    def test_every_fallible_export_can_return_invalid_argument(self, bodies):
        for name, body in bodies.items():
            if (
                name in INFALLIBLE
                or name in VOID_EXPORTS
                or name in OWN_ERROR_PROTOCOL
                or name in COUNTING_EXPORTS
                or name in TEST_EXPORTS
            ):
                continue
            assert "InvalidArgument" in body or "INVALID_ARGUMENT" in body, (
                f"{name} never returns INVALID_ARGUMENT. Every export on this boundary "
                "takes at least one pointer or length, and the only alternative to "
                "refusing a bad one is dereferencing it."
            )

    def test_every_pointer_taking_export_checks_for_null(self, code, bodies):
        for name, body in bodies.items():
            decl = re.search(
                rf'EntryPoint = "{name}"\)\]\s*\n\s*public static [^\n]*\(([^)]*)', code, re.S
            )
            if decl is None or "*" not in decl.group(1):
                continue
            assert re.search(r"==\s*null|IsNull|Null\(", body), (
                f"{name} takes a pointer and never compares one against null. A null "
                "handle has to come back as INVALID_ARGUMENT, not as a segfault in a "
                "process that has no idea what happened."
            )

    def test_the_header_promises_the_defaults_a_null_limits_pointer_means(self):
        with open(HEADER) as f:
            header = f.read()
        # Two short phrases rather than one long one: the header is wrapped to
        # fit a terminal and a comment continuation sits in the middle of the
        # sentence, so matching the whole thing tests the wrapping.
        assert "null pointer to either open call" in header
        assert "documented defaults" in header


class TestTheTestOnlyExport:
    def test_it_exists(self, bodies):
        assert TEST_EXPORT in bodies, (
            f"{TEST_EXPORT} is what proves the catch-all is real. Without it the only "
            "evidence that exceptions cannot escape is that none has escaped yet."
        )

    def test_every_test_export_is_compiled_only_behind_the_define(self, code):
        block = re.search(rf"#if {TEST_DEFINE}(.*?)#endif", code, re.S)
        assert block, f"there is no #if {TEST_DEFINE} block"
        for name in TEST_EXPORTS:
            assert name in block.group(1), (
                f"{name} is outside the guarded block, so a release build of the shim "
                "ships an export that exists only to be tested against."
            )

    def test_it_actually_throws(self, bodies):
        assert "throw" in bodies[TEST_EXPORT], (
            "an export named after throwing that does not throw proves nothing about "
            "the wrapper it is there to test"
        )

    def test_the_define_comes_only_from_the_test_configuration(self):
        with open(CSPROJ) as f:
            csproj = f.read()
        assert "<Configurations>" in csproj and TEST_CONFIGURATION in csproj
        m = re.search(
            rf"<PropertyGroup Condition=\"'\$\(Configuration\)' == '{TEST_CONFIGURATION}'\">"
            r"(.*?)</PropertyGroup>",
            csproj,
            re.S,
        )
        assert m, f"the project has no {TEST_CONFIGURATION} configuration property group"
        assert TEST_DEFINE in m.group(1)
        outside = csproj.replace(m.group(0), "")
        assert TEST_DEFINE not in outside, (
            f"{TEST_DEFINE} is defined outside the {TEST_CONFIGURATION} configuration, "
            "which is the one way a release build quietly grows the throwing export back"
        )

    def test_the_configuration_is_not_called_test(self):
        with open(CSPROJ) as f:
            csproj = f.read()
        m = re.search(r"<Configurations>([^<]*)</Configurations>", csproj)
        assert m, "the project declares no configurations"
        names = [n.strip() for n in m.group(1).split(";")]
        assert "Test" not in names, (
            "a configuration called Test makes the SDK define TEST, which switches on "
            "`#if TEST` regions inside the vendored upstream source that do not compile. "
            "The build then fails in a dependency with an error that never mentions "
            "configurations, which took a build to work out once already."
        )
        assert TEST_CONFIGURATION in names
