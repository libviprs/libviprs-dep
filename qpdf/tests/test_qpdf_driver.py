"""The qpdf build driver: version pin, naming, matrix and configure flags."""

import os
import subprocess
import re

import build_qpdf as b
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))


class TestVersionPin:
    def test_version_file_is_a_plain_version(self):
        assert re.fullmatch(r"\d+\.\d+\.\d+", b.read_version())

    def test_shipped_version_has_a_pinned_source_hash(self):
        assert re.fullmatch(r"[0-9a-f]{64}", b.source_sha256(b.read_version()))

    def test_unpinned_version_is_refused(self):
        with pytest.raises(ValueError, match="No pinned source hash"):
            b.source_sha256("0.0.1")

    def test_source_url_is_the_upstream_release_tarball(self):
        assert b.source_url("12.4.2") == (
            "https://github.com/qpdf/qpdf/releases/download/v12.4.2/qpdf-12.4.2.tar.gz"
        )


class TestNaming:
    def test_archive_names(self):
        assert b.archive_name("linux", "amd64") == "qpdf-linux-x64.tgz"
        assert b.archive_name("linux", "arm64") == "qpdf-linux-arm64.tgz"
        assert b.archive_name("mac", "arm64") == "qpdf-mac-arm64.tgz"

    def test_staging_dir_matches_archive_stem(self):
        assert b.staging_dir_name("linux", "amd64") == "qpdf-linux-x64"

    def test_release_tag(self):
        assert b.release_tag("12.4.2") == "qpdf-12.4.2"


class TestMatrix:
    def test_default_matrix_is_the_two_linux_cells(self):
        assert b.resolve_jobs(None, None) == [("linux", "amd64"), ("linux", "arm64")]

    def test_mac_arm64_is_supported(self):
        assert b.resolve_jobs(["mac"], "arm64") == [("mac", "arm64")]

    def test_mac_alone_means_arm64_only(self):
        assert b.resolve_jobs(["mac"], None) == [("mac", "arm64")]

    def test_mac_amd64_is_refused(self):
        with pytest.raises(ValueError, match="mac"):
            b.resolve_jobs(["mac"], "amd64")

    def test_musl_is_not_a_platform(self):
        assert "musl" not in b.PLATFORMS


class TestConfigure:
    def args(self, plat="linux", arch="amd64"):
        return b.cmake_configure_args(plat, arch, "/staging")

    def test_crypto_is_native_so_no_openssl_is_needed(self):
        a = self.args()
        assert "-DUSE_IMPLICIT_CRYPTO=OFF" in a
        assert "-DREQUIRE_CRYPTO_NATIVE=ON" in a

    def test_both_library_kinds_and_a_plain_libdir(self):
        a = self.args()
        assert "-DBUILD_SHARED_LIBS=ON" in a
        assert "-DBUILD_STATIC_LIBS=ON" in a
        assert "-DCMAKE_INSTALL_LIBDIR=lib" in a

    def test_docs_and_examples_are_not_built(self):
        a = self.args()
        assert "-DBUILD_DOC=OFF" in a
        assert "-DINSTALL_EXAMPLES=OFF" in a

    def test_cli_finds_its_library_after_unpacking(self):
        assert "-DCMAKE_INSTALL_RPATH=$ORIGIN/../lib" in self.args()
        assert "-DCMAKE_INSTALL_RPATH=@loader_path/../lib" in self.args("mac", "arm64")

    def test_mac_pins_arch_and_deployment_target(self):
        a = self.args("mac", "arm64")
        assert "-DCMAKE_OSX_ARCHITECTURES=arm64" in a
        assert any(x.startswith("-DCMAKE_OSX_DEPLOYMENT_TARGET=") for x in a)

    def test_args_text_lists_every_flag(self):
        text = b.cmake_args_text("linux", "amd64", "/staging")
        for flag in self.args():
            assert flag in text.splitlines()


class TestReleaseNotes:
    def test_notes_name_version_source_and_licence(self):
        v = b.read_version()
        notes = b.release_notes(v)
        assert v in notes
        assert b.source_sha256(v) in notes
        assert "Apache-2.0" in notes
        assert "test-time" in notes


class TestFixtures:
    def test_good_pdf_has_a_correct_xref(self):
        data = open(os.path.join(HERE, "fixtures", "good.pdf"), "rb").read()
        assert data.startswith(b"%PDF-")
        start = int(re.search(rb"startxref\n(\d+)\n", data).group(1))
        assert data[start : start + 4] == b"xref"
        for n, off in enumerate(re.findall(rb"(\d{10}) 00000 n ", data), 1):
            assert data[int(off) :].startswith(b"%d 0 obj" % n)

    def test_corrupt_pdf_has_no_trailer(self):
        data = open(os.path.join(HERE, "fixtures", "corrupt.pdf"), "rb").read()
        assert b"startxref" not in data


class TestRelocatablePkgConfig:
    """relocate-pc.sh rewrites every absolute directory CMake baked into the file."""

    def relocate(self, tmp_path):
        pc_dir = tmp_path / "stage" / "lib" / "pkgconfig"
        pc_dir.mkdir(parents=True)
        (pc_dir / "libqpdf.pc").write_text(
            "prefix=/home/builder/out/stage\n"
            "exec_prefix=${prefix}\n"
            "libdir=/home/builder/out/stage/lib\n"
            "includedir=/home/builder/out/stage/include\n"
            "Name: libqpdf\n"
            "Libs: -L${libdir} -lqpdf\n"
        )
        script = tmp_path / "relocate-pc.sh"
        script.write_text(b.RELOCATE_PKGCONFIG_SH)
        subprocess.run(["sh", str(script), str(tmp_path / "stage")], check=True)
        return (pc_dir / "libqpdf.pc").read_text()

    def test_prefix_is_relative_to_the_file(self, tmp_path):
        assert "prefix=${pcfiledir}/../..\n" in self.relocate(tmp_path)

    def test_libdir_and_includedir_hang_off_the_prefix(self, tmp_path):
        text = self.relocate(tmp_path)
        assert "libdir=${prefix}/lib\n" in text
        assert "includedir=${prefix}/include\n" in text

    def test_no_build_host_path_is_left(self, tmp_path):
        assert "/home/builder" not in self.relocate(tmp_path)


class TestMacNativeBuild:
    """The mac path records a neutral prefix, as the Linux Dockerfile does with /staging."""

    def source(self):
        import inspect

        return inspect.getsource(b._build_mac_native)

    def test_cmake_args_txt_does_not_carry_the_staging_path(self):
        assert 'cmake_args_text("mac", arch, "/staging", JPEG_LINUX_PREFIX)' in self.source()

    def test_configure_uses_the_neutral_prefix_and_install_picks_the_real_one(self):
        src = self.source()
        assert 'cmake_configure_args("mac", arch, "/staging", jpeg)' in src
        assert '"--prefix", staging' in src


class TestRelocatableCmakeTargets:
    """relocate-cmake.sh ships libjpeg.a in the archive and points the targets at it (#177)."""

    BAKED = (
        "add_library(qpdf::libqpdf_static STATIC IMPORTED)\n"
        "set_target_properties(qpdf::libqpdf_static PROPERTIES\n"
        '  INTERFACE_INCLUDE_DIRECTORIES "${_IMPORT_PREFIX}/include"\n'
        '  INTERFACE_LINK_LIBRARIES "%s;%s"\n)\n'
    )

    def relocate(
        self, tmp_path, libz="/usr/lib/x86_64-linux-gnu/libz.so", jpeg="/opt/jpeg/lib/libjpeg.a"
    ):
        stage = tmp_path / "stage"
        cmake_dir = stage / "lib" / "cmake" / "qpdf"
        cmake_dir.mkdir(parents=True)
        (cmake_dir / "libqpdfTargets.cmake").write_text(self.BAKED % (libz, jpeg))
        (cmake_dir / "libqpdfTargets-release.cmake").write_text(
            'IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/lib/libqpdf.a"\n'
        )
        src = tmp_path / "jpeg" / "lib"
        src.mkdir(parents=True)
        (src / "libjpeg.a").write_bytes(b"!<arch>\nfake\n")
        script = tmp_path / "relocate-cmake.sh"
        script.write_text(b.RELOCATE_CMAKE_SH)
        subprocess.run(["sh", str(script), str(stage), str(src / "libjpeg.a")], check=True)
        return stage, (cmake_dir / "libqpdfTargets.cmake").read_text()

    def test_libjpeg_is_copied_into_the_archive_lib_dir(self, tmp_path):
        stage, _ = self.relocate(tmp_path)
        assert (stage / "lib" / "libjpeg.a").read_bytes() == b"!<arch>\nfake\n"

    def test_a_read_only_source_gives_a_writable_copy(self, tmp_path):
        jpeg = tmp_path / "jpeg" / "lib" / "libjpeg.a"
        jpeg.parent.mkdir(parents=True)
        jpeg.write_bytes(b"!<arch>\n")
        jpeg.chmod(0o444)
        stage = tmp_path / "stage"
        (stage / "lib" / "cmake" / "qpdf").mkdir(parents=True)
        (stage / "lib" / "cmake" / "qpdf" / "libqpdfTargets.cmake").write_text(
            'INTERFACE_LINK_LIBRARIES "z;/opt/jpeg/lib/libjpeg.a"\n'
        )
        script = tmp_path / "relocate-cmake.sh"
        script.write_text(b.RELOCATE_CMAKE_SH)
        subprocess.run(["sh", str(script), str(stage), str(jpeg)], check=True)
        assert os.access(stage / "lib" / "libjpeg.a", os.W_OK)

    def test_the_target_points_at_the_copy_relative_to_the_prefix(self, tmp_path):
        _, text = self.relocate(tmp_path)
        assert '"z;${_IMPORT_PREFIX}/lib/libjpeg.a"' in text

    def test_the_homebrew_path_is_rewritten_too(self, tmp_path):
        _, text = self.relocate(
            tmp_path, libz="z", jpeg="/opt/homebrew/opt/jpeg-turbo/lib/libjpeg.a"
        )
        assert '"z;${_IMPORT_PREFIX}/lib/libjpeg.a"' in text

    def test_no_build_host_path_is_left(self, tmp_path):
        _, text = self.relocate(tmp_path)
        assert "/opt/" not in text and "/usr/" not in text

    def test_other_cmake_files_are_left_alone(self, tmp_path):
        stage, _ = self.relocate(tmp_path)
        rel = (stage / "lib" / "cmake" / "qpdf" / "libqpdfTargets-release.cmake").read_text()
        assert rel == 'IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/lib/libqpdf.a"\n'

    def test_a_rewrite_that_did_not_take_fails_the_build(self, tmp_path):
        stage = tmp_path / "stage"
        cmake_dir = stage / "lib" / "cmake" / "qpdf"
        cmake_dir.mkdir(parents=True)
        (cmake_dir / "libqpdfTargets.cmake").write_text(
            'INTERFACE_LINK_LIBRARIES "/weird/libfoo.a"\n'
        )
        script = tmp_path / "relocate-cmake.sh"
        script.write_text(b.RELOCATE_CMAKE_SH)
        jpeg = tmp_path / "libjpeg.a"
        jpeg.write_bytes(b"!<arch>\n")
        r = subprocess.run(
            ["sh", str(script), str(stage), str(jpeg)], capture_output=True, text=True
        )
        assert r.returncode != 0
        assert "/weird/libfoo.a" in r.stdout + r.stderr

    def test_it_is_one_of_the_helper_scripts(self):
        assert "relocate-cmake.sh" in dict(b.HELPER_SCRIPTS)

    def test_the_linux_dockerfile_relocates_after_install_and_before_finalize(self):
        text = b.make_dockerfile(b.read_version(), "amd64", "linux")
        assert "relocate-cmake.sh /staging /opt/jpeg/lib/libjpeg.a" in text
        assert text.index("cmake --install") < text.index("relocate-cmake.sh /staging")
        assert text.index("relocate-cmake.sh /staging") < text.index("finalize.sh /staging")

    def test_the_mac_native_build_relocates_with_homebrews_libjpeg(self):
        import inspect

        src = inspect.getsource(b._build_mac_native)
        assert 'helpers["relocate-cmake.sh"]' in src
        assert 'os.path.join(jpeg, "lib", "libjpeg.a")' in src


class TestFindPackageSmoke:
    """A cmake consumer resolves the archive from wherever it is unpacked (#177)."""

    FIX = os.path.join(HERE, "fixtures", "find_package")
    SCRIPT = os.path.join(HERE, "..", "scripts", "find_package_smoke.sh")

    def test_the_consumer_project_uses_find_package_on_both_targets(self):
        text = open(os.path.join(self.FIX, "CMakeLists.txt")).read()
        assert "find_package(qpdf CONFIG REQUIRED)" in text
        assert "qpdf::libqpdf_static" in text
        assert "qpdf::libqpdf)" in text or "qpdf::libqpdf " in text

    def test_the_consumer_project_names_no_path_of_its_own(self):
        text = open(os.path.join(self.FIX, "CMakeLists.txt")).read()
        assert not re.search(r'(^|[\s"=(])/[A-Za-z]', text, re.M)

    def test_the_consumer_program_checks_the_version(self):
        text = open(os.path.join(self.FIX, "main.c")).read()
        assert "qpdf_get_qpdf_version" in text

    def test_the_script_copies_the_prefix_before_using_it(self):
        text = open(self.SCRIPT).read()
        assert "cp -R" in text or "cp -a" in text
        assert "CMAKE_PREFIX_PATH" in text

    def test_the_linux_build_runs_it_on_the_staged_tree(self):
        text = b.make_dockerfile(b.read_version(), "amd64", "linux")
        assert "find_package_smoke.sh /staging" in text
        assert text.index("find_package_smoke.sh /staging") > text.index("smoke.sh /staging")

    def test_the_smoke_script_survives_a_path_with_spaces(self):
        # the native mac build ran from a directory with a space in it
        assert "run_qpdf()" in b.SMOKE_TEST_SH
        assert '"$STAGING/bin/qpdf" "$@"' in b.SMOKE_TEST_SH

    def test_the_driver_puts_the_script_in_the_build_context(self):
        import inspect

        assert "find_package_smoke.sh" in inspect.getsource(b._build_docker)
