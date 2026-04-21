"""Tests for pdfium/scripts/verify_archive.sh.

The verify script runs at the end of every release build job (and
locally during development). It inspects the just-built libpdfium.a /
libpdfium.so / libpdfium.dylib and fails the build if any of these
invariants break:

  * required FPDF_* C API symbols are present (public ABI not stripped).
  * in libpdfium.a: std::__Cr:: symbols are absent (no Chromium custom
    libc++ leaked — rustc consumers can't resolve __Cr).
  * in libpdfium.a on linux: std::__cxx11:: libstdc++ symbols present.
  * in libpdfium.a on macOS: std::__1:: Apple libc++ symbols present.
  * .so and .dylib files are checked ONLY for the public FPDF_* API —
    __Cr:: is acceptable there because dlopen consumers never see it.

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

    def test_cr_namespace_strict_only_for_static_archive(self):
        # Chromium's std::__Cr::* is only toxic for .a consumers (rustc
        # links against the system C++ runtime and can't resolve __Cr).
        # For .so/.dylib, __Cr symbols are internal to the dynamic
        # library and dlopen consumers never see them, so those libs
        # are allowed to keep Chromium's bundled libc++ — which is what
        # the linux shared build actually does (use_custom_libcxx=true
        # is needed on linux/arm64 for the sysroot's bundled glib).
        assert "*.a)" in self.sh, (
            "verify_archive.sh must dispatch on *.a separately so the "
            "__Cr:: check only applies to the static archive"
        )
        assert "*.so|*.dylib)" in self.sh or "*.so)" in self.sh, (
            "verify_archive.sh must also dispatch on .so/.dylib so those "
            "artifacts are allowed to keep internal __Cr:: symbols"
        )


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


class TestVerifyArchiveScriptMachOSymbolMatch:
    def setup_method(self):
        with open(SCRIPT_PATH) as f:
            self.sh = f.read()

    def test_fpdf_regex_tolerates_mach_o_underscore_prefix(self):
        # On mac, mach-o nm prefixes C symbols with `_`, so a real line
        # looks like:
        #     0000000000012ab0 T _FPDF_InitLibrary
        # A `\b` word boundary between `_` and `F` does NOT exist (both
        # are word chars), so the original `\bFPDF_InitLibrary\b` pattern
        # silently reported every mac dylib as missing its public C API.
        # Pin the fix: the FPDF_* match must accept either whitespace or
        # underscore before the symbol name.
        import re as _re

        match = _re.search(r'grep -qE "\[([^\]]+)\]\$\{sym\}\$"', self.sh)
        assert match, "FPDF_* regex pattern not found in verify script"
        charclass = match.group(1)
        assert "_" in charclass, (
            f"FPDF_* regex char class {charclass!r} must include `_` to "
            "match mac mach-o underscore-prefixed symbols"
        )
        assert " " in charclass, (
            f"FPDF_* regex char class {charclass!r} must include ` ` to "
            "match linux/musl ELF symbols (no prefix)"
        )


class TestVerifyArchiveScriptPipefailSafety:
    def setup_method(self):
        import re as _re

        with open(SCRIPT_PATH) as f:
            raw = f.read()
        # Drop shell comments and blank lines so assertions only match
        # executable code, not explanatory prose that quotes the
        # forbidden pattern.
        self.code = "\n".join(
            line for line in raw.splitlines() if not _re.match(r"\s*#", line) and line.strip()
        )

    def test_symbol_checks_do_not_pipe_echo_to_grep(self):
        # Under `set -o pipefail`, `echo "$syms" | grep -q …` fails the
        # whole pipeline when grep matches early and echo gets SIGPIPE
        # on a subsequent write. That false-fails every invariant on
        # linux .a (the archive's symbol list is big enough that echo
        # never finishes before grep exits). Dump symbols to a tempfile
        # and grep the file instead.
        assert 'echo "$syms" | grep' not in self.code, (
            "verify_archive.sh must not pipe $syms into grep — pipefail "
            "promotes echo's SIGPIPE to a pipeline failure. Write to a "
            "temp file and grep the file."
        )

    def test_symbol_dump_goes_to_temp_file(self):
        # Encode the chosen mitigation so a future refactor can't silently
        # re-introduce the pipe form.
        assert "mktemp" in self.code
        assert "syms_file" in self.code


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
