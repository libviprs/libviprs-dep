"""Tests for pdfium/build_mac_native.sh.

The mac build runs on a macos-15 GitHub Actions runner (instead of inside
the Debian build container used for linux/musl) because PDFium's
``gn gen`` invokes ``xcodebuild``, which doesn't exist in Docker. This
native build path writes its own ``out/Release/args.gn`` heredoc.

Currently this script only produces a ``libpdfium.dylib`` — a single
shared-library phase. Internal C++ symbols are resolved inside the
dylib itself before any ``dlopen`` consumer sees them, so Chromium's
bundled libc++ (``use_custom_libcxx = true``, the default) is fine and
we intentionally do *not* override it. This matches the linux .so path
in ``build_pdfium.py``: libcxx overrides are scoped to the static
archive only, because rustc is the single consumer that actually links
against internal C++ symbols and can't resolve ``std::__Cr::``.

Setting ``use_custom_libcxx = false`` here additionally broke on
Xcode 26.0 / MacOSX26.0.sdk (Chromium's libc++ module sources expect
macros that are only defined when the bundled libc++ is active).
"""

import os
import re

SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "..", "build_mac_native.sh")


def load_script():
    with open(SCRIPT_PATH) as f:
        return f.read()


def extract_args_gn_heredoc(sh: str) -> str:
    # Pull just the `cat > out/Release/args.gn <<EOF ... EOF` block so
    # assertions test what actually ends up in args.gn, not commentary
    # in surrounding shell comments.
    m = re.search(r"cat > out/Release/args\.gn <<EOF\n(.*?)\nEOF\n", sh, re.DOTALL)
    assert m, "args.gn heredoc not found in build_mac_native.sh"
    return m.group(1)


class TestMacBuildScriptLibcxx:
    def setup_method(self):
        self.args_gn = extract_args_gn_heredoc(load_script())

    def test_dylib_keeps_chromium_bundled_libcxx(self):
        # mac dylib is shared-only — dlopen consumers never see internal
        # __Cr:: symbols, so Chromium's bundled libc++ is harmless and we
        # MUST NOT set use_custom_libcxx = false here. Doing so broke the
        # Xcode 26.0 build with module compile errors in Chromium's own
        # libc++ sources. If a mac static build is added later it needs
        # its own out/Static args.gn with the override isolated there —
        # see build_pdfium.py's gn_args_static_for() for the linux
        # pattern.
        assert "use_custom_libcxx = false" not in self.args_gn
        assert "use_custom_libcxx_for_host" not in self.args_gn


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
