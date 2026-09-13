"""Tests for .github/workflows/release-acadsharp.yml.

The ACadSharp archives have to be publishable from CI rather than from
whoever owns a Mac that week, and the ordering inside the workflow is
the whole point: build, then verify, then upload, so an archive that
fails ``acadsharp/scripts/verify_archive.sh`` never reaches the release
page. These read the workflow the way
``zstd/tests/test_zstd_release_workflow.py`` reads ``release-zstd.yml``
and ``pdfium/tests/test_release_workflow.py`` reads ``release.yml``.

Two things differ from the zstd shape on purpose, and both are pinned
below.

ADR 0001 measured cross-compiling failing at the native link and
recorded that .NET does not support QEMU, so every cell here runs on a
runner of its own architecture. ``release-zstd.yml`` registers
``setup-qemu-action`` and emulates; inheriting that here would be a
silent regression against a measured decision, so
``test_no_cell_is_emulated`` refuses it.

The release notes are generated from the manifests rather than typed,
so ``TestReleaseNotesAreGenerated`` asserts the workflow reads each
manifest by name and carries no copy of the version, the SDK pin or the
DWG range as a literal.

``build_acadsharp.py`` does not carry ``archive_name()``,
``release_tag()`` or a default matrix yet: issue #48 is writing them in
parallel with this workflow. Everything here asserts against the
contract that issue freezes, and ``TestTheDriverSpeaksTheSameContract``
re-asserts the same values *through the driver* as soon as those
functions exist, so the two lanes cannot drift apart quietly.
"""

import json
import os
import re

import build_acadsharp as ba
import pytest

yaml = pytest.importorskip("yaml")


ACADSHARP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REPO_ROOT = os.path.dirname(ACADSHARP_DIR)
WORKFLOW_PATH = os.path.join(REPO_ROOT, ".github", "workflows", "release-acadsharp.yml")
PDFIUM_RELEASE_WORKFLOW = os.path.join(REPO_ROOT, ".github", "workflows", "release.yml")
README_PATH = os.path.join(ACADSHARP_DIR, "README.md")
GLOBAL_JSON = os.path.join(ACADSHARP_DIR, "native", "global.json")
ABI_HEADER = os.path.join(ACADSHARP_DIR, "include", "viprs_acadsharp.h")

# What acadsharp/README.md says while no release has been cut. The
# download section carries either this or real URLs, never both and
# never neither. #43 is the standing example: zstd/README.md advertised
# six URLs for a release that had never been cut and nothing checked.
UNPUBLISHED_MARKER = "No archives are published yet"

# ---------------------------------------------------------------------------
# The contract issue #48 freezes.
#
# Archive names are <dep>-<platform>-<cpu>.tgz with platform in
# linux | musl | mac and cpu in x64 | arm64, which is what
# pdfium/tests/test_naming.py and zstd/tests/test_zstd_naming.py already
# pin for the other two dependencies and what the verifier infers from a
# filename. The version lives in the release tag, not the archive name.
# ---------------------------------------------------------------------------
DEFAULT_CELLS = [("linux", "x64"), ("linux", "arm64"), ("musl", "x64"), ("musl", "arm64")]
MAC_CELLS = [("mac", "arm64")]
ALL_CELLS = DEFAULT_CELLS + MAC_CELLS

TAG_PREFIX = "acadsharp-"

# The jobs that call `gh release`, so the ones that need contents: write.
PUBLISHING_JOBS = ("create-release", "build-linux", "build-mac", "release-notes")
BUILD_JOBS = ("build-linux", "build-mac")

VERIFIER = "acadsharp/scripts/verify_archive.sh"
DRIVER = "acadsharp/build_acadsharp.py"


def expected_archive(platform, cpu):
    return f"{TAG_PREFIX}{platform}-{cpu}.tgz"


def driver_attr(name):
    """The driver's copy of a contract value, or None while #48 is unlanded."""
    return getattr(ba, name, None)


def load_workflow():
    with open(WORKFLOW_PATH) as f:
        return yaml.safe_load(f)


def workflow_text():
    with open(WORKFLOW_PATH) as f:
        return f.read()


def read_readme():
    with open(README_PATH) as f:
        return f.read()


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


def step_named(job, name_substr):
    i = step_index(job, name_substr)
    assert i >= 0, f"no step whose name contains {name_substr!r}"
    return job["steps"][i]


def matrix_cells(job):
    return job["strategy"]["matrix"]["include"]


def run_text(job):
    return "\n".join(step.get("run", "") for step in job.get("steps", []))


def job_text(job):
    """Everything a job says: its run scripts, its `uses`, its `with` and `env`."""
    return yaml.safe_dump(job)


class TestWorkflowShape:
    def setup_method(self):
        self.wf = load_workflow()

    def test_workflow_parses(self):
        assert self.wf["name"]

    def test_the_jobs_that_publish_exist(self):
        for job in ("resolve-version", *PUBLISHING_JOBS):
            assert job in self.wf["jobs"], f"release-acadsharp.yml has no {job} job"

    def test_every_publishing_job_can_write_releases(self):
        for name in PUBLISHING_JOBS:
            job = self.wf["jobs"][name]
            assert job.get("permissions", {}).get("contents") == "write", (
                f"{name} calls `gh release`, so it needs permissions.contents: write"
            )

    def test_the_preflight_job_cannot_write_releases(self):
        # resolve-version exists to refuse things. It never publishes, so
        # handing it a release-writing token is scope it does not need.
        perms = self.wf["jobs"]["resolve-version"].get("permissions", {})
        assert perms.get("contents") != "write", (
            "resolve-version does not publish anything, so it should not be able to"
        )

    def test_runs_are_serialised_per_ref_without_cancelling(self):
        # Two overlapping runs would race on the same tag's assets, and a
        # release halfway through five uploads is worth finishing.
        conc = self.wf["concurrency"]
        assert "github.ref" in conc["group"], "serialize per ref, not globally"
        assert conc["cancel-in-progress"] is False, (
            "cancel-in-progress would discard a run that is midway through its uploads"
        )

    def test_this_is_a_workflow_of_its_own_not_an_extension_of_release_yml(self):
        # release.yml is pdfium-shaped end to end (chromium branch input,
        # a lipo'd universal job, its own summary table). An acadsharp
        # flake must not colour a pdfium release run red, which is the
        # same reason release-zstd.yml exists.
        with open(PDFIUM_RELEASE_WORKFLOW) as f:
            pdfium_release = f.read()
        assert "acadsharp" not in pdfium_release.lower(), (
            "release.yml has grown an acadsharp step. acadsharp publishes through "
            "release-acadsharp.yml so one dependency's flake cannot redden another's run"
        )

    def test_nothing_names_a_windows_target_or_toolchain(self):
        # The org ships no Microsoft-platform artifact for any dependency,
        # and neither this workflow nor its runners may quietly introduce one.
        blob = workflow_text().lower()
        for word in ("windows", "win-x64", "win-arm64", "msvc", ".dll"):
            assert word not in blob, (
                f"release-acadsharp.yml names {word!r}; no Windows target is shipped here"
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

    def test_it_publishes_from_exactly_one_branch(self):
        assert self.on["push"]["branches"] == ["release"], (
            "release is the only branch a publish comes from; anything else is a "
            "second door onto the release page"
        )

    def test_can_be_dispatched_by_hand(self):
        # The first publish of an already-committed version changes no
        # file, so a push trigger alone would leave the owner with no way
        # to cut it.
        assert "workflow_dispatch" in self.on

    def test_the_dispatch_version_input_is_optional(self):
        inputs = self.on["workflow_dispatch"]["inputs"]
        assert "version" in inputs, (
            "dispatch needs a version input so a throwaway pre-release can be cut "
            "without committing a VERSION bump"
        )
        assert not inputs["version"].get("required"), (
            "the version input overrides acadsharp/VERSION; making it required means "
            "the ordinary case has to retype what the file already says"
        )

    def test_the_dispatch_input_reaches_the_script_through_env(self):
        # A `${{ }}` spliced straight into bash is the shape that turns a
        # workflow input into shell. release-zstd.yml was fixed for exactly
        # this; the fix does not carry over by itself.
        wf = load_workflow()
        for job in wf["jobs"].values():
            for step in job.get("steps", []):
                assert "inputs.version" not in step.get("run", ""), (
                    "the dispatch version input is interpolated into a run script; "
                    "pass it through env: instead"
                )


class TestVersionPreflight:
    """A VERSION with no pinned source hash, a malformed one, or an SDK
    pin the runner cannot install must stop the run before any build, and
    must stop it red rather than quietly skipping."""

    def setup_method(self):
        self.wf = load_workflow()
        self.job = self.wf["jobs"]["resolve-version"]

    def test_version_comes_from_the_version_file(self):
        assert "acadsharp/VERSION" in run_text(self.job), (
            "the release version must be resolved from acadsharp/VERSION, not typed in"
        )

    def test_tag_matches_the_drivers_release_tag(self):
        assert f"tag={TAG_PREFIX}" in run_text(self.job), (
            f"the workflow's tag must be {TAG_PREFIX}<version>, the tag the "
            "acadsharp-rs crate's release lane resolves against"
        )

    def test_an_unpinned_version_is_refused(self):
        assert "source_sha256" in run_text(self.job), (
            "resolve-version must ask build_acadsharp for the pinned sha256, so a "
            "VERSION bump without a SOURCE_SHA256 entry fails the run"
        )

    def test_a_malformed_version_is_refused(self):
        assert "split_version" in run_text(self.job), (
            "resolve-version must ask build_acadsharp to split the version, so an "
            "empty or malformed acadsharp/VERSION fails here and not five jobs later"
        )

    def test_the_sdk_pin_is_proved_installable_here(self):
        # actions/setup-dotnet reading global.json is the only cheap way to
        # find out that the pinned SDK is gone before five build jobs each
        # discover it for themselves.
        blob = job_text(self.job)
        assert "setup-dotnet" in blob, (
            "resolve-version must install the SDK global.json pins, or an "
            "uninstallable pin fails in every build job instead of once here"
        )
        assert "global-json-file" in blob, (
            "setup-dotnet must read acadsharp/native/global.json rather than a "
            "second copy of the version string"
        )
        assert "acadsharp/native/global.json" in blob

    def test_a_global_json_with_no_sdk_version_is_refused(self):
        assert "global.json" in run_text(self.job), (
            "resolve-version must read global.json itself: setup-dotnet with a "
            "global-json-file that has no sdk.version line is not a loud failure"
        )

    def test_the_refusal_runs_before_anything_builds(self):
        for name in BUILD_JOBS:
            needs = self.wf["jobs"][name]["needs"]
            assert "resolve-version" in needs, (
                f"{name} must wait on resolve-version, or an unpinned version still "
                "gets as far as a build"
            )

    def test_no_build_job_carries_the_refusal_itself(self):
        # Moving the refusal down into the build jobs is the mutation this
        # names directly: it would still refuse, but only after five
        # runners have already been claimed and the release already exists.
        for name in BUILD_JOBS:
            blob = run_text(self.wf["jobs"][name])
            assert "source_sha256" not in blob, (
                f"{name} carries the unpinned-version refusal. It belongs in "
                "resolve-version, before anything builds"
            )

    def test_the_refusal_cannot_be_switched_off(self):
        # `test_an_unpinned_version_is_refused` only asserts that the step
        # *calls* source_sha256. Adding `continue-on-error: true` to the
        # step, or `|| true` after the heredoc, leaves that assertion green
        # while the refusal stops refusing.
        for step in self.job["steps"]:
            if "source_sha256" not in (step.get("run") or ""):
                continue
            assert not step.get("continue-on-error"), (
                "the source-hash refusal carries continue-on-error, so an unpinned "
                "version would warn and the run would go green"
            )
            assert "|| true" not in step["run"], (
                "the source-hash refusal swallows its own exit status with `|| true`, "
                "so an unpinned version would not stop the run"
            )
            break
        else:
            raise AssertionError(
                "no step in resolve-version calls source_sha256, so there is no "
                "refusal to switch off and this guard is reading nothing"
            )

    def test_the_whole_job_cannot_be_switched_off(self):
        # Same hole one level up: `continue-on-error` on the job means
        # every dependent still runs even though the refusal failed.
        assert not self.job.get("continue-on-error"), (
            "resolve-version carries continue-on-error, so the build jobs that need "
            "it would run anyway after an unpinned version"
        )

    def test_the_release_is_not_created_before_the_preflight(self):
        assert "resolve-version" in self.wf["jobs"]["create-release"]["needs"], (
            "create-release must wait on resolve-version, or a version the driver "
            "refuses still gets a tag and a release page"
        )


class TestBuildJobsVerifyBeforeTheyUpload:
    def setup_method(self):
        self.wf = load_workflow()

    @pytest.mark.parametrize("name", BUILD_JOBS)
    def test_build_then_verify_then_upload(self, name):
        job = self.wf["jobs"][name]
        build_i = step_index(job, "Build")
        verify_i = step_index(job, "Verify")
        upload_i = step_index(job, "Upload release")
        assert 0 <= build_i < verify_i < upload_i, (
            f"{name}: expected Build < Verify < Upload, got "
            f"{build_i}/{verify_i}/{upload_i}. A bad archive must be rejected before "
            "it reaches the release page"
        )

    @pytest.mark.parametrize("name", BUILD_JOBS)
    def test_the_build_step_does_not_upload(self, name):
        job = self.wf["jobs"][name]
        run = job["steps"][step_index(job, "Build")].get("run", "")
        assert "--upload" not in run, (
            f"{name}: --upload inside the Build step publishes before Verify runs, "
            "which makes the verification decorative"
        )

    @pytest.mark.parametrize("name", BUILD_JOBS)
    def test_the_build_step_calls_the_driver(self, name):
        job = self.wf["jobs"][name]
        run = job["steps"][step_index(job, "Build")].get("run", "")
        assert DRIVER in run, (
            f"{name}: the build must go through {DRIVER}, which is where the pins, "
            "the container image and the packaging live"
        )

    @pytest.mark.parametrize("name", BUILD_JOBS)
    def test_verify_invokes_the_acadsharp_verifier(self, name):
        job = self.wf["jobs"][name]
        run = job["steps"][step_index(job, "Verify")].get("run", "")
        assert VERIFIER in run

    @pytest.mark.parametrize("name", BUILD_JOBS)
    def test_verify_tells_the_verifier_what_the_archive_should_be(self, name):
        # `verify_archive.sh <tgz> [platform] [cpu]`. Passing the pair the
        # matrix cell claims means a cell that builds the wrong target is
        # caught, rather than the verifier believing whatever the filename
        # it was handed says.
        job = self.wf["jobs"][name]
        run = job["steps"][step_index(job, "Verify")].get("run", "")
        assert "matrix.platform" in run and "matrix.cpu" in run, (
            f"{name}: verify must pass the cell's platform and cpu, so an archive "
            "built for the wrong target fails rather than self-certifying"
        )

    @pytest.mark.parametrize("name", BUILD_JOBS)
    def test_verify_names_the_archive_instead_of_globbing(self, name):
        # A glob plus nullglob verifies zero archives and exits 0, which is
        # a pass reported for a check that ran over nothing.
        job = self.wf["jobs"][name]
        run = job["steps"][step_index(job, "Verify")].get("run", "")
        assert "*" not in run, (
            f"{name}: verify must name the archive it checks, so a missing archive "
            "fails the job rather than verifying nothing"
        )

    @pytest.mark.parametrize("name", BUILD_JOBS)
    def test_one_cell_failing_does_not_discard_the_others(self, name):
        assert self.wf["jobs"][name]["strategy"]["fail-fast"] is False, (
            f"{name}: fail-fast would throw away archives that built fine because "
            "another cell flaked"
        )


class TestMatrixCoversEveryArchive:
    def setup_method(self):
        self.wf = load_workflow()
        self.cells = matrix_cells(self.wf["jobs"]["build-linux"]) + matrix_cells(
            self.wf["jobs"]["build-mac"]
        )

    def test_every_archive_the_release_carries_is_in_the_matrix(self):
        in_workflow = {(c["platform"], c["cpu"]) for c in self.cells}
        assert in_workflow == set(ALL_CELLS), (
            f"workflow builds {sorted(in_workflow)}, the release ships {sorted(ALL_CELLS)}"
        )

    def test_the_container_cells_are_the_default_matrix(self):
        cells = [(c["platform"], c["cpu"]) for c in matrix_cells(self.wf["jobs"]["build-linux"])]
        assert sorted(cells) == sorted(DEFAULT_CELLS)

    def test_each_cell_carries_the_cpu_the_archive_name_uses(self):
        # The matrix spells the cpu (`x64`) because that is what the
        # archive filename and the verifier's third argument use. If the
        # cell and the name ever disagree the workflow verifies and uploads
        # a path that does not exist.
        for cell in self.cells:
            assert cell["cpu"] in ("x64", "arm64"), (
                f"{cell} names a cpu the archive naming convention does not use"
            )
            assert cell["platform"] in ("linux", "musl", "mac")
            assert expected_archive(cell["platform"], cell["cpu"]) in workflow_text()

    def test_mac_builds_on_a_mac(self):
        assert str(self.wf["jobs"]["build-mac"]["runs-on"]).startswith("macos"), (
            "there is no macOS container image, so the mac slice needs a macOS runner"
        )
        assert "macos-15" in str(self.wf["jobs"]["build-mac"]["runs-on"])

    def test_every_arm64_container_cell_runs_on_an_arm64_runner(self):
        # ADR 0001: ILC produced the object file when cross-compiling but
        # the native link failed with `unrecognised emulation mode:
        # aarch64linux`, and GitHub hosts ubuntu-24.04-arm for public
        # repositories. So arm64 gets a native runner.
        for cell in matrix_cells(self.wf["jobs"]["build-linux"]):
            if cell["cpu"] != "arm64":
                continue
            assert "arm" in cell["runner"], (
                f"{cell} builds arm64 on {cell['runner']!r}. ADR 0001 measured the "
                "native link failing when the host arch differs, so arm64 cells need "
                "an arm64 runner"
            )

    def test_no_cell_is_emulated(self):
        # release-zstd.yml registers QEMU and builds foreign-arch cells
        # under emulation. ADR 0001 rules that out here: .NET documents
        # qemu-user-static as unsupported, and this workflow was copied
        # from that one, so the guard is against inheriting it.
        blob = workflow_text().lower()
        for word in ("setup-qemu", "binfmt", "qemu"):
            assert word not in blob, (
                f"release-acadsharp.yml mentions {word!r}. ADR 0001 decides native "
                "runners per architecture because .NET does not support QEMU"
            )

    def test_the_build_jobs_run_where_the_matrix_says(self):
        # A container cell that hardcodes `runs-on: ubuntu-latest` makes
        # the `runner` key decorative and quietly puts arm64 back on x64.
        assert self.wf["jobs"]["build-linux"]["runs-on"] == "${{ matrix.runner }}", (
            "build-linux must take its runner from the matrix cell, or the arm64 "
            "runner assertion above is reading a key nothing uses"
        )


class TestUploadsGoToTheResolvedTag:
    def setup_method(self):
        self.wf = load_workflow()

    @pytest.mark.parametrize("name", BUILD_JOBS)
    def test_upload_uses_the_tag_resolve_version_computed(self, name):
        job = self.wf["jobs"][name]
        step = job["steps"][step_index(job, "Upload release")]
        blob = step.get("run", "") + str(step.get("env", ""))
        assert "resolve-version" in blob and "tag" in blob, (
            f"{name}: the upload must target the tag resolve-version produced, not a "
            "second copy of the version string"
        )

    @pytest.mark.parametrize("name", BUILD_JOBS)
    def test_a_re_run_replaces_its_own_assets(self, name):
        job = self.wf["jobs"][name]
        run = job["steps"][step_index(job, "Upload release")].get("run", "")
        assert "--clobber" in run, (
            f"{name}: without --clobber a re-run of a partially failed release fails "
            "on the assets that did upload"
        )

    def test_the_release_exists_before_the_fan_out_uploads(self):
        for name in BUILD_JOBS:
            assert "create-release" in self.wf["jobs"][name]["needs"], (
                f"{name} uploads, so it must wait for create-release. Parallel "
                "`gh release create` calls race"
            )

    def test_the_release_is_created_at_the_resolved_tag(self):
        job = self.wf["jobs"]["create-release"]
        blob = run_text(job) + job_text(job)
        assert "resolve-version" in blob and "tag" in blob


class TestReleaseNotesAreGenerated:
    """The notes come from the manifests. A release that quietly ships
    four of five targets is worse than one that ships four and says so."""

    def setup_method(self):
        self.wf = load_workflow()
        self.notes = self.wf["jobs"]["release-notes"]

    def test_the_notes_job_runs_whatever_happened(self):
        assert self.notes["if"] == "always()", (
            "release-notes must run after a partial publish, which is exactly the "
            "case it exists to report"
        )

    def test_the_notes_job_waits_for_every_build(self):
        needs = self.notes["needs"]
        for name in ("resolve-version", "create-release", *BUILD_JOBS):
            assert name in needs, f"release-notes must wait on {name}"

    def test_the_notes_read_the_matrix_results(self):
        blob = job_text(self.notes)
        for name in BUILD_JOBS:
            assert f"needs.{name}.result" in blob, (
                f"release-notes never reads {name}'s result, so a failed job would "
                "not be named in the notes or the summary"
            )

    def test_the_notes_name_every_target_that_did_not_publish(self):
        blob = run_text(self.notes).lower()
        assert "did not publish" in blob, (
            "the notes must carry a line per target that did not publish. Shipping "
            "four of five quietly is the failure this prevents"
        )

    def test_the_notes_report_static_certified_per_target(self):
        blob = run_text(self.notes)
        assert "static_certified" in blob and "LINKINFO.json" in blob, (
            "static_certified is read out of each archive's LINKINFO.json, which is "
            "the manifest the acadsharp-rs build.rs reads too"
        )

    def test_the_notes_carry_a_digest_per_published_archive(self):
        blob = run_text(self.notes)
        assert "sha256" in blob.lower(), (
            "each published archive's sha256 belongs in the notes; it is what the "
            "README's download table quotes"
        )

    def test_the_preamble_is_read_from_the_manifests(self):
        blob = run_text(self.wf["jobs"]["create-release"])
        for manifest in (
            "acadsharp/VERSION",
            "acadsharp/native/global.json",
            "acadsharp/include/viprs_acadsharp.h",
            DRIVER,
        ):
            assert manifest in blob, (
                f"the release notes never read {manifest}, so whatever they say about "
                "it is a typed copy that can rot"
            )

    def test_nothing_version_bearing_is_typed_into_the_workflow(self):
        # The whole point of generating the notes is that no number in
        # them is a second copy. If the pinned version, the SDK pin or the
        # DWG range appears as a literal here, bumping the manifest leaves
        # the workflow claiming the old value.
        blob = workflow_text()
        version = ba.read_version()
        upstream, _ = ba.split_version(version)
        with open(GLOBAL_JSON) as f:
            sdk = json.load(f)["sdk"]["version"]
        dwg_min, dwg_max = dwg_range()

        for literal, source in (
            (version, "acadsharp/VERSION"),
            (upstream, "acadsharp/VERSION"),
            (sdk, "acadsharp/native/global.json"),
            (ba.source_sha256(upstream), "SOURCE_SHA256 in build_acadsharp.py"),
            (dwg_min, "the ABI header"),
            (dwg_max, "the ABI header"),
        ):
            assert literal not in blob, (
                f"release-acadsharp.yml types {literal!r}, which {source} already "
                "says. Read it rather than copying it"
            )


def dwg_range():
    """The AC10xx read range, out of the ABI header's capabilities block.

    The header is the manifest for this one: it is what
    ``viprs_acad_capabilities_v1`` documents and what the adapter fills in
    at run time. The notes generator reads it with the same regex, so a
    header edit that breaks the extraction fails here rather than in a
    release run.
    """
    with open(ABI_HEADER) as f:
        header = f.read()
    match = re.search(r"numeric AC10xx codes, so (\d{4}) and (\d{4})", header)
    assert match, (
        "viprs_acadsharp.h no longer states the AC10xx read range where the release "
        "notes generator looks for it"
    )
    return match.group(1), match.group(2)


class TestTheHeaderStillStatesTheReadRange:
    def test_the_range_is_extractable(self):
        dwg_min, dwg_max = dwg_range()
        assert int(dwg_min) < int(dwg_max)

    def test_the_workflow_uses_the_same_regex(self):
        assert "numeric AC10xx codes, so" in workflow_text(), (
            "the notes generator must read the read range out of the header; a "
            "different phrase here means the two have already drifted"
        )


class TestThirdPartyActionsArePinned:
    """Anything that is not `actions/*` runs next to a token that can
    rewrite releases, so it is pinned to a commit rather than a tag."""

    def test_every_non_github_action_is_pinned_to_a_sha(self):
        wf = load_workflow()
        unpinned = []
        seen = 0
        for job_name, job in wf["jobs"].items():
            for step in job.get("steps", []):
                uses = step.get("uses")
                if not uses:
                    continue
                seen += 1
                if uses.startswith("actions/"):
                    continue
                ref = uses.split("@", 1)[1] if "@" in uses else ""
                if len(ref) != 40 or not all(c in "0123456789abcdef" for c in ref):
                    unpinned.append(f"{job_name}: {uses}")
        assert seen, "no step in this workflow uses an action, so this guard reads nothing"
        assert not unpinned, (
            "these third-party actions are not pinned to a commit sha, so a moved tag "
            "changes what runs:\n  " + "\n  ".join(unpinned)
        )


class TestReadmeHoldsUrlsAndDigestsTogether:
    """#43 is the standing example in both directions: zstd/README.md
    advertised six URLs for a release that had never been cut, and
    nothing checked. Publishing and leaving the README silent is the same
    failure with the sign flipped."""

    def setup_method(self):
        self.readme = read_readme()

    def test_readme_either_links_archives_or_says_there_are_none(self):
        links = f"/releases/download/{TAG_PREFIX}" in self.readme
        says_none = UNPUBLISHED_MARKER in self.readme
        assert links != says_none, (
            "acadsharp/README.md must either link published archives or say "
            f"{UNPUBLISHED_MARKER!r}. It currently does " + ("both" if links else "neither")
        )

    def test_every_download_url_carries_its_digest(self):
        offenders = [
            line
            for line in self.readme.splitlines()
            if f"/releases/download/{TAG_PREFIX}" in line
            and not re.search(r"\b[0-9a-f]{64}\b", line)
        ]
        assert not offenders, (
            "these acadsharp/README.md download lines have no sha256 next to them, so "
            "they ask the consumer to trust the transport:\n  " + "\n  ".join(offenders)
        )

    def test_every_digest_names_what_it_digests(self):
        offenders = [
            line
            for line in self.readme.splitlines()
            if re.search(r"\b[0-9a-f]{64}\b", line) and "http" not in line
        ]
        assert not offenders, (
            "these acadsharp/README.md lines carry a sha256 with no link beside it, so "
            "nothing says what was hashed:\n  " + "\n  ".join(offenders)
        )

    def test_a_published_readme_lists_every_archive(self):
        # Vacuous while nothing is published, which is what the either/or
        # test above exists to stop being a silent state.
        if UNPUBLISHED_MARKER in self.readme:
            return
        for platform, cpu in ALL_CELLS:
            assert expected_archive(platform, cpu) in self.readme, (
                f"acadsharp/README.md links published archives but never mentions "
                f"{expected_archive(platform, cpu)}"
            )

    def test_readme_names_the_release_tag_for_the_pinned_version(self):
        version = ba.read_version()
        assert f"{TAG_PREFIX}{version}" in self.readme, (
            f"acadsharp/README.md never mentions the {TAG_PREFIX}{version} release tag"
        )

    def test_no_stale_version_in_readme_download_urls(self):
        version = ba.read_version()
        stale = [
            url
            for url in re.findall(rf"/releases/download/{TAG_PREFIX}(\S+?)/", self.readme)
            if url != version
        ]
        assert not stale, (
            f"acadsharp/README.md links archives from {sorted(set(stale))} but "
            f"acadsharp/VERSION says {version}"
        )

    def test_readme_points_at_the_workflow_that_cuts_the_release(self):
        assert "release-acadsharp.yml" in self.readme, (
            "the README has to say how the archives it describes get published"
        )


class TestTheDriverSpeaksTheSameContract:
    """Everything above asserts against the contract issue #48 freezes.
    These re-assert the same values through ``build_acadsharp.py`` as soon
    as it grows them, so a rename in the driver fails here rather than
    publishing five assets nobody links to.

    They skip while #48 is unlanded. The frozen constants carry the
    workflow assertions either way, so nothing above goes quiet.
    """

    def test_release_tag_matches_the_workflow_prefix(self):
        release_tag = driver_attr("release_tag")
        if release_tag is None:
            pytest.skip("build_acadsharp.release_tag() lands with issue #48")
        version = ba.read_version()
        assert release_tag(version) == f"{TAG_PREFIX}{version}"

    def test_archive_names_match_the_workflow(self):
        archive_name = driver_attr("archive_name")
        if archive_name is None:
            pytest.skip("build_acadsharp.archive_name() lands with issue #48")
        for platform, cpu in ALL_CELLS:
            assert archive_name(platform, cpu) == expected_archive(platform, cpu)

    def test_the_default_matrix_matches_the_container_cells(self):
        default_jobs = driver_attr("DEFAULT_JOBS")
        if default_jobs is None:
            pytest.skip("build_acadsharp.DEFAULT_JOBS lands with issue #48")
        assert sorted(tuple(cell) for cell in default_jobs) == sorted(DEFAULT_CELLS)

    def test_the_verifier_the_workflow_calls_exists(self):
        script = os.path.join(REPO_ROOT, VERIFIER)
        if not os.path.exists(script):
            pytest.skip(f"{VERIFIER} lands with issue #48")
        assert os.access(script, os.X_OK), f"{VERIFIER} is not executable"
