"""CI has to find new dependency directories by itself.

Before this, `.github/workflows/ci.yml` and `pyproject.toml` named
`pdfium/` in every step, so `zstd/` would have landed lint-free,
test-free and shellcheck-free without anything going red. These tests
pin the fix: the paths CI checks are discovered from the tree, and any
attempt to hardcode one back in fails here.

They deliberately parse the workflow as text rather than with PyYAML —
the test job installs pytest and nothing else, and a guard that
`importorskip`s itself out of existence in CI is not a guard.
"""

import os
import re
import shutil
import subprocess
import sys

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CI_WORKFLOW = os.path.join(REPO_ROOT, ".github", "workflows", "ci.yml")
PYPROJECT = os.path.join(REPO_ROOT, "pyproject.toml")
HOOK_INSTALLER = os.path.join(REPO_ROOT, "tools", "install-hooks.sh")
SHELLCHECK_SCRIPT = os.path.join(REPO_ROOT, "tools", "shellcheck-all.sh")
README = os.path.join(REPO_ROOT, "README.md")


def dependency_dirs():
    """Every dependency directory in the repo, found the way CI has to.

    A dependency is a top-level directory with a build driver in it —
    pdfium/build_pdfium.py, zstd/build_zstd.py, and whatever comes next.
    """
    found = []
    for entry in sorted(os.listdir(REPO_ROOT)):
        path = os.path.join(REPO_ROOT, entry)
        if not os.path.isdir(path) or entry.startswith("."):
            continue
        if any(f.startswith("build_") and f.endswith(".py") for f in os.listdir(path)):
            found.append(entry)
    return found


def without_comments(path):
    """File contents with whole-line comments dropped.

    A check that greps a whole file for a forbidden string will fire on
    the prose explaining why the string is forbidden. Both this
    workflow and the hook installer talk about `pdfium/` in comments,
    so the comments have to come out before asserting on the code.
    """
    with open(path) as f:
        return "\n".join(
            line for line in f.read().splitlines() if not line.lstrip().startswith("#")
        )


def tracked_shell_scripts(glob="*.sh"):
    result = subprocess.run(
        ["git", "ls-files", glob],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("not a git checkout")
    return [line for line in result.stdout.splitlines() if line]


class TestDependencyDiscovery:
    def test_finds_both_known_dependencies(self):
        assert {"pdfium", "zstd"} <= set(dependency_dirs())

    def test_every_dependency_ships_tests(self):
        # "CI collects the whole repo" only means something if each
        # dependency actually has tests to collect.
        for dep in dependency_dirs():
            assert os.path.isdir(os.path.join(REPO_ROOT, dep, "tests")), (
                f"{dep}/ has a build driver but no tests/ directory"
            )


class TestWorkflowIsPathAgnostic:
    def test_no_step_hardcodes_a_dependency_directory(self):
        code = without_comments(CI_WORKFLOW)
        for dep in dependency_dirs():
            assert f"{dep}/" not in code, (
                f"ci.yml names {dep}/ directly. Adding a second hardcoded path is how "
                "the previous dependency ended up invisible to CI — discover the paths "
                "instead."
            )

    def test_ruff_runs_over_the_repo(self):
        code = without_comments(CI_WORKFLOW)
        assert "ruff check ." in code
        assert "ruff format --check ." in code

    def test_pytest_runs_without_a_path_argument(self):
        code = without_comments(CI_WORKFLOW)
        assert re.search(r"run:\s*pytest -v\s*$", code, re.MULTILINE), (
            "the test job must invoke pytest with no path so collection covers every dependency"
        )

    def test_shellcheck_discovery_is_guarded_against_finding_nothing(self):
        # xargs with no input runs shellcheck over zero files and exits
        # 0, so a broken discovery would turn the job green rather than
        # red. The discovery moved out of ci.yml and into a tracked
        # script (see TestShellcheckRunsThroughATrackedScript for why),
        # so the guard has to be where the discovery is.
        code = without_comments(SHELLCHECK_SCRIPT)
        assert 'if [ -z "$scripts" ]' in code and "exit 1" in code


class TestShellcheckDiscoveryCoversEveryScript:
    def test_workflow_glob_matches_every_tracked_script(self):
        code = without_comments(SHELLCHECK_SCRIPT)
        match = re.search(r"git ls-files '([^']+)'", code)
        assert match, "tools/shellcheck-all.sh no longer discovers shell scripts with git ls-files"

        discovered = set(tracked_shell_scripts(match.group(1)))
        everything = set(tracked_shell_scripts("*.sh"))
        assert discovered == everything, (
            f"the pattern {match.group(1)!r} misses {sorted(everything - discovered)}"
        )

    def test_each_dependency_with_scripts_is_covered(self):
        code = without_comments(SHELLCHECK_SCRIPT)
        match = re.search(r"git ls-files '([^']+)'", code)
        assert match
        discovered = tracked_shell_scripts(match.group(1))
        everything = tracked_shell_scripts("*.sh")
        for dep in dependency_dirs():
            owned = [s for s in everything if s.startswith(f"{dep}/")]
            if not owned:
                continue
            assert [s for s in discovered if s.startswith(f"{dep}/")] == owned, (
                f"{dep}/'s shell scripts are not all reaching shellcheck"
            )


class TestOptionalTestDependenciesAreInstalled:
    def test_every_importorskip_is_installed_by_the_test_job(self):
        # `pytest.importorskip("yaml")` at the top of a test module skips
        # the whole file when the package is missing. That is how 13
        # pdfium release-workflow tests ran nowhere while the job stayed
        # green. Anything a test module skips itself over has to be in
        # the test job's pip install line.
        needed = set()
        for dirpath, _dirs, files in os.walk(REPO_ROOT):
            if os.path.basename(dirpath) != "tests":
                continue
            for name in files:
                if not name.startswith("test_") or not name.endswith(".py"):
                    continue
                with open(os.path.join(dirpath, name)) as f:
                    for line in f:
                        match = re.search(r'importorskip\(\s*["\'](\w+)["\']', line)
                        if match:
                            needed.add(match.group(1))

        installs = re.findall(r"run:\s*pip install (.+)", without_comments(CI_WORKFLOW))
        installed = {pkg for line in installs for pkg in line.split()}
        # import name -> distribution name, where they differ
        aliases = {"yaml": "pyyaml"}
        for module in needed:
            package = aliases.get(module, module)
            assert package in installed, (
                f"tests importorskip {module!r} but CI never installs {package!r}, so "
                "those tests skip silently on every run"
            )


class TestPytestCollectionCoversEveryDependency:
    def test_repo_wide_collection_reaches_every_tests_directory(self):
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"collection failed:\n{result.stdout}\n{result.stderr}"
        for dep in dependency_dirs():
            assert f"{dep}/tests/" in result.stdout, (
                f"running pytest from the repo root collects nothing from {dep}/tests/"
            )

    def test_pyproject_does_not_pin_collection_to_one_dependency(self):
        code = without_comments(PYPROJECT)
        for dep in dependency_dirs():
            assert dep not in code, f"pyproject.toml pins pytest to {dep}"


class TestThereIsOneHookInstaller:
    """This repo installs its hooks from libviprs-tests, not from here.

    Two installers wrote the same .git/hooks/pre-commit and whichever ran
    last won. The one that used to live here skipped shellcheck when
    shellcheck was missing, which is a pass reported for a check that did
    not run, and nothing held it to this workflow. The shared one fails
    loudly on a missing tool and a guard over there drives the generated
    hook and compares what it invokes against this ci.yml.
    """

    def test_this_repo_ships_no_hook_installer_of_its_own(self):
        assert not os.path.exists(HOOK_INSTALLER), (
            "tools/install-hooks.sh is back. Two installers writing the same "
            "hook means whichever ran last wins, and this one is the copy "
            "nothing holds to ci.yml."
        )

    def test_the_readme_points_at_the_shared_installer(self):
        readme = without_comments(README)
        assert "libviprs-tests/tools/install-hooks.sh" in readme, (
            "the README must say where hooks now come from, or the only "
            "instruction a contributor has is one that no longer exists"
        )


class TestShellcheckRunsThroughATrackedScript:
    """CI's shellcheck step has to be one command, not an inline block.

    The shared pre-commit hook mirrors this workflow command for command,
    and its guard refuses to stand in for a multi-line `run:` block: such
    a step has to be exempted instead, and an exempted step is one the
    local hook does not run. So an inline block here does not make the
    hook stricter, it silently removes shellcheck from it, which is the
    same false green in a new place.
    """

    def test_ci_runs_the_script_rather_than_an_inline_block(self):
        ci = without_comments(CI_WORKFLOW)
        assert "run: tools/shellcheck-all.sh" in ci, (
            "the shellcheck job must invoke the tracked script as a single "
            "command so the shared pre-commit hook can mirror it exactly"
        )
        assert "xargs shellcheck" not in ci, (
            "the discovery logic belongs in tools/shellcheck-all.sh, where "
            "shellcheck itself covers it, not inline in the workflow"
        )

    def test_the_script_is_tracked_and_executable(self):
        assert os.path.exists(SHELLCHECK_SCRIPT)
        assert os.access(SHELLCHECK_SCRIPT, os.X_OK), (
            "ci.yml invokes it directly, so a non-executable file fails the job"
        )
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "tools/shellcheck-all.sh"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert tracked.returncode == 0, "tools/shellcheck-all.sh must be tracked"

    def test_the_script_checks_itself(self):
        # It is a tracked *.sh, so its own discovery has to reach it.
        assert "tools/shellcheck-all.sh" in tracked_shell_scripts()


class TestRuffActuallySeesEveryDependency:
    def test_ruff_file_discovery_includes_every_dependency(self):
        # Belt and braces on top of the "no hardcoded path" check above:
        # ask ruff itself which files it would lint. Skipped where ruff
        # isn't installed (the CI test job doesn't install it), which is
        # why the assertion above exists and does not skip.
        if not shutil.which("ruff"):
            pytest.skip("ruff not installed on this host")
        result = subprocess.run(
            ["ruff", "check", "--show-files", "."],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        for dep in dependency_dirs():
            assert f"/{dep}/" in result.stdout, f"ruff would not lint anything in {dep}/"
