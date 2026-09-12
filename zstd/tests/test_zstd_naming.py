"""Archive, staging directory and release tag naming.

The names are the contract with everything downstream: the release
workflow's asset globs, the download URLs in the READMEs, and
verify_archive.sh's platform/cpu inference all key off them.
"""

import build_zstd as bz


class TestArchiveName:
    def test_linux_amd64(self):
        assert bz.archive_name("linux", "amd64") == "zstd-linux-x64.tgz"

    def test_linux_arm64(self):
        assert bz.archive_name("linux", "arm64") == "zstd-linux-arm64.tgz"

    def test_musl_amd64(self):
        assert bz.archive_name("musl", "amd64") == "zstd-musl-x64.tgz"

    def test_musl_arm64(self):
        assert bz.archive_name("musl", "arm64") == "zstd-musl-arm64.tgz"

    def test_mac_arm64(self):
        assert bz.archive_name("mac", "arm64") == "zstd-mac-arm64.tgz"

    def test_matches_pdfium_convention(self):
        # <library>-<platform>-<cpu>.tgz, same shape pdfium publishes, so
        # a consumer's URL template works for both dependencies.
        for plat, arch in bz.DEFAULT_JOBS:
            name = bz.archive_name(plat, arch)
            assert name.startswith(f"zstd-{plat}-")
            assert name.endswith(".tgz")
            assert name.count("-") == 2


class TestStagingDirName:
    def test_matches_archive_name(self):
        # verify_archive.sh derives the expected top-level directory by
        # stripping .tgz off the filename; if these two ever disagree
        # every archive fails verification.
        for plat, arch in bz.DEFAULT_JOBS + [("mac", "arm64")]:
            assert bz.staging_dir_name(plat, arch) + ".tgz" == bz.archive_name(plat, arch)

    def test_no_extension(self):
        assert "." not in bz.staging_dir_name("linux", "amd64")


class TestReleaseTag:
    def test_format(self):
        assert bz.release_tag("1.5.7") == "zstd-1.5.7"

    def test_does_not_collide_with_pdfium_tags(self):
        # Both dependencies publish to the same repo's Releases, so the
        # tag namespace has to stay disjoint.
        assert bz.release_tag("1.5.7").startswith("zstd-")


class TestSharedExt:
    def test_linux_and_musl_are_so(self):
        assert bz.shared_ext("linux") == "so"
        assert bz.shared_ext("musl") == "so"

    def test_mac_is_dylib(self):
        assert bz.shared_ext("mac") == "dylib"
