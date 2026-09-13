"""Tests for the libunwind rename, and for the skip that hid the need for it.

Two things are pinned here, and they are two halves of one failure.

The first is a gap in the checking. `verify_archive.sh` holds
`static_certified: true` to an archive that links and runs, but only when
the host can build for the target, and otherwise prints

    static_certified: not link-tested (this host cannot build for musl/x64)

into a log and exits 0. Every musl cell in release-acadsharp.yml builds on
a glibc runner, so that line is what both musl archives got, every
release. They shipped a documented cargo recipe that nothing had ever
run.

The second is what running it finally showed. NativeAOT statically links
its own llvm-libunwind into the runtime archives, and rustc links a
`self-contained/libunwind.a` of its own for every musl target and for no
glibc one. Measured on native x86_64 musl and native arm64 musl against
the published 3.7.1-viprs.1 archives, with the glibc archive through the
same harness on the same machine as the control: the glibc archive links
and runs, the musl one dies on 59 duplicate symbols. Renaming ours across
the whole merged archive fixes it, and the renamed archive still links
through the C recipe, still runs 3000 managed throws, and runs 2000 Rust
panics through `catch_unwind` in the same binary, which is the half that
proves the two unwinders coexist rather than merely link.

None of that is reproducible from pytest: it needs a NativeAOT archive
and a musl rustc. What is testable here is everything around it. That the
rename script does what it says to a real archive, that the verifier
refuses an archive carrying the original names *and* refuses one carrying
neither, that the build runs the rename where it has to, and that the two
workflows verify a musl archive on a musl host instead of writing a line
about it into a log.
"""

import os
import platform
import shutil
import subprocess
import sys

import build_acadsharp as ba
import pytest
import test_verify_archive as fixtures
import toolchain

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ACAD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS = os.path.join(ACAD_DIR, "scripts")
PRIVATISE = os.path.join(SCRIPTS, "privatise_unwind.sh")
VERIFY = os.path.join(SCRIPTS, "verify_archive.sh")
MATCHED_HOST = os.path.join(SCRIPTS, "verify_archive_matched_host.sh")

HOST_CPU = "arm64" if platform.machine() in ("arm64", "aarch64") else "x64"
OTHER_CPU = "x64" if HOST_CPU == "arm64" else "arm64"


def _require_binutils():
    for tool in ("cc", "ar", "nm", "objcopy"):
        if not shutil.which(tool):
            toolchain.missing_tool(tool, "the fixtures here are real ELF objects")
    # A platform skip rather than a toolchain one, so it stays a skip under
    # VIPRS_REQUIRE_COMPILED_FIXTURES. A mac cell cannot produce an ELF and
    # no flag changes that.
    if sys.platform != "linux":
        pytest.skip("the fixtures here are ELF ones; run this suite on Linux")


def _compile(work, name, source):
    src = os.path.join(work, f"{name}.c")
    with open(src, "w") as f:
        f.write(source)
    obj = os.path.join(work, f"{name}.o")
    subprocess.run(["cc", "-fPIC", "-c", src, "-o", obj], check=True)
    return obj


def _defined(archive):
    out = subprocess.run(
        ["nm", "--defined-only", "-g", archive], capture_output=True, text=True, check=True
    ).stdout
    return {line.split()[2] for line in out.splitlines() if len(line.split()) >= 3}


def _undefined(archive):
    out = subprocess.run(
        ["nm", "--undefined-only", archive], capture_output=True, text=True, check=True
    ).stdout
    return {line.split()[1] for line in out.splitlines() if len(line.split()) == 2}


def _members(archive):
    return subprocess.run(
        ["ar", "t", archive], capture_output=True, text=True, check=True
    ).stdout.splitlines()


# The shape the real archive has, in miniature: a definition of the
# bundled unwinder in one member, a reference to it from another, the
# public personality ABI beside it, and a mangled name from the libunwind
# C++ namespace. The asm labels are how an exact symbol name is written
# from C.
UNWINDER = """\
void step(void) __asm__("__unw_step");
void step(void) { }

void getcontext(void) __asm__("unw_getcontext");
void getcontext(void) { }

void space(void) __asm__("_ZN9libunwind17LocalAddressSpace10getULEB128ERmm");
void space(void) { }

void vtable(void) __asm__("_ZTVN9libunwind12UnwindCursorINS_17LocalAddressSpaceEEE");
void vtable(void) { }

void raise_exception(void) __asm__("_Unwind_RaiseException");
void raise_exception(void) { }
"""

# A second member that calls into the first. Renaming a definition without
# renaming this reference produces an archive that links nowhere at all,
# so this is the member that says the rename reached the whole file.
CALLER = """\
extern void step(void) __asm__("__unw_step");
extern void space(void) __asm__("_ZN9libunwind17LocalAddressSpace10getULEB128ERmm");
void viprs_test_walk(void);
void viprs_test_walk(void) { step(); space(); }
"""


@pytest.fixture
def bundled_archive(tmp_path):
    """An archive carrying libunwind under its original names."""
    _require_binutils()
    work = str(tmp_path)
    unwinder = _compile(work, "unwinder", UNWINDER)
    caller = _compile(work, "caller", CALLER)
    path = os.path.join(work, "libfixture.a")
    subprocess.run(["ar", "rcs", path, unwinder, caller], check=True)
    return path


def _privatise(*archives):
    return subprocess.run(["sh", PRIVATISE, *archives], capture_output=True, text=True, check=False)


def _path_without(tool, tmp_path):
    """A PATH holding everything the real one does, minus one tool.

    Symlinked rather than filtered, because the tools these tests take
    away live in different directories on a runner than on a container
    and a hardcoded list drops the wrong thing on the other one.
    """
    bare = os.path.join(str(tmp_path), f"no-{tool}")
    os.makedirs(bare, exist_ok=True)
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory or not os.path.isdir(directory):
            continue
        for entry in os.listdir(directory):
            if entry == tool:
                continue
            link = os.path.join(bare, entry)
            if not os.path.lexists(link):
                try:
                    os.symlink(os.path.join(directory, entry), link)
                except OSError:
                    pass
    return bare


@pytest.fixture(scope="session")
def good_tree(tmp_path_factory):
    """The release layout, with the unwinder already private.

    Built here rather than imported: pytest fixtures do not cross files,
    and the builder does, which is the half that matters.
    """
    fixtures._require_toolchain()
    return fixtures._build_linux_tree(str(tmp_path_factory.mktemp("unwind-good")))


class TestTheRenameMovesTheWholeArchive:
    def test_the_bundled_names_are_gone(self, bundled_archive):
        before = _defined(bundled_archive)
        assert "__unw_step" in before and "unw_getcontext" in before

        result = _privatise(bundled_archive)
        assert result.returncode == 0, result.stdout + result.stderr

        after = _defined(bundled_archive)
        assert "__unw_step" not in after
        assert "unw_getcontext" not in after
        assert "__viprs_unw_step" in after
        assert "viprs_unw_getcontext" in after

    def test_a_reference_in_another_member_moves_with_the_definition(self, bundled_archive):
        """The whole reason this runs over the archive and not the objects.

        `objcopy --redefine-syms` rewrites a name wherever it appears, so
        one pass over the archive moves the definition in one member and
        the undefined reference in the next together. Rename them apart
        and the archive stops linking at all, which is a worse outcome
        than the collision it was meant to fix.
        """
        assert "__unw_step" in _undefined(bundled_archive)

        _privatise(bundled_archive)

        undefined = _undefined(bundled_archive)
        assert "__unw_step" not in undefined, (
            "the caller still asks for the old name, so nothing defines what it wants"
        )
        assert "__viprs_unw_step" in undefined

    def test_the_mangled_names_travel_and_still_demangle(self, bundled_archive):
        _privatise(bundled_archive)

        after = _defined(bundled_archive)
        # `libunwind` is nine characters and `viprs_libunwind` is fifteen,
        # so the length prefix moves with the name. Written as a plain
        # prefix the result is an invalid mangling that no backtrace can
        # read.
        assert "_ZN15viprs_libunwind17LocalAddressSpace10getULEB128ERmm" in after
        assert "_ZTVN15viprs_libunwind12UnwindCursorINS_17LocalAddressSpaceEEE" in after
        assert not any(name.startswith("_ZN9libunwind") for name in after)
        assert not any(name.startswith("_ZTVN9libunwind") for name in after)

    def test_the_personality_abi_is_left_alone(self, bundled_archive):
        """`_Unwind_*` is the name a compiler emits, not one we choose.

        A landing pad in any C++ or Rust frame calls `_Unwind_Resume` by
        that name. Renaming it would split the language runtimes instead
        of the unwinder, which is not the problem. Measured, the real
        merged archive defines none of them, so this is a rule about what
        the selection must never reach rather than about today's archive.
        """
        _privatise(bundled_archive)

        after = _defined(bundled_archive)
        assert "_Unwind_RaiseException" in after
        assert "viprs__Unwind_RaiseException" not in after
        assert "__viprs_Unwind_RaiseException" not in after

    def test_member_order_survives(self, bundled_archive):
        """The linker emits `.init_array` in the order it pulls members.

        stage.sh spends a long comment on this: built from the
        filesystem's order instead of the source archives' order, the
        merged archive links perfectly and the binary segfaults on the way
        out, every time. A rewrite that reorders members would put that
        back, silently.
        """
        before = _members(bundled_archive)
        _privatise(bundled_archive)
        assert _members(bundled_archive) == before

    def test_it_says_how_many_it_renamed(self, bundled_archive):
        result = _privatise(bundled_archive)
        assert "renamed 4 libunwind symbols" in result.stdout, result.stdout

    def test_running_it_twice_is_a_no_op(self, bundled_archive):
        _privatise(bundled_archive)
        second = _privatise(bundled_archive)
        assert second.returncode == 0
        assert "nothing matched" in second.stdout

    def test_it_refuses_an_archive_that_is_not_there(self, tmp_path):
        _require_binutils()
        result = _privatise(os.path.join(str(tmp_path), "absent.a"))
        assert result.returncode == 2
        assert "is not a file" in result.stderr

    def test_it_refuses_to_run_with_no_argument(self):
        # Before any tool check, so this one holds on a host with no
        # binutils too.
        result = subprocess.run(["sh", PRIVATISE], capture_output=True, text=True, check=False)
        assert result.returncode == 2
        assert "no archive given" in result.stderr

    def test_two_archives_share_one_map(self, tmp_path):
        """The init archive goes through the same call, with the same map.

        It defines only the runtime's static initialiser today and
        references no unwinder at all, so this is about the day it does:
        renaming the main archive alone would leave it calling a name
        that had moved.
        """
        _require_binutils()
        work = str(tmp_path)
        unwinder = _compile(work, "u", UNWINDER)
        caller = _compile(work, "c", CALLER)
        main = os.path.join(work, "libmain.a")
        other = os.path.join(work, "libother.a")
        subprocess.run(["ar", "rcs", main, unwinder], check=True)
        subprocess.run(["ar", "rcs", other, caller], check=True)

        assert _privatise(main, other).returncode == 0

        assert "__viprs_unw_step" in _defined(main)
        assert "__viprs_unw_step" in _undefined(other), (
            "the second archive kept the old name, so the map was not shared"
        )


# ---------------------------------------------------------------------------
# What the verifier asks of the bytes that ship
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def tree_with_a_bundled_unwinder(tmp_path_factory):
    fixtures._require_toolchain()
    return fixtures._build_linux_tree(str(tmp_path_factory.mktemp("bundled")), unwind="bundled")


@pytest.fixture(scope="session")
def tree_with_no_unwinder(tmp_path_factory):
    fixtures._require_toolchain()
    return fixtures._build_linux_tree(str(tmp_path_factory.mktemp("nounwind")), unwind="none")


class TestTheVerifierReadsTheShippedNames:
    """Read out of the symbol index, so it holds for any target on any host.

    That is the point of doing it this way rather than by linking. The
    consumer link runs only where the host libc matches the archive's, and
    a check that runs only there is how this shipped broken twice. This
    one runs in the x64 cell for the arm64 archive and on a developer's
    Mac for both.
    """

    def test_a_good_archive_says_the_unwinder_is_private(self, tmp_path, good_tree):
        tgz = fixtures._pack(fixtures._clone(good_tree, tmp_path))
        result = fixtures._verify(tgz)
        assert result.returncode == 0, fixtures._output(result)
        assert "the bundled libunwind is private" in fixtures._output(result)

    def test_the_original_names_are_refused(self, tmp_path, tree_with_a_bundled_unwinder):
        tgz = fixtures._pack(fixtures._clone(tree_with_a_bundled_unwinder, tmp_path))
        result = fixtures._verify(tgz)
        assert result.returncode != 0
        out = fixtures._output(result)
        assert "libunwind symbols under their original" in out, out
        assert "cargo recipe cannot link this archive on any musl" in out

    def test_an_archive_with_neither_name_is_refused(self, tmp_path, tree_with_no_unwinder):
        """The zero needs a positive control.

        Checking only for the old names passes an archive that bundles no
        libunwind at all, and that is also exactly what "the rename step
        stopped running" looks like from here on the day the runtime
        bundles one again. So the absence of both is a refusal, and a
        runtime that genuinely drops its copy costs one red release and a
        human reading this message.
        """
        tgz = fixtures._pack(fixtures._clone(tree_with_no_unwinder, tmp_path))
        result = fixtures._verify(tgz)
        assert result.returncode != 0
        out = fixtures._output(result)
        assert "no libunwind symbol at all" in out, out

    def test_it_does_not_need_a_linker(self, tmp_path, good_tree):
        """The same archive, verified with no cc on PATH at all.

        If this check ever starts needing a linker it stops covering the
        cells that skip the consumer link, which are the only cells it was
        written for.
        """
        tgz = fixtures._pack(fixtures._clone(good_tree, tmp_path))
        bare = _path_without("cc", tmp_path)
        result = subprocess.run(
            ["bash", VERIFY, tgz],
            capture_output=True,
            text=True,
            check=False,
            env=dict(os.environ, PATH=bare),
        )
        out = result.stdout + result.stderr
        assert "the bundled libunwind is private" in out, out
        assert "not link-tested" in out, (
            "cc is still being found, so this did not test what it says"
        )


# ---------------------------------------------------------------------------
# The skip that hid all of it
# ---------------------------------------------------------------------------


class TestASkippedLinkTestCanBeMadeAFailure:
    def test_by_default_it_is_still_only_a_line(self, tmp_path, musl_labelled_tree):
        """Unchanged for anyone running the verifier by hand.

        A developer on an arm64 Mac verifying an x64 archive has nothing
        to link it with and should not be told the archive is broken.
        """
        tgz = fixtures._pack(fixtures._clone(musl_labelled_tree, tmp_path))
        result = fixtures._verify(tgz)
        assert result.returncode == 0, fixtures._output(result)
        assert "not link-tested" in fixtures._output(result)

    def test_the_flag_turns_it_into_a_failure(self, tmp_path, musl_labelled_tree):
        tgz = fixtures._pack(fixtures._clone(musl_labelled_tree, tmp_path))
        result = subprocess.run(
            ["bash", VERIFY, tgz],
            capture_output=True,
            text=True,
            check=False,
            env=dict(os.environ, VIPRS_REQUIRE_LINK_TEST="1"),
        )
        out = result.stdout + result.stderr
        assert result.returncode != 0, out
        assert "VIPRS_REQUIRE_LINK_TEST is set" in out
        assert "verify_archive_matched_host.sh" in out, (
            "the failure has to name the way out of it, or it is a wall"
        )

    def test_a_missing_cargo_is_a_failure_under_the_flag_too(self, tmp_path, good_tree):
        """The other half of the same skip, and the half that shipped.

        The C link ran on the musl cells' own architecture for years. The
        line that was never reached is the cargo one, and without this it
        goes missing under exactly the same silence.
        """
        _require_binutils()
        tgz = fixtures._pack(fixtures._clone(good_tree, tmp_path))
        bare = _path_without("cargo", tmp_path)
        if not os.path.lexists(os.path.join(bare, "cc")):
            toolchain.missing_tool("cc", "the static link this rides on never runs")
        result = subprocess.run(
            ["bash", VERIFY, tgz],
            capture_output=True,
            text=True,
            check=False,
            env=dict(os.environ, PATH=bare, VIPRS_REQUIRE_LINK_TEST="1"),
        )
        out = result.stdout + result.stderr
        assert result.returncode != 0, out
        assert "there is no cargo here" in out, out


@pytest.fixture(scope="session")
def musl_labelled_tree(tmp_path_factory):
    """A musl archive, on whatever host this is.

    The compiler is the container's, so this is a glibc ELF under a musl
    manifest and a musl filename. Every byte-level check reads it happily,
    and it is the only way to put a musl archive in front of a glibc host
    from pytest, which is the situation both musl cells were verified in
    for every release.
    """
    fixtures._require_toolchain()
    if platform.system() != "Linux":
        pytest.skip("a glibc host is needed for the mismatch to be a mismatch")
    line = subprocess.run(
        ["sh", "-c", "ldd --version 2>&1 | sed -n 1p"], capture_output=True, text=True
    ).stdout
    if "musl" in line.lower():
        pytest.skip("this host is musl, so a musl archive is not a mismatch here")
    return fixtures._build_linux_tree(str(tmp_path_factory.mktemp("musl")), plat="musl")


class TestTheWrapperPicksTheHost:
    def test_it_is_there_and_executable(self):
        assert os.path.isfile(MATCHED_HOST)
        assert os.access(MATCHED_HOST, os.X_OK)

    def test_it_ends_up_running_the_verifier(self):
        with open(MATCHED_HOST) as f:
            text = f.read()
        assert "verify_archive.sh" in text, (
            "the wrapper has to reach the verifier, or the indirection is a check "
            "that stopped happening"
        )

    def test_it_requires_the_link_test_on_both_paths(self):
        with open(MATCHED_HOST) as f:
            text = f.read()
        assert "export VIPRS_REQUIRE_LINK_TEST=1" in text
        assert "-e VIPRS_REQUIRE_LINK_TEST=1" in text, (
            "an exported variable does not cross into a container; the docker run "
            "has to carry it or the containerised half can still skip silently"
        )

    def test_the_container_platform_is_explicit(self):
        """An unpinned `docker run` gets DOCKER_DEFAULT_PLATFORM.

        That has been both values on the machines this repo is developed
        on, so an architecture result from an unpinned container means
        nothing, and this one is entirely about architecture.
        """
        with open(MATCHED_HOST) as f:
            text = f.read()
        assert "--platform" in text
        assert "linux/amd64" in text and "linux/arm64" in text

    def test_the_image_is_pinned(self):
        with open(MATCHED_HOST) as f:
            text = f.read()
        image = [ln for ln in text.splitlines() if "VIPRS_MUSL_VERIFY_IMAGE" in ln and ":-" in ln]
        assert image, "the wrapper names no default image"
        assert ":latest" not in image[0] and "-alpine}" in image[0], (
            f"{image[0].strip()} floats. Whether rustc links its self-contained "
            "libunwind is the thing being measured, so the rustc has to be pinned"
        )

    def test_it_refuses_to_emulate_another_architecture(self, tmp_path):
        """A cross-architecture link answers a question nobody asked.

        ADR 0001 measured a cross-architecture publish producing the
        object file and then failing at the native link, and .NET
        documents qemu-user-static as unsupported. So the wrapper picks
        the libc and never the architecture.
        """
        tgz = os.path.join(str(tmp_path), f"acadsharp-musl-{OTHER_CPU}.tgz")
        with open(tgz, "w") as f:
            f.write("")
        result = subprocess.run(
            ["bash", MATCHED_HOST, tgz], capture_output=True, text=True, check=False
        )
        out = result.stdout + result.stderr
        assert result.returncode == 2, out
        assert "Emulating the link proves nothing" in out

    def test_it_refuses_an_archive_that_is_not_there(self, tmp_path):
        result = subprocess.run(
            ["bash", MATCHED_HOST, os.path.join(str(tmp_path), "acadsharp-musl-x64.tgz")],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 2
        assert "does not exist" in result.stdout + result.stderr


# ---------------------------------------------------------------------------
# The build side
# ---------------------------------------------------------------------------


class TestTheBuildRunsTheRename:
    def test_the_stage_script_calls_it(self):
        # One script for every cell since #72, so this is one assertion
        # rather than one per platform. The rename runs on glibc too:
        # nothing collides there, and a branch that only ever runs in the
        # two musl cells is a branch nothing cheap exercises.
        assert "privatise_unwind.sh" in ba.stage_script(), (
            "stage.sh assembles the archive without renaming its unwinder"
        )

    def test_the_stage_script_in_the_tree_is_the_one_that_runs(self):
        """#72 took stage.sh out of a Python string and into a real file.

        A rebase that dropped the rename in that move would leave every
        other assertion here reading whatever the file happens to say, so
        this one pins that `stage_script()` is the file itself.
        """
        with open(os.path.join(SCRIPTS, "stage.sh")) as f:
            assert f.read() == ba.stage_script()

    def test_it_runs_after_the_archive_is_assembled(self):
        """Before `ar qc` it would rename loose objects and miss the rest.

        The whole value of doing this to the archive is that one pass
        reaches every member, so the definitions and the references move
        together.
        """
        stage = ba.stage_script()
        assert stage.index("ar qc") < stage.index("privatise_unwind.sh")

    def test_it_runs_before_the_static_smoke(self):
        """So the C link is measured against the archive that ships.

        Renaming after the smoke means the smoke passed on a file nobody
        gets.
        """
        stage = ba.stage_script()
        assert stage.index("privatise_unwind.sh") < stage.index("---- static smoke ----")

    def test_the_init_archive_goes_through_the_same_call(self):
        stage = ba.stage_script()
        # From the assembly of the archive, so the `if` that guards the
        # call is inside the slice rather than just before it.
        at = stage.index('ranlib "$MERGED"')
        call = stage[at : stage.index("---- static smoke ----", at)]
        assert '"$MERGED"' in call and '"$INIT_A"' in call, (
            "both archives have to share one map, or a reference in one binds to a "
            "name the other no longer defines"
        )

    def test_a_failed_rename_is_not_swallowed(self):
        """A pipeline reports the last command's status.

        Written as `PRIVATISED=$(... | sed ...)` this reads a failed
        rename as a success and ships the archive anyway, which is the
        same shape as the `|| true` that re-shipped the #67 archive.
        """
        stage = ba.stage_script()
        # From the assembly of the archive, so the `if` that guards the
        # call is inside the slice rather than just before it.
        at = stage.index('ranlib "$MERGED"')
        call = stage[at : stage.index("---- static smoke ----", at)]
        assert "|| true" not in call
        assert "if sh " in call, "the status has to be the condition, not a pipeline's tail"
        assert 'rm -f "$MERGED" "$INIT_A"' in call, (
            "a failed rename has to drop the static archives; shared-only is a "
            "recorded outcome and an unlinkable .a is not"
        )

    def test_the_script_reaches_the_build_container(self):
        dockerfile = ba.make_dockerfile(ba.read_version(), "musl", "arm64")
        assert "privatise_unwind.sh" in dockerfile, (
            "stage.sh calls a script the image does not have"
        )

    def test_the_build_context_carries_it(self, tmp_path):
        # No platform argument since #72: the two per-platform lists
        # stage.sh used to have substituted into it arrive as environment
        # variables now, so the context is the same for every cell.
        ctx = ba._write_build_context(str(tmp_path))
        assert os.path.isfile(os.path.join(ctx, "privatise_unwind.sh"))

    def test_the_count_is_recorded(self):
        stage = ba.stage_script()
        assert "fact privatised_unwind_symbols" in stage, (
            "the build records what it renamed nowhere, so a log is the only evidence the step ran"
        )


class TestTheWorkflowsVerifyOnAMatchingHost:
    def test_the_release_workflow_uses_the_wrapper(self):
        yaml = pytest.importorskip("yaml")
        path = os.path.join(REPO_ROOT, ".github", "workflows", "release-acadsharp.yml")
        with open(path) as f:
            wf = yaml.safe_load(f)
        steps = wf["jobs"]["build-linux"]["steps"]
        verify = next(s for s in steps if s.get("name", "").startswith("Verify"))
        assert "verify_archive_matched_host.sh" in verify["run"], (
            "the musl cells verify on their glibc runner, which is the skip that "
            "let a never-linked recipe ship twice"
        )

    def test_the_conformance_workflow_builds_and_verifies_a_musl_archive(self):
        yaml = pytest.importorskip("yaml")
        path = os.path.join(REPO_ROOT, ".github", "workflows", "acadsharp-conformance.yml")
        with open(path) as f:
            wf = yaml.safe_load(f)
        bodies = [s.get("run", "") for job in wf["jobs"].values() for s in job["steps"]]
        assert any("--platform musl" in b for b in bodies), (
            "nothing on a pull request builds a musl archive, so the musl link is "
            "still only checked at release time"
        )
        assert any("verify_archive_matched_host.sh" in b for b in bodies)

    def test_the_musl_job_runs_on_its_own_architecture(self):
        yaml = pytest.importorskip("yaml")
        path = os.path.join(REPO_ROOT, ".github", "workflows", "acadsharp-conformance.yml")
        with open(path) as f:
            wf = yaml.safe_load(f)
        job = next(
            j
            for j in wf["jobs"].values()
            if any("--platform musl" in s.get("run", "") for s in j["steps"])
        )
        assert "arm" in job["runs-on"], (
            f"the musl job runs on {job['runs-on']} and builds arm64, which is the "
            "cross-architecture publish ADR 0001 measured failing at the link"
        )
