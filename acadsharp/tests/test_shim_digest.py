"""The recorded corpus, bound to the code that recorded it.

Every adapter test in this directory reads a committed capture, because
pytest has no .NET and is not getting one (ADR 0001). A capture is only
evidence if it is a run of the thing in the tree, and until this file there
was half a proof: `MANIFEST.json` carries the sha256 of every *fixture* and
the tests recompute it, so a changed DWG with a stale dump goes red. Nothing
said anything about the shim. A change to `Flattener.cs`, `RecordEncoder.cs`
or `Primitive.cs` left the whole adapter suite green against a recording of
the old behaviour, which is the expensive shape of a test that cannot fail.

So the manifest carries the shim it was recorded from as well: one sha256
per source the fixture generator compiles, and one rollup over the sorted
paths and the bytes behind them. Move any of them without rerunning
`tests/fixtures/gen/regenerate.py` and this file goes red naming the file
that moved, rather than every other file here staying quietly green.

The source list comes out of the generator's csproj rather than being
restated here. That is deliberate: the set that matters is the set that
actually compiled into the run, and a new file under `native/Adapter/`,
`native/Sources/` or `native/Wire/` is swept up by the same glob the
generator uses, so it is covered the day it lands.

What this does not do is prove the recorded *behaviour* is still the shim's
behaviour. Only running the decoder can do that, and that is what
`.github/workflows/acadsharp-conformance.yml` is for. This is the cheap
half: it turns silent staleness into a red test with an instruction.
"""

import os

import pytest
from g13_support import (
    ACAD_ROOT,
    manifest,
    sha256_file,
    shim_digest,
    shim_sources,
)

MANIFEST = manifest()
SHIM = MANIFEST.get("shim", {})
RECORDED = SHIM.get("sources", {})
RECORDED_NAMES = sorted(RECORDED)

REGENERATE = "acadsharp/tests/fixtures/gen/regenerate.py"

# The three the review named, because a digest over an empty set passes
# every check below and proves nothing. If the generator ever stops
# compiling these, the guard has gone hollow and this says so.
MUST_COVER = (
    "native/Adapter/Flattener.cs",
    "native/Wire/RecordEncoder.cs",
    "native/Wire/Primitive.cs",
)


class TestTheManifestRecordsItsShim:
    def test_there_is_a_shim_block_at_all(self):
        assert SHIM, (
            "MANIFEST.json records no shim, so every capture beside it is a "
            f"recording of code nobody can identify. Rerun {REGENERATE}"
        )

    def test_it_carries_a_rollup_and_a_file_per_source(self):
        assert SHIM.get("sha256"), "the shim block has no rollup digest"
        assert RECORDED, "the shim block names no source files"

    def test_every_recorded_digest_is_a_sha256(self):
        for name, digest in RECORDED.items():
            assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest), (
                f"{name} is recorded as {digest!r}, which is not a sha256"
            )


class TestTheGuardIsNotHollow:
    """A digest over nothing is a green test that measures nothing."""

    @pytest.mark.parametrize("name", MUST_COVER)
    def test_the_files_the_review_named_are_covered(self, name):
        assert name in shim_sources(), (
            f"{name} is not in the set the fixture generator compiles, so the "
            "captures are no longer bound to it"
        )

    def test_the_generator_compiles_more_than_a_handful(self):
        # The three directories held twelve sources plus Abi.cs and Probe.cs
        # when this landed. An arbitrary floor, low enough never to need
        # touching and high enough that a glob resolving to one file fails.
        assert len(shim_sources()) >= 10, (
            f"the generator compiles only {shim_sources()}, which is too few to be "
            "the shim. Check the <Compile Include> globs in its csproj"
        )


class TestTheCapturesAreOfTheShimInTheTree:
    """The check that makes every other adapter test in this directory mean
    something. It is the shim's half of what the fixture digests already do
    for the DWG files."""

    def test_the_recorded_set_is_the_set_the_generator_compiles(self):
        actual = set(shim_sources())
        recorded = set(RECORDED_NAMES)
        added = sorted(actual - recorded)
        gone = sorted(recorded - actual)
        assert not added and not gone, (
            f"the shim gained {added} and lost {gone} since the captures were "
            f"recorded, so they are a run of a different program. Rerun {REGENERATE}"
        )

    @pytest.mark.parametrize("name", RECORDED_NAMES)
    def test_each_recorded_source_is_still_there(self, name):
        assert os.path.isfile(os.path.join(ACAD_ROOT, name)), (
            f"{name} recorded the captures and is no longer in the tree. Rerun {REGENERATE}"
        )

    @pytest.mark.parametrize("name", RECORDED_NAMES)
    def test_each_source_is_byte_for_byte_what_recorded_the_captures(self, name):
        path = os.path.join(ACAD_ROOT, name)
        if not os.path.isfile(path):
            pytest.skip("covered by test_each_recorded_source_is_still_there")
        assert sha256_file(path) == RECORDED[name], (
            f"{name} has changed since the captures under tests/expectations were "
            f"recorded, so every test that reads one is measuring the old behaviour. "
            f"Rerun {REGENERATE} and read the diff it makes."
        )

    def test_the_rollup_covers_the_whole_set(self):
        assert shim_digest() == SHIM.get("sha256"), (
            "the shim rollup in MANIFEST.json is not the digest of the sources in "
            f"the tree. Rerun {REGENERATE}"
        )
