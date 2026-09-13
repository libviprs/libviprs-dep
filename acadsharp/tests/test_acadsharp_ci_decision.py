"""The C# coverage decision, pinned where it can fail.

ADR 0001 decides that the shim is not covered by `.github/workflows/ci.yml`.
Two separate things make a `dotnet` job there expensive:

* `zstd/tests/test_ci_coverage.py::test_no_step_hardcodes_a_dependency_directory`
  forbids naming `acadsharp/` in that workflow at all, and a job that
  builds a csproj has to name a path.
* libviprs-tests' `Hook Mirror (every repo in the org)` job reads this
  repo's ci.yml at the main tip and mirrors every job into the shared
  pre-commit hook unless it is listed as deferred. A .NET SDK is not
  something a pre-commit hook should require, so the job would have to
  land with a pairing PR in that repo or CI goes red on both.

So the decision is recorded, and this test fails the moment someone
adds the job without revisiting it.

What changed with #59 is where the shim *is* covered, not this. Both
guards above read ci.yml and nothing else, so
`.github/workflows/acadsharp-conformance.yml` runs the two conformance
consumers from a file of its own, gated on `acadsharp/` paths, and ci.yml
is exactly as it was. The last class here holds that pairing together: the
decision only survives while something else actually runs the boundary, and
a repository with neither is the state the decision was never meant to
describe.
"""

import os

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CI_WORKFLOW = os.path.join(REPO_ROOT, ".github", "workflows", "ci.yml")
CONFORMANCE_WORKFLOW = os.path.join(REPO_ROOT, ".github", "workflows", "acadsharp-conformance.yml")
ADR = os.path.join(os.path.dirname(__file__), "..", "docs", "adr", "0001-nativeaot-feasibility.md")


def without_comments(path):
    with open(path) as f:
        return "\n".join(
            line for line in f.read().splitlines() if not line.lstrip().startswith("#")
        )


class TestCsharpIsNotInCi:
    def test_ci_yml_has_no_dotnet_step(self):
        code = without_comments(CI_WORKFLOW).lower()
        assert "dotnet" not in code, (
            "a dotnet job in ci.yml needs the libviprs-tests Hook Mirror pairing PR in the "
            "same breath, and ADR 0001 records why it is not there yet. Update the ADR "
            "before this test"
        )

    def test_the_adr_records_the_decision(self):
        with open(ADR) as f:
            adr = f.read()
        assert "Hook Mirror" in adr, "the ADR has to say what a ci.yml job would engage"


class TestSomethingElseRunsTheBoundary:
    """The decision is "not in ci.yml", never "nowhere".

    It was read as the second for a while, and the cost of that is the whole
    of #59: two conformance consumers and a recorded corpus that ran only
    when a person remembered. Keeping the decision means keeping the file
    that makes it affordable.
    """

    def test_a_workflow_of_its_own_runs_the_consumers(self):
        assert os.path.isfile(CONFORMANCE_WORKFLOW), (
            "nothing runs the conformance consumers again. The ci.yml decision "
            "above is only defensible while a separate workflow covers the shim"
        )
        with open(CONFORMANCE_WORKFLOW) as f:
            body = f.read()
        for runner in ("conformance/c/run.sh", "conformance/rust/run.sh"):
            assert runner in body, f"{runner} is not run by the conformance workflow"

    def test_the_adr_records_where_the_coverage_went(self):
        with open(ADR) as f:
            adr = f.read()
        assert "acadsharp-conformance.yml" in adr, (
            "the ADR still says the shim is covered by the recorded captures "
            "alone, which stopped being the whole story"
        )
