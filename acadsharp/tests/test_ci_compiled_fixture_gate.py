"""Where the strict-skip gate is turned on, and what says so.

#76 made a missing build tool a failure rather than a skip, because six
suites here compile a stand-in library before they assert anything and a
skip is the same character as a pass in a summary line. It deliberately
did not edit a workflow: `toolchain.required()` derives its answer from
`CI` being present, Actions always sets `CI`, and
`VIPRS_REQUIRE_COMPILED_FIXTURES` overrides that in both directions. The
alternative it left as a decision was one `env:` line on ci.yml's test
job.

I did not add the line, and this file is the reasoning as tests rather
than as a paragraph in a pull request.

An `env:` line would be inert: `required()` already answers True on every
Actions run, so adding it changes nothing that can be measured. What it
would add is a knob, in a file where the knob's only interesting value is
the one that switches the gate off, and a second statement of a rule that
is already stated once. What it would genuinely give is visibility, since
a reader of ci.yml would otherwise have no way to know the run is strict.
That part is real, so ci.yml says it, in the place a reader is already
looking, without also becoming a place the answer can be changed.

So three things are pinned here. An Actions run is strict. Nothing in
`.github/workflows` quietly opts out of that. And ci.yml tells the person
reading ci.yml where the strictness comes from.

Parsed as text, deliberately. This file is about tests that vanish
quietly, and a guard that `importorskip`s itself out of existence when
PyYAML is missing is the same failure wearing a different hat.
"""

import os
import re
import subprocess
import sys
import textwrap

import toolchain

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WORKFLOW_DIR = os.path.join(REPO_ROOT, ".github", "workflows")
CI_WORKFLOW = os.path.join(WORKFLOW_DIR, "ci.yml")
TOOLCHAIN = os.path.join(os.path.dirname(__file__), "toolchain.py")

REQUIRE_ENV = "VIPRS_REQUIRE_COMPILED_FIXTURES"

# What Actions puts in the environment of every step of every job, on
# every runner, without being asked. It is the whole mechanism.
ACTIONS_ENV = {"CI": "true"}


def workflow_files():
    return [
        os.path.join(WORKFLOW_DIR, name)
        for name in sorted(os.listdir(WORKFLOW_DIR))
        if name.endswith((".yml", ".yaml"))
    ]


def read(path):
    with open(path) as f:
        return f.read()


def under(env):
    """`toolchain.required()` as it would answer in `env`, and nowhere else."""
    saved = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(env)
        return toolchain.required()
    finally:
        os.environ.clear()
        os.environ.update(saved)


class TestAnActionsRunIsStrictWithoutBeingTold:
    def test_ci_alone_turns_the_gate_on(self):
        assert under(ACTIONS_ENV) is True, (
            "an Actions run does not make the compiled fixtures compulsory, so the "
            "decision not to put a flag in a workflow is resting on nothing"
        )

    def test_a_machine_that_is_not_ci_still_skips(self):
        # The control for the case above. This Mac has no host toolchain by
        # design and the suite has to stay runnable on it, so a gate that is
        # on everywhere is not the gate that was wanted.
        assert under({}) is False, (
            "a developer machine is now strict about fixtures it cannot build, which "
            "makes the suite unrunnable on the machine it is developed on"
        )

    def test_the_line_ci_yml_could_carry_would_change_nothing(self):
        # The decision, measured rather than argued. If this ever stops being
        # true, the reasoning in the docstring is stale and the line is worth
        # revisiting.
        with_line = dict(ACTIONS_ENV, **{REQUIRE_ENV: "1"})
        assert under(with_line) == under(ACTIONS_ENV), (
            f"setting {REQUIRE_ENV}=1 in a workflow now means something the "
            "derivation does not already mean, so the explicit line has a job again"
        )

    def test_the_override_still_works_both_ways(self):
        # A cell that genuinely cannot compile has to be able to say so, and a
        # developer has to be able to ask for the strict behaviour without
        # pretending to be CI. Both are the reason the derivation is a default
        # and not a rule.
        assert under(dict(ACTIONS_ENV, **{REQUIRE_ENV: "0"})) is False
        assert under({REQUIRE_ENV: "1"}) is True


class TestNoWorkflowOptsOutOfIt:
    """The one edit that would matter, and the only one worth refusing.

    `VIPRS_REQUIRE_COMPILED_FIXTURES=1` in a workflow is inert. `=0` is the
    same silence #76 exists to remove, reintroduced in a file nobody reads
    for that, so it has to arrive with the reasoning rather than as a way
    of getting a red run green.
    """

    FALSY = re.compile(
        r"""VIPRS_REQUIRE_COMPILED_FIXTURES\s*:\s*["']?(0|false|no)["']?\s*$""",
        re.I | re.M,
    )

    def test_no_workflow_turns_the_gate_off(self):
        offenders = []
        for path in workflow_files():
            for lineno, line in enumerate(read(path).splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                if self.FALSY.search(line):
                    offenders.append(f"{os.path.basename(path)}:{lineno}: {line.strip()}")
        assert not offenders, (
            "these workflows switch the compiled-fixture gate off:\n  "
            + "\n  ".join(offenders)
            + "\nA cell with no compiler is allowed to, but it has to say which cell "
            "and why, because the alternative is 114 tests going quiet again."
        )

    def test_the_guard_would_catch_one(self):
        # An assertion over a directory that happens to be clean says nothing
        # about the pattern.
        for line in (
            '          VIPRS_REQUIRE_COMPILED_FIXTURES: "0"',
            "          VIPRS_REQUIRE_COMPILED_FIXTURES: 0",
            "          VIPRS_REQUIRE_COMPILED_FIXTURES: false",
        ):
            assert self.FALSY.search(line), f"{line!r} would not be caught"
        assert not self.FALSY.search('          VIPRS_REQUIRE_COMPILED_FIXTURES: "1"')


class TestCiYmlSaysWhereTheStrictnessComesFrom:
    """The half of the explicit line that was worth keeping.

    Nothing in ci.yml made the run strict and nothing in ci.yml said it
    was, so the only way to find out was to read a module three
    directories away. That is a real cost and it is the one an `env:` line
    would have paid. A comment pays it too, and a comment cannot be given
    a value.
    """

    def test_it_names_the_switch(self):
        ci = read(CI_WORKFLOW)
        assert REQUIRE_ENV in ci, (
            f"ci.yml never mentions {REQUIRE_ENV}, so a reader of the workflow has no "
            "way to learn that this run fails on a missing compiler, and no way to "
            "learn what to set when a runner genuinely has none"
        )

    def test_it_names_the_file_that_decides(self):
        ci = read(CI_WORKFLOW)
        assert "toolchain.py" in ci, (
            "ci.yml says the run is strict without saying what makes it strict, which "
            "sends the next reader looking through conftest files"
        )

    def test_it_says_so_without_also_setting_it(self):
        # Recorded, not defended against a future change of mind. The line
        # #76 offered is inert today (TestAnActionsRunIsStrictWithoutBeingTold
        # measures that), and what it would add is a second place to state one
        # rule, whose only interesting value is the one that switches the rule
        # off. If this ever has to change, change it with the reasoning above
        # rather than quietly.
        code = "\n".join(
            line for line in read(CI_WORKFLOW).splitlines() if not line.lstrip().startswith("#")
        )
        assert REQUIRE_ENV not in code, (
            f"ci.yml now sets {REQUIRE_ENV} as well as explaining it. That is a knob "
            "in a file where the only value worth turning it to is the one that makes "
            "114 tests go quiet, and the derivation already covers every Actions run"
        )


class TestTheGateActuallyBites:
    """Run it. Both ways.

    Everything above reads `required()`, which is the decision and not the
    consequence. This spends a subprocess on the consequence, because the
    thing being claimed is that a missing tool stops a run, and a claim
    about pytest's exit status is worth asking pytest.
    """

    CASE = textwrap.dedent(
        '''
        """A suite that needs a tool no host has."""

        import importlib.util

        spec = importlib.util.spec_from_file_location("toolchain", {toolchain!r})
        toolchain = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(toolchain)


        def test_it_needs_a_compiler_that_is_not_here():
            toolchain.missing_tool("viprs-no-such-compiler", "this case cannot run")
        '''
    )

    def run(self, tmp_path, env):
        case = tmp_path / "test_missing_tool_case.py"
        case.write_text(self.CASE.format(toolchain=TOOLCHAIN))
        full = dict(os.environ)
        for key in ("CI", REQUIRE_ENV):
            full.pop(key, None)
        full.update(env)
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(case)],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(tmp_path),
            env=full,
        )

    def test_a_dev_machine_skips_it(self, tmp_path):
        done = self.run(tmp_path, {})
        assert done.returncode == 0, done.stdout + done.stderr
        assert "1 skipped" in done.stdout

    def test_an_actions_run_fails_it(self, tmp_path):
        done = self.run(tmp_path, ACTIONS_ENV)
        assert done.returncode != 0, (
            "a missing build tool passed under CI, so the gate is decorative and every "
            "compiled fixture can go quiet again:\n" + done.stdout + done.stderr
        )
        assert "1 failed" in done.stdout
        assert "viprs-no-such-compiler" in done.stdout

    def test_the_opt_out_puts_it_back_to_a_skip(self, tmp_path):
        done = self.run(tmp_path, dict(ACTIONS_ENV, **{REQUIRE_ENV: "0"}))
        assert done.returncode == 0, done.stdout + done.stderr
        assert "1 skipped" in done.stdout
