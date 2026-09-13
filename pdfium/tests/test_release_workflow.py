"""Tests for .github/workflows/release.yml.

The release workflow publishes every archive in the default build matrix
to a GitHub Release. Symbol verification (``pdfium/scripts/verify_archive.sh``)
must run AFTER each build and BEFORE the matching upload, so a bad archive
can never reach the Release page. These tests lock that ordering in.
"""

import os
import re

import pytest

yaml = pytest.importorskip("yaml")


WORKFLOW_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    ".github",
    "workflows",
    "release.yml",
)


def load_workflow():
    with open(WORKFLOW_PATH) as f:
        return yaml.safe_load(f)


def step_names(job):
    return [s.get("name", "") for s in job.get("steps", [])]


def step_index(job, name_substr):
    for i, s in enumerate(job.get("steps", [])):
        if name_substr.lower() in s.get("name", "").lower():
            return i
    return -1


class TestReleaseWorkflowShape:
    def setup_method(self):
        self.wf = load_workflow()

    def test_workflow_parses(self):
        assert self.wf["name"] == "Release"

    def test_build_linux_job_exists(self):
        assert "build-linux" in self.wf["jobs"]

    def test_build_mac_job_exists(self):
        assert "build-mac" in self.wf["jobs"]


class TestBuildLinuxVerifyStep:
    """build-linux must build, verify, then upload — in that order."""

    def setup_method(self):
        self.wf = load_workflow()
        self.job = self.wf["jobs"]["build-linux"]

    def test_verify_step_present(self):
        names = step_names(self.job)
        assert any("verify" in n.lower() for n in names), (
            f"build-linux is missing a verify step; steps were: {names}"
        )

    def test_verify_after_build(self):
        build_i = step_index(self.job, "Build")
        verify_i = step_index(self.job, "Verify")
        assert build_i >= 0, "no Build step found"
        assert verify_i >= 0, "no Verify step found"
        assert verify_i > build_i, (
            "Verify must run AFTER Build — otherwise there's nothing to verify"
        )

    def test_verify_before_upload(self):
        verify_i = step_index(self.job, "Verify")
        upload_i = step_index(self.job, "Upload release")
        assert verify_i >= 0, "no Verify step found"
        assert upload_i >= 0, (
            "no 'Upload release' step found — build_pdfium.py --upload was "
            "expected to be split into separate Build + Upload steps so "
            "Verify can gate the upload"
        )
        assert verify_i < upload_i, (
            "Verify must run BEFORE the release upload — otherwise a bad "
            "archive reaches the Release page before we reject it"
        )

    def test_build_step_does_not_upload(self):
        """--upload must not run as part of the Build step, or verification is pointless."""
        steps = self.job["steps"]
        build_i = step_index(self.job, "Build")
        assert build_i >= 0
        run = steps[build_i].get("run", "")
        assert "--upload" not in run, (
            "Build step must NOT pass --upload; upload must be a separate "
            "step that runs only after Verify succeeds"
        )

    def test_verify_invokes_verify_script(self):
        steps = self.job["steps"]
        verify_i = step_index(self.job, "Verify")
        run = steps[verify_i].get("run", "")
        assert "verify_archive.sh" in run, f"Verify step must invoke verify_archive.sh; got:\n{run}"


class TestBuildMacVerifyStep:
    """build-mac must build, verify, then upload — same invariants as linux."""

    def setup_method(self):
        self.wf = load_workflow()
        self.job = self.wf["jobs"]["build-mac"]

    def test_verify_step_present(self):
        names = step_names(self.job)
        assert any("verify" in n.lower() for n in names), (
            f"build-mac is missing a verify step; steps were: {names}"
        )

    def test_verify_after_build_before_upload(self):
        build_i = step_index(self.job, "Build")
        verify_i = step_index(self.job, "Verify")
        upload_i = step_index(self.job, "Upload release")
        assert 0 <= build_i < verify_i < upload_i, (
            f"expected Build < Verify < Upload, got indices {build_i}/{verify_i}/{upload_i}"
        )

    def test_verify_invokes_verify_script(self):
        steps = self.job["steps"]
        verify_i = step_index(self.job, "Verify")
        run = steps[verify_i].get("run", "")
        assert "verify_archive.sh" in run


class TestBuildMacUniversalVerify:
    """The lipo'd universal dylib is a distinct artifact — it also must be
    verified before upload, because the fusing step could theoretically
    introduce symbol mismatches (it won't in practice, but belt + braces
    catches any future regression in our lipo invocation)."""

    def setup_method(self):
        self.wf = load_workflow()
        self.job = self.wf["jobs"]["build-mac-universal"]

    def test_verify_step_present(self):
        names = step_names(self.job)
        assert any("verify" in n.lower() for n in names), (
            f"build-mac-universal is missing a verify step; steps were: {names}"
        )

    def test_verify_before_upload(self):
        verify_i = step_index(self.job, "Verify")
        upload_i = step_index(self.job, "Upload")
        assert verify_i >= 0 and upload_i >= 0
        assert verify_i < upload_i


class TestVerifyScriptShellReference:
    """The path passed to verify_archive.sh must match what build_pdfium.py
    actually writes to --output-dir. Guards against drift between
    archive_name() in build_pdfium.py and the workflow."""

    def setup_method(self):
        self.wf = load_workflow()

    def test_linux_verify_uses_bin_glob_or_explicit_tgz(self):
        job = self.wf["jobs"]["build-linux"]
        steps = job["steps"]
        verify_i = step_index(job, "Verify")
        run = steps[verify_i].get("run", "")
        # Either a glob matching build_pdfium.py's default output-dir, or
        # an explicit filename derived from the matrix vars.
        assert re.search(r"bin[/\\].*\.tgz", run) or "pdfium-" in run, (
            f"Verify step should reference bin/*.tgz or pdfium-<plat>-<arch>.tgz; got:\n{run}"
        )


LATEST_SCRIPT = "tools/publish_latest_tag.sh"


def _run_text(job):
    return "\n".join(step.get("run", "") for step in job.get("steps", []))


def _step_named(job, name_substr):
    i = step_index(job, name_substr)
    assert i >= 0, f"no step whose name contains {name_substr!r}"
    return job["steps"][i]


class TestTheLatestTagFollowsEveryFullyPublishedRelease:
    """A floating pointer for edge consumers, distinct from the pinned tag.

    Same job as release-acadsharp.yml's ``publish-latest``, calling the
    same shared script with pdfium's own dependency name and asset glob.
    ``tools/tests/test_publish_latest_tag.py`` covers the script itself;
    this only has to prove pdfium's copy of the job calls it right.
    """

    def setup_method(self):
        self.wf = load_workflow()
        assert "publish-latest" in self.wf["jobs"], "release.yml has no publish-latest job"
        self.job = self.wf["jobs"]["publish-latest"]

    def test_it_waits_on_every_build_job_and_the_summary(self):
        needs = self.job["needs"]
        for name in ("build-linux", "build-mac", "build-mac-universal", "summary"):
            assert name in needs, f"publish-latest must wait on {name}"

    def test_it_only_moves_the_tag_when_every_target_published(self):
        cond = self.job.get("if", "")
        for name in ("build-linux", "build-mac", "build-mac-universal"):
            assert f"needs.{name}.result" in cond, (
                f"publish-latest's if: does not gate on {name} succeeding, so a "
                "partial release could still become latest"
            )
        assert "'success'" in cond

    def test_it_calls_the_shared_script_with_this_dependencys_name_and_glob(self):
        run = _run_text(self.job)
        assert LATEST_SCRIPT in run, (
            f"publish-latest does not call {LATEST_SCRIPT}, so pdfium has its own "
            "copy of the latest-moving logic rather than the shared one"
        )
        assert f"{LATEST_SCRIPT} pdfium " in run
        assert "pdfium-*.tgz" in run

    def test_it_passes_the_tag_resolve_version_computed(self):
        step = _step_named(self.job, "pdfium-latest")
        assert step.get("env", {}).get("TAG") == "${{ needs.resolve-version.outputs.tag }}"

    def test_it_has_contents_write(self):
        assert self.job.get("permissions", {}).get("contents") == "write"
