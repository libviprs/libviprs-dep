"""Generated Dockerfile content, one class per (platform, arch).

zstd builds in a container pinned to the *target* architecture, which is
what lets a single configure pass emit both libraries and lets the smoke
test actually run them. These assertions lock that structure in.
"""

import build_zstd as bz
import pytest


class TestLinuxAmd64:
    def setup_method(self):
        self.df = bz.make_dockerfile("1.5.7", "amd64", "linux")

    def test_base_image(self):
        assert "FROM --platform=linux/amd64 debian:bookworm-slim" in self.df

    def test_uses_apt(self):
        assert "apt-get install" in self.df
        assert "apk add" not in self.df

    def test_source_url_and_pinned_hash(self):
        assert bz.source_url("1.5.7") in self.df
        assert bz.source_sha256("1.5.7") in self.df

    def test_checksum_is_enforced(self):
        # Downloading without checking is how a mirror compromise gets
        # compiled into a published binary.
        assert "sha256sum -c -" in self.df

    def test_both_libraries_from_one_pass(self):
        assert "-DZSTD_BUILD_STATIC=ON" in self.df
        assert "-DZSTD_BUILD_SHARED=ON" in self.df
        assert self.df.count("cmake --build") == 1, (
            "zstd emits the static archive and the shared library from a single "
            "configure; a second build pass means something regressed"
        )

    def test_libdir_is_pinned(self):
        # GNUInstallDirs would otherwise resolve to lib/x86_64-linux-gnu
        # on Debian and break the documented archive layout.
        assert "-DCMAKE_INSTALL_LIBDIR=lib" in self.df

    def test_position_independent(self):
        assert "-DCMAKE_POSITION_INDEPENDENT_CODE=ON" in self.df

    def test_build_flags_match_zstd_sys_expectations(self):
        assert "-DZSTD_LEGACY_SUPPORT=OFF" in self.df
        assert "-DZSTD_MULTITHREAD_SUPPORT=ON" in self.df

    def test_programs_and_tests_skipped(self):
        assert "-DZSTD_BUILD_PROGRAMS=OFF" in self.df
        assert "-DZSTD_BUILD_TESTS=OFF" in self.df

    def test_stages_licence_and_build_args(self):
        assert "cp LICENSE /staging/LICENSE" in self.df
        assert "/staging/cmake-args.txt" in self.df

    def test_smoke_test_runs_after_install(self):
        install = self.df.index("cmake --install out")
        smoke = self.df.index("sh /tmp/smoke.sh")
        assert smoke > install

    def test_pkgconfig_relocated_before_packaging(self):
        install = self.df.index("cmake --install out")
        relocate = self.df.index("sh /tmp/relocate-pc.sh")
        assert relocate > install

    def test_no_pdfium_style_toolchain_machinery(self):
        # zstd needs none of what PDFium needs; if any of this shows up
        # here, someone has copied the wrong template. Comment lines are
        # stripped first — the prose in this Dockerfile says the words
        # "no sysroot", and matching that would be a false positive.
        instructions = "\n".join(
            line for line in self.df.splitlines() if not line.lstrip().startswith("#")
        )
        for junk in ("depot_tools", "gclient", "gn gen", "sysroot", "musl-cross"):
            assert junk not in instructions


class TestLinuxArm64:
    def setup_method(self):
        self.df = bz.make_dockerfile("1.5.7", "arm64", "linux")

    def test_container_is_pinned_to_the_target_arch(self):
        assert "FROM --platform=linux/arm64 debian:bookworm-slim" in self.df

    def test_no_cross_compiler(self):
        # The whole point of pinning the container platform is that the
        # build is native inside it — no cross toolchain, no sysroot.
        assert "aarch64-linux-gnu" not in self.df
        assert "CMAKE_TOOLCHAIN_FILE" not in self.df

    def test_smoke_test_still_executes(self):
        # An emulated container still runs the binaries it builds, so the
        # arm64 archive gets the same round-trip proof as amd64.
        assert "sh /tmp/smoke.sh /staging 1.5.7 so" in self.df


class TestMuslAmd64:
    def setup_method(self):
        self.df = bz.make_dockerfile("1.5.7", "amd64", "musl")

    def test_alpine_base(self):
        assert "FROM --platform=linux/amd64 alpine:3.20" in self.df

    def test_uses_apk(self):
        assert "apk add --no-cache" in self.df
        assert "apt-get" not in self.df

    def test_same_cmake_flags_as_glibc(self):
        glibc = bz.make_dockerfile("1.5.7", "amd64", "linux")
        for flag in bz.cmake_configure_args("musl", "amd64", "/staging"):
            assert flag in self.df
            assert flag in glibc


class TestMuslArm64:
    def test_container_pinned(self):
        df = bz.make_dockerfile("1.5.7", "arm64", "musl")
        assert "FROM --platform=linux/arm64 alpine:3.20" in df


class TestMacHasNoDockerfile:
    def test_raises(self):
        # There is no macOS container image; asking for one has to fail
        # loudly rather than silently produce a linux archive named mac.
        with pytest.raises(ValueError):
            bz.make_dockerfile("1.5.7", "arm64", "mac")


class TestCmakeArgsAreSharedWithTheMacPath:
    def test_mac_adds_only_apple_specific_flags(self):
        linux_args = bz.cmake_configure_args("linux", "arm64", "/staging")
        mac_args = bz.cmake_configure_args("mac", "arm64", "/staging")
        assert linux_args == mac_args[: len(linux_args)]
        extra = mac_args[len(linux_args) :]
        assert any("CMAKE_OSX_ARCHITECTURES=arm64" in a for a in extra)
        assert any("CMAKE_INSTALL_NAME_DIR=@rpath" in a for a in extra), (
            "without an @rpath install_name the dylib records the build "
            "machine's staging path and won't load anywhere else"
        )

    def test_mac_x64_slice(self):
        args = bz.cmake_configure_args("mac", "amd64", "/staging")
        assert "-DCMAKE_OSX_ARCHITECTURES=x86_64" in args

    def test_args_file_is_the_configure_line(self):
        text = bz.cmake_args_text("linux", "amd64", "/staging")
        for arg in bz.cmake_configure_args("linux", "amd64", "/staging"):
            assert arg in text
