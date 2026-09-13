"""Import build_acadsharp.py as a module for testing.

The build driver is a standalone script, not a package, so it is loaded
by path and registered in sys.modules under its own name, the same
trick pdfium/tests/conftest.py and zstd/tests/conftest.py use.
"""

import importlib.util
import os
import sys

_script = os.path.join(os.path.dirname(__file__), "..", "build_acadsharp.py")
_spec = importlib.util.spec_from_file_location("build_acadsharp", _script)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
sys.modules["build_acadsharp"] = _mod
