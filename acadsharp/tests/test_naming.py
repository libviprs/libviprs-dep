"""Archive, staging directory and release tag naming.

The names are the contract with everything downstream: the release
workflow's asset globs, the download URLs in the docs, and
`scripts/verify_archive.sh`'s platform/cpu inference all key off them.
This dependency follows the repo's `<dep>-<platform>-<cpu>.tgz` shape
rather than the design document's `acadsharp-native-<version>-<triple>`
form, because a third scheme would break this repo's own verifier.
"""

import importlib.util
import os

import build_acadsharp as ba

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestArchiveName:
    def test_linux_amd64(self):
        assert ba.archive_name("linux", "amd64") == "acadsharp-linux-x64.tgz"

    def test_linux_arm64(self):
        assert ba.archive_name("linux", "arm64") == "acadsharp-linux-arm64.tgz"

    def test_musl_amd64(self):
        assert ba.archive_name("musl", "amd64") == "acadsharp-musl-x64.tgz"

    def test_musl_arm64(self):
        assert ba.archive_name("musl", "arm64") == "acadsharp-musl-arm64.tgz"

    def test_mac_arm64(self):
        assert ba.archive_name("mac", "arm64") == "acadsharp-mac-arm64.tgz"

    def test_shape_matches_the_other_dependencies(self):
        for plat, arch in ba.MATRIX:
            name = ba.archive_name(plat, arch)
            assert name.startswith(f"acadsharp-{plat}-")
            assert name.endswith(".tgz")
            assert name.count("-") == 2

    def test_no_version_in_the_archive_name(self):
        # The version lives in the release tag, not the filename — the
        # same split pdfium and zstd use, so a consumer's URL template
        # works across all three.
        version = ba.read_version()
        for plat, arch in ba.MATRIX:
            assert version not in ba.archive_name(plat, arch)

    def test_no_rust_triple_in_the_archive_name(self):
        # The triple lives inside LINKINFO.json where build.rs reads it.
        for plat, arch in ba.MATRIX:
            assert ba.rust_triple(plat, arch) not in ba.archive_name(plat, arch)


class TestStagingDirName:
    def test_matches_archive_name(self):
        # verify_archive.sh derives the expected top-level directory by
        # stripping .tgz off the filename; if these two ever disagree
        # every archive fails verification.
        for plat, arch in ba.MATRIX:
            assert ba.staging_dir_name(plat, arch) + ".tgz" == ba.archive_name(plat, arch)

    def test_no_extension(self):
        assert "." not in ba.staging_dir_name("linux", "amd64")


class TestReleaseTag:
    def test_format(self):
        assert ba.release_tag("3.7.1-viprs.1") == "acadsharp-3.7.1-viprs.1"

    def test_carries_the_shim_revision(self):
        # Two shims over the same upstream release must not collide on
        # the tag, which is the only place the version is recorded.
        assert ba.release_tag("3.7.1-viprs.1") != ba.release_tag("3.7.1-viprs.2")

    def test_does_not_collide_with_the_other_dependencies_tags(self):
        tag = ba.release_tag(ba.read_version())
        assert tag.startswith("acadsharp-")
        for other in ("pdfium-", "zstd-"):
            assert not tag.startswith(other)


class TestLibraryNames:
    def test_shared_ext(self):
        assert ba.shared_ext("linux") == "so"
        assert ba.shared_ext("musl") == "so"
        assert ba.shared_ext("mac") == "dylib"

    def test_shared_library_is_named_for_the_dependency_not_the_assembly(self):
        # The publish emits viprs_acadsharp.so (the assembly name). The
        # archive ships libacadsharp_native.so, because that is what
        # LINKINFO.json's `shared_library` says and what a `-l` flag can
        # name.
        assert ba.shared_library_name("linux") == "libacadsharp_native.so"
        assert ba.shared_library_name("musl") == "libacadsharp_native.so"
        assert ba.shared_library_name("mac") == "libacadsharp_native.dylib"

    def test_static_library_name(self):
        assert ba.STATIC_LIBRARY_NAME == "libacadsharp_native.a"

    def test_link_name_is_what_dash_l_would_take(self):
        # `-lacadsharp_native` has to find both.
        for name in (ba.shared_library_name("linux"), ba.STATIC_LIBRARY_NAME):
            assert name.startswith("lib")


class TestNamingAgreesWithTheSiblingDrivers:
    """One convention across the repo, read out of the other drivers.

    Repeating the shape as a string here would let the three drift apart
    silently, so this asks zstd's own driver what it produces and checks
    the shapes line up.
    """

    def test_same_platform_and_cpu_vocabulary_as_zstd(self):
        zstd = _load("zstd_for_naming_check", os.path.join(REPO_ROOT, "zstd", "build_zstd.py"))
        for plat, arch in (("linux", "amd64"), ("musl", "arm64"), ("mac", "arm64")):
            mine = ba.archive_name(plat, arch)
            theirs = zstd.archive_name(plat, arch)
            assert mine.replace("acadsharp", "zstd", 1) == theirs

    def test_same_tag_shape_as_zstd(self):
        zstd = _load("zstd_for_tag_check", os.path.join(REPO_ROOT, "zstd", "build_zstd.py"))
        assert ba.release_tag("9.9.9") == zstd.release_tag("9.9.9").replace("zstd", "acadsharp", 1)
