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

There is a fourth now, and it is the same argument one libc over. #73 made the
static cargo recipe work on musl and proved it with a link, so musl has linking
coverage; nothing ever ran a consumer against a musl **shared** library, so it
had no behaviour coverage at all. The runs are named here positively rather
than counted: each consumer against the unpacked glibc archive, each against
the AbiTest build, each against an unpacked musl archive, in a container whose
own libc is musl. A count would let any two of those stand in for the third.
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


def jobs_with_steps():
    """Every job, with its own steps, its own env and nothing borrowed.

    Most of what is worth checking here is a property of one job: the archive
    a job verifies is the archive that job's consumers run against, and the
    image a job names is the libc its consumers get. Flattening every job into
    one list lets a step in the glibc job satisfy a claim about the musl one.
    """
    return [(name, job, job.get("steps", [])) for name, job in workflow()["jobs"].items()]


def consumer_steps(candidates=None):
    """Every step that runs a conformance consumer, paired with its job."""
    out = []
    for name, job, job_steps in jobs_with_steps():
        for step in job_steps:
            body = step.get("run", "")
            if any(runner in body for runner in (candidates or (C_RUNNER, RUST_RUNNER))):
                out.append((name, job, step))
    return out


def lib_dir(step):
    return step.get("env", {}).get("VIPRS_LIB_DIR", "")


def job_image(job):
    """The image a job's consumers run in, as this workflow names it.

    The runners default to a local-only image, so a job that does not set this
    is not naming a libc at all.
    """
    return job.get("env", {}).get("VIPRS_CONFORMANCE_IMAGE", "")


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
        # This used to read `len(runs) == 2`, and the two were the point
        # rather than the number: one run against what a consumer downloads,
        # one against the only configuration carrying the exports that make
        # the exception and handle-leak cases runnable. Naming them is what
        # the count was standing in for, and naming them survives a third run
        # being added without a fourth quietly replacing one of these.
        for runner in (C_RUNNER, RUST_RUNNER):
            dirs = [lib_dir(step) for _, _, step in consumer_steps([runner])]
            assert any("unpacked" in d for d in dirs), (
                f"{runner} never runs against an unpacked archive: {dirs}. A run "
                "against the build tree alone proves the artefact nobody downloads"
            )
            assert any("abitest" in d.lower() for d in dirs), (
                f"{runner} never runs against the test configuration: {dirs}. That "
                "is the only build carrying viprs_acad__test_throw and "
                "viprs_acad__test_live_handles, so without it the exception and "
                "handle-leak cases are skipped in CI too"
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
        # Per job, because the ordering is a fact about one job's steps. Read
        # across the whole file it says only that *some* job verified *an*
        # archive before *some* consumer ran, which the glibc job satisfies on
        # its own no matter what the musl job does.
        for name, _job, job_steps in jobs_with_steps():
            bodies = [s["run"] for s in job_steps if "run" in s]
            runs = [i for i, b in enumerate(bodies) if "/run.sh" in b]
            if not runs:
                continue
            verifies = [i for i, b in enumerate(bodies) if "verify_archive" in b]
            assert verifies and verifies[0] < runs[0], (
                f"{name} runs a consumer against an archive before anything "
                "verified it, which makes the verification decorative"
            )


class TestTheMuslLibraryIsRunAndNotOnlyLinked:
    """musl had linking coverage and no behaviour coverage.

    #73 made the static cargo recipe work on musl and proved it with a link
    and a runtime probe. It never ran a conformance consumer, so nothing had
    ever asked the musl shared library to answer the ABI handshake, report its
    capabilities or read a file, and `static_certified: true` says nothing
    about any of that.

    One run per consumer, against the unpacked musl archive, and deliberately
    no second AbiTest publish on this side. The musl-specific risk is whether
    the .so loads and answers at all. `viprs_acad__test_throw` and
    `viprs_acad__test_live_handles` exercise managed logic that cannot differ
    by libc, so publishing a musl AbiTest build to reach them would buy
    nothing for the runner time it costs.
    """

    def musl_job(self):
        """The job that builds a musl archive, found by what it builds."""
        for name, job, job_steps in jobs_with_steps():
            bodies = " ".join(s.get("run", "") for s in job_steps)
            if "build_acadsharp.py" in bodies and "--platform musl" in bodies:
                return name, job, job_steps
        raise AssertionError(
            "no job builds a musl archive, so there is nothing for a musl "
            "consumer run to run against"
        )

    def musl_archive_stem(self):
        """`acadsharp-musl-<arch>`, read off the build the job actually runs.

        Spelled out here it would be a second copy of the cell name, and a
        consumer could then be pointed at a musl archive of the other
        architecture without this noticing.
        """
        _name, _job, job_steps = self.musl_job()
        bodies = " ".join(s.get("run", "") for s in job_steps)
        arch = re.search(r"--platform musl --arch (\S+)", bodies)
        assert arch, (
            "the musl job's build step does not name an architecture, so the "
            "archive its consumers should run against cannot be derived"
        )
        return f"acadsharp-musl-{arch.group(1)}"

    def test_both_consumers_run_against_the_musl_archive(self):
        name, _job, job_steps = self.musl_job()
        stem = self.musl_archive_stem()
        for runner in (C_RUNNER, RUST_RUNNER):
            dirs = [lib_dir(s) for s in job_steps if runner in s.get("run", "")]
            assert any(stem in d for d in dirs), (
                f"{runner} never runs against {stem} in {name}: {dirs}. musl "
                "then has a link test and no behaviour test, which is the state "
                "#73 left and #74 records"
            )

    def test_the_musl_run_is_against_the_unpacked_archive(self):
        stem = self.musl_archive_stem()
        _name, _job, job_steps = self.musl_job()
        for step in job_steps:
            if lib_dir(step) and stem in lib_dir(step):
                assert "unpacked" in lib_dir(step), (
                    f"{step.get('name')} points at {lib_dir(step)}, which is not "
                    "an unpacked archive. The .so a musl consumer downloads is "
                    "the one worth running"
                )

    def test_the_musl_consumers_run_in_a_musl_container(self):
        name, job, _steps = self.musl_job()
        image = job_image(job)
        assert image.endswith("-alpine"), (
            f"{name} runs its consumers in {image!r}, which is not a musl "
            "image. A musl .so asks for /lib/ld-musl-*.so.1 and a glibc "
            "container has no such loader, so this is the difference between "
            "running the musl library and failing to"
        )

    def test_both_libcs_run_the_same_toolchain(self):
        # The point of the second run is the libc. If the two containers also
        # differ by compiler version, a failure on one side and not the other
        # no longer says which of the two it was.
        images = set(re.findall(r"VIPRS_CONFORMANCE_IMAGE:\s*(\S+)", workflow_text()))
        musl = {i for i in images if i.endswith("-alpine")}
        glibc = images - musl
        assert musl and glibc, (
            f"the workflow names {sorted(images)}, which is not one glibc image and one musl image"
        )
        assert {f"{i}-alpine" for i in glibc} == musl, (
            f"{sorted(glibc)} and {sorted(musl)} are not the same image in two "
            "libcs, so a difference between the two runs could be the toolchain "
            "rather than the libc"
        )

    def test_the_musl_job_does_not_publish_a_second_test_configuration(self):
        # Recorded, not enforced against a future change of mind: this is the
        # reduced shape #74 chose, and the reason it is worth doing at all.
        # If this ever needs to be deleted, delete it with the reasoning above
        # rather than quietly.
        _name, _job, job_steps = self.musl_job()
        bodies = " ".join(s.get("run", "") for s in job_steps)
        assert "-c AbiTest" not in bodies, (
            "the musl job publishes an AbiTest build. The cases that needs are "
            "managed logic that cannot differ by libc, so it costs a NativeAOT "
            "publish for no musl-specific answer"
        )


class TestTheImageIsOneARegistryCanServe:
    def test_the_conformance_image_is_set(self):
        text = workflow_text()
        assert "VIPRS_CONFORMANCE_IMAGE" in text, (
            "without it the runners fall back to their local default, which is "
            "an image name that exists on one developer's machine"
        )

    def test_it_is_not_the_local_default(self):
        # findall, not search: there is more than one of these now, and a
        # regex that reads the first one says nothing about the second.
        images = re.findall(r"VIPRS_CONFORMANCE_IMAGE:\s*(\S+)", workflow_text())
        assert images, "VIPRS_CONFORMANCE_IMAGE is named but never given a value"
        for image in images:
            assert image != LOCAL_ONLY_IMAGE, (
                f"{LOCAL_ONLY_IMAGE} is the runners' local default and no registry "
                "serves it, so every consumer step would fail on the pull"
            )

    def test_it_is_pinned_rather_than_floating(self):
        images = re.findall(r"VIPRS_CONFORMANCE_IMAGE:\s*(\S+)", workflow_text())
        assert images, "VIPRS_CONFORMANCE_IMAGE is named but never given a value"
        for image in images:
            tag = image.rsplit(":", 1)[-1] if ":" in image else ""
            assert tag and tag != "latest", (
                f"{image} floats, so a run that passed today and fails tomorrow says "
                "nothing about the change that was pushed"
            )

    def test_every_job_that_runs_a_consumer_names_one(self):
        for name, job, job_steps in jobs_with_steps():
            if not any(
                runner in s.get("run", "") for s in job_steps for runner in (C_RUNNER, RUST_RUNNER)
            ):
                continue
            assert job_image(job), (
                f"{name} runs a consumer without naming VIPRS_CONFORMANCE_IMAGE, "
                f"so the runners fall back to {LOCAL_ONLY_IMAGE}, which exists on "
                "one developer's machine"
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
