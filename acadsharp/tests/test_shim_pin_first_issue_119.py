"""#119: a stale `SHIM_DIGESTS` row has to be the first failure named.

Every manifest-shape test builds a LINKINFO through `linkinfo_skeleton`,
which raises when the shim pin disagrees with the tree, so one mistyped
character detonates across forty tests that never mention the shim. The
refusal is correct and stays. What changes is the order: the class that
asserts the pin runs first, so the first failure on screen is the true one.

Collection order is the whole mechanism, so that is what is asserted, by
collecting the modules that build manifests in a subprocess and looking at
what comes out first.
"""

import os
import subprocess
import sys

ACAD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REPO_ROOT = os.path.dirname(ACAD_DIR)
TESTS = os.path.join(ACAD_DIR, "tests")
PIN = "TestTheArchiveSaysWhichShimIsInIt"


def _collect(*files):
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"]
        + [os.path.join(TESTS, f) for f in files],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=True,
    )
    return [line for line in out.stdout.splitlines() if "::" in line]


def test_the_pin_assertions_are_collected_before_everything_that_builds_a_manifest():
    items = _collect("test_abi_header.py", "test_build_manifests.py", "test_verify_archive.py")
    assert items, "nothing was collected"
    assert PIN in items[0], f"the first test to run is {items[0]}, not one of {PIN}"


def test_every_pin_assertion_precedes_the_first_other_test_in_its_module():
    items = _collect("test_build_manifests.py")
    pins = [i for i, item in enumerate(items) if PIN in item]
    assert pins, f"{PIN} collected nothing"
    assert pins == list(range(len(pins))), "the pin assertions are interleaved with other tests"
