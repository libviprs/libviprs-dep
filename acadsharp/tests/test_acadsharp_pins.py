"""The source pins, and the submodule that is not in the tarball.

ACadSharp's GitHub source tarball cannot build on its own:
`src/CSUtilities` is a git submodule, GitHub-generated tarballs never
contain submodules, and `ACadSharp.csproj` imports
`..\\CSUtilities\\CSMath\\CSMath.projitems` during evaluation, so the
failure lands before restore. That fact is the reason this driver
carries a second pin, and these tests keep the second pin from being
dropped as redundant.
"""

import re

import build_acadsharp as ba
import pytest


class TestPinnedSource:
    def test_the_version_in_version_has_a_pinned_hash(self):
        upstream, _shim = ba.split_version(ba.read_version())
        assert upstream in ba.SOURCE_SHA256, (
            f"acadsharp/VERSION says {upstream} but SOURCE_SHA256 has no entry for it, "
            "add the tarball's sha256 to build_acadsharp.py"
        )

    def test_the_hash_is_a_sha256(self):
        for version, digest in ba.SOURCE_SHA256.items():
            assert re.fullmatch(r"[0-9a-f]{64}", digest), f"{version} has no usable digest"

    def test_an_unpinned_version_refuses_to_resolve(self):
        with pytest.raises(KeyError, match="SOURCE_SHA256"):
            ba.source_sha256("99.99.99")

    def test_the_source_url_is_the_pinned_tag(self):
        upstream, _shim = ba.split_version(ba.read_version())
        url = ba.source_url(upstream)
        assert url.endswith(f"v{upstream}.tar.gz")
        assert "DomCR/ACadSharp" in url


class TestCsUtilitiesSubmodule:
    def test_the_submodule_commit_is_pinned(self):
        assert re.fullmatch(r"[0-9a-f]{40}", ba.CSUTILITIES_COMMIT)

    def test_the_submodule_url_is_recorded(self):
        assert ba.CSUTILITIES_URL.endswith("CSUtilities.git")

    def test_a_tarball_only_tree_is_rejected(self, tmp_path):
        # The exact state the spike hit: the tarball unpacks, src/CSUtilities
        # is an empty directory, and msbuild fails on a missing Import with
        # nothing pointing at the submodule. The driver has to say so first.
        tree = tmp_path / "ACadSharp-3.7.1"
        (tree / "src" / "ACadSharp").mkdir(parents=True)
        (tree / "src" / "ACadSharp" / "ACadSharp.csproj").write_text("<Project />")
        (tree / "src" / "CSUtilities").mkdir()
        with pytest.raises(ba.SourceTreeError, match="CSUtilities"):
            ba.check_source_tree(str(tree))

    def test_a_complete_tree_is_accepted(self, tmp_path):
        tree = tmp_path / "ACadSharp-3.7.1"
        (tree / "src" / "ACadSharp").mkdir(parents=True)
        (tree / "src" / "ACadSharp" / "ACadSharp.csproj").write_text("<Project />")
        for shared in ("CSMath", "CSUtilities"):
            d = tree / "src" / "CSUtilities" / shared
            d.mkdir(parents=True)
            (d / f"{shared}.projitems").write_text("<Project />")
        ba.check_source_tree(str(tree))


class TestDigestVerification:
    def test_a_mismatched_download_is_rejected(self, tmp_path):
        blob = tmp_path / "src.tar.gz"
        blob.write_bytes(b"not the tarball")
        with pytest.raises(ba.SourceTreeError, match="sha256"):
            ba.verify_digest(str(blob), "0" * 64)

    def test_a_matching_download_is_accepted(self, tmp_path):
        import hashlib

        blob = tmp_path / "src.tar.gz"
        blob.write_bytes(b"not the tarball")
        ba.verify_digest(str(blob), hashlib.sha256(b"not the tarball").hexdigest())
