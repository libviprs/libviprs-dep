"""`acadsharp/VERSION` drives the source, the tag and both manifests.

`tests/test_acadsharp_version.py` from the spike already holds the split
itself (`3.7.1-viprs.1` into an upstream version and a shim revision).
What lands here is the packaging half of the same file: the upstream half
has to have a pinned tarball digest and a pinned upstream commit, and the
version has to reach `LINKINFO.json` in both of its forms, because the
consumer reads `artifact_version` and `acadsharp_version` separately.
"""

import os
import re

import build_acadsharp as ba
import pytest

ACAD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VERSION_PATH = os.path.join(ACAD_DIR, "VERSION")


class TestBothHalvesParse:
    def test_the_file_is_one_bare_version(self):
        with open(VERSION_PATH) as f:
            assert len(f.read().strip().splitlines()) == 1
        assert re.fullmatch(r"\d+\.\d+\.\d+-viprs\.\d+", ba.read_version())

    def test_both_halves_come_back(self):
        upstream, shim = ba.split_version(ba.read_version())
        assert re.fullmatch(r"\d+\.\d+\.\d+", upstream)
        assert re.fullmatch(r"\d+", shim)

    def test_an_empty_file_is_refused(self, tmp_path):
        empty = tmp_path / "VERSION"
        empty.write_text("\n")
        with pytest.raises(ValueError, match="single source of truth"):
            ba.read_version(str(empty))

    def test_a_bare_upstream_version_is_refused(self):
        with pytest.raises(ValueError):
            ba.split_version("3.7.1")

    def test_a_non_numeric_shim_revision_is_refused(self):
        with pytest.raises(ValueError):
            ba.split_version("3.7.1-viprs.alpha")


class TestTheUpstreamHalfIsPinned:
    def test_it_has_a_source_sha256(self):
        upstream, _ = ba.split_version(ba.read_version())
        assert upstream in ba.SOURCE_SHA256, (
            f"acadsharp/VERSION says {upstream} but SOURCE_SHA256 has no entry for it — "
            "record the tarball digest before building against it"
        )

    def test_it_has_an_upstream_commit(self):
        # `acadsharp_commit` is a frozen LINKINFO field, so a version
        # with no commit pin cannot produce a complete manifest.
        upstream, _ = ba.split_version(ba.read_version())
        assert upstream in ba.SOURCE_COMMIT
        assert re.fullmatch(r"[0-9a-f]{40}", ba.SOURCE_COMMIT[upstream])

    def test_every_pinned_digest_is_a_sha256(self):
        for version, digest in ba.SOURCE_SHA256.items():
            assert re.fullmatch(r"[0-9a-f]{64}", digest), f"{version} has a malformed sha256"

    def test_an_unpinned_version_refuses_to_build(self):
        with pytest.raises(KeyError, match="no sha256"):
            ba.source_sha256("0.0.0")

    def test_an_unpinned_version_has_no_commit(self):
        with pytest.raises(KeyError, match="no upstream commit"):
            ba.source_commit("0.0.0")

    def test_the_source_url_points_at_the_pinned_tag(self):
        assert ba.source_url("3.7.1") == (
            "https://github.com/DomCR/ACadSharp/archive/refs/tags/v3.7.1.tar.gz"
        )


class TestTheSdkIsPinnedToo:
    """`dotnet_sdk` is a frozen LINKINFO field and ADR 0001 asks for it.

    The library is built on a base Microsoft does not test the SDK
    against, so the SDK version that was proven there is part of what an
    archive records about itself.
    """

    def test_the_driver_and_global_json_agree(self):
        import json

        with open(os.path.join(ACAD_DIR, "native", "global.json")) as f:
            pinned = json.load(f)["sdk"]["version"]
        assert ba.DOTNET_SDK_VERSION == pinned

    def test_it_is_a_three_part_version(self):
        assert re.fullmatch(r"\d+\.\d+\.\d+", ba.DOTNET_SDK_VERSION)


class TestTheVersionReachesTheManifest:
    def test_artifact_version_is_the_whole_string(self):
        info = ba.linkinfo_skeleton("linux", "amd64")
        assert info["artifact_version"] == ba.read_version()

    def test_acadsharp_version_is_the_upstream_half_only(self):
        upstream, _ = ba.split_version(ba.read_version())
        info = ba.linkinfo_skeleton("linux", "amd64")
        assert info["acadsharp_version"] == upstream
        assert "viprs" not in info["acadsharp_version"]

    def test_the_commit_is_the_pinned_one(self):
        upstream, _ = ba.split_version(ba.read_version())
        info = ba.linkinfo_skeleton("linux", "amd64")
        assert info["acadsharp_commit"] == ba.SOURCE_COMMIT[upstream]


class TestReleaseNotes:
    def test_the_notes_name_the_pinned_source_and_digest(self):
        version = ba.read_version()
        upstream, _ = ba.split_version(version)
        notes = ba.release_notes(version)
        assert ba.source_url(upstream) in notes
        assert ba.source_sha256(upstream) in notes

    def test_an_unpinned_version_has_no_notes(self):
        with pytest.raises(KeyError):
            ba.release_notes("0.0.0-viprs.1")
