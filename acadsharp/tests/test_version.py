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
            f"acadsharp/VERSION says {upstream} but SOURCE_SHA256 has no entry for it: "
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


class TestBothFrozenSurfacesAnswerTheSameQuestion:
    """`viprs_acad_get_capabilities_v1` writes what the header calls "the pinned
    ACadSharp version", and `LINKINFO.json` records `acadsharp_version`. They
    are the same question asked at run time and at link time, and for a while
    they gave different answers: the MSBuild target read `VERSION` verbatim, so
    the call wrote `3.7.1-viprs.1` while the manifest said `3.7.1`.

    Nothing here runs a compiler. What it can check is that the generator takes
    the upstream half, that both conformance consumers hold the string against
    `VERSION` rather than against a copy, and that neither of them types it.
    """

    CSPROJ = os.path.join(ACAD_DIR, "native", "Viprs.ACadSharp.Native.csproj")
    CONFORMANCE = os.path.join(ACAD_DIR, "tests", "conformance")

    @staticmethod
    def read(path):
        with open(path) as f:
            return f.read()

    def test_the_generator_takes_the_upstream_half(self):
        csproj = self.read(self.CSPROJ)
        generated = re.search(r"AcadSharpVersion\.g\.cs\"(.*?)/>", csproj, re.S)
        assert generated, "the project no longer generates AcadSharpVersion.g.cs"
        assert "$(ViprsUpstreamVersion)" in generated.group(1), (
            "the capability string is generated from something other than the upstream "
            "half of VERSION. The header documents it as the pinned ACadSharp version, "
            "and LINKINFO.json's acadsharp_version is that half and nothing else."
        )
        assert "$(ViprsShimVersion)" not in generated.group(1)

    def test_the_upstream_half_is_derived_and_not_typed(self):
        csproj = self.read(self.CSPROJ)
        assert "-viprs." in csproj, (
            "nothing in the project splits VERSION on the shim suffix, so the upstream "
            "half is coming from somewhere other than the file that decides it"
        )
        upstream, _ = ba.split_version(ba.read_version())
        assert upstream not in csproj, (
            f"the project types {upstream!r}. VERSION is the single source of truth and "
            "a bump would leave this claiming the old release."
        )

    # Each consumer keeps its own prefix: the C one already compiles in
    # VIPRS_EXPECTED_FINGERPRINT, the crate's build.rs already reads
    # VIPRS_ACAD_HEADER, and a name that matched neither would be the odd one.
    EXPECTED_NAME = {
        "c": "VIPRS_EXPECTED_ACADSHARP_VERSION",
        "rust": "VIPRS_ACAD_EXPECTED_ACADSHARP_VERSION",
    }

    @pytest.mark.parametrize("consumer", ["c", "rust"])
    def test_each_consumer_reads_the_version_out_of_the_file(self, consumer):
        runner = self.read(os.path.join(self.CONFORMANCE, consumer, "run.sh"))
        assert "acadsharp/VERSION" in runner, (
            f"the {consumer} runner does not read acadsharp/VERSION, so whatever the "
            "consumer compares the capability string against is a copy"
        )
        assert "-viprs." in runner, (
            f"the {consumer} runner does not take the upstream half, so it would hold "
            "the call to the artifact version the manifest disagrees with"
        )
        assert self.EXPECTED_NAME[consumer] in runner

    def test_the_c_consumer_compares_the_exact_string(self):
        code = self.read(os.path.join(self.CONFORMANCE, "c", "conformance.c"))
        assert "VIPRS_EXPECTED_ACADSHARP_VERSION" in code
        assert re.search(r"strcmp\(text, VIPRS_EXPECTED_ACADSHARP_VERSION\) == 0", code), (
            "the C consumer never compares the capability string against the expected "
            "one, so it reads a string and asserts nothing about it"
        )

    def test_the_generated_consumer_compares_the_exact_string(self):
        code = self.read(os.path.join(self.CONFORMANCE, "rust", "src", "main.rs"))
        assert "VIPRS_ACAD_EXPECTED_ACADSHARP_VERSION" in code
        assert re.search(r"text == VIPRS_ACAD_EXPECTED_ACADSHARP_VERSION", code)

    @pytest.mark.parametrize(
        "path",
        [
            os.path.join("c", "conformance.c"),
            os.path.join("rust", "src", "main.rs"),
            os.path.join("rust", "build.rs"),
        ],
    )
    def test_no_consumer_types_the_version(self, path):
        version = ba.read_version()
        upstream, _ = ba.split_version(version)
        code = self.read(os.path.join(self.CONFORMANCE, path))
        for literal in (version, upstream):
            assert literal not in code, (
                f"tests/conformance/{path} types {literal!r}. acadsharp/VERSION already "
                "says it, and a copy here passes the day it is written and never again."
            )
