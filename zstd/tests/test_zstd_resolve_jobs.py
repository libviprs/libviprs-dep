"""CLI --platform / --arch resolution and arch alias handling."""

import build_zstd as bz
import pytest


class TestDefaultMatrix:
    def test_four_combos(self):
        assert bz.resolve_jobs(None, None) == [
            ("linux", "amd64"),
            ("linux", "arm64"),
            ("musl", "amd64"),
            ("musl", "arm64"),
        ]

    def test_mac_excluded(self):
        # mac needs a macOS host, so it is opt-in exactly like pdfium's.
        assert all(plat != "mac" for plat, _ in bz.resolve_jobs(None, None))

    def test_returns_a_copy(self):
        jobs = bz.resolve_jobs(None, None)
        jobs.append(("bogus", "arch"))
        assert ("bogus", "arch") not in bz.DEFAULT_JOBS


class TestPlatformFilter:
    def test_single_platform(self):
        assert bz.resolve_jobs(["musl"], None) == [("musl", "amd64"), ("musl", "arm64")]

    def test_two_platforms(self):
        jobs = bz.resolve_jobs(["linux", "musl"], None)
        assert len(jobs) == 4

    def test_mac_falls_back_to_both_arches(self):
        # mac isn't in the default matrix, so filtering can't find it —
        # the request has to be honoured by cross-producing instead.
        assert bz.resolve_jobs(["mac"], None) == [("mac", "amd64"), ("mac", "arm64")]


class TestArchFilter:
    def test_amd64_only(self):
        assert bz.resolve_jobs(None, "amd64") == [("linux", "amd64"), ("musl", "amd64")]

    def test_arm64_only(self):
        assert bz.resolve_jobs(None, "arm64") == [("linux", "arm64"), ("musl", "arm64")]


class TestBothFlags:
    def test_cross_product(self):
        assert bz.resolve_jobs(["mac"], "arm64") == [("mac", "arm64")]

    def test_explicit_beats_default_matrix(self):
        assert bz.resolve_jobs(["linux", "musl"], "arm64") == [
            ("linux", "arm64"),
            ("musl", "arm64"),
        ]


class TestNormalizeArch:
    def test_none_passes_through(self):
        assert bz.normalize_arch(None) is None

    def test_canonical(self):
        assert bz.normalize_arch("amd64") == "amd64"
        assert bz.normalize_arch("arm64") == "arm64"

    def test_aliases(self):
        assert bz.normalize_arch("x86_64") == "amd64"
        assert bz.normalize_arch("x64") == "amd64"
        assert bz.normalize_arch("aarch64") == "arm64"

    def test_unknown_raises(self):
        with pytest.raises(ValueError):
            bz.normalize_arch("riscv64")
