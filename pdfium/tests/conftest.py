"""Import build_pdfium.py as a module for testing.

The build script is a standalone file, not a package, so we use importlib
to load it by path and register it in sys.modules.
"""

import importlib.util
import os
import sys

_script = os.path.join(os.path.dirname(__file__), "..", "build_pdfium.py")
_spec = importlib.util.spec_from_file_location("build_pdfium", _script)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
sys.modules["build_pdfium"] = _mod
