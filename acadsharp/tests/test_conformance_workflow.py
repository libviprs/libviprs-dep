"""Tests for .github/workflows/acadsharp-conformance.yml.

The workflow exists because nothing ran the two conformance consumers. ci.yml
never mentioned them and release-acadsharp.yml checks that the exported
symbols are present without calling one, so the C consumer's checks and the
generated Rust consumer's ran only when a person remembered to run them.

Three properties are worth pinning, and all three are things a later edit
could quietly undo.

It has to stay out of ci.yml. libviprs-tests' `Hook Mirror (every repo in the
org)` job reads this repo's ci.yml at the main tip and mirrors every job into
the shared pre-commit hook unless it is deferred there, and a .NET SDK is not
something a pre-commit hook should need. `test_acadsharp_ci_decision.py` holds
the other end of that: ci.yml still carries no dotnet step.

It has to be gated on this dependency's paths, or a workflow that builds an
archive runs on every push to every branch.

And it has to run against the archive. The premise of the epic is that a
consumer links what we publish, so a run against the build tree alone proves
the thing nobody downloads.
"""

import inspect
import os
import re

import build_acadsharp as ba
import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WORKFLOW_PATH = os.path.join(REPO_ROOT, ".github", "workflows", "acadsharp-conformance.yml")
CI_WORKFLOW = os.path.join(REPO_ROOT, ".github", "workflows", "ci.yml")

C_RUNNER = "acadsharp/tests/conformance/c/run.sh"
RUST_RUNNER = "acadsharp/tests/conformance/rust/run.sh"

# The runners' own default, which is a local image name on one machine.
LOCAL_ONLY_IMAGE = "viprs-rust:arm64"


def workflow():
    with open(WORKFLOW_PATH) as f:
        return yaml.safe_load(f)


def workflow_text():
    with open(WORKFLOW_PATH) as f:
        return f.read()


def workflow_code():
    """The workflow with whole-line comments dropped.

    A check that greps a file for a forbidden string fires on the prose
    explaining why the string is forbidden, and the reasons here are worth
    keeping in the file next to what they justify.
    """
    return "\n".join(
        line for line in workflow_text().splitlines() if not line.lstrip().startswith("#")
    )


def triggers(wf):
    # PyYAML reads the key `on` as the boolean True, because YAML 1.1 says so.
    if "on" in wf:
        return wf["on"]
    return wf[True]


def steps():
    out = []
    for job in workflow()["jobs"].values():
        out += job.get("steps", [])
    return out


def run_steps():
    return [s["run"] for s in steps() if "run" in s]


class TestItExistsAndIsNotInCi:
    def test_the_workflow_is_there(self):
        assert os.path.isfile(WORKFLOW_PATH)

    def test_ci_does_not_run_the_consumers(self):
        # Not a style point. A job here reddens libviprs-tests' Hook Mirror
        # until a pairing PR merges over there, and a workflow file of its
        # own is outside what that guard reads.
        with open(CI_WORKFLOW) as f:
            ci = f.read()
        assert "run.sh" not in ci and "conformance" not in ci, (
            "the conformance run has moved into ci.yml, which engages the "
            "libviprs-tests Hook Mirror contract. ADR 0001 has the reasoning"
        )

    def test_it_can_be_run_by_hand(self):
        assert "workflow_dispatch" in triggers(workflow()), (
            "a workflow gated on paths has to be dispatchable, or a change "
            "outside acadsharp/ that breaks it cannot be retried"
        )


class TestItIsGatedOnThisDependency:
    def test_it_only_fires_on_acadsharp_paths(self):
        paths = triggers(workflow())["push"]["paths"]
        assert "acadsharp/**" in paths, (
            f"the push trigger is {paths}, so this builds an archive on pushes "
            "that cannot have changed it"
        )

    def test_it_fires_on_changes_to_itself(self):
        paths = triggers(workflow())["push"]["paths"]
        assert any(WORKFLOW_PATH.endswith(p.lstrip("./")) for p in paths), (
            f"{paths} does not include this workflow, so an edit to it cannot "
            "be tested by pushing it"
        )

    def test_the_job_has_a_timeout(self):
        for name, job in workflow()["jobs"].items():
            assert job.get("timeout-minutes"), f"{name} can hold a runner forever"


class TestItRunsBothConsumers:
    def test_the_c_consumer_runs(self):
        assert any(C_RUNNER in step for step in run_steps()), (
            "nothing in the workflow runs the C conformance consumer, which is "
            "the whole reason the file exists"
        )

    def test_the_generated_consumer_runs(self):
        assert any(RUST_RUNNER in step for step in run_steps())

    def test_each_consumer_runs_against_the_archive_and_the_test_build(self):
        # Twice each, and not by accident: once against what a consumer
        # downloads, once against the only configuration carrying the exports
        # that make the exception and handle-leak cases runnable.
        for runner in (C_RUNNER, RUST_RUNNER):
            runs = [s for s in run_steps() if runner in s]
            assert len(runs) == 2, (
                f"{runner} runs {len(runs)} times. It is meant to run against the "
                "unpacked archive and against the test configuration"
            )

    def test_every_consumer_step_is_told_where_the_library_is(self):
        for step in steps():
            body = step.get("run", "")
            if C_RUNNER not in body and RUST_RUNNER not in body:
                continue
            assert "VIPRS_LIB_DIR" in step.get("env", {}), (
                f"{step.get('name')} runs a consumer without VIPRS_LIB_DIR, so it "
                "falls back to a publish directory this job never wrote"
            )

    def test_one_of_the_library_directories_is_the_unpacked_archive(self):
        dirs = [
            step.get("env", {}).get("VIPRS_LIB_DIR", "")
            for step in steps()
            if "run.sh" in step.get("run", "")
        ]
        assert any("unpacked" in d for d in dirs), (
            f"no consumer runs against an unpacked archive: {dirs}. A run against "
            "the build tree alone proves the artefact nobody downloads"
        )

    def test_the_archive_is_verified_before_a_consumer_runs_against_it(self):
        bodies = run_steps()
        verify = next(i for i, b in enumerate(bodies) if "verify_archive.sh" in b)
        first_run = next(i for i, b in enumerate(bodies) if "run.sh" in b)
        assert verify < first_run, (
            "a consumer runs against the archive before verify_archive.sh has "
            "looked at it, which makes the verification decorative"
        )


class TestTheImageIsOneARegistryCanServe:
    def test_the_conformance_image_is_set(self):
        text = workflow_text()
        assert "VIPRS_CONFORMANCE_IMAGE" in text, (
            "without it the runners fall back to their local default, which is "
            "an image name that exists on one developer's machine"
        )

    def test_it_is_not_the_local_default(self):
        image = re.search(r"VIPRS_CONFORMANCE_IMAGE:\s*(\S+)", workflow_text())
        assert image, "VIPRS_CONFORMANCE_IMAGE is named but never given a value"
        assert image.group(1) != LOCAL_ONLY_IMAGE, (
            f"{LOCAL_ONLY_IMAGE} is the runners' local default and no registry "
            "serves it, so every consumer step would fail on the pull"
        )

    def test_it_is_pinned_rather_than_floating(self):
        image = re.search(r"VIPRS_CONFORMANCE_IMAGE:\s*(\S+)", workflow_text()).group(1)
        tag = image.rsplit(":", 1)[-1] if ":" in image else ""
        assert tag and tag != "latest", (
            f"{image} floats, so a run that passed today and fails tomorrow says "
            "nothing about the change that was pushed"
        )


class TestTheBuilderImageIsAskedForRatherThanSpelled:
    """The test configuration is published in the image the archive build made.

    That image already holds the SDK, clang, the pinned upstream source and a
    warm package cache, so it costs one publish rather than a second
    toolchain. The name is assembled from the version and the cell, so the
    workflow asks the driver rather than repeating the rule.
    """

    def test_the_driver_exposes_the_name(self):
        assert ba.builder_image_tag("3.7.1-viprs.1", "linux", "arm64") == (
            "acadsharp-builder-3.7.1-viprs.1-linux-arm64"
        )

    def test_the_build_uses_the_same_function(self):
        code = inspect.getsource(ba._build_docker)
        assert "builder_image_tag(" in code, (
            "the docker build assembles the tag itself again, so the name the "
            "workflow asks for and the name the build writes can drift"
        )

    def test_the_workflow_asks_for_it(self):
        text = workflow_text()
        assert "builder_image_tag(" in text, (
            "the workflow spells the builder image name out, which goes stale on "
            "the first version bump"
        )
        assert "acadsharp-builder-" not in workflow_code(), (
            "the workflow carries a literal builder image name as well as asking the driver for one"
        )

    def test_the_test_configuration_is_what_it_publishes(self):
        text = workflow_text()
        assert "-c AbiTest" in text, (
            "the second run has to be against the test configuration, or the "
            "exception and handle-leak cases are skipped in CI too"
        )


class TestNothingIsEmulated:
    """ADR 0001 measured the cross-architecture link failing and .NET
    documents qemu-user-static as unsupported, so every cell here runs on a
    runner of its own architecture. release-acadsharp.yml refuses an emulator
    for the same reason."""

    def test_no_emulator_is_registered(self):
        text = workflow_code().lower()
        for token in ("setup-qemu", "qemu-user-static", "binfmt"):
            assert token not in text, f"{token} is back; ADR 0001 says why it must not be"

    def test_the_runner_matches_the_platform_it_builds(self):
        wf = workflow()
        for name, job in wf["jobs"].items():
            runner = job["runs-on"]
            platform = job.get("env", {}).get("VIPRS_CONFORMANCE_PLATFORM", "")
            if not platform:
                continue
            assert ("arm" in runner) == ("arm64" in platform), (
                f"{name} runs on {runner} and asks for {platform}"
            )
