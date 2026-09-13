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
        init = source.index("static:+whole-archive=")
        main = source.index("cargo:rustc-link-lib=static={}")
        assert init < main, "the initialiser archive has to come first"

    def test_it_refuses_a_non_empty_static_link_args(self):
        source = read(BUILD_RS)
        assert "link_args.is_empty()" in source
        # It may say the words, at length, in the panic explaining why.
        # What it must never do is emit one.
        assert 'println!("cargo:rustc-link-arg' not in source

    def test_manual_documents_the_same_recipe(self):
        # Two places to write it down is one place to get it wrong, so
        # the doc and the script are held to each other.
        manual = read(MANUAL)
        assert "static:+whole-archive=acadsharp_native_init" in manual
        assert "cargo:rustc-link-lib=static=acadsharp_native" in manual
        assert manual.index("static:+whole-archive=acadsharp_native_init") < manual.index(
            "cargo:rustc-link-lib=static=acadsharp_native"
        )


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
        source = read(path)
        source = source.replace(
            """    println!(
        "cargo:rustc-link-lib=static:+whole-archive={}",
        link_name(&init_lib)
    );""",
            """    println!("cargo:rustc-link-lib=static={}", link_name(&init_lib));
    println!("cargo:rustc-link-arg=-Wl,-u,_GLOBAL__sub_I_fixture");""",
        )
        assert "rustc-link-arg" in source, "the broken recipe was not substituted in"
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
