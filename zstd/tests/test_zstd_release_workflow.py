"""Tests for .github/workflows/release-zstd.yml.

zstd's archives have to be publishable from CI rather than from whoever
happens to own a Mac that week, and the ordering inside the workflow is
the whole point: build, then verify, then upload, so an archive that
fails ``zstd/scripts/verify_archive.sh`` can never reach the release
page. These read the workflow the way
``pdfium/tests/test_release_workflow.py`` reads ``release.yml``.

The names and the matrix are checked against ``build_zstd.py`` itself
rather than against string literals, so renaming an archive in the
driver fails here instead of silently publishing six assets nobody
links to.
"""

import os

import build_zstd as bz
import pytest

yaml = pytest.importorskip("yaml")


WORKFLOW_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    ".github",
    "workflows",
    "release-zstd.yml",
)

# Every archive the release is supposed to carry: the driver's default
# Docker matrix plus the two mac slices that need a macOS host.
ALL_CELLS = bz.DEFAULT_JOBS + [("mac", "arm64"), ("mac", "amd64")]


def load_workflow():
    with open(WORKFLOW_PATH) as f:
        return yaml.safe_load(f)


def triggers(wf):
    """The ``on:`` block.

    PyYAML resolves a bare ``on`` key to the boolean ``True`` (YAML 1.1
    treats it as a truthy literal), so the key has to be looked up both
    ways or every trigger test passes vacuously.
    """
    if "on" in wf:
        return wf["on"]
    return wf[True]


def step_index(job, name_substr):
    for i, step in enumerate(job.get("steps", [])):
        if name_substr.lower() in step.get("name", "").lower():
            return i
    return -1


def matrix_cells(job):
    return job["strategy"]["matrix"]["include"]


def run_text(job):
    return "\n".join(step.get("run", "") for step in job.get("steps", []))


class TestWorkflowShape:
    def setup_method(self):
        self.wf = load_workflow()

    def test_workflow_parses(self):
        assert self.wf["name"]

    def test_the_jobs_that_publish_exist(self):
        for job in ("resolve-version", "create-release", "build-linux", "build-mac"):
            assert job in self.wf["jobs"], f"release-zstd.yml has no {job} job"

    def test_every_publishing_job_can_write_releases(self):
        for name in ("create-release", "build-linux", "build-mac"):
            job = self.wf["jobs"][name]
            assert job.get("permissions", {}).get("contents") == "write", (
                f"{name} calls `gh release`, so it needs permissions.contents: write"
            )


class TestTriggers:
    def setup_method(self):
        self.on = triggers(load_workflow())

    def test_fires_on_a_push_to_release(self):
        assert "release" in self.on["push"]["branches"]

    def test_does_not_fire_on_main(self):
        branches = self.on["push"]["branches"]
        assert "main" not in branches, "publishing must not happen on a push to main"
        for pattern in branches:
            assert "*" not in pattern, (
                f"branch pattern {pattern!r} would catch main; name the release branch"
            )

    def test_can_be_dispatched_by_hand(self):
        # The first publish of an already-committed version changes no
        # file, so a push trigger alone would leave the owner with no
        # way to cut it.
        assert "workflow_dispatch" in self.on


class TestVersionPreflight:
    """A VERSION with no pinned source hash must stop the run before any
    build, and it must stop it red rather than quietly skipping."""

    def setup_method(self):
        self.wf = load_workflow()
        self.job = self.wf["jobs"]["resolve-version"]

    def test_version_comes_from_the_version_file(self):
        assert "zstd/VERSION" in run_text(self.job), (
            "the release version must be resolved from zstd/VERSION, not typed in"
        )

    def test_tag_matches_the_drivers_release_tag(self):
        prefix = bz.release_tag("")
        assert f"tag={prefix}" in run_text(self.job), (
            f"the workflow's tag must be {prefix}<version>, the same tag "
            "build_zstd.upload_release publishes to"
        )

    def test_an_unpinned_version_is_refused(self):
        assert "source_sha256" in run_text(self.job), (
            "resolve-version must ask build_zstd for the pinned sha256, so a "
            "VERSION bump without a SOURCE_SHA256 entry fails the run"
        )

    def test_the_refusal_runs_before_anything_builds(self):
        for name in ("build-linux", "build-mac"):
            needs = self.wf["jobs"][name]["needs"]
            assert "resolve-version" in needs, (
                f"{name} must wait on resolve-version, or an unpinned version "
                "still gets as far as a build"
            )

    def test_the_refusal_cannot_be_switched_off(self):
        # `test_an_unpinned_version_is_refused` only asserts that the step
        # *calls* source_sha256. Adding `continue-on-error: true` to the step,
        # or `|| true` after the heredoc, leaves that assertion green while the
        # refusal stops refusing, and the README and MANUAL both promise it
        # "fails red rather than being skipped". This org removed exactly that
        # skip arm from libviprs-org's sync gate three weeks ago, so it is a
        # documented way for a gate here to go quiet.
        for step in self.job["steps"]:
            if "source_sha256" not in (step.get("run") or ""):
                continue
            assert not step.get("continue-on-error"), (
                "the source-hash refusal carries continue-on-error, so an "
                "unpinned version would warn and the run would go green"
            )
            assert "|| true" not in step["run"], (
                "the source-hash refusal swallows its own exit status with "
                "`|| true`, so an unpinned version would not stop the run"
            )
            break
        else:
            raise AssertionError(
                "no step in resolve-version calls source_sha256, so there is "
                "no refusal to switch off and this guard is reading nothing"
            )

    def test_the_whole_job_cannot_be_switched_off(self):
        # Same hole one level up: `continue-on-error` on the job means every
        # dependent still runs even though the refusal failed.
        assert not self.job.get("continue-on-error"), (
            "resolve-version carries continue-on-error, so the build jobs "
            "that need it would run anyway after an unpinned version"
        )


class TestThirdPartyActionsArePinned:
    """The one action here that is not `actions/*` runs privileged, in a job
    that can write releases, so it is pinned to a commit rather than a tag."""

    def setup_method(self):
        self.wf = load_workflow()

    def test_every_non_github_action_is_pinned_to_a_sha(self):
        unpinned = []
        for job_name, job in self.wf["jobs"].items():
            for step in job.get("steps", []):
                uses = step.get("uses")
                if not uses or uses.startswith("actions/"):
                    continue
                ref = uses.split("@", 1)[1] if "@" in uses else ""
                if len(ref) != 40 or not all(c in "0123456789abcdef" for c in ref):
                    unpinned.append(f"{job_name}: {uses}")
        assert not unpinned, (
            "these third-party actions are not pinned to a commit sha, so a "
            "moved tag changes what runs:\n  " + "\n  ".join(unpinned)
        )


class TestBuildJobsVerifyBeforeTheyUpload:
    def setup_method(self):
        self.wf = load_workflow()

    @pytest.mark.parametrize("name", ["build-linux", "build-mac"])
    def test_build_then_verify_then_upload(self, name):
        job = self.wf["jobs"][name]
        build_i = step_index(job, "Build")
        verify_i = step_index(job, "Verify")
        upload_i = step_index(job, "Upload release")
        assert 0 <= build_i < verify_i < upload_i, (
            f"{name}: expected Build < Verify < Upload, got "
            f"{build_i}/{verify_i}/{upload_i} — a bad archive must be rejected "
            "before it reaches the release page"
        )

    @pytest.mark.parametrize("name", ["build-linux", "build-mac"])
    def test_the_build_step_does_not_upload(self, name):
        job = self.wf["jobs"][name]
        run = job["steps"][step_index(job, "Build")].get("run", "")
        assert "--upload" not in run, (
            f"{name}: --upload inside the Build step publishes before Verify runs, "
            "which makes the verification decorative"
        )

    @pytest.mark.parametrize("name", ["build-linux", "build-mac"])
    def test_verify_invokes_the_zstd_verifier(self, name):
        job = self.wf["jobs"][name]
        run = job["steps"][step_index(job, "Verify")].get("run", "")
        assert "zstd/scripts/verify_archive.sh" in run

    @pytest.mark.parametrize("name", ["build-linux", "build-mac"])
    def test_verify_names_the_archive_instead_of_globbing(self, name):
        # A glob plus nullglob verifies zero archives and exits 0, which
        # is a pass reported for a check that ran over nothing.
        job = self.wf["jobs"][name]
        run = job["steps"][step_index(job, "Verify")].get("run", "")
        assert "*" not in run, (
            f"{name}: verify must name the archive it checks, so a missing "
            "archive fails the job rather than verifying nothing"
        )


class TestMatrixCoversEveryArchive:
    def setup_method(self):
        self.wf = load_workflow()
        self.cells = matrix_cells(self.wf["jobs"]["build-linux"]) + matrix_cells(
            self.wf["jobs"]["build-mac"]
        )

    def test_every_cell_the_driver_builds_is_in_the_matrix(self):
        in_workflow = {(c["platform"], c["arch"]) for c in self.cells}
        assert in_workflow == set(ALL_CELLS), (
            f"workflow builds {sorted(in_workflow)}, driver ships {sorted(ALL_CELLS)}"
        )

    def test_the_docker_cells_are_the_drivers_default_matrix(self):
        linux = [(c["platform"], c["arch"]) for c in matrix_cells(self.wf["jobs"]["build-linux"])]
        assert sorted(linux) == sorted(bz.DEFAULT_JOBS)

    def test_each_cell_carries_the_cpu_the_archive_name_uses(self):
        # The matrix spells the CPU (`x64`) as well as the arch
        # (`amd64`) because the archive filename uses the former. If the
        # two ever disagree the workflow verifies and uploads a path
        # that does not exist.
        for cell in self.cells:
            expected = bz.archive_name(cell["platform"], cell["arch"])
            assert f"zstd-{cell['platform']}-{cell['cpu']}.tgz" == expected

    def test_mac_builds_on_a_mac(self):
        assert str(self.wf["jobs"]["build-mac"]["runs-on"]).startswith("macos"), (
            "there is no macOS container image, so the mac slices need a macOS runner"
        )

    def test_foreign_architecture_cells_get_an_emulator(self):
        # Each cell builds in a container of the *target* architecture,
        # so an arm64 cell on an x86_64 runner needs binfmt handlers
        # registered or `docker build --platform=linux/arm64` fails.
        job = self.wf["jobs"]["build-linux"]
        blob = run_text(job) + "".join(s.get("uses", "") for s in job["steps"])
        assert "binfmt" in blob or "qemu" in blob.lower(), (
            "build-linux runs arm64 cells under emulation but never registers QEMU"
        )


class TestUploadsGoToTheResolvedTag:
    def setup_method(self):
        self.wf = load_workflow()

    @pytest.mark.parametrize("name", ["build-linux", "build-mac"])
    def test_upload_uses_the_tag_resolve_version_computed(self, name):
        job = self.wf["jobs"][name]
        step = job["steps"][step_index(job, "Upload release")]
        blob = step.get("run", "") + str(step.get("env", ""))
        assert "resolve-version" in blob and "tag" in blob, (
            f"{name}: the upload must target the tag resolve-version produced, "
            "not a second copy of the version string"
        )

    def test_the_release_exists_before_the_fan_out_uploads(self):
        for name in ("build-linux", "build-mac"):
            assert "create-release" in self.wf["jobs"][name]["needs"], (
                f"{name} uploads, so it must wait for create-release — parallel "
                "`gh release create` calls race"
            )


LATEST_SCRIPT = "tools/publish_latest_tag.sh"


def _step_named(job, name_substr):
    i = step_index(job, name_substr)
    assert i >= 0, f"no step whose name contains {name_substr!r}"
    return job["steps"][i]


class TestTheLatestTagFollowsEveryFullyPublishedRelease:
    """A floating pointer for edge consumers, distinct from the pinned tag.

    Same job as release-acadsharp.yml's ``publish-latest``, calling the
    same shared script with zstd's own dependency name and asset glob.
    ``tools/tests/test_publish_latest_tag.py`` covers the script itself;
    this only has to prove zstd's copy of the job calls it right.
    """

    def setup_method(self):
        self.wf = load_workflow()
        assert "publish-latest" in self.wf["jobs"], "release-zstd.yml has no publish-latest job"
        self.job = self.wf["jobs"]["publish-latest"]

    def test_it_waits_on_both_build_matrices_and_the_summary(self):
        needs = self.job["needs"]
        for name in ("build-linux", "build-mac", "summary"):
            assert name in needs, f"publish-latest must wait on {name}"

    def test_it_only_moves_the_tag_when_every_target_published(self):
        cond = self.job.get("if", "")
        assert "needs.build-linux.result" in cond and "'success'" in cond, (
            "publish-latest has no if: gating on build-linux succeeding, so a "
            "partial release could still become latest"
        )
        assert "needs.build-mac.result" in cond

    def test_it_calls_the_shared_script_with_this_dependencys_name_and_glob(self):
        run = run_text(self.job)
        assert LATEST_SCRIPT in run, (
            f"publish-latest does not call {LATEST_SCRIPT}, so zstd has its own "
            "copy of the latest-moving logic rather than the shared one"
        )
        assert f"{LATEST_SCRIPT} zstd " in run
        assert "zstd-*.tgz" in run

    def test_it_passes_the_tag_resolve_version_computed(self):
        step = _step_named(self.job, "zstd-latest")
        assert step.get("env", {}).get("TAG") == "${{ needs.resolve-version.outputs.tag }}"

    def test_it_has_contents_write(self):
        assert self.job.get("permissions", {}).get("contents") == "write"
