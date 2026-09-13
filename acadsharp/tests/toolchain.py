"""The gate between "this host cannot build a fixture" and "nobody noticed".

Six suites here compile a stand-in library before they assert anything:
`cc` and `ar` produce a real ELF with a real dynamic symbol table, and the
byte-level readers are then read against real bytes. On a host without a
compiler every one of those tests skips, and a skip is the same character
as a pass in a summary line.

That is not a hypothetical either. A lane's own post-rebase run reported
`1718 passed, 122 skipped` and read green; CI then failed seven tests,
because the local container had no C compiler and the half of the suite
that matters never ran. This directory is 114 tests lighter without a
compiler than with one.

So a missing build tool comes through `missing_tool()` rather than through
a bare `pytest.skip`, and `missing_tool()` has two behaviours:

* quiet skip, which is what a dev machine wants. This repo's Mac has no
  host toolchain by design and the suite has to stay runnable there.
* a failure, when the environment claims it can compile. That is
  `VIPRS_REQUIRE_COMPILED_FIXTURES=1`, and it mirrors
  `VIPRS_REQUIRE_LINK_TEST=1` in `scripts/verify_archive.sh`, which turns
  the same silence into a refusal for the musl link.

The default is derived rather than configured: any CI run is an
environment that claims it can compile, so `CI` in the environment turns
the gate on. A workflow cannot forget to set a flag it never had to set,
which is the failure mode that produced this in the first place.
`VIPRS_REQUIRE_COMPILED_FIXTURES` overrides the derivation in both
directions, so a CI job that genuinely has no compiler can say `0` and a
dev can force the strict behaviour with `1` without pretending to be CI.

Whatever the gate does, the count is printed at the end of the run under a
separator of its own, because the second half of this problem is that
`122 skipped` scrolls past and `1718 passed` does not.
"""

import os
import re

import pytest

REQUIRE_ENV = "VIPRS_REQUIRE_COMPILED_FIXTURES"

# Every skip that goes through this module carries it, so the census below
# counts the compiled fixtures and not the skips that are about something
# else. A prefix rather than a marker, because the census reads finished
# reports and a report only carries the reason string.
MARK = "compiled fixture: "

# The vocabulary `test_verify_archive.py` scans the suites for, so a new
# gate that skips on a missing build tool by hand is caught rather than
# quietly reopening this. Short names are matched on word boundaries: `ar`
# is a tool and `are` is not.
BUILD_TOOLS = (
    "cc",
    "gcc",
    "clang",
    "ar",
    "nm",
    "objcopy",
    "objdump",
    "ranlib",
    "cargo",
    "rustc",
    "dotnet",
)

BUILD_TOOL_RE = re.compile(r"\b(%s)\b" % "|".join(BUILD_TOOLS))


def required():
    """Is this a host that claims it can build the compiled fixtures?"""
    forced = os.environ.get(REQUIRE_ENV)
    if forced is not None:
        return forced.strip().lower() not in ("", "0", "false", "no")
    return os.environ.get("CI", "").strip().lower() not in ("", "0", "false", "no")


def missing_tool(tool, detail=None):
    """`tool` is not on this host, so a compiled fixture cannot be built.

    Never returns. Either the test skips, or it fails, depending on
    whether this environment claims it can compile.
    """
    reason = f"{tool} is not on this host"
    if detail:
        reason = f"{reason}, so {detail}"
    if required():
        pytest.fail(
            f"{reason}. {REQUIRE_ENV} is set (or this is CI), which says this host "
            "builds the compiled fixtures, so a skip here is the suite silently "
            "dropping the tests that read real bytes rather than a host that cannot "
            f"run them. Install {tool}, or set {REQUIRE_ENV}=0 to go back to skipping.",
            pytrace=False,
        )
    pytest.skip(MARK + reason)


def _reason(report):
    longrepr = getattr(report, "longrepr", None)
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        return str(longrepr[2])
    return str(longrepr or "")


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Say out loud how many compiled fixtures did not run.

    `acadsharp/tests/conftest.py` imports this to register it. A summary
    line that reads `1718 passed, 122 skipped` is the shape of the problem,
    so the count gets a separator of its own and the reasons get listed.
    """
    reports = [r for r in terminalreporter.stats.get("skipped", []) if MARK in _reason(r)]
    if not reports:
        return
    counts = {}
    for report in reports:
        reason = _reason(report).split(MARK, 1)[1]
        counts[reason] = counts.get(reason, 0) + 1
    terminalreporter.write_sep(
        "!",
        f"{len(reports)} compiled-fixture tests did not run on this host",
        red=True,
        bold=True,
    )
    for reason, count in sorted(counts.items()):
        terminalreporter.write_line(f"  {count:>4}  {reason}")
    terminalreporter.write_line(
        f"  these read a real ELF built by cc and ar. Set {REQUIRE_ENV}=1 to make "
        "this a failure instead of a line."
    )
