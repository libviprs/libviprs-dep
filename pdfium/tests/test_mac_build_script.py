"""Tests for pdfium/build_mac_native.sh.

The mac build runs on a macos-15 GitHub Actions runner (instead of inside
the Debian build container used for linux/musl) because PDFium's
``gn gen`` invokes ``xcodebuild``, which doesn't exist in Docker. This
native build path writes its own ``out/Release/args.gn`` heredoc — these
tests lock in the GN flags that make the resulting ``libpdfium.dylib``
(and, once we split static/shared on mac, ``libpdfium.a``) export
standard ``std::__1::*`` symbols from Apple's system libc++ instead of
Chromium's ``std::__Cr::*`` namespace.
"""

import os

SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "..", "build_mac_native.sh")


def load_script():
    with open(SCRIPT_PATH) as f:
        return f.read()


class TestMacBuildScriptLibcxx:
    def setup_method(self):
        self.sh = load_script()

    def test_libcxx_standard_not_custom(self):
        # mac: fall back to Apple's system libc++ (inline namespace
        # std::__1::) instead of Chromium's std::__Cr::. Apple libc++ is
        # the standard C++ runtime on mac — pdfium-render + rustc both
        # expect this.
        assert "use_custom_libcxx = false" in self.sh
        assert "use_custom_libcxx_for_host = false" in self.sh

    def test_no_custom_libcxx_true(self):
        assert "use_custom_libcxx = true" not in self.sh


class TestMacBuildScriptXcodePin:
    def setup_method(self):
        self.sh = load_script()

    def test_xcode_pin_present(self):
        # Regression guard for PR #19's Xcode 26.0 pin — MacOSX15.5.sdk
        # (Xcode 16.4 default) removes DarwinFoundation1.modulemap which
        # PDFium 7725's ninja graph still references.
        assert "Xcode_26.0.app" in self.sh

    def test_xcode_pin_exported_via_developer_dir(self):
        # Use DEVELOPER_DIR rather than `sudo xcode-select -s` so the
        # script is safe to run locally without modifying the
        # developer's global toolchain.
        assert "export DEVELOPER_DIR=" in self.sh
