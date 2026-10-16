"""Import the build driver and the toolchain gate under stable names.

The build driver is a standalone script, not a package, so it is loaded
by path and registered in sys.modules under its own name, the same
trick pdfium/tests/conftest.py and zstd/tests/conftest.py use.

`toolchain.py` is loaded the same way rather than left to sys.path, so
every suite here and this file share one instance of it: the census hook
reads reports that `missing_tool` produced, and two copies of the module
would be two copies of the marker they agree on.
"""

import importlib.util
import os
import sys

HERE = os.path.dirname(__file__)


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load(os.path.join(HERE, "..", "build_acadsharp.py"), "build_acadsharp")
_toolchain = _load(os.path.join(HERE, "toolchain.py"), "toolchain")

# Registered by assignment rather than by `from toolchain import`, because
# pluggy finds a hook by its name on this module and this keeps the one
# instance above. It prints how many compiled fixtures did not run, which
# is the half of this that a summary line hides.
pytest_terminal_summary = _toolchain.pytest_terminal_summary


# #119. Every manifest-shape test builds a LINKINFO through `linkinfo_skeleton`,
# which raises when the shim pin disagrees with the tree, so a stale SHIM_DIGESTS
# row fails dozens of tests that never mention the shim. The refusal stays. The
# class that asserts the pin runs first, so the first failure named is the true
# one. A stable partition: nothing else moves relative to anything else.
FIRST = "TestTheArchiveSaysWhichShimIsInIt"


def pytest_collection_modifyitems(items):
    pinned = [item for item in items if item.cls is not None and item.cls.__name__ == FIRST]
    if pinned:
        rest = [item for item in items if item not in pinned]
        items[:] = pinned + rest
