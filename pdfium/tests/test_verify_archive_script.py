"""Tests for pdfium/scripts/verify_archive.sh.

The verify script runs at the end of every release build job (and
locally during development). It inspects the just-built libpdfium.a /
libpdfium.so / libpdfium.dylib and fails the build if any of these
invariants break:

  * required FPDF_* C API symbols are present (public ABI not stripped).
  * std::__Cr:: symbols are absent (no Chromium custom libc++ leaked).
  * on linux: std::__cxx11:: libstdc++ symbols are present.
  * on macOS: std::__1:: Apple libc++ symbols are present.

These tests cover script-level invariants (existence, executability,
shellcheck-cleanliness, argument handling). Full symbol verification is
exercised end-to-end in the release workflow against real .tgz files.
"""

import os
import shutil
import stat
import subprocess

SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "verify_archive.sh")


class TestVerifyArchiveScriptShape:
    def test_script_exists(self):
        assert os.path.exists(SCRIPT_PATH), f"verify script missing at {SCRIPT_PATH}"

    def test_script_is_executable(self):
        mode = os.stat(SCRIPT_PATH).st_mode
        assert mode & stat.S_IXUSR, "script must have user-execute bit set"

    def test_script_has_shebang(self):
        with open(SCRIPT_PATH) as f:
            first = f.readline().rstrip()
        assert first.startswith("#!"), f"missing shebang, got: {first!r}"
        assert "bash" in first

    def test_script_uses_strict_mode(self):
        with open(SCRIPT_PATH) as f:
            text = f.read()
        assert "set -euo pipefail" in text, (
            "verify script must use strict mode; a silent tool miss would let a bad archive ship"
        )


class TestVerifyArchiveScriptContent:
    def setup_method(self):
        with open(SCRIPT_PATH) as f:
            self.sh = f.read()

    def test_forbids_chromium_cr_namespace(self):
        # This is the core invariant — every consumer of this script
        # relies on the Chromium __Cr namespace being rejected.
        assert "__Cr::" in self.sh

    def test_requires_fpdf_init_library(self):
        # FPDF_InitLibrary is the canonical entry point; if the public
        # FPDF_* C API was stripped, downstream builds link-fail.
        assert "FPDF_InitLibrary" in self.sh

    def test_mac_uses_cxxfilt_fallback(self):
        # Apple's nm(1) does not support -C; the script must demangle
        # via `c++filt` on mac. Linux nm -C works directly.
        assert "c++filt" in self.sh

    def test_linux_expects_cxx11_std(self):
        # libstdc++'s dual-ABI inline namespace — the presence of this
        # namespace in demangled symbols proves we're on libstdc++ and
        # not libc++.
        assert "__cxx11" in self.sh

    def test_mac_expects_libcxx_std(self):
        # Apple libc++ inline namespace — the mac "standard" namespace.
        assert "__1::" in self.sh or "__1\\\\:\\\\:" in self.sh


class TestVerifyArchiveScriptShellcheck:
    def test_passes_shellcheck(self):
        if not shutil.which("shellcheck"):
            # Mirror existing conftest pattern: skip if tool unavailable
            # rather than fail the test run. CI has shellcheck pinned.
            import pytest

            pytest.skip("shellcheck not installed on this host")
        result = subprocess.run(
            ["shellcheck", SCRIPT_PATH],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"shellcheck failed:\n{result.stdout}\n{result.stderr}"


class TestVerifyArchiveScriptUsage:
    def test_fails_without_argument(self):
        # If invoked with no archive path, the script must exit non-zero
        # with a usage message — never succeed silently.
        result = subprocess.run(
            ["bash", SCRIPT_PATH],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0
        combined = (result.stdout + result.stderr).lower()
        assert "usage" in combined or "missing" in combined
