"""The path MANUAL.md actually tells a consumer to take.

The C smoke proves the archives are linkable. It proves nothing about
whether the directives a `-sys` crate's build script emits reach the
binary that depends on it, and they do not all propagate: Cargo sends
`rustc-link-search` and `rustc-link-lib` to a dependent's link line and
keeps `rustc-link-arg` for the emitting package's own targets. So a
requirement written as a link argument passes the `-sys` crate's tests,
stays green in its CI, and is missing from every consumer.

That is not a hypothetical. This archive's static half needs the
runtime's initialiser forced, the obvious way to force it is
`-Wl,-u,<symbol>`, and a manifest froze with exactly that in it. These
tests run the real recipe against a real archive through a real two-crate
workspace, and one of them puts the broken recipe back to show it fails.
"""

import json
import os
import re
import shutil
import subprocess

import pytest

# The fixture archives are built by the verifier suite, which compiles a
# stand-in library laid out exactly like a release. Importing them keeps
# one builder rather than two that can drift.
import test_verify_archive as fixtures

ACAD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REPO_ROOT = os.path.dirname(ACAD_DIR)
WORKSPACE = os.path.join(ACAD_DIR, "tests", "link_consumer")
BUILD_RS = os.path.join(WORKSPACE, "acadsharp-sys", "build.rs")
SMOKE = os.path.join(ACAD_DIR, "scripts", "link_consumer_smoke.sh")
MANUAL = os.path.join(REPO_ROOT, "MANUAL.md")


def _require_cargo():
    if not shutil.which("cargo"):
        pytest.skip("cargo is not installed on this host")


def read(path):
    with open(path) as f:
        return f.read()


class TestTheWorkspaceIsTheShapeThatCatchesThis:
    def test_it_is_two_crates_and_the_binary_is_the_dependent(self):
        # A check inside acadsharp-sys would get every directive its own
        # build script emits, including the ones that do not propagate,
        # and would stay green while every consumer broke.
        workspace = read(os.path.join(WORKSPACE, "Cargo.toml"))
        assert '"acadsharp-sys"' in workspace and '"consumer"' in workspace
        consumer = read(os.path.join(WORKSPACE, "consumer", "Cargo.toml"))
        assert 'acadsharp-sys = { path = "../acadsharp-sys" }' in consumer

    def test_the_sys_crate_declares_links(self):
        # Without `links`, cargo will not run the build script's metadata
        # through to dependents at all.
        assert 'links = "acadsharp_native"' in read(
            os.path.join(WORKSPACE, "acadsharp-sys", "Cargo.toml")
        )

    def test_neither_crate_has_dependencies(self):
        # The check runs offline inside a build container, so the
        # manifest is read by hand rather than with serde_json.
        for crate in ("acadsharp-sys", "consumer"):
            text = read(os.path.join(WORKSPACE, crate, "Cargo.toml"))
            body = text.split("[dependencies]", 1)[1] if "[dependencies]" in text else ""
            assert "serde" not in body and "version =" not in body


class TestTheBuildScriptImplementsTheDocumentedRecipe:
    def test_it_whole_archives_the_initialiser_before_the_main_archive(self):
        source = read(BUILD_RS)
        init = source.index("static:-bundle,+whole-archive=")
        main = source.index("cargo:rustc-link-lib=static:-bundle={}")
        assert init < main, "the initialiser archive has to come first"

    def test_both_libraries_are_unbundled(self):
        # With the default +bundle, rustc packs a static native library
        # into the rlib, the rlib precedes the whole-archived init
        # archive on the link line, and the link fails on
        # RhRegisterOSModule. Measured: it is the difference between the
        # recipe working and not.
        source = read(BUILD_RS)
        assert source.count("static:-bundle") == 2

    def test_it_refuses_a_non_empty_static_link_args(self):
        source = read(BUILD_RS)
        assert "link_args.is_empty()" in source
        # It may say the words, at length, in the panic explaining why.
        # What it must never do is emit one.
        assert 'println!("cargo:rustc-link-arg' not in source

    def _manual_build_script(self):
        """The worked `build.rs` out of MANUAL.md, code only.

        Scoped to the fenced block on purpose. The prose around it names
        `cargo:rustc-link-arg` repeatedly, at length, explaining why the
        script must never emit one, so a whole-file search fires on the
        explanation exactly as readily as on a violation.
        """
        blocks = [
            block
            for block in re.findall(r"```rust\n(.*?)```", read(MANUAL), re.S)
            if "cargo:rustc-link" in block
        ]
        assert len(blocks) == 1, (
            f"MANUAL.md has {len(blocks)} rust blocks emitting link directives; the "
            "checks below assume the acadsharp build script is the only one"
        )
        return blocks[0]

    def test_the_manual_works_the_recipe_through_a_build_script(self):
        # Four directives in a list are not a worked example. The thing a
        # consumer author copies is a build script, and the parts that go
        # wrong (the schema check, the certified gate, the radix parse)
        # only exist in one.
        source = self._manual_build_script()
        init = "cargo:rustc-link-lib=static:-bundle,+whole-archive={}"
        main = 'cargo:rustc-link-lib=static:-bundle={}", stem('
        assert init in source and main in source
        assert source.index(init) < source.index(main)

    def test_the_manual_build_script_emits_no_link_argument(self):
        source = self._manual_build_script()
        assert 'println!("cargo:rustc-link-arg' not in source, (
            "the worked example is what gets copied, and a link argument in it "
            "reaches the -sys crate's own targets and no dependent's"
        )

    def test_the_manual_build_script_gates_on_the_measured_facts(self):
        source = self._manual_build_script()
        assert "static_certified" in source, (
            "an archive that certified nothing ships no .a at all, so the static "
            "path has to be refused rather than attempted"
        )
        assert "static_link_args" in source and "is_empty()" in source
        assert "schema_version" in source, (
            "a manifest from a later schema is refused, which is the forward rule "
            "docs/LINKINFO.md states"
        )
        assert "from_str_radix" in source, (
            "abi_fingerprint is 16 hex digits meaning a 64-bit number, and a string "
            "comparison against a formatted runtime value disagrees over a leading "
            "zero and over case"
        )

    def test_manual_documents_the_same_recipe(self):
        # Two places to write it down is one place to get it wrong, so
        # the doc and the script are held to each other.
        manual = read(MANUAL)
        init = "static:-bundle,+whole-archive=acadsharp_native_init"
        main = "cargo:rustc-link-lib=static:-bundle=acadsharp_native"
        assert init in manual
        assert main in manual
        assert manual.index(init) < manual.index(main)


@pytest.fixture(scope="session")
def archive(tmp_path_factory):
    fixtures._require_toolchain()
    return fixtures._build_linux_tree(str(tmp_path_factory.mktemp("consumer-good")))


@pytest.fixture(scope="session")
def inert_archive(tmp_path_factory):
    fixtures._require_toolchain()
    return fixtures._build_linux_tree(
        str(tmp_path_factory.mktemp("consumer-inert")), init_effective=False
    )


class TestTheRecipeReallyLinksAndRuns:
    def test_the_smoke_script_links_and_runs_the_binary(self, archive):
        _require_cargo()
        result = subprocess.run(
            ["bash", SMOKE, archive], capture_output=True, text=True, check=False
        )
        output = result.stdout + result.stderr
        assert result.returncode == 0, output
        assert "the documented recipe links and runs" in output

    def test_the_fingerprint_comes_back_through_the_link(self, archive):
        _require_cargo()
        result = subprocess.run(
            ["bash", SMOKE, archive], capture_output=True, text=True, check=False
        )
        with open(os.path.join(archive, "metadata", "LINKINFO.json")) as f:
            expected = json.load(f)["abi_fingerprint"]
        assert f"ABI_FINGERPRINT={expected}" in result.stdout

    def test_an_inert_initialiser_makes_the_binary_abort(self, inert_archive):
        # The archive links perfectly. Only running it can tell.
        _require_cargo()
        result = subprocess.run(
            ["bash", SMOKE, inert_archive], capture_output=True, text=True, check=False
        )
        output = result.stdout + result.stderr
        assert result.returncode == 1, output
        assert "exited 134" in output

    def test_an_uncertified_archive_is_refused_rather_than_half_linked(
        self, tmp_path, inert_archive
    ):
        _require_cargo()
        root = fixtures._clone(inert_archive, tmp_path)
        for name in ("libacadsharp_native.a", "libacadsharp_native_init.a"):
            os.remove(os.path.join(root, "lib", name))

        def drop(doc):
            for field in fixtures.ba.STATIC_LINKINFO_FIELDS:
                doc.pop(field, None)
            doc["static_certified"] = False

        fixtures._edit_json(root, "LINKINFO.json", drop)
        result = subprocess.run(["bash", SMOKE, root], capture_output=True, text=True, check=False)
        assert result.returncode != 0
        assert "static_certified is false" in (result.stdout + result.stderr)


class TestTheBrokenRecipeStillFails:
    """Put the link-argument route back and watch it not arrive.

    This is the regression the whole change exists for. The `-Wl,-u,...`
    reaches `acadsharp-sys`'s own targets and never reaches `consumer`,
    so the initialiser archive is linked without `--whole-archive`,
    nothing references it, the linker leaves it out, the link succeeds
    and the binary aborts. Every step of that is invisible to a check
    that only compiles.
    """

    def test_forcing_the_symbol_from_a_dependency_build_script_does_not_arrive(self, tmp_path):
        _require_cargo()
        fixtures._require_toolchain()
        archive = fixtures._build_linux_tree(str(tmp_path / "archive"))

        broken = str(tmp_path / "ws")
        shutil.copytree(WORKSPACE, broken)
        path = os.path.join(broken, "acadsharp-sys", "build.rs")
        original = read(path)

        # Drop the whole-archive modifier and force the initialiser with a
        # link argument instead, which is the recipe that was frozen into
        # a manifest and the one that reads as obviously correct.
        source = original.replace(
            '"cargo:rustc-link-lib=static:-bundle,+whole-archive={}",',
            '"cargo:rustc-link-lib=static:-bundle={}",',
        )
        assert source != original, "the whole-archive modifier moved; fix this substitution"

        anchor = '    for lib in json_string_array(&text, "static_system_libraries") {'
        assert anchor in source, "the system-library loop moved; fix this substitution"
        source = source.replace(
            anchor,
            '    println!("cargo:rustc-link-arg=-Wl,-u,_GLOBAL__sub_I_fixture");\n' + anchor,
        )
        # The guard is the substitution having happened, not the words
        # being present: build.rs says `cargo:rustc-link-arg` in the
        # comment explaining why it never emits one, so a containment
        # check passes over a replace that matched nothing and the test
        # then measures the working recipe. It did, once.
        assert 'println!("cargo:rustc-link-arg' in source
        with open(path, "w") as f:
            f.write(source)

        result = subprocess.run(
            [
                "cargo",
                "run",
                "--offline",
                "--quiet",
                "--manifest-path",
                os.path.join(broken, "Cargo.toml"),
                "-p",
                "consumer",
            ],
            capture_output=True,
            text=True,
            check=False,
            env=dict(
                os.environ,
                ACADSHARP_ARCHIVE=archive,
                CARGO_TARGET_DIR=str(tmp_path / "target"),
            ),
        )
        assert result.returncode != 0, (
            "the link argument reached the dependent binary, which would mean "
            "cargo's propagation rules changed and static_init_library could go"
        )
        # 134 through a shell, -6 straight from subprocess: either way
        # the binary took SIGABRT, which is the runtime never coming up.
        assert result.returncode in (134, -6, 101), result.stdout + result.stderr
