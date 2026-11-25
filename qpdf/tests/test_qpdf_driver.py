"""The qpdf build driver: version pin, naming, matrix and configure flags."""

import os
import re

import pytest

import build_qpdf as b

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
