"""Tests for pdfium/build_mac_native.sh.

The mac build runs on a macos-15 GitHub Actions runner (instead of inside
the Debian build container used for linux/musl) because PDFium's
``gn gen`` invokes ``xcodebuild``, which doesn't exist in Docker. This
native build path writes its own ``args.gn`` heredocs.

The script runs a two-phase build (mirroring the linux/musl path in
``build_pdfium.py``): an ``out/Static`` pass with
``pdf_is_complete_lib = true`` plus the Apple libc++ override
(``use_custom_libcxx = false``) yields ``libpdfium.a``, then an
``out/Release`` pass on Chromium's default bundled libc++ yields
``libpdfium.dylib``.

The libc++ override is scoped to ``out/Static`` only. For the dylib,
internal C++ symbols are resolved inside the shared object before any
``dlopen`` consumer sees them, so Chromium's bundled libc++
(``use_custom_libcxx = true``, the default) is fine and we intentionally
do *not* override it there. rustc, the single consumer that links
against internal C++ symbols and can't resolve ``std::__Cr::``, only
touches the static archive. Setting ``use_custom_libcxx = false`` on the
dylib pass additionally broke on Xcode 26.0 / MacOSX26.0.sdk (Chromium's
libc++ module sources expect macros only defined when the bundled libc++
is active), so keeping it scoped to ``out/Static`` is load-bearing.
"""

import os
import re

SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "..", "build_mac_native.sh")


def load_script():
    with open(SCRIPT_PATH) as f:
        return f.read()


def extract_args_gn_heredoc(sh: str, out_dir: str = "out/Release") -> str:
    # Pull just the `cat > <out_dir>/args.gn <<EOF ... EOF` block so
    # assertions test what actually ends up in that args.gn, not
    # commentary in surrounding shell comments. The mac build now writes
    # two heredocs (out/Static for libpdfium.a, out/Release for the
    # dylib), so callers pass the dir they want to inspect.
    pat = r"cat > " + re.escape(out_dir) + r"/args\.gn <<EOF\n(.*?)\nEOF\n"
    m = re.search(pat, sh, re.DOTALL)
    assert m, f"{out_dir}/args.gn heredoc not found in build_mac_native.sh"
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


class TestMacBuildScriptStaticArchive:
    """The mac build now produces libpdfium.a alongside libpdfium.dylib.

    A two-phase build mirrors the linux/musl path: an out/Static pass with
    pdf_is_complete_lib = true (fat static_library) plus the Apple libc++
    override, then the out/Release pass for the dylib on Chromium's default
    bundled libc++. The libc++ override stays scoped to out/Static so the
    dylib pass never re-triggers the Xcode 26.0 / USE_LIBCXX_MODULES failure.
    """

    def setup_method(self):
        self.sh = load_script()

    def test_mac_writes_static_and_release_args(self):
        # Both heredocs must exist: out/Static for the archive, out/Release
        # for the dylib.
        assert "cat > out/Static/args.gn <<EOF" in self.sh
        assert "cat > out/Release/args.gn <<EOF" in self.sh

    def test_mac_static_args_has_pdf_is_complete_lib(self):
        # pdf_is_complete_lib = true fires PDFium's own BUILD.gn branch that
        # emits a fat archive (drops the thin_archive config). Without it,
        # ar writes a GNU thin archive whose .o paths vanish downstream.
        static_args = extract_args_gn_heredoc(self.sh, "out/Static")
        assert "pdf_is_complete_lib = true" in static_args

    def test_mac_static_args_disables_custom_libcxx(self):
        # The Apple libc++ override (std::__1::) belongs to the static
        # archive ONLY — rustc links its internal C++ symbols against the
        # system runtime and cannot resolve Chromium's std::__Cr::.
        static_args = extract_args_gn_heredoc(self.sh, "out/Static")
        release_args = extract_args_gn_heredoc(self.sh, "out/Release")
        assert "use_custom_libcxx = false" in static_args
        assert "use_custom_libcxx_for_host = false" in static_args
        # Must NOT leak into the dylib pass (regression guard for the
        # Xcode 26.0 module compile failure — see
        # test_dylib_keeps_chromium_bundled_libcxx which pins the same).
        assert "use_custom_libcxx = false" not in release_args
        assert "use_custom_libcxx_for_host" not in release_args

    def test_mac_stages_libpdfium_a(self):
        # The staging step must copy the fat archive from its GN output
        # path (obj/libpdfium.a) into $STAGE_DEST/lib/ next to the dylib.
        assert "cp out/Static/obj/libpdfium.a  \"$STAGE_DEST/lib/\"" in self.sh
        assert "cp out/Release/libpdfium.dylib \"$STAGE_DEST/lib/\"" in self.sh
        # The static GN args must also ship as args.static.gn (matches the
        # linux/musl release layout so consumers can inspect both builds).
        assert 'cp out/Static/args.gn          "$STAGE_DEST/args.static.gn"' in self.sh

    def test_mac_verifies_fat_archive(self):
        # Build-level gate: reject a GNU thin archive and enforce the
        # member/size floors (mirrors build_pdfium.py's
        # VERIFY_COMPLETE_STATIC_LIB). mac uses BSD stat (-f%z).
        assert "'!<arch>'" in self.sh
        assert "'!<thin>'" in self.sh
        assert "stat -f%z" in self.sh

    def test_mac_invokes_verify_archive_script(self):
        # The script must end by running the shared symbol/namespace gate
        # over the staged .tgz for the mac platform.
        assert 'verify_archive.sh" "$ARCHIVE" mac' in self.sh
