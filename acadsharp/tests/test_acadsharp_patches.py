"""The patches this build applies to the pinned upstream tree.

`acadsharp/patches/` held a `.gitkeep` and nothing else, and
`build_acadsharp.py` had no step that would have applied anything in it. The
directory was advertising a capability the driver did not have, which matters
here more than it looks: the reader allocates from sizes a DWG declares, the
allocating types are `internal` with no injection point, and the only
process-wide lever is a GC hard limit that kills the process. A patch to
upstream is the only place a bound can go, so the mechanism is the fix.

These checks hold the driver to pdfium's shape, which is the one in this
repository that already works: the script is copied into the Docker build
context, it runs inside the container against the unpacked source, and it runs
at the point in the build where it can still affect what is compiled. They also
hold the patch itself to a rule pdfium's does not follow: an anchor it cannot
find is a refusal, not a warning. A patch that prints "already patched?" and
returns 0 produces an archive nobody can tell from a patched one.
"""

import hashlib
import importlib.util
import os
import subprocess
import sys

import build_acadsharp as ba
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ACADSHARP = os.path.dirname(HERE)
PATCHES_DIR = os.path.join(ACADSHARP, "patches")


def _marker(name):
    """The string a patch writes into every file it edits.

    Read off the script, because the whole point of a marker is that the
    script and whatever checks it agree on one spelling.
    """
    spec = importlib.util.spec_from_file_location(
        "viprs_patch_probe_" + name[:-3], os.path.join(PATCHES_DIR, name)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    marker = getattr(module, "MARKER", None)
    assert marker, f"{name} declares no MARKER, so a second run cannot tell it has run"
    return marker


@pytest.fixture(scope="module")
def dockerfile():
    return ba.make_dockerfile("3.7.1-viprs.1", "linux", "arm64")


class TestThereIsAPatchToApply:
    def test_the_driver_knows_where_the_patches_live(self):
        assert os.path.isdir(ba.PATCHES_DIR)
        assert os.path.abspath(ba.PATCHES_DIR) == PATCHES_DIR

    def test_the_directory_holds_at_least_one(self):
        assert ba.patch_scripts(), (
            "patches/ holds no patch script. Every check below compares the "
            "Dockerfile against this list, so an empty one would make all of them "
            "pass over nothing, which is exactly the state the directory was in "
            "while it held only a .gitkeep."
        )

    def test_every_named_script_is_in_the_tree(self):
        for name in ba.patch_scripts():
            assert os.path.isfile(os.path.join(PATCHES_DIR, name))


class TestTheDockerfileAppliesThem:
    def test_the_patch_is_copied_into_the_build_context(self, dockerfile):
        for name in ba.patch_scripts():
            assert f"COPY patches/{name}" in dockerfile

    def test_the_patch_runs_against_the_unpacked_source(self, dockerfile):
        for name in ba.patch_scripts():
            assert f"RUN python3 /tmp/patches/{name} /build/ACadSharp-3.7.1" in dockerfile

    def test_the_patch_runs_after_the_submodule_clone(self, dockerfile):
        # Before it, the tree the patch edits is a tarball with an empty
        # src/CSUtilities, and the clone step removes and recreates that
        # directory. Nothing the patch writes is under it today, and a patch
        # that reached into CSUtilities would be silently undone.
        clone = dockerfile.index(ba.CSUTILITIES_URL)
        for name in ba.patch_scripts():
            assert clone < dockerfile.index(f"RUN python3 /tmp/patches/{name}")

    def test_the_patch_runs_before_the_publish(self, dockerfile):
        publish = dockerfile.index("dotnet publish")
        for name in ba.patch_scripts():
            assert dockerfile.index(f"RUN python3 /tmp/patches/{name}") < publish

    def test_the_build_context_carries_them(self, tmp_path):
        ba._write_build_context(str(tmp_path))
        for name in ba.patch_scripts():
            staged = tmp_path / "patches" / name
            assert staged.is_file(), f"{name} is not in the build context the COPY reads"
            with open(os.path.join(PATCHES_DIR, name), "rb") as f:
                assert staged.read_bytes() == f.read()


class TestTheArchiveSaysWhatWasApplied:
    """`SOURCE_COMMIT` and `SOURCE_SHA256` pin what was downloaded. Without
    this, nothing in a published archive says the source was then changed."""

    def test_buildinfo_carries_the_patch_list(self):
        assert "source_patches" in ba.BUILDINFO_FIELDS

    def test_it_names_every_patch_with_its_digest(self):
        info = ba.make_buildinfo(
            driver_commit="0" * 40,
            builder_image="debian:bookworm-slim",
            dotnet_version="10.0.401",
            clang_version="clang version 14.0.6",
            linker_version="GNU ld 2.40",
            aot_warning_count=16,
        )
        recorded = info["source_patches"]
        assert [p["name"] for p in recorded] == list(ba.patch_scripts())
        for entry in recorded:
            with open(os.path.join(PATCHES_DIR, entry["name"]), "rb") as f:
                assert entry["sha256"] == hashlib.sha256(f.read()).hexdigest()

    def test_the_verifier_reads_the_same_field(self):
        # verify_archive.sh keeps its own copy of the frozen field list, so a
        # field added here and not there is a field no published archive is
        # ever checked for.
        with open(os.path.join(ACADSHARP, "scripts", "verify_archive.sh")) as f:
            assert '"source_patches"' in f.read()


class TestAPatchThatDoesNotApplyIsARefusal:
    """pdfium's scripts print a warning and exit 0 when an anchor has moved.
    That is the failure mode this whole file exists to stop: the build goes
    green, the archive ships, and the bound is not in it."""

    def _run(self, tree, name):
        return subprocess.run(
            [sys.executable, os.path.join(PATCHES_DIR, name), str(tree)],
            capture_output=True,
            text=True,
        )

    def test_an_empty_tree_is_refused(self, tmp_path):
        for name in ba.patch_scripts():
            result = self._run(tmp_path, name)
            assert result.returncode != 0, (
                f"{name} reported success against a directory holding no ACadSharp "
                "source at all. A patch that cannot fail cannot be trusted to have run."
            )

    def test_a_tree_with_the_right_files_and_the_wrong_contents_is_refused(self, tmp_path):
        # Every file the patch edits, present and empty. This is the shape an
        # upstream bump produces: the paths still resolve and the anchors are
        # gone.
        for name in ba.patch_scripts():
            tree = tmp_path / name
            for rel in ba.patch_targets(name):
                path = tree / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("")
            result = self._run(tree, name)
            assert result.returncode != 0, (
                f"{name} reported success against files that do not contain a single "
                "anchor it edits"
            )

    def test_a_tree_that_already_carries_the_marker_is_left_alone(self, tmp_path):
        # The fixture generator applies these to its own checkout so a capture
        # is a run of the code the archive ships, and that checkout is reused
        # across runs. A patch that stacked would double every guard it adds.
        for name in ba.patch_scripts():
            tree = tmp_path / f"applied-{name}"
            marker = _marker(name)
            for rel in ba.patch_targets(name):
                path = tree / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"// {marker}\n")
            result = self._run(tree, name)
            assert result.returncode == 0, (
                f"{name} refused a tree it had already patched:\n{result.stderr}"
            )
            for rel in ba.patch_targets(name):
                assert (tree / rel).read_text() == f"// {marker}\n", (
                    f"{name} edited a file that already carried its marker"
                )

    def test_a_half_applied_tree_is_refused(self, tmp_path):
        # One file patched and the rest not is a tree somebody unpacked over,
        # and applying the rest on top of it produces a build whose guards are
        # in some files and not others.
        for name in ba.patch_scripts():
            targets = ba.patch_targets(name)
            if len(targets) < 2:
                continue
            tree = tmp_path / f"half-{name}"
            for index, rel in enumerate(targets):
                path = tree / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"// {_marker(name)}\n" if index == 0 else "")
            assert self._run(tree, name).returncode != 0, (
                f"{name} accepted a tree it had only half patched"
            )
