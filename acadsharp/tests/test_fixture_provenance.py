"""PROVENANCE.md's digests, recomputed against the files beside it.

The file opens with "Every DWG in this directory, where it came from, and
under what licence", and it carries a sha256 for each one. Until this file a
grep of the whole suite for PROVENANCE returned nothing, so every one of those
numbers was hand-carried and verified by nothing: a fixture could be
regenerated, its digest updated in MANIFEST.json by the generator, and this
file left saying the old number, with no test anywhere able to tell. That is
the failure docs/LINKINFO.md is written against, one directory over.

It is cheap to close. The digests are of files in the same directory, the
recomputation is hashlib, and there is no container and no .NET in it, so it
runs in the existing pytest job.

The table reader is strict on purpose. A row it cannot parse is an error
rather than a skip, for the same reason `g13_support.shim_sources` refuses a
`<Compile Include>` shape it does not understand: a row silently dropped here
is a digest silently unchecked, which is the thing this file exists to stop.
"""

import os
import re

import pytest
from g13_support import FIXTURES, committed_fixtures, sha256_file

PROVENANCE = os.path.join(FIXTURES, "PROVENANCE.md")

# One row of one of the tables: a file name in backticks, a digest in
# backticks, and then whatever that table's remaining columns are. The
# `real_*` rows carry a source and a licence where the generated ones carry a
# description, so nothing past the second column is assumed.
ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|")

# A table row that is not a fixture row. There is one table whose second
# column is a source rather than a digest, and a reader that tried to hash its
# way through that would fail on the table rather than on a stale number.
SHA256 = re.compile(r"^[0-9a-f]{64}$")

# Named here rather than derived, because "the licence table" is a judgement
# about prose and this file should not be making one.
DOCUMENTED_SUFFIX = ".dwg"

# DWGs in tests/fixtures that PROVENANCE.md does not have a row for. These six
# predate #94's table and are all recorded in MANIFEST.json, so their bytes are
# pinned and the gap is the documentation rather than the evidence: the file
# claims to cover every DWG in the directory and covers 41 of 47. Nothing here
# can write the missing rows, because what goes in the third column is a
# description of what the drawing holds and only the person who made it knows.
UNDOCUMENTED = (
    "g13_bad_extents.dwg",
    "g13_empty_view.dwg",
    "g13_ocs_mirror.dwg",
    "g13_ocs_plane.dwg",
    "g13_ocs_rotated.dwg",
    "g13_ocs_skew.dwg",
)


def rows():
    """Every (file, second column) pair the tables in PROVENANCE.md carry.

    A row whose second column is not a digest comes back anyway rather than
    being dropped, and is refused by a test of its own below. Dropping it here
    would be the failure this file exists to stop, one level up: a row nobody
    can read is a digest nobody checks, and the quiet version of that reads as
    a shorter green list.
    """
    out = []
    with open(PROVENANCE) as f:
        for line in f:
            m = ROW.match(line.strip())
            if m and m.group(1).endswith(DOCUMENTED_SUFFIX):
                out.append((m.group(1), m.group(2)))
    return out


ROWS = rows()
DOCUMENTED = [name for name, _ in ROWS]


class TestTheTableIsReadable:
    """A reader that found nothing passes every case below."""

    def test_the_file_is_there(self):
        assert os.path.isfile(PROVENANCE)

    def test_it_carries_rows_this_reader_can_see(self):
        assert len(ROWS) >= 40, (
            f"the reader found {len(ROWS)} fixture rows in PROVENANCE.md, which is "
            "fewer than the directory holds, so most of the file is going unchecked"
        )

    def test_no_file_is_documented_twice(self):
        dupes = sorted({n for n in DOCUMENTED if DOCUMENTED.count(n) > 1})
        assert not dupes, f"{dupes} appear in more than one table with a digest each"

    @pytest.mark.parametrize("name,second", ROWS, ids=[n for n, _ in ROWS])
    def test_every_row_carries_a_digest_in_the_digest_column(self, name, second):
        assert SHA256.match(second), (
            f"PROVENANCE.md's row for {name} carries {second!r} where a sha256 "
            "belongs. Either the row is a shape this reader does not understand, in "
            "which case teach it the shape, or the number is not one"
        )

    def test_the_reader_would_reject_a_row_that_is_not_a_digest(self):
        # The control. The digest column is matched rather than trusted, and a
        # scan that accepted anything would report every row as checked.
        assert not SHA256.match("samples/sample_AC1032.dwg")
        assert SHA256.match("0" * 64)


class TestEveryDocumentedDigestIsTheFileBesideIt:
    @pytest.mark.parametrize("name,digest", ROWS, ids=[n for n, _ in ROWS])
    def test_the_file_is_in_the_directory(self, name, digest):
        assert os.path.isfile(os.path.join(FIXTURES, name)), (
            f"PROVENANCE.md documents {name} and there is no such file, so the row "
            "outlived the fixture"
        )

    @pytest.mark.parametrize("name,digest", ROWS, ids=[n for n, _ in ROWS])
    def test_the_digest_is_what_the_file_hashes_to(self, name, digest):
        path = os.path.join(FIXTURES, name)
        if not os.path.isfile(path):
            pytest.skip("covered by test_the_file_is_in_the_directory")
        # A second column that is not a digest fails here too, and says so in
        # the same sentence, which is why this does not guard against it.
        assert sha256_file(path) == digest, (
            f"PROVENANCE.md records {name} as {digest} and the file in this "
            "directory hashes to something else, so the provenance of the fixture "
            "being tested is a statement about a file nobody has"
        )


class TestTheFileCoversWhatItSaysItCovers:
    """It opens with "Every DWG in this directory", which is a claim.

    It is six short of true. Those six are pinned by MANIFEST.json, so the gap
    is the documentation and not the evidence, and the list shrinks the same
    way the fixture allow-lists do: an entry that gains a row goes red here
    telling you to take it off.
    """

    @pytest.mark.parametrize("name", committed_fixtures())
    def test_every_fixture_has_a_row(self, name):
        if name in UNDOCUMENTED:
            assert name not in DOCUMENTED, f"{name} has a row now, so take it off UNDOCUMENTED"
            return
        assert name in DOCUMENTED, (
            f"{name} is in tests/fixtures and PROVENANCE.md does not say where it "
            "came from or under what licence, which is what that file is for"
        )

    @pytest.mark.parametrize("name", UNDOCUMENTED)
    def test_the_allow_list_names_files_that_are_here(self, name):
        assert os.path.isfile(os.path.join(FIXTURES, name)), (
            f"{name} is excused from PROVENANCE.md and is not in the tree"
        )
