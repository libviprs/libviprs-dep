"""Tests for archive naming and release tag conventions."""

import build_pdfium as bp


class TestArchiveName:
    def test_linux_amd64(self):
        assert bp.archive_name("linux", "amd64") == "pdfium-linux-x64.tgz"

    def test_linux_arm64(self):
        assert bp.archive_name("linux", "arm64") == "pdfium-linux-arm64.tgz"

    def test_musl_amd64(self):
        assert bp.archive_name("musl", "amd64") == "pdfium-musl-x64.tgz"

    def test_musl_arm64(self):
        assert bp.archive_name("musl", "arm64") == "pdfium-musl-arm64.tgz"

    def test_format_is_platform_cpu(self):
        name = bp.archive_name("linux", "amd64")
        assert name.startswith("pdfium-linux-")
        assert name.endswith(".tgz")


class TestStagingDirName:
    def test_linux_amd64(self):
        assert bp.staging_dir_name("linux", "amd64") == "pdfium-linux-x64"

    def test_linux_arm64(self):
        assert bp.staging_dir_name("linux", "arm64") == "pdfium-linux-arm64"

    def test_musl_amd64(self):
        assert bp.staging_dir_name("musl", "amd64") == "pdfium-musl-x64"

    def test_musl_arm64(self):
        assert bp.staging_dir_name("musl", "arm64") == "pdfium-musl-arm64"

    def test_no_extension(self):
        name = bp.staging_dir_name("linux", "amd64")
        assert "." not in name


class TestReleaseTag:
    def test_format(self):
        assert bp.release_tag("7725") == "pdfium-7725"

    def test_numeric_string(self):
        assert bp.release_tag("6666") == "pdfium-6666"
