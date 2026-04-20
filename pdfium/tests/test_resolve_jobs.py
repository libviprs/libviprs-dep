"""Tests for resolve_jobs (CLI --platform/--arch -> concrete job list)."""

import pytest
from build_pdfium import DEFAULT_JOBS, normalize_arch, resolve_jobs


class TestDefaultMatrix:
    def test_no_flags_returns_default(self):
        assert resolve_jobs(None, None) == list(DEFAULT_JOBS)

    def test_default_is_four_jobs(self):
        # mac was removed from the default matrix because PDFium's GN
        # config invokes xcodebuild during `gn gen`, which doesn't exist
        # in a Debian container — mac builds require a macOS host.
        assert len(DEFAULT_JOBS) == 4

    def test_default_excludes_mac_amd64(self):
        assert ("mac", "amd64") not in DEFAULT_JOBS

    def test_default_excludes_mac_arm64(self):
        assert ("mac", "arm64") not in DEFAULT_JOBS


class TestPlatformFilter:
    def test_platform_linux_only(self):
        jobs = resolve_jobs(["linux"], None)
        assert jobs == [("linux", "amd64"), ("linux", "arm64")]

    def test_platform_musl_only(self):
        jobs = resolve_jobs(["musl"], None)
        assert jobs == [("musl", "amd64"), ("musl", "arm64")]

    def test_platform_mac_falls_back_to_both_archs(self):
        # mac is not in the default matrix, so resolve_jobs hits the
        # ``missing`` branch that cross-products the requested platform
        # with both archs.
        jobs = resolve_jobs(["mac"], None)
        assert jobs == [("mac", "amd64"), ("mac", "arm64")]

    def test_platform_linux_musl_both(self):
        jobs = resolve_jobs(["linux", "musl"], None)
        assert jobs == [
            ("linux", "amd64"),
            ("linux", "arm64"),
            ("musl", "amd64"),
            ("musl", "arm64"),
        ]


class TestArchFilter:
    def test_arch_amd64_excludes_mac(self):
        jobs = resolve_jobs(None, "amd64")
        assert jobs == [("linux", "amd64"), ("musl", "amd64")]
        assert ("mac", "amd64") not in jobs

    def test_arch_arm64_excludes_mac(self):
        # mac not in DEFAULT_JOBS, so filtering by arch never surfaces it.
        jobs = resolve_jobs(None, "arm64")
        assert jobs == [("linux", "arm64"), ("musl", "arm64")]


class TestBothFlags:
    def test_mac_amd64_honored_when_explicit(self):
        jobs = resolve_jobs(["mac"], "amd64")
        assert jobs == [("mac", "amd64")]

    def test_linux_arm64_explicit(self):
        jobs = resolve_jobs(["linux"], "arm64")
        assert jobs == [("linux", "arm64")]

    def test_multiple_platforms_single_arch(self):
        jobs = resolve_jobs(["linux", "mac", "musl"], "amd64")
        assert jobs == [("linux", "amd64"), ("mac", "amd64"), ("musl", "amd64")]


class TestNormalizeArch:
    def test_none_stays_none(self):
        assert normalize_arch(None) is None

    def test_canonical_names_pass_through(self):
        assert normalize_arch("amd64") == "amd64"
        assert normalize_arch("arm64") == "arm64"

    def test_x86_64_aliases_to_amd64(self):
        assert normalize_arch("x86_64") == "amd64"

    def test_x64_aliases_to_amd64(self):
        assert normalize_arch("x64") == "amd64"

    def test_aarch64_aliases_to_arm64(self):
        assert normalize_arch("aarch64") == "arm64"

    def test_unknown_arch_raises(self):
        with pytest.raises(ValueError):
            normalize_arch("riscv64")
