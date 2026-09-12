"""zstd/VERSION is the single source of truth — these keep it honest.

The version drives the source URL, the pinned checksum, the release tag
and the download URLs in the docs. Each of those is a separate place the
number can rot, so each gets a check that fails when it does.
"""

import os
import re

import build_zstd as bz

ZSTD_DIR = os.path.join(os.path.dirname(__file__), "..")
VERSION_PATH = os.path.join(ZSTD_DIR, "VERSION")
README_PATH = os.path.join(ZSTD_DIR, "README.md")


def read_readme():
    with open(README_PATH) as f:
        return f.read()


class TestVersionFile:
    def test_exists_and_parses(self):
        assert os.path.isfile(VERSION_PATH)
        assert re.fullmatch(r"\d+\.\d+\.\d+", bz.read_version())

    def test_is_a_bare_version(self):
        # release.yml-style consumers do `tr -d '[:space:]' < VERSION`,
        # so the file must hold the number and nothing else — no comment
        # explaining the pin, no trailing prose.
        with open(VERSION_PATH) as f:
            assert len(f.read().strip().splitlines()) == 1

    def test_empty_file_is_an_error(self, tmp_path):
        empty = tmp_path / "VERSION"
        empty.write_text("\n")
        try:
            bz.read_version(str(empty))
        except ValueError as exc:
            assert "single source of truth" in str(exc)
        else:
            raise AssertionError("an empty VERSION must not silently build nothing")


class TestPinnedSource:
    def test_version_has_a_pinned_hash(self):
        # Bumping VERSION without adding the new tarball's sha256 would
        # otherwise mean downloading whatever upstream serves that day.
        version = bz.read_version()
        assert version in bz.SOURCE_SHA256, (
            f"zstd/VERSION says {version} but SOURCE_SHA256 has no entry for it — "
            "add the release tarball's sha256 to build_zstd.py"
        )

    def test_hash_is_a_sha256(self):
        for version, digest in bz.SOURCE_SHA256.items():
            assert re.fullmatch(r"[0-9a-f]{64}", digest), f"{version} has a malformed sha256"

    def test_unknown_version_refuses_to_build(self):
        try:
            bz.source_sha256("0.0.0")
        except ValueError as exc:
            assert "No pinned source hash" in str(exc)
        else:
            raise AssertionError("an unpinned version must not be downloadable")

    def test_source_url_points_at_the_upstream_release(self):
        url = bz.source_url("1.5.7")
        assert url == (
            "https://github.com/facebook/zstd/releases/download/v1.5.7/zstd-1.5.7.tar.gz"
        )


class TestDocsTrackTheVersion:
    def test_readme_download_urls_use_the_pinned_version(self):
        version = bz.read_version()
        readme = read_readme()
        assert f"/releases/download/zstd-{version}/" in readme, (
            f"zstd/README.md's download URLs don't mention zstd-{version}"
        )

    def test_readme_explains_why_this_version(self):
        # The pin exists because it is what zstd-sys vendors; if that
        # reasoning gets dropped the next bump has nothing to check
        # itself against.
        readme = read_readme()
        assert "zstd-sys" in readme

    def test_no_stale_versions_in_readme_download_urls(self):
        version = bz.read_version()
        stale = {
            m
            for m in re.findall(r"/releases/download/zstd-([0-9.]+)/", read_readme())
            if m != version
        }
        assert not stale, f"README download URLs still point at {sorted(stale)}"
