"""The build matrix, which is five cells and not one more.

The epic says so, the issue says so, and the consumer's `build.rs` keys off
the Rust triple that each cell records in `LINKINFO.json`. A sixth cell
appearing, or a triple drifting, is a downstream break that nothing else in
this tree would notice, so it is pinned here.
"""

import build_acadsharp as ba
import pytest

# The whole matrix, exactly as issue #48 tabulates it: the archive this repo
# publishes, and the Rust triple the crate asks for.
EXPECTED = {
    ("linux", "amd64"): ("acadsharp-linux-x64.tgz", "x86_64-unknown-linux-gnu"),
    ("linux", "arm64"): ("acadsharp-linux-arm64.tgz", "aarch64-unknown-linux-gnu"),
    ("musl", "amd64"): ("acadsharp-musl-x64.tgz", "x86_64-unknown-linux-musl"),
    ("musl", "arm64"): ("acadsharp-musl-arm64.tgz", "aarch64-unknown-linux-musl"),
    ("mac", "arm64"): ("acadsharp-mac-arm64.tgz", "aarch64-apple-darwin"),
}


class TestTheMatrixIsExactlyFiveCells:
    def test_matrix_has_the_five_cells_and_nothing_else(self):
        assert sorted(ba.MATRIX) == sorted(EXPECTED)

    def test_each_cell_maps_to_its_archive_name(self):
        for (plat, arch), (name, _triple) in EXPECTED.items():
            assert ba.archive_name(plat, arch) == name

    def test_each_cell_maps_to_its_rust_triple(self):
        for (plat, arch), (_name, triple) in EXPECTED.items():
            assert ba.rust_triple(plat, arch) == triple

    def test_no_cell_is_a_microsoft_one(self):
        for plat, arch in ba.MATRIX:
            assert plat in ("linux", "musl", "mac")
            assert not ba.rid_for(plat, arch).startswith("win")


class TestTheDefaultJobsAreTheContainerBuildableFour:
    """`mac` is out of the default for the same reason it is in pdfium and zstd.

    There is no macOS container image, so a default matrix that included it
    would fail on every Linux host. It stays in MATRIX because it is a cell
    this repo publishes; it stays out of DEFAULT_JOBS because no Linux host
    can run it.
    """

    def test_default_jobs_are_the_four_linux_and_musl_cells(self):
        assert sorted(ba.DEFAULT_JOBS) == sorted(
            [("linux", "amd64"), ("linux", "arm64"), ("musl", "amd64"), ("musl", "arm64")]
        )

    def test_default_jobs_are_the_matrix_minus_mac(self):
        assert sorted(ba.DEFAULT_JOBS) == sorted(c for c in ba.MATRIX if c[0] != "mac")

    def test_mac_is_reachable_by_asking_for_it(self):
        assert ba.resolve_jobs(["mac"], "arm64") == [("mac", "arm64")]


class TestTheDriverRefusesAnythingElse:
    def test_an_unknown_platform_is_refused(self):
        with pytest.raises(ValueError, match="platform"):
            ba.rid_for("freebsd", "amd64")

    def test_an_unknown_cpu_is_refused(self):
        with pytest.raises(ValueError, match="[Uu]nknown arch"):
            ba.normalize_arch("riscv64")

    def test_a_windows_platform_is_refused(self):
        with pytest.raises(ValueError):
            ba.rid_for("windows", "amd64")

    def test_mac_on_x64_is_not_a_cell(self):
        # Apple Silicon only. An x86_64 mac archive would be a target
        # nobody has built, measured or promised.
        assert ("mac", "amd64") not in ba.MATRIX
        with pytest.raises(ValueError, match="not a target"):
            ba.rid_for("mac", "amd64")

    def test_the_cli_only_offers_the_three_platforms(self):
        assert ba.PLATFORMS == ["linux", "musl", "mac"]


class TestArchNormalisation:
    def test_aliases_resolve(self):
        assert ba.normalize_arch("x86_64") == "amd64"
        assert ba.normalize_arch("x64") == "amd64"
        assert ba.normalize_arch("aarch64") == "arm64"
        assert ba.normalize_arch("arm64") == "arm64"

    def test_none_passes_through(self):
        assert ba.normalize_arch(None) is None


class TestResolveJobs:
    def test_no_flags_is_the_default_matrix(self):
        assert ba.resolve_jobs(None, None) == list(ba.DEFAULT_JOBS)

    def test_platform_filters_the_default(self):
        assert ba.resolve_jobs(["musl"], None) == [("musl", "amd64"), ("musl", "arm64")]

    def test_arch_filters_the_default(self):
        assert ba.resolve_jobs(None, "arm64") == [("linux", "arm64"), ("musl", "arm64")]

    def test_both_flags_is_the_explicit_cross_product(self):
        assert ba.resolve_jobs(["linux", "musl"], "amd64") == [
            ("linux", "amd64"),
            ("musl", "amd64"),
        ]

    def test_a_platform_the_default_does_not_cover_falls_back_to_its_own_cells(self):
        # mac has one cell, so asking for the platform alone must not
        # invent a mac/amd64 job the matrix does not have.
        assert ba.resolve_jobs(["mac"], None) == [("mac", "arm64")]


class TestRuntimeIdentifiers:
    def test_every_cell_has_a_dotnet_rid(self):
        assert ba.rid_for("linux", "amd64") == "linux-x64"
        assert ba.rid_for("linux", "arm64") == "linux-arm64"
        assert ba.rid_for("musl", "amd64") == "linux-musl-x64"
        assert ba.rid_for("musl", "arm64") == "linux-musl-arm64"
        assert ba.rid_for("mac", "arm64") == "osx-arm64"

    def test_the_rid_table_and_the_matrix_agree(self):
        assert sorted(ba.rid_for(p, a) for p, a in ba.MATRIX) == sorted(ba.TARGETS)


class TestStaticIsAttemptedOnlyWhereItCanBe:
    """NativeAOT static archives are a Linux/musl story, per ADR 0001.

    The mac cell is unmeasured for shared, let alone static, so it is not
    in the static list. Nothing here promises the static smoke passes; that
    is what `static_certified` in the manifest records, per target.
    """

    def test_mac_is_not_a_static_target(self):
        assert "osx-arm64" not in ba.STATIC_TARGETS

    def test_every_static_target_is_in_the_matrix(self):
        assert set(ba.STATIC_TARGETS) <= set(ba.TARGETS)
