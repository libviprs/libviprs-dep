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

``build_acadsharp.py`` grows ``archive_name()``, ``release_tag()``,
``resolve_jobs()`` and a default matrix in issue #48, which was written
in parallel with this workflow. Everything here asserts against the
contract that issue freezes, and ``TestTheDriverSpeaksTheSameContract``
re-asserts the same values *through the driver* as soon as those exist.
That is not decoration: this workflow shipped passing ``--cpu`` to the
driver, which takes ``--arch``, and those tests are what caught it the
moment the two branches were composed. They stay skipped on a branch
that does not have the driver, so composing is the only place they can
speak. Running this file alone, or this branch alone, would not have
found it.
"""

import hashlib
import inspect
import json
import os
import re
import shlex
import subprocess
import sys
import tarfile
import textwrap

import build_acadsharp as ba
import pytest
import test_acadsharp_targets

yaml = pytest.importorskip("yaml")

# The acceptance bullet's own pattern, borrowed rather than restated.
# test_acadsharp_targets.py walks the acadsharp directory; this workflow
# is not in it.
MICROSOFT_TARGET = test_acadsharp_targets.TestNothingNamesAMicrosoftTarget.FORBIDDEN


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

# And what it says in the third state, which #43 did not have and this
# dependency now does: a release exists, VERSION has moved past it, and the
# table therefore describes the previous tag on purpose. Left unsaid, that
# reads exactly like a table nobody updated, and a consumer downloads
# archives believing they are a build of the tree beside them.
NOT_CUT_MARKER = "is not cut yet"

# ---------------------------------------------------------------------------
# The contract issue #48 freezes, in the two vocabularies it is written in.
#
# Archive names are <dep>-<platform>-<cpu>.tgz with platform in
# linux | musl | mac and cpu in x64 | arm64, which is what
# pdfium/tests/test_naming.py and zstd/tests/test_zstd_naming.py already
# pin for the other two dependencies and what the verifier infers from a
# filename. The version lives in the release tag, not the archive name.
#
# The drivers' CLI speaks a different word for the same thing: --arch
# takes amd64 | arm64, and build_acadsharp.py follows build_zstd.py and
# build_pdfium.py in that. So a cell has both, and CPU_FOR_ARCH is the
# only place the two are tied together. I had the workflow passing --cpu
# to the driver, which the driver does not accept; the tests below now
# hold each vocabulary against the side that actually speaks it.
# ---------------------------------------------------------------------------
CPU_FOR_ARCH = {"amd64": "x64", "arm64": "arm64"}

# (platform, arch): what the CLI and the driver's default matrix speak.
DEFAULT_JOBS_EXPECTED = [
    ("linux", "amd64"),
    ("linux", "arm64"),
    ("musl", "amd64"),
    ("musl", "arm64"),
]
MAC_JOBS_EXPECTED = [("mac", "arm64")]
ALL_JOBS_EXPECTED = DEFAULT_JOBS_EXPECTED + MAC_JOBS_EXPECTED

# (platform, cpu): what the archive name and the verifier speak.
DEFAULT_CELLS = [(p, CPU_FOR_ARCH[a]) for p, a in DEFAULT_JOBS_EXPECTED]
MAC_CELLS = [(p, CPU_FOR_ARCH[a]) for p, a in MAC_JOBS_EXPECTED]
ALL_CELLS = DEFAULT_CELLS + MAC_CELLS

TAG_PREFIX = "acadsharp-"

# The jobs that call `gh release`, so the ones that need contents: write.
PUBLISHING_JOBS = ("create-release", "build-linux", "build-mac", "release-notes")
BUILD_JOBS = ("build-linux", "build-mac")

# Every build cell goes through the wrapper, and for two different
# reasons that land on the same script. Both musl cells run on glibc
# runners, and verify_archive.sh link-tests `static_certified` only when
# the host can build for the target, so those two got a "not link-tested"
# line in a log and a green job while shipping a cargo recipe nothing had
# run; the wrapper puts a musl archive in front of a musl host, in a
# container on the same runner. The mac cell already *is* the matched
# host, so the wrapper runs the script straight through -- what it adds
# there is the exported VIPRS_REQUIRE_LINK_TEST, which is what makes
# verify_archive.sh run the documented cargo recipe against a shared-only
# archive and refuse when nothing linked it. Called directly, the script
# prints "cargo recipe not run" and exits 0, which on the one target
# where shared is the only link mode is #95's shape again.
MATCHED_HOST_VERIFIER = "acadsharp/scripts/verify_archive_matched_host.sh"
# Per job, and asserted per job. Collapsing this to "the wrapper appears
# somewhere" once both rows agree would stop noticing a single cell
# regressing to the bare script, which is the defect this mapping exists
# to catch.
VERIFIER_FOR = {
    "build-linux": MATCHED_HOST_VERIFIER,
    "build-mac": MATCHED_HOST_VERIFIER,
}
DRIVER = "acadsharp/build_acadsharp.py"

# The two conformance consumers. The C one compiles against the header the
# archive ships and links the library beside it; the Rust one is generated
# from that same header by its build script. Between them they are the only
# thing in this repository that asks a published library to answer.
C_RUNNER = "acadsharp/tests/conformance/c/run.sh"
RUST_RUNNER = "acadsharp/tests/conformance/rust/run.sh"
CONSUMERS = (C_RUNNER, RUST_RUNNER)

# The guard that says the library about to be run asks for the libc this
# cell is about. A script rather than a block of shell inside a workflow,
# because a guard nobody can run is a guard nobody has watched fail.
LIBC_CONTROL = "acadsharp/tests/conformance/expect_libc.sh"

# Shared with release-zstd.yml and release.yml, so the tag-moving logic
# has exactly one implementation to keep right across the three.
LATEST_SCRIPT = "tools/publish_latest_tag.sh"

# The conformance runners' own fallback, which is an image name that exists
# on one developer's machine and no registry serves.
LOCAL_ONLY_IMAGE = "viprs-rust:arm64"


def expected_archive(platform, cpu):
    return f"{TAG_PREFIX}{platform}-{cpu}.tgz"


def driver_help():
    """``build_acadsharp.py --help``, as the workflow would see it."""
    done = subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, DRIVER), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout + done.stderr


def load_workflow():
    with open(WORKFLOW_PATH) as f:
        return yaml.safe_load(f)


def inline_python_blocks():
    """Every ``python3 - ... <<'PY'`` heredoc in the workflow, dedented.

    The release notes are generated by scripts that live inside this
    workflow, which means nothing compiles them until a release run does.
    A typo in one of them would be discovered on the release page.
    """
    blocks = re.findall(r"<<'PY'\n(.*?)\n\s*PY\n", workflow_text(), re.S)
    return [textwrap.dedent(b) for b in blocks]


def notes_generator(marker):
    """The one inline script containing ``marker``."""
    found = [b for b in inline_python_blocks() if marker in b]
    assert len(found) == 1, f"expected exactly one inline script containing {marker!r}"
    return found[0]


def workflow_text():
    with open(WORKFLOW_PATH) as f:
        return f.read()


def workflow_code():
    """The workflow with whole-line comments dropped.

    A check that greps the whole file for a forbidden word fires on the
    prose explaining why the word is forbidden, and the comment saying
    why this workflow does not emulate is worth keeping. Same treatment
    ``zstd/tests/test_ci_coverage.py`` gives ci.yml.
    """
    return "\n".join(
        line for line in workflow_text().splitlines() if not line.lstrip().startswith("#")
    )


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


def build_command_for(job, cell, version):
    """The Build step's own command line, with one matrix cell filled in.

    Reconstructed from the step rather than retyped, so what gets run is
    what the runner would run, line continuations and all. ``$VERSION``
    is the step's own env, which on a real run holds whatever
    resolve-version computed, so the caller says what that was.
    """
    run = job["steps"][step_index(job, "Build")]["run"]
    line = " ".join(run.replace("\\\n", " ").split())
    for key, value in cell.items():
        line = line.replace("${{ matrix." + key + " }}", str(value))
    argv = shlex.split(line)
    return [version if a == "$VERSION" else a for a in argv]


def run_text(job):
    return "\n".join(step.get("run", "") for step in job.get("steps", []))


def job_text(job):
    """Everything a job says: its run scripts, its `uses`, its `with` and `env`."""
    return yaml.safe_dump(job)


# The range every real archive carries, since the smoke measures it on
# every target. Staged by default so a test about something else is not
# also a test about a manifest with no read range in it; pass None for
# either to leave it out.
STAGED_RANGE = {"dwg_version_min": 1014, "dwg_version_max": 1032}


def stage_archive(tmp_path, platform, cpu, **linkinfo):
    """Write assets/<stem>.tgz laid out the way issue #48 freezes it."""
    linkinfo = {k: v for k, v in dict(STAGED_RANGE, **linkinfo).items() if v is not None}
    assets = tmp_path / "assets"
    assets.mkdir(exist_ok=True)
    stem = f"{TAG_PREFIX}{platform}-{cpu}"
    staged = tmp_path / stem / "metadata"
    staged.mkdir(parents=True, exist_ok=True)
    (staged / "LINKINFO.json").write_text(json.dumps(linkinfo))
    tgz = assets / f"{stem}.tgz"
    with tarfile.open(tgz, "w:gz") as tf:
        tf.add(tmp_path / stem, arcname=stem)
    return stem, tgz


def notes_env(step, version, **overrides):
    env = dict(os.environ)
    env.update(
        {
            "TARGETS": step["env"]["TARGETS"],
            "VERSION": version,
            "LINUX_RESULT": "failure",
            "MAC_RESULT": "failure",
        }
    )
    env.update(overrides)
    return env


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

    def test_every_environment_variable_a_step_declares_is_read_by_it(self):
        # This is the finding in general form. Both build jobs declared
        # `env: VERSION` and never mentioned it in the script, so the
        # resolved version reached the tag and not the build, and the
        # declaration made it look otherwise. An env key nothing reads is
        # either a bug or a leftover, and neither should sit there
        # looking like plumbing that works.
        #
        # GH_TOKEN is the one exception: `gh` reads it from the
        # environment, so it is consumed without ever being named.
        implicitly_consumed = {"GH_TOKEN"}
        unread = []
        for job_name, job in self.wf["jobs"].items():
            for step in job.get("steps", []):
                run = step.get("run", "")
                for key in step.get("env", {}):
                    if key in implicitly_consumed:
                        continue
                    if key not in run:
                        unread.append(f"{job_name} / {step.get('name', '?')}: {key}")
        assert not unread, (
            "these steps declare an environment variable and never read it, which is "
            "how the dispatched version came to reach the tag but not the build:\n  "
            + "\n  ".join(unread)
        )

    def test_the_release_is_not_marked_a_pre_release(self):
        # Pinned because it drifted once already. release.yml and
        # release-zstd.yml mark nothing, build_acadsharp.upload_release()
        # creates a plain release for this same tag, and a pre-release is
        # never "latest". TestTheDriverSpeaksTheSameContract holds this
        # against the driver's own publishing path as well.
        assert "--prerelease" not in workflow_code(), (
            "release-acadsharp.yml marks the release a pre-release and nothing "
            "promotes it, so the kind of release depends on whether a laptop or CI "
            "cut it"
        )

    def test_nothing_names_a_microsoft_platform_target_or_toolchain(self):
        # `test_acadsharp_targets.py` already makes this an acceptance
        # check over the whole acadsharp directory, but it walks that
        # directory only, and this workflow lives in .github/workflows.
        # Its pattern is borrowed rather than restated: spelling the
        # runtime identifiers out a second time would itself trip that
        # guard, and one regex is easier to keep honest than two.
        offenders = [
            f"{lineno}: {line.strip()}"
            for lineno, line in enumerate(workflow_text().splitlines(), 1)
            if MICROSOFT_TARGET.search(line)
        ]
        assert not offenders, (
            "release-acadsharp.yml names a runtime identifier or toolchain this org "
            "ships no artifact for:\n  " + "\n  ".join(offenders)
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

    def test_a_version_whose_shim_has_moved_is_refused_here(self):
        assert "packaged_shim_digest" in run_text(self.job), (
            "resolve-version must ask build_acadsharp which shim this tree is, so a "
            "dispatch naming a published version on moved sources fails in this job "
            "rather than after five runners have compiled a library for nothing"
        )

    def test_that_refusal_really_refuses_and_really_accepts(self):
        # The step's own script, run twice. Asserting that it names the
        # function leaves the case where it names it and ignores what it
        # says, and a refusal that has never been watched refusing is a
        # refusal nobody has seen work.
        script = None
        for step in self.job["steps"]:
            run = step.get("run") or ""
            if "packaged_shim_digest" in run:
                script = run.split("<<'PY'", 1)[1].rsplit("PY", 1)[0]
                break
        assert script, "no step in resolve-version runs the shim refusal"

        def preflight(version):
            return subprocess.run(
                [sys.executable, "-c", script, version],
                capture_output=True,
                text=True,
                cwd=REPO_ROOT,
                check=False,
            )

        accepted = preflight(ba.read_version())
        assert accepted.returncode == 0, accepted.stdout + accepted.stderr
        assert ba.shim_digest() in accepted.stdout

        # A published version this tree is not. `3.7.1-viprs.1` is the one
        # in the tree's own SHIM_DIGESTS, and #110 moved the shim past it.
        stale = [v for v in ba.SHIM_DIGESTS if ba.SHIM_DIGESTS[v] != ba.shim_digest()]
        assert stale, (
            "every row in SHIM_DIGESTS matches the tree, so there is no published "
            "version for this to be refused against and the control is hollow"
        )
        refused = preflight(stale[0])
        assert refused.returncode != 0, refused.stdout + refused.stderr
        assert "pinned to shim" in refused.stdout + refused.stderr

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
        assert VERIFIER_FOR[name] in run

    def test_the_container_cells_verify_on_a_host_that_matches_the_archive(self):
        """The musl cells are the reason, and they share a job with the glibc ones.

        One step covers all four, so the wrapper decides per archive
        rather than the workflow carrying a condition that can drift from
        the matrix.
        """
        job = self.wf["jobs"]["build-linux"]
        run = job["steps"][step_index(job, "Verify")].get("run", "")
        assert MATCHED_HOST_VERIFIER in run, (
            "build-linux calls the verifier directly, so its two musl cells verify "
            "on a glibc runner and the consumer link never runs"
        )

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


class TestEveryContainerCellIsRunAndNotOnlyLinked:
    """#75 asked a musl library to answer for itself. On one architecture.

    That lane put both conformance consumers in front of an unpacked musl
    archive in `acadsharp-conformance.yml`, which runs arm64 and nothing else
    on purpose, so musl/x64 came out of it exactly where it went in: it links,
    `verify_archive.sh` runs a static probe against it through the matched-host
    wrapper, and no consumer has ever loaded the shared library it ships.

    musl/x64 is built here, so it gets covered here. And once the steps are in
    this job they cost nothing extra to apply to every cell, which is the
    reason there is no `if:` on them: linux/x64 had the same gap and nobody had
    named it, the two arm64 cells re-ask the question against the archive that
    is about to be uploaded rather than against a branch build, and a rule with
    no exception is a rule that cannot be got wrong by editing the matrix.

    The consumers run between Verify and Upload, so a library that loads and
    then refuses the ABI handshake keeps its archive off the release page.
    """

    def setup_method(self):
        self.wf = load_workflow()
        self.job = self.wf["jobs"]["build-linux"]
        self.steps = self.job.get("steps", [])

    def job_env(self, key):
        return str(self.job.get("env", {}).get(key, ""))

    def consumer_indices(self, runner=None):
        wanted = (runner,) if runner else CONSUMERS
        return [
            i for i, step in enumerate(self.steps) if any(r in step.get("run", "") for r in wanted)
        ]

    def test_both_consumers_run_in_the_job_that_builds_the_archives(self):
        for runner in CONSUMERS:
            assert self.consumer_indices(runner), (
                f"build-linux never runs {runner}, so every archive it publishes has "
                "been linked and never loaded. musl/x64 is the cell that has nowhere "
                "else to be covered"
            )

    def test_nothing_conditions_the_consumers_onto_a_subset_of_the_matrix(self):
        for i in self.consumer_indices():
            step = self.steps[i]
            assert "if" not in step, (
                f"{step.get('name')} carries an `if:`, so which cells get run rather "
                "than only linked is a second copy of the matrix that can drift from "
                "the matrix"
            )

    def test_the_musl_x64_cell_exists_to_be_covered(self):
        cells = {(c["platform"], c["cpu"]) for c in matrix_cells(self.job)}
        assert ("musl", "x64") in cells, (
            f"build-linux builds {sorted(cells)}. The gap this class is about is "
            "musl/x64, and it has to be in this matrix for the steps above to reach it"
        )

    def test_the_consumers_are_pointed_at_the_archive_the_cell_unpacked(self):
        lib_dir = self.job_env("VIPRS_LIB_DIR")
        assert lib_dir, (
            "build-linux names no VIPRS_LIB_DIR, so the consumers fall back to a "
            "publish directory this job never wrote"
        )
        assert "unpacked" in lib_dir, (
            f"VIPRS_LIB_DIR is {lib_dir!r}, which is not an unpacked archive. The "
            "premise is that a consumer links what we publish"
        )
        for key in ("${{ matrix.platform }}", "${{ matrix.cpu }}"):
            assert key in lib_dir, (
                f"VIPRS_LIB_DIR is {lib_dir!r} and does not carry {key}, so one cell "
                "could run its consumers against another cell's archive"
            )

    def test_the_archive_is_unpacked_before_a_consumer_looks_for_it(self):
        unpack = step_index(self.job, "Unpack")
        assert unpack >= 0, "build-linux never unpacks the archive it just verified"
        first = min(self.consumer_indices())
        assert unpack < first, "the consumers run before anything unpacked an archive for them"

    def test_verify_runs_before_the_consumers_and_upload_after_them(self):
        verify = step_index(self.job, "Verify")
        upload = step_index(self.job, "Upload release")
        for i in self.consumer_indices():
            assert verify < i < upload, (
                f"{self.steps[i].get('name')} sits outside Verify..Upload. A consumer "
                "run after the upload reports on an archive that is already published, "
                "and one before Verify runs against an archive nothing has checked"
            )

    def test_every_cell_names_a_container_whose_libc_is_the_cell_s(self):
        for cell in matrix_cells(self.job):
            image = cell.get("image", "")
            assert image, (
                f"{cell} names no image, so its consumers fall back to "
                f"{LOCAL_ONLY_IMAGE}, which exists on one developer's machine"
            )
            assert image.endswith("-alpine") == (cell["platform"] == "musl"), (
                f"{cell} runs its consumers in {image!r}. A musl library asks for "
                "/lib/ld-musl-*.so.1 and a glibc container has no such loader, so this "
                "is the difference between running the library and failing to"
            )

    def test_the_job_takes_that_image_from_the_cell(self):
        assert self.job_env("VIPRS_CONFORMANCE_IMAGE") == "${{ matrix.image }}", (
            "build-linux does not read the image out of its matrix cell, so the key "
            "the assertion above reads is decorative"
        )

    def test_both_libcs_run_the_same_toolchain(self):
        # Same argument acadsharp-conformance.yml makes one job over: if the
        # two containers differ by compiler version as well as by libc, a check
        # that passes on one side and fails on the other stops saying which of
        # the two it was.
        images = {c.get("image", "") for c in matrix_cells(self.job)}
        musl = {i for i in images if i.endswith("-alpine")}
        glibc = images - musl
        assert musl and glibc, f"the cells name {sorted(images)}, which is not two libcs"
        assert {f"{i}-alpine" for i in glibc} == musl, (
            f"{sorted(glibc)} and {sorted(musl)} are not one image in two libcs"
        )

    def test_the_image_is_pinned_and_is_not_the_local_default(self):
        for cell in matrix_cells(self.job):
            image = cell.get("image", "")
            assert image != LOCAL_ONLY_IMAGE, (
                f"{LOCAL_ONLY_IMAGE} is the runners' local fallback and no registry "
                "serves it, so every consumer step would fail on the pull"
            )
            tag = image.rsplit(":", 1)[-1] if ":" in image else ""
            assert tag and tag != "latest", (
                f"{image} floats, so a cell that passed today and fails tomorrow says "
                "nothing about the change that was pushed"
            )

    def test_the_container_architecture_follows_the_cell(self):
        # `--platform` is not optional and it is not guessable: the runners
        # here are two architectures and an unpinned run on the wrong one
        # either emulates or fails at the load. matrix.arch already speaks
        # docker's vocabulary, so it is read rather than restated.
        assert self.job_env("VIPRS_CONFORMANCE_PLATFORM") == "linux/${{ matrix.arch }}", (
            "build-linux does not take the container architecture from the cell it is "
            "building, so an arm64 cell can run its consumers on an x64 image"
        )


class TestTheMacCellLinksWhatItPublishes:
    """The gap that shipped #95, held shut.

    `build-linux` runs two consumers against the tarball it is about to
    upload. `build-mac` ran none, and every check it did have passed on an
    archive no consumer could link: the build's shared smoke is `dlopen`
    on an absolute path and `dlopen` never consults `LC_ID_DYLIB`, and
    `verify_archive.sh`'s link-and-run probe was gated on
    `static_certified`, which is false on mac and is not going to change.
    So the one target where shared is the only link mode was the one
    target where nothing linked anything, five releases running.

    It cannot run the linux consumers: both go through `docker run`, and
    macos-15 runners have no docker. What it can run is the cargo recipe
    MANUAL.md documents, which needs cargo and python3 and nothing else,
    and which is the path a consumer actually takes.

    `verify_archive.sh` now runs that recipe for a shared-only archive as
    well, when the host matches and `VIPRS_REQUIRE_LINK_TEST=1` is set,
    which is why this cell's Verify step goes through
    `verify_archive_matched_host.sh`. This step stays anyway: it is the
    one that links the unpacked tree between Verify and Upload, and the
    premise of the epic is that a check living in exactly one lane is how
    #95 shipped.
    """

    SMOKE = "acadsharp/scripts/link_consumer_smoke.sh"

    def setup_method(self):
        self.wf = load_workflow()
        self.job = self.wf["jobs"]["build-mac"]
        self.steps = self.job["steps"]

    def test_it_links_the_archive_through_the_documented_recipe(self):
        step = step_named(self.job, "Link the unpacked archive")
        assert self.SMOKE in step.get("run", ""), (
            "build-mac's consumer step does not call "
            f"{self.SMOKE}, which is the only thing in this repository that links a "
            "shared-only archive and runs the binary"
        )

    def test_it_links_the_unpacked_tarball_and_not_the_build_tree(self):
        unpack = step_index(self.job, "Unpack")
        link = step_index(self.job, "Link the unpacked archive")
        assert unpack >= 0, "build-mac never unpacks the archive it just verified"
        assert unpack < link, "the consumer runs before anything unpacked an archive for it"
        run = self.steps[link].get("run", "")
        assert "bin/unpacked/" in run, (
            f"build-mac's consumer step reads {run!r}. The premise of the epic is that "
            "a consumer links what we publish, and the build tree is not what anybody "
            "downloads"
        )

    def test_it_names_the_cell_rather_than_globbing(self):
        run = step_named(self.job, "Link the unpacked archive").get("run", "")
        for key in ("${{ matrix.platform }}", "${{ matrix.cpu }}"):
            assert key in run, (
                f"build-mac's consumer step does not carry {key}, so it could link an "
                "archive this cell did not build"
            )
        assert "*" not in run, (
            "a glob that matches nothing links nothing and still exits 0, which is a "
            "pass reported for a check that never ran"
        )

    def test_it_sits_between_verify_and_upload(self):
        verify = step_index(self.job, "Verify")
        upload = step_index(self.job, "Upload release")
        link = step_index(self.job, "Link the unpacked archive")
        assert verify < link < upload, (
            f"Verify/Link/Upload are at {verify}/{link}/{upload}. An archive that "
            "cannot be linked has to stay off the release page, not be explained "
            "afterwards"
        )

    def test_nothing_conditions_it_away(self):
        for name in ("Unpack", "Link the unpacked archive"):
            assert "if" not in step_named(self.job, name), (
                f"build-mac's {name!r} step carries an `if:`, so whether the archive "
                "gets linked is a second decision that can drift from the matrix"
            )

    def test_the_script_it_calls_exists_and_is_the_consumer_path(self):
        # A workflow naming a script that is not there fails on the runner,
        # by which time five jobs have claimed machines.
        path = os.path.join(REPO_ROOT, self.SMOKE)
        assert os.path.isfile(path), f"{self.SMOKE} is not in the tree"
        with open(path) as f:
            body = f.read()
        assert "link_consumer" in body and "cargo run" in body, (
            f"{self.SMOKE} no longer links the two-crate workspace and runs the "
            "binary, so the step above is not the check this class is about"
        )


class TestADispatchCannotBuildSomethingTheConsumersDoNotExpect:
    """The one way the runs above could be asked an unanswerable question.

    Both conformance runners read the expected upstream version out of
    `acadsharp/VERSION` and hold the library's capability string to it, which
    is right for a push: the archive in front of them was built from that
    file. This job can be dispatched with a version input instead, and the
    Build step honours it, so a dispatch whose upstream half differs from the
    file would build one ACadSharp and then run a consumer expecting another.

    It cannot happen today, and that is a fact about the pin table rather
    than about this workflow: the preflight refuses any version whose
    upstream has no SOURCE_SHA256 entry, and there is exactly one entry. So
    the coupling is pinned here instead of papered over, and it fails on the
    day a second pin lands, which is the day it would start to matter.
    """

    def test_only_the_upstream_the_consumers_expect_can_be_built(self):
        with open(os.path.join(ACADSHARP_DIR, "VERSION")) as f:
            upstream, _shim = ba.split_version(f.read().strip())
        pinned = set(ba.SOURCE_SHA256)
        assert pinned == {upstream}, (
            f"acadsharp/VERSION is {upstream} and the driver pins {sorted(pinned)}. A "
            "dispatch can now name an upstream the conformance consumers in this same "
            "job do not expect: they read acadsharp/VERSION and the Build step takes "
            "--version. Hand the runners the resolved version before adding the pin, "
            "or a release cut with the input set fails at a consumer for a reason "
            "that has nothing to do with the archive."
        )


class TestTheLibcOfTheLibraryUnderTestIsProved:
    """A green consumer run does not say which library it ran against.

    A musl .so and a glibc .so are both ELF, `find_shim.sh` finds either, and
    a consumer linked against either reports the same passes. So before the
    consumers there is a step that reads the library's own NEEDED entries,
    which is the linker's statement about its libc, and refuses anything that
    is not the libc this cell is about.

    The decision lives in a script rather than in the workflow, so the guard
    can be run, and `test_conformance_workflow.py` runs it both ways.
    """

    def setup_method(self):
        self.wf = load_workflow()
        self.job = self.wf["jobs"]["build-linux"]

    def control_index(self):
        for i, step in enumerate(self.job.get("steps", [])):
            if LIBC_CONTROL in step.get("run", ""):
                return i
        return -1

    def test_the_control_is_there(self):
        assert self.control_index() >= 0, (
            f"nothing in build-linux calls {LIBC_CONTROL}, so a cell that silently ran "
            "a library of the other libc would look exactly like one that did not"
        )

    def test_it_runs_before_the_consumers_it_is_vouching_for(self):
        control = self.control_index()
        steps = self.job.get("steps", [])
        consumers = [
            i for i, step in enumerate(steps) if any(r in step.get("run", "") for r in CONSUMERS)
        ]
        assert consumers, "there are no consumer runs for the control to vouch for"
        assert 0 <= control < min(consumers), (
            "the libc control runs after the consumers, so it reports on a run that "
            "has already happened"
        )

    def test_it_reads_the_library_the_consumers_will_load(self):
        step = self.job["steps"][self.control_index()]
        body = step.get("run", "")
        assert "find_shim.sh" in body, (
            "the control picks the library by hand rather than through find_shim.sh, "
            "so it can vouch for a file the consumers do not load"
        )
        assert "readelf" in body, (
            "the control does not read the library's own NEEDED entries, which is the "
            "only statement about its libc that the linker wrote"
        )

    def test_it_asks_about_the_cell_it_is_in(self):
        body = self.job["steps"][self.control_index()].get("run", "")
        assert "${{ matrix.platform }}" in body or "matrix.platform" in str(
            self.job["steps"][self.control_index()].get("env", {})
        ), (
            "the control does not take the platform from the cell, so it asks the same "
            "question of every cell and two of them are a different libc"
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
        # archive filename and the verifier's third argument use. Anything
        # else and the workflow verifies and uploads a path that does not
        # exist.
        for cell in self.cells:
            assert cell["cpu"] in ("x64", "arm64"), (
                f"{cell} names a cpu the archive naming convention does not use"
            )
            assert cell["platform"] in ("linux", "musl", "mac"), (
                f"{cell} names a platform the archive naming convention does not use"
            )

    def test_every_cell_carries_both_vocabularies_and_they_agree(self):
        # A cell says `arch: amd64` to the driver and `cpu: x64` to the
        # verifier and the archive name. Nothing in the workflow ties
        # those together, so a cell with `arch: arm64, cpu: x64` would
        # build one target and then verify and upload another, under a
        # name that the build never produced. The driver's ARCH_ALIASES
        # would happily accept `--arch x64` and hide the whole problem,
        # which is the reason both are spelled out rather than derived.
        for cell in self.cells:
            assert "arch" in cell, f"{cell} has no arch, so the Build step has nothing to pass"
            assert cell["arch"] in CPU_FOR_ARCH, (
                f"{cell} names an arch the drivers' CLI does not take"
            )
            assert CPU_FOR_ARCH[cell["arch"]] == cell["cpu"], (
                f"{cell} builds {cell['arch']} and then names the archive "
                f"{cell['cpu']}, so it would verify and upload a target it did not build"
            )

    def test_the_build_step_selects_the_cell_by_arch(self):
        # The flag the driver takes is --arch. It was --cpu here, which
        # the driver rejects, and nothing in this file noticed until
        # build_acadsharp.py grew a CLI to be checked against.
        wf = load_workflow()
        for name in BUILD_JOBS:
            job = wf["jobs"][name]
            run = job["steps"][step_index(job, "Build")].get("run", "")
            assert "--arch ${{ matrix.arch }}" in run, (
                f"{name}'s Build step does not select its cell with --arch ${{{{ matrix.arch }}}}"
            )
            assert "--cpu" not in run, (
                f"{name}'s Build step passes --cpu, which build_acadsharp.py, "
                "build_zstd.py and build_pdfium.py all reject"
            )

    def test_the_build_step_names_the_resolved_version(self):
        # The dispatch input is honoured by resolve-version, the tag and
        # the notes. It has to be honoured by the thing that actually
        # produces the archive too, or an override publishes archives
        # built from the committed VERSION under a different tag, with
        # LINKINFO.json and the release page disagreeing.
        wf = load_workflow()
        for name in BUILD_JOBS:
            job = wf["jobs"][name]
            step = job["steps"][step_index(job, "Build")]
            run = step.get("run", "")
            assert "--version" in run, (
                f"{name}'s Build step does not pass --version, so it builds whatever "
                "acadsharp/VERSION says regardless of what was dispatched"
            )
            assert "$VERSION" in run, (
                f"{name}'s Build step passes a version that is not the one resolve-version computed"
            )
            assert step.get("env", {}).get("VERSION", "").strip() == (
                "${{ needs.resolve-version.outputs.version }}"
            ), (
                f"{name}'s VERSION must come from resolve-version, not from a second "
                "reading of the file"
            )

    def test_the_archive_name_is_built_from_the_cell(self):
        # Not five literal filenames: the name the build produces, the
        # name the verifier is handed and the name that is uploaded all
        # come from the same two matrix keys, so a cell can never verify
        # one archive and upload another.
        templated = TAG_PREFIX + "${{ matrix.platform }}-${{ matrix.cpu }}.tgz"
        assert templated in workflow_text(), (
            f"no step names {templated}, so the archive name is typed somewhere "
            "rather than derived from the matrix cell"
        )

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
        # release-zstd.yml registers an emulator and builds foreign-arch
        # cells under it. ADR 0001 rules that out here, and this workflow
        # is a copy of that one, so the guard is against inheriting it.
        # Comments are dropped first: the workflow explains the decision
        # in prose and would otherwise fail its own check.
        blob = workflow_code().lower()
        for word in ("setup-qemu", "binfmt", "qemu"):
            assert word not in blob, (
                f"release-acadsharp.yml runs {word!r}. ADR 0001 decides native runners "
                "per architecture because the .NET runtime documents qemu-user-static "
                "as unsupported"
            )

    def test_the_workflow_records_why_it_does_not_emulate(self):
        # The guard above reads code only, so without this the rationale
        # could be deleted and nothing would notice. The next person to
        # copy release-zstd.yml needs to find the reason in the file.
        assert "qemu" in workflow_text().lower(), (
            "nothing in release-acadsharp.yml says why it does not emulate, so the "
            "next copy of release-zstd.yml will bring the emulator back"
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

    def test_the_notes_job_knows_every_matrix_cell_and_whose_job_it_is(self):
        # release-notes has no strategy of its own, and a job cannot read
        # the matrix cells of the jobs it waits on, so its TARGETS list is
        # the one place this workflow repeats its own matrix. That copy
        # gets checked rather than trusted: a cell added to a build job
        # and not to TARGETS would drop silently out of the notes, which
        # is the failure mode the whole job exists to prevent.
        step = step_named(self.notes, "Write the release notes")
        listed = set()
        for line in step["env"]["TARGETS"].split("\n"):
            parts = line.split()
            if not parts:
                continue
            assert len(parts) == 3, f"TARGETS line {line!r} is not `platform cpu job`"
            listed.add(tuple(parts))

        in_matrix = set()
        for name in BUILD_JOBS:
            for cell in matrix_cells(self.wf["jobs"][name]):
                in_matrix.add((cell["platform"], cell["cpu"], name))

        assert listed == in_matrix, (
            "release-notes' TARGETS list and the build matrices disagree.\n"
            f"  only in TARGETS: {sorted(listed - in_matrix)}\n"
            f"  only in the matrix: {sorted(in_matrix - listed)}"
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
    """The AC10xx read range, out of the shim's own constants.

    Not out of the header, which never states it: the range is a fact
    about the backing reader, `viprs_acad_get_capabilities_v1` answers with it
    at run time, and the build records what the library answered in each
    archive's LINKINFO.json. This reads `AbiConstants` only so the test
    below has a number to look for in the workflow, which is the one
    place it must never appear.
    """
    with open(os.path.join(ACADSHARP_DIR, "native", "Abi.cs")) as f:
        shim = f.read()
    codes = []
    for name in ("DwgVersionMin", "DwgVersionMax"):
        match = re.search(rf"{name}\s*=\s*(\d+)u", shim)
        assert match, f"AbiConstants no longer declares {name}"
        codes.append(match.group(1))
    return tuple(codes)


class TestTheNotesDoNotReadTheHeadersProse:
    """The preamble used to pull the read range out of an English sentence
    in the header with a regex, and a test in this file pinned that
    wording, so rephrasing a comment in a frozen contract document broke a
    release. The range is not in the header at all now: it is measured off
    the library during the build and recorded in every archive's
    LINKINFO.json, which is where the notes read it."""

    def test_the_workflow_no_longer_matches_on_the_headers_wording(self):
        assert "numeric AC10xx codes" not in workflow_text(), (
            "the workflow is matching prose in viprs_acadsharp.h again. A comment "
            "rewritten in a contract document is not a release failure, and the "
            "range the notes report has to be the one the archives carry"
        )

    def test_the_range_is_read_out_of_the_manifests(self):
        run = step_named(load_workflow()["jobs"]["release-notes"], "Write the release notes")["run"]
        assert "dwg_version_min" in run and "dwg_version_max" in run, (
            "the notes never read the read range out of the archives, so the release "
            "page says nothing about what the thing it is publishing reads"
        )


class TestTheInlineGeneratorsRun:
    """The notes are produced by Python that lives inside the workflow,
    so nothing compiles it until a release run does and a typo would be
    found on the release page. These compile all of it and run the two
    scripts that write the notes, against this tree, with no network and
    no runner."""

    def test_every_inline_script_compiles(self):
        blocks = inline_python_blocks()
        assert len(blocks) >= 4, (
            f"only {len(blocks)} inline scripts found, so this guard is reading "
            "less of the workflow than it thinks"
        )
        for i, code in enumerate(blocks, 1):
            compile(code, f"<release-acadsharp.yml block {i}>", "exec")

    def test_the_preamble_generator_produces_the_pins_it_promises(self):
        # Not just "it runs": the numbers it prints have to be the ones
        # in the manifests. This is the test that would have caught the
        # header regex silently matching nothing.
        version = ba.read_version()
        upstream, _ = ba.split_version(version)
        with open(GLOBAL_JSON) as f:
            sdk = json.load(f)["sdk"]["version"]

        done = subprocess.run(
            [sys.executable, "-c", notes_generator("Generated from acadsharp/VERSION"), version],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
        )
        assert done.returncode == 0, f"the preamble generator failed:\n{done.stderr}"
        notes = done.stdout
        for expected in (
            upstream,
            sdk,
            ba.source_sha256(upstream),
            ba.CSUTILITIES_COMMIT,
        ):
            assert expected in notes, (
                f"the generated preamble never mentions {expected!r}:\n{notes}"
            )

    def test_the_notes_generator_names_every_target_that_did_not_publish(self, tmp_path):
        # Run it against an empty assets directory, which is what a run
        # where every cell failed leaves behind. All five targets have to
        # be named, each with the job that should have produced it. A
        # release that quietly ships four of five is the failure this
        # whole job exists to prevent, and asserting on the workflow's
        # text alone would never have proved the loop reaches every cell.
        wf = load_workflow()
        step = step_named(wf["jobs"]["release-notes"], "Write the release notes")
        env = notes_env(step, ba.read_version())
        (tmp_path / "assets").mkdir()

        done = subprocess.run(
            [sys.executable, "-c", notes_generator("did not publish")],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=env,
            check=False,
        )
        assert done.returncode == 0, f"the notes generator failed:\n{done.stderr}"
        notes = done.stdout
        for platform, cpu in ALL_CELLS:
            assert f"`{platform}/{cpu}` did not publish" in notes, (
                f"{platform}/{cpu} published nothing and the notes never say so:\n{notes}"
            )
        for job in BUILD_JOBS:
            assert f"`{job}`" in notes, f"the notes never name the job {job}"
        assert "Nothing published." in notes

    def test_the_notes_generator_reports_what_did_publish(self, tmp_path):
        # The other half: a real archive laid out the way issue #48
        # freezes it, so the LINKINFO read and the digest are exercised
        # rather than assumed.
        wf = load_workflow()
        step = step_named(wf["jobs"]["release-notes"], "Write the release notes")
        version = ba.read_version()

        platform, cpu = ALL_CELLS[0]
        stem, tgz = stage_archive(
            tmp_path,
            platform,
            cpu,
            artifact_version=version,
            target="x86_64-unknown-linux-gnu",
            static_certified=True,
            acadsharp_commit="d7dc111023477d8a9fffc2153139459c95b4f345",
        )
        digest = hashlib.sha256(tgz.read_bytes()).hexdigest()

        env = notes_env(step, version)
        done = subprocess.run(
            [sys.executable, "-c", notes_generator("did not publish")],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=env,
            check=False,
        )
        assert done.returncode == 0, f"the notes generator failed:\n{done.stderr}"
        notes = done.stdout
        assert f"`{stem}.tgz`" in notes
        assert f"`{digest}`" in notes, f"the archive's own sha256 is not in the notes:\n{notes}"
        assert "x86_64-unknown-linux-gnu" in notes
        assert "| true |" in notes, f"static_certified was not reported:\n{notes}"
        assert "d7dc111023477d8a9fffc2153139459c95b4f345" in notes
        assert f"`{platform}/{cpu}` did not publish" not in notes

    def test_an_archive_built_as_another_version_is_refused(self, tmp_path):
        # The dispatch bug's second half. Even with --version passed, an
        # archive can reach the release page carrying a LINKINFO that
        # disagrees with the tag: a re-run against a moved VERSION, a
        # stale asset a --clobber did not replace, a cell that resolved
        # differently. A release whose assets disagree with its own tag
        # is worse than one that fails, because it looks right.
        wf = load_workflow()
        step = step_named(wf["jobs"]["release-notes"], "Write the release notes")
        version = ba.read_version()

        platform, cpu = ALL_CELLS[0]
        stem, _ = stage_archive(
            tmp_path,
            platform,
            cpu,
            artifact_version="3.7.1-viprs.0",
            target="x86_64-unknown-linux-gnu",
            static_certified=True,
        )
        done = subprocess.run(
            [sys.executable, "-c", notes_generator("did not publish")],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=notes_env(step, version),
            check=False,
        )
        assert done.returncode == 0, f"the notes generator failed:\n{done.stderr}"
        notes = done.stdout

        assert "3.7.1-viprs.0" in notes and version in notes, (
            f"the notes must name both the version built and the version this release is:\n{notes}"
        )
        assert f"`{stem}.tgz`" in notes
        assert "| true |" not in notes, (
            f"a refused archive is still being reported as published:\n{notes}"
        )
        assert "Every target published." not in notes

        refused = (tmp_path / "refused-assets.txt").read_text().split()
        assert refused == [f"{stem}.tgz"], (
            f"the refusal list the shell acts on says {refused}, so the asset would "
            "stay on the release"
        )

    def test_an_archive_with_no_recorded_version_is_refused(self, tmp_path):
        # "Cannot be checked" is not "checked and fine". An archive whose
        # LINKINFO carries no artifact_version at all gets the same
        # refusal as one that disagrees.
        wf = load_workflow()
        step = step_named(wf["jobs"]["release-notes"], "Write the release notes")
        platform, cpu = ALL_CELLS[0]
        stem, _ = stage_archive(tmp_path, platform, cpu, target="x86_64-unknown-linux-gnu")

        done = subprocess.run(
            [sys.executable, "-c", notes_generator("did not publish")],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=notes_env(step, ba.read_version()),
            check=False,
        )
        assert done.returncode == 0, f"the notes generator failed:\n{done.stderr}"
        assert "no artifact_version" in done.stdout, (
            f"an archive with no recorded version was not refused:\n{done.stdout}"
        )
        assert (tmp_path / "refused-assets.txt").read_text().split() == [f"{stem}.tgz"]

    def test_the_notes_report_the_range_the_archives_carry(self, tmp_path):
        # Every archive is asked what it reads during its own build, and
        # the answer is in its manifest. The notes say what that was, so
        # the release page's claim about the read range is a measurement
        # of the five things on the page rather than a sentence somebody
        # kept up to date.
        wf = load_workflow()
        step = step_named(wf["jobs"]["release-notes"], "Write the release notes")
        version = ba.read_version()
        for platform, cpu in ALL_CELLS:
            stage_archive(
                tmp_path,
                platform,
                cpu,
                artifact_version=version,
                target="x86_64-unknown-linux-gnu",
                dwg_version_min=1014,
                dwg_version_max=1032,
            )

        done = subprocess.run(
            [sys.executable, "-c", notes_generator("did not publish")],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=notes_env(step, version),
            check=False,
        )
        assert done.returncode == 0, f"the notes generator failed:\n{done.stderr}"
        notes = done.stdout
        assert "AC1014 to AC1032" in notes, f"the notes never state the read range:\n{notes}"
        assert not (tmp_path / "range-disagreement.txt").read_text().strip(), (
            "five archives that agree were reported as disagreeing"
        )

    def test_archives_that_disagree_about_the_range_fail_the_run(self, tmp_path):
        # Five archives are five builds, and a release whose targets do
        # not read the same formats is not one artifact in five shapes.
        # No asset is removed: with five manifests disagreeing there is no
        # way to say which one is wrong, so the run fails and the page
        # says what was found.
        wf = load_workflow()
        step = step_named(wf["jobs"]["release-notes"], "Write the release notes")
        version = ba.read_version()
        for i, (platform, cpu) in enumerate(ALL_CELLS):
            stage_archive(
                tmp_path,
                platform,
                cpu,
                artifact_version=version,
                target="x86_64-unknown-linux-gnu",
                dwg_version_min=1014,
                dwg_version_max=1032 if i else 1035,
            )

        done = subprocess.run(
            [sys.executable, "-c", notes_generator("did not publish")],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=notes_env(step, version),
            check=False,
        )
        assert done.returncode == 0, f"the notes generator failed:\n{done.stderr}"
        notes = done.stdout
        assert "1035" in notes and "1032" in notes, (
            f"the notes have to name both ranges that were found:\n{notes}"
        )
        assert (tmp_path / "range-disagreement.txt").read_text().strip(), (
            "nothing tells the shell to fail the run, so a release whose archives "
            "disagree about what they read would publish green"
        )

    def test_an_archive_with_no_recorded_range_is_reported(self, tmp_path):
        # "Cannot be checked" is not "checked and fine", the same rule
        # artifact_version already has.
        wf = load_workflow()
        step = step_named(wf["jobs"]["release-notes"], "Write the release notes")
        version = ba.read_version()
        platform, cpu = ALL_CELLS[0]
        stage_archive(
            tmp_path,
            platform,
            cpu,
            artifact_version=version,
            target="x86_64-unknown-linux-gnu",
            dwg_version_min=None,
            dwg_version_max=None,
        )

        done = subprocess.run(
            [sys.executable, "-c", notes_generator("did not publish")],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=notes_env(step, version),
            check=False,
        )
        assert done.returncode == 0, f"the notes generator failed:\n{done.stderr}"
        assert "no read range" in done.stdout, (
            f"an archive recording no read range was not reported:\n{done.stdout}"
        )
        assert (tmp_path / "range-disagreement.txt").read_text().strip()

    def test_one_archive_with_no_range_beside_archives_that_have_one(self, tmp_path):
        # The mixed case, which is the one that actually happens: four
        # cells rebuilt and one stale asset a --clobber did not replace.
        # The two kinds of key have to be orderable together, and in
        # Python a tuple holding None does not compare with a tuple
        # holding integers.
        wf = load_workflow()
        step = step_named(wf["jobs"]["release-notes"], "Write the release notes")
        version = ba.read_version()
        for i, (platform, cpu) in enumerate(ALL_CELLS):
            stage_archive(
                tmp_path,
                platform,
                cpu,
                artifact_version=version,
                target="x86_64-unknown-linux-gnu",
                dwg_version_min=None if i == 2 else 1014,
                dwg_version_max=None if i == 2 else 1032,
            )

        done = subprocess.run(
            [sys.executable, "-c", notes_generator("did not publish")],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=notes_env(step, version),
            check=False,
        )
        assert done.returncode == 0, f"the notes generator failed:\n{done.stderr}"
        notes = done.stdout
        assert "no read range" in notes and "AC1014 to AC1032" in notes, (
            f"the notes have to name both what was found and what was missing:\n{notes}"
        )
        assert (tmp_path / "range-disagreement.txt").read_text().strip()

    def test_a_range_disagreement_fails_the_run_after_the_notes_go_up(self):
        run = step_named(load_workflow()["jobs"]["release-notes"], "Write the release notes")["run"]
        assert "range-disagreement.txt" in run, (
            "the generator classifies and the shell acts; without this the "
            "disagreement is a paragraph in the notes and a green run"
        )
        # rindex: the generator writes the file and the shell reads it, so
        # the name appears twice and it is the second one that has to come
        # after the notes are published.
        assert run.index("gh release edit") < run.rindex("range-disagreement.txt"), (
            "the notes have to go up before the step fails, or the release page says "
            "nothing about why the run is red"
        )

    def test_a_refused_archive_is_taken_off_the_release_and_fails_the_run(self):
        # The generator only classifies. The shell around it is what
        # removes the asset and fails the run, so that is asserted too:
        # writing "refused" into the notes and leaving the asset up would
        # be the worst of both.
        wf = load_workflow()
        run = step_named(wf["jobs"]["release-notes"], "Write the release notes")["run"]
        assert "refused-assets.txt" in run
        assert "gh release delete-asset" in run, (
            "a refused archive stays on the release page unless something removes it"
        )
        assert "exit 1" in run, (
            "a release carrying an archive that disagrees with its own tag must fail "
            "the run, not just mention it in the notes"
        )
        notes_i = run.index("gh release edit")
        delete_i = run.index("gh release delete-asset")
        assert notes_i < delete_i, (
            "the notes must go up before the assets come off, or a run that dies "
            "mid-deletion leaves a release page that never says what happened"
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

    def test_the_download_urls_are_all_one_release(self):
        linked = sorted(set(re.findall(rf"/releases/download/{TAG_PREFIX}(\S+?)/", self.readme)))
        assert len(linked) <= 1, (
            f"acadsharp/README.md links archives from {linked}. A download table mixing "
            "two releases is five digests a reader cannot tell apart by looking."
        )

    def test_a_table_behind_the_pinned_version_says_so(self):
        # The stale-version check, with the one honest exception it needs.
        #
        # It used to be "every download URL is the pinned version", which is
        # right until the day a shim revision is bumped and wrong from then
        # until the release is cut: `viprs.1` archives are what exists, and
        # deleting the table rather than explaining it hands a consumer
        # nothing. So the older table is allowed, and the price is a sentence
        # naming the pinned tag and saying it is not published, because a
        # table that is deliberately behind and a table nobody updated look
        # identical from the outside.
        version = ba.read_version()
        linked = sorted(set(re.findall(rf"/releases/download/{TAG_PREFIX}(\S+?)/", self.readme)))
        if not linked or linked == [version]:
            return
        # Hard wrapping collapsed first, so a reflow is not a failure.
        flat = re.sub(r"\s+", " ", self.readme)
        pattern = re.escape(f"{TAG_PREFIX}{version}") + r"[^.]{0,40}?" + NOT_CUT_MARKER
        assert re.search(pattern, flat), (
            f"acadsharp/README.md's download table is {linked[0]} and acadsharp/VERSION "
            f"says {version}, and nothing in the README says {TAG_PREFIX}{version} "
            f"{NOT_CUT_MARKER}. Either the table is stale, or it is deliberately the "
            "previous release and a reader has no way to tell which."
        )

    def test_readme_points_at_the_workflow_that_cuts_the_release(self):
        assert "release-acadsharp.yml" in self.readme, (
            "the README has to say how the archives it describes get published"
        )


class TestTheDriverSpeaksTheSameContract:
    """Everything above asserts against the contract issue #48 freezes.
    These re-assert the same values through ``build_acadsharp.py``, so a
    rename in the driver fails here rather than publishing five assets
    nobody links to.

    These used to skip when the driver had not grown the function yet,
    which was right while #48 was unlanded and wrong the moment it
    merged: a skip that can never fire again is a permanent silent pass,
    and it would have been sitting on the strongest cross-lane
    assertions in this file. The driver is reached by plain attribute
    access now, so a rename is an AttributeError rather than a shrug.
    """

    def test_release_tag_matches_the_workflow_prefix(self):
        version = ba.read_version()
        assert ba.release_tag(version) == f"{TAG_PREFIX}{version}"

    def test_archive_names_match_the_workflow(self):
        # archive_name takes the CLI's arch and returns the name's cpu.
        # It is the function that crosses between the two vocabularies,
        # so it is the one worth asking rather than assuming.
        for platform, arch in ALL_JOBS_EXPECTED:
            assert ba.archive_name(platform, arch) == expected_archive(platform, CPU_FOR_ARCH[arch])

    def test_the_default_matrix_matches_the_container_cells(self):
        assert sorted(tuple(cell) for cell in ba.DEFAULT_JOBS) == sorted(DEFAULT_JOBS_EXPECTED)

    def test_the_workflows_cells_are_what_the_driver_resolves(self):
        # The end-to-end version of the two above: hand the driver the
        # flags each cell actually passes and check it comes back with
        # that one cell. This is the assertion that would have caught
        # --cpu directly, rather than through the flag-name check.
        wf = load_workflow()
        cells = matrix_cells(wf["jobs"]["build-linux"]) + matrix_cells(wf["jobs"]["build-mac"])
        for cell in cells:
            got = ba.resolve_jobs([cell["platform"]], cell["arch"])
            assert got == [(cell["platform"], cell["arch"])], (
                f"the driver resolves --platform {cell['platform']} --arch "
                f"{cell['arch']} to {got}, not to that one cell"
            )

    def test_the_container_cells_are_what_the_driver_builds_by_default(self):
        assert ba.resolve_jobs(None, None) == DEFAULT_JOBS_EXPECTED

    @pytest.mark.parametrize("name", sorted(set(VERIFIER_FOR.values())))
    def test_the_verifier_the_workflow_calls_exists(self, name):
        script = os.path.join(REPO_ROOT, name)
        assert os.path.exists(script), f"the workflow calls {name}, which is not there"
        assert os.access(script, os.X_OK), f"{name} is not executable"

    def test_both_paths_to_this_tag_create_the_same_kind_of_release(self):
        # build_acadsharp.upload_release() publishes to the same tag from
        # a laptop. If one path marks the release and the other does not,
        # what a consumer resolving the tag sees depends on who cut it,
        # which is not something the release page records.
        from_laptop = "--prerelease" in inspect.getsource(ba.upload_release)
        from_ci = "--prerelease" in workflow_code()
        assert from_laptop == from_ci, (
            "upload_release() and release-acadsharp.yml disagree about whether this "
            f"release is a pre-release (driver: {from_laptop}, workflow: {from_ci}), so "
            "the kind of release depends on which one cut it"
        )

    def test_the_build_step_command_actually_plans_the_right_archive(self):
        # The strongest form of this seam, and the one that would have
        # caught --cpu on its own: take the Build step's real command
        # line, substitute the cell, and run it with --plan. That goes
        # through the driver's argparse and its own resolution, so a flag
        # it rejects, a cell it resolves to something else, an archive it
        # would name differently and a version it would ignore all fail
        # here. --plan prints the commands and stops, so nothing is built
        # and no container starts.
        wf = load_workflow()
        for name in BUILD_JOBS:
            job = wf["jobs"][name]
            for cell in matrix_cells(job):
                argv = build_command_for(job, cell, ba.read_version())
                assert not any("${{" in a for a in argv), (
                    f"{name}: {argv} still carries an unsubstituted expression, so "
                    "this check is running something the workflow does not"
                )
                done = subprocess.run(
                    [sys.executable, *argv[1:], "--plan"],
                    capture_output=True,
                    text=True,
                    cwd=REPO_ROOT,
                    check=False,
                )
                assert done.returncode == 0, (
                    f"{name} {cell['platform']}/{cell['cpu']}: the driver refused "
                    f"{' '.join(argv[1:])}\n{done.stdout}{done.stderr}"
                )
                wanted = expected_archive(cell["platform"], cell["cpu"])
                assert wanted in done.stdout, (
                    f"{name} {cell['platform']}/{cell['cpu']}: the driver plans "
                    f"something other than {wanted}, which is the archive this cell "
                    f"goes on to verify and upload\n{done.stdout}"
                )

    def test_an_overridden_version_reaches_the_build(self):
        # The dispatch input said it overrode acadsharp/VERSION and only
        # the tag believed it: both build jobs set env VERSION and never
        # read it, so an override published archives built from the
        # committed version under a different tag.
        #
        # Two runs, because one proves nothing. A driver that ignores
        # --version plans happily for any override, so the override that
        # must FAIL is the real control: 9.9.9 has no SOURCE_SHA256 entry,
        # so a driver reading the flag refuses it, and a driver ignoring
        # the flag builds the committed version and exits 0. The pinned
        # override then has to be accepted, or the first result could just
        # be a driver that refuses everything.
        #
        # Neither run may fail with argparse's "unrecognized arguments",
        # which names the value it rejected and so would otherwise look
        # exactly like the flag being honoured.
        pinned = "3.7.1-viprs.0"
        unpinned = "9.9.9-viprs.7"
        assert pinned != ba.read_version() and unpinned != ba.read_version()

        wf = load_workflow()
        for name in BUILD_JOBS:
            job = wf["jobs"][name]
            cell = matrix_cells(job)[0]

            assert "$VERSION" in job["steps"][step_index(job, "Build")]["run"], (
                f"{name}'s Build step never names the resolved version, so a "
                "dispatched override would tag one version and build another"
            )

            def plan(version, job=job, cell=cell):
                argv = build_command_for(job, cell, version)
                assert version in argv, f"{version} did not reach the command line"
                return subprocess.run(
                    [sys.executable, *argv[1:], "--plan"],
                    capture_output=True,
                    text=True,
                    cwd=REPO_ROOT,
                    check=False,
                )

            refused = plan(unpinned)
            assert "unrecognized arguments" not in refused.stderr, (
                f"{name}: {DRIVER} does not take --version, so the Build step's "
                f"command does not run at all\n{refused.stderr}"
            )
            assert refused.returncode != 0, (
                f"{name}: the driver planned a build for {unpinned}, which has no "
                "pinned source digest. It is ignoring --version and building "
                f"whatever acadsharp/VERSION says\n{refused.stdout}"
            )

            accepted = plan(pinned)
            assert "unrecognized arguments" not in accepted.stderr, accepted.stderr
            assert accepted.returncode == 0, (
                f"{name}: the driver refused {pinned}, whose upstream half is pinned. "
                f"The refusal above was not about the version\n"
                f"{accepted.stdout}{accepted.stderr}"
            )

    def test_the_build_step_passes_flags_the_driver_accepts(self):
        # The workflow selects a cell with --platform/--arch and names the
        # version with --version. This is the cheap form of the checks
        # above: it compares flag names rather than running anything, so
        # it names the offending flag directly when they fail together.
        help_text = driver_help()
        wf = load_workflow()
        for name in BUILD_JOBS:
            job = wf["jobs"][name]
            run = job["steps"][step_index(job, "Build")].get("run", "")
            for flag in sorted(set(re.findall(r"(?<!\S)--[a-z][a-z0-9-]*", run))):
                assert flag in help_text, (
                    f"{name}'s Build step passes {flag}, which {DRIVER} does not accept"
                )


class TestTheTagPointsAtTheCommitThatBuiltIt:
    """`gh release create` with no `--target` tags the default branch.

    A dispatch runs the workflow from whatever ref it was launched on, so
    without this the archives come from that ref and the tag points at
    `main`'s head. Everything looks right and the tag is a lie, which is
    the worst shape a release can have: nothing fails, and the commit a
    consumer bisects to never produced those bytes.
    """

    def test_create_release_pins_the_target(self):
        with open(WORKFLOW_PATH) as f:
            text = f.read()

        # The header comment names the command too, so anchor on the
        # invocation rather than the first mention of it.
        at = text.index('gh release create "$TAG"')
        step = text[at : at + 300]

        assert "--target" in step, "the tag would default to the repository's default branch"
        assert "GITHUB_SHA" in step, "it has to be the commit this run built, not a branch name"


class TestTheLatestTagFollowsEveryFullyPublishedRelease:
    """A floating pointer for edge consumers, distinct from the pinned tag.

    `acadsharp-<version>` never moves once published, which is the whole
    point of a version. Something that always resolves to the newest
    build needs a tag of its own, and it needs to move only when every
    target published, because a "latest" that can point at a partial
    release is worse than none: a consumer tracking edge would silently
    start missing whichever target happened to fail.

    The moving itself is `tools/publish_latest_tag.sh`, shared with
    release-zstd.yml and release.yml, so this job's own body is thin: it
    only has to prove it calls the script with the right arguments and
    gates on the right condition. The script carries its own tests.
    """

    def setup_method(self):
        self.wf = load_workflow()
        assert "publish-latest" in self.wf["jobs"], (
            "release-acadsharp.yml has no publish-latest job"
        )
        self.job = self.wf["jobs"]["publish-latest"]

    def test_it_waits_on_both_build_matrices_and_the_notes(self):
        needs = self.job["needs"]
        for name in ("build-linux", "build-mac", "release-notes"):
            assert name in needs, f"publish-latest must wait on {name}"

    def test_it_only_moves_the_tag_when_every_target_published(self):
        cond = self.job.get("if", "")
        assert "needs.build-linux.result" in cond and "'success'" in cond, (
            "publish-latest has no if: gating on build-linux succeeding, so a "
            "partial release could still become latest"
        )
        assert "needs.build-mac.result" in cond, (
            "publish-latest has no if: gating on build-mac succeeding"
        )

    def test_it_calls_the_shared_script_with_this_dependencys_name_and_glob(self):
        run = run_text(self.job)
        assert LATEST_SCRIPT in run, (
            f"publish-latest does not call {LATEST_SCRIPT}, so this dependency's "
            "latest-moving logic is a second copy rather than the shared one"
        )
        assert f"{LATEST_SCRIPT} acadsharp " in run, (
            "the script needs the dependency name as its first argument, to know "
            "which tag to move and what to name the archives it looks for"
        )
        assert "acadsharp-*.tgz" in run, (
            "the script needs this dependency's own asset glob, not a hardcoded one"
        )

    def test_it_passes_the_tag_resolve_version_computed(self):
        step = step_named(self.job, "acadsharp-latest")
        assert step.get("env", {}).get("TAG") == "${{ needs.resolve-version.outputs.tag }}", (
            "the step must pass the tag resolve-version resolved, not a literal or a "
            "second reading of acadsharp/VERSION"
        )

    def test_it_has_contents_write(self):
        assert self.job.get("permissions", {}).get("contents") == "write", (
            "publish-latest calls `gh release`, so it needs permissions.contents: write"
        )

    def test_it_checks_out_the_repo_so_the_script_is_on_disk(self):
        uses = [step.get("uses", "") for step in self.job.get("steps", [])]
        assert any("actions/checkout" in u for u in uses), (
            "publish-latest never checks out the repo, so tools/publish_latest_tag.sh "
            "is not there to run"
        )
