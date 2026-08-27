"""Import build_zstd.py as a module for testing.

The build driver is a standalone script, not a package, so it is loaded
by path and registered in sys.modules under its own name — the same
trick pdfium/tests/conftest.py uses.
"""

import importlib.util
import os
import sys

_script = os.path.join(os.path.dirname(__file__), "..", "build_zstd.py")
_spec = importlib.util.spec_from_file_location("build_zstd", _script)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
sys.modules["build_zstd"] = _mod
