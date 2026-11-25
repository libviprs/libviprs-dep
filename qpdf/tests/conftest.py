"""Import build_qpdf.py as a module for testing, the way zstd/tests/conftest.py does."""

import importlib.util
import os
import sys

_script = os.path.join(os.path.dirname(__file__), "..", "build_qpdf.py")
_spec = importlib.util.spec_from_file_location("build_qpdf", _script)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
sys.modules["build_qpdf"] = _mod
