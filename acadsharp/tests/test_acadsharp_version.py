"""acadsharp/VERSION carries two numbers and both have to stay honest.

Unlike zstd and pdfium, this dependency's version is the upstream
library version plus a shim revision, `3.7.1-viprs.2`. The driver
splits it, so there is one file to bump, and these hold the split
against a VERSION that stops parsing.

The class at the bottom is the one that bites. Holding the split is
cheap; holding the shim revision to the shim is what stops a published
artifact name meaning two different libraries, and it did not exist
while the campaign that needed it was landing.
"""

import hashlib
import os
import re

import build_acadsharp as ba
import pytest
from g13_support import sha256_file, shim_digest, shim_sources

ACAD_DIR = os.path.join(os.path.dirname(__file__), "..")
VERSION_PATH = os.path.join(ACAD_DIR, "VERSION")


class TestVersionFile:
    def test_exists_and_parses(self):
        assert os.path.isfile(VERSION_PATH)
        assert re.fullmatch(r"\d+\.\d+\.\d+-viprs\.\d+", ba.read_version())

    def test_is_a_bare_version(self):
        # Consumers do `tr -d '[:space:]' < VERSION`, so the file holds
        # the number and nothing else.
        with open(VERSION_PATH) as f:
            assert len(f.read().strip().splitlines()) == 1

    def test_splits_into_upstream_and_shim_revision(self):
        upstream, shim = ba.split_version(ba.read_version())
        assert re.fullmatch(r"\d+\.\d+\.\d+", upstream)
        assert re.fullmatch(r"\d+", shim)

    def test_empty_file_is_an_error(self, tmp_path):
        empty = tmp_path / "VERSION"
        empty.write_text("\n")
        with pytest.raises(ValueError, match="single source of truth"):
            ba.read_version(str(empty))

    def test_a_bare_upstream_version_is_rejected(self):
        # `3.7.1` on its own would build the library but leave the shim
        # revision nowhere, and every artifact name would then collide
        # across shim rebuilds of the same upstream release.
        with pytest.raises(ValueError):
            ba.split_version("3.7.1")


class TestTheShimRevisionMovesWhenTheShimDoes:
    """The other half of the version, and the half nothing was holding.

    `SOURCE_SHA256` pins the upstream half: bump `3.7.1` without recording a
    tarball digest and the driver refuses to build. The shim half had no
    equivalent, so seven flattener changes landed on top of a published
    `3.7.1-viprs.1` with the number unmoved and every check in the repository
    stayed green. Nothing downstream would have caught it either:
    `release-acadsharp.yml` uploads with `--clobber` and says a re-run is safe,
    and `acadsharp-rs`'s COMPAT.toml globs `3.7.1-viprs.*`.

    `build_acadsharp.SHIM_DIGESTS` is that pin, one row per artifact version,
    and this holds the tree against the row for the version VERSION names. The
    viprs.1 row is the `shim.sha256` block the same function wrote into
    `tests/expectations/MANIFEST.json` at the published tag, so putting VERSION
    back to `3.7.1-viprs.1` fails here with the two digests side by side.

    It needs no git and no .NET: `g13_support.shim_digest()` reads the sources
    the fixture generator compiles, which is every `.cs` under `native/Adapter`,
    `native/Sources` and `native/Wire` plus `Abi.cs` and `Probe.cs`.
    """

    def test_the_version_in_the_tree_has_a_row(self):
        version = ba.read_version()
        assert version in ba.SHIM_DIGESTS, (
            f"acadsharp/VERSION says {version} and SHIM_DIGESTS in build_acadsharp.py "
            "has no row for it, so nothing says which shim that name is. Add the row "
            "in the same commit as the bump."
        )

    def test_the_shim_in_the_tree_is_the_one_that_version_names(self):
        version = ba.read_version()
        assert ba.shim_digest_for(version) == shim_digest(), (
            f"the sources under native/ are not the shim {version} names. Either this "
            "is a change to the shim, in which case bump the revision in "
            "acadsharp/VERSION and add its row to SHIM_DIGESTS, or it is a change to "
            "an unpublished revision, in which case move that revision's row in the "
            "same commit. What is not on offer is leaving the number still: a "
            "published artifact name that means two different libraries is a consumer "
            "pinning a version and getting whichever build it happened to download."
        )

    def test_a_change_under_the_adapter_would_be_seen(self, tmp_path):
        # The control. Everything above compares two values that happen to
        # agree today, and a rollup over a set this reader could not find
        # would agree just as well. So: take the real source list, change one
        # byte of Flattener.cs in a copy, and check the number moves.
        sources = shim_sources()
        assert any(s.startswith("native/Adapter/") for s in sources), (
            f"the shim source set is {sources}, which holds nothing under "
            "native/Adapter/, so this guard is not watching the flattener at all"
        )
        target = "native/Adapter/Flattener.cs"
        assert target in sources

        root = tmp_path / "acadsharp"
        for rel in sources:
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(os.path.join(ACAD_DIR, rel), "rb") as f:
                dest.write_bytes(f.read())
        (root / target).write_bytes((root / target).read_bytes() + b"\n// one byte\n")

        moved = _digest_over(str(root), sources)
        assert moved != shim_digest(), (
            "a change to Flattener.cs leaves the shim rollup where it was, so the row "
            "in SHIM_DIGESTS would keep matching a shim that is no longer the one in "
            "the tree"
        )


def _digest_over(root, sources):
    """`g13_support.shim_digest`'s rollup, against a tree that is not this one.

    The formula rather than a second implementation of it: the real function
    walks `ACAD_ROOT`, and the control above needs the same arithmetic over a
    copy. Kept here, next to its one caller.
    """
    h = hashlib.sha256()
    for rel in sources:
        h.update(rel.encode())
        h.update(b"\0")
        h.update(sha256_file(os.path.join(root, rel)).encode())
        h.update(b"\0")
    return h.hexdigest()
