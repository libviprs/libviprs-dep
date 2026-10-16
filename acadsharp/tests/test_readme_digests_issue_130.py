"""Issue #130: the README's archive digests have to be the published ones.

`TestReadmeHoldsUrlsAndDigestsTogether` in test_release_workflow.py checks
that every download line in acadsharp/README.md *carries* a sha256. Nothing
checks that it is the *right* one, and all five rows at
`acadsharp-3.7.1-viprs.1` describe an earlier upload of the same tag: the
table went in at 17:06Z on 2026-09-13 and the assets the release serves were
uploaded at 22:19Z.

This asks the GitHub API for the release each row links to and compares the
row's sha256 with the asset's `digest`, falling back to downloading and
hashing the asset when the API does not report one. It needs the network.
Without it the test fails and says so, unless LIBVIPRS_DEP_OFFLINE=1, which
skips it with a reason that says the check did not happen.
"""

import hashlib
import json
import os
import re
import urllib.error
import urllib.request

import pytest

HERE = os.path.dirname(__file__)
README = os.path.join(HERE, "..", "README.md")

# What the README's download table holds today, written out so a table that
# loses rows is red here rather than shrinking this test to nothing.
EXPECTED_ARCHIVES = {
    "acadsharp-linux-x64.tgz",
    "acadsharp-linux-arm64.tgz",
    "acadsharp-musl-x64.tgz",
    "acadsharp-musl-arm64.tgz",
    "acadsharp-mac-arm64.tgz",
}

ROW = re.compile(
    r"\[`(?P<name>acadsharp-[a-z0-9-]+\.tgz)`\]"
    r"\(https://github\.com/(?P<repo>[^/\s]+/[^/\s]+)/releases/download/"
    r"(?P<tag>[^/\s]+)/(?P=name)\)"
    r"[^\n]*?`(?P<sha>[0-9a-f]{64})`"
)


def readme_rows():
    with open(README, encoding="utf-8") as f:
        text = f.read()
    return [m.groupdict() for m in ROW.finditer(text)]


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


def test_issue_130_the_readme_table_lists_every_archive_once():
    names = [r["name"] for r in ROWS]
    assert sorted(names) == sorted(EXPECTED_ARCHIVES), (
        "acadsharp/README.md's download table does not list exactly the five archives, so the "
        f"digest check below would cover the wrong set: {names}"
    )


@pytest.mark.parametrize("row", ROWS, ids=[r["name"] for r in ROWS])
def test_issue_130_readme_digest_matches_the_published_asset(row):
    if offline():
        pytest.skip(
            "LIBVIPRS_DEP_OFFLINE=1, so the README digests were NOT compared with the release"
        )
    try:
        assets = published_assets(row["repo"], row["tag"])
    except (urllib.error.URLError, OSError) as e:
        pytest.fail(
            f"could not ask the GitHub API about {row['repo']} {row['tag']} ({e}). This check "
            "needs the network; set LIBVIPRS_DEP_OFFLINE=1 to skip it on purpose."
        )
    assert row["name"] in assets, (
        f"acadsharp/README.md links {row['name']} at {row['tag']}, and that release has no such "
        f"asset (it has {sorted(assets)})"
    )
    digest, url = assets[row["name"]]
    if digest and digest.startswith("sha256:"):
        published = digest.split(":", 1)[1]
        source = "the asset's API digest"
    else:
        published = sha256_of(url)
        source = "the sha256 of the downloaded asset"
    assert row["sha"] == published, (
        f"acadsharp/README.md says {row['name']} at {row['tag']} has sha256 {row['sha']}, and "
        f"{source} is {published}. A consumer who checks the digest before unpacking, as the "
        "README asks, gets a mismatch on a good archive."
    )
