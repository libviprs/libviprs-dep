"""Issue #43: zstd/README.md has to link the published zstd archives, digests included.

The README used to link a zstd-1.5.7 release that did not exist, then was
changed to say no archives are published. This holds it to the other end:
every archive the driver builds gets a row in the Download table with its
URL at the zstd-<VERSION> tag and its sha256, and that sha256 is the one the
release actually serves.

The digest check asks the GitHub API for the release each row links to and
compares the row's sha256 with the asset's `digest`, falling back to
downloading and hashing the asset when the API does not report one. It needs
the network. Without it the test fails and says so, unless
LIBVIPRS_DEP_OFFLINE=1, which skips it with a reason that says the check did
not happen. A draft release is not published, so it counts as missing.
"""

import hashlib
import json
import os
import re
import urllib.error
import urllib.request

import build_zstd as bz
import pytest

README = os.path.join(os.path.dirname(__file__), "..", "README.md")
VERSION = bz.read_version()
TAG = bz.release_tag(VERSION)
REPO = bz.GITHUB_REPO

# Every cell the driver builds, written out so a table that loses a row is red
# here rather than shrinking this test to nothing.
EXPECTED_ARCHIVES = sorted(
    bz.archive_name(plat, arch) for plat in ("linux", "musl", "mac") for arch in ("amd64", "arm64")
)

ROW = re.compile(
    r"\[`(?P<name>zstd-[a-z0-9-]+\.tgz)`\]"
    r"\(https://github\.com/(?P<repo>[^/\s]+/[^/\s]+)/releases/download/"
    r"(?P<tag>[^/\s]+)/(?P=name)\)"
    r"[^\n]*?`(?P<sha>[0-9a-f]{64})`"
)


def readme_rows():
    with open(README, encoding="utf-8") as f:
        text = f.read()
    return {m["name"]: m.groupdict() for m in ROW.finditer(text)}


ROWS = readme_rows()

_RELEASES = {}


def offline():
    return os.environ.get("LIBVIPRS_DEP_OFFLINE") == "1"


def _get(url, accept):
    headers = {"Accept": accept, "User-Agent": "libviprs-dep-readme-digest-check"}
    req = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(req, timeout=60)


def published_assets(repo, tag):
    """name -> (digest or None, download url), for one release, cached."""
    key = (repo, tag)
    if key not in _RELEASES:
        url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
        with _get(url, "application/vnd.github+json") as r:
            data = json.load(r)
        _RELEASES[key] = {
            a["name"]: (a.get("digest"), a["browser_download_url"]) for a in data["assets"]
        }
    return _RELEASES[key]


def sha256_of(url):
    h = hashlib.sha256()
    with _get(url, "application/octet-stream") as r:
        for chunk in iter(lambda: r.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def test_issue_43_readme_has_a_row_for_every_archive():
    missing = [name for name in EXPECTED_ARCHIVES if name not in ROWS]
    assert not missing, (
        f"zstd/README.md has no download row (link plus sha256) for {missing}. Every archive "
        f"the driver builds is published at {TAG}, so each needs one."
    )


@pytest.mark.parametrize("name", EXPECTED_ARCHIVES)
def test_issue_43_readme_row_links_the_pinned_release(name):
    row = ROWS.get(name)
    assert row, f"zstd/README.md has no download row for {name}"
    assert (row["repo"], row["tag"]) == (REPO, TAG), (
        f"zstd/README.md links {name} at {row['repo']} {row['tag']}, and zstd/VERSION "
        f"says the release is {REPO} {TAG}"
    )


@pytest.mark.parametrize("name", EXPECTED_ARCHIVES)
def test_issue_43_readme_digest_matches_the_published_asset(name):
    row = ROWS.get(name)
    assert row, f"zstd/README.md has no download row for {name}, so there is no digest to check"
    if offline():
        pytest.skip(
            "LIBVIPRS_DEP_OFFLINE=1, so the README digests were NOT compared with the release"
        )
    try:
        assets = published_assets(row["repo"], row["tag"])
    except urllib.error.HTTPError as e:
        if e.code == 404:
            pytest.fail(
                f"{row['repo']} has no published {row['tag']} release (a draft does not count), "
                f"so the {name} URL in zstd/README.md is dead"
            )
        raise
    except (urllib.error.URLError, OSError) as e:
        pytest.fail(
            f"could not ask the GitHub API about {row['repo']} {row['tag']} ({e}). This check "
            "needs the network; set LIBVIPRS_DEP_OFFLINE=1 to skip it on purpose."
        )
    assert name in assets, (
        f"zstd/README.md links {name} at {row['tag']}, and that release has no such "
        f"asset (it has {sorted(assets)})"
    )
    digest, url = assets[name]
    if digest and digest.startswith("sha256:"):
        published = digest.split(":", 1)[1]
        source = "the asset's API digest"
    else:
        published = sha256_of(url)
        source = "the sha256 of the downloaded asset"
    assert row["sha"] == published, (
        f"zstd/README.md says {name} at {row['tag']} has sha256 {row['sha']}, and "
        f"{source} is {published}"
    )
