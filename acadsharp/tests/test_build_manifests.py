"""`LINKINFO.json` and `BUILDINFO.json`, which are the consumer contract.

`acadsharp-rs`'s `build.rs` parses `LINKINFO.json` to emit
`cargo:rustc-link-search`, `cargo:rustc-link-lib` and
`cargo:rustc-link-arg`, and checks `abi_version`, `abi_fingerprint` and
`wire_version` at first use. So the field names are frozen, the fingerprint
is derived from the shipped header rather than typed, and the two fields
that describe the real link cannot be filled in from an example.
"""

import hashlib
import json
import os

import build_acadsharp as ba
import pytest

ACAD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HEADER = os.path.join(ACAD_DIR, "include", "viprs_acadsharp.h")

FROZEN_LINKINFO_FIELDS = (
    "schema_version",
    "artifact_version",
    "acadsharp_version",
    "acadsharp_commit",
    "dotnet_sdk",
    "target",
    "platform",
    "cpu",
    "abi_version",
    "wire_version",
    "abi_header_sha256",
    "abi_fingerprint",
    "shared_library",
    "static_library",
    "static_certified",
    "system_libraries",
    "link_args",
)

# Every entry point the frozen header declares. The verifier compares this
# list against the library's export table, so it is spelled out once here
# and extracted from the header everywhere else.
ENTRY_POINTS = (
    "viprs_acad_abi_version",
    "viprs_acad_abi_fingerprint",
    "viprs_acad_capabilities_v1",
    "viprs_acad_open_path_utf8",
    "viprs_acad_open_memory",
    "viprs_acad_view_count",
    "viprs_acad_view_info_v1",
    "viprs_acad_decode_begin",
    "viprs_acad_decode_next_batch",
    "viprs_acad_decode_close",
    "viprs_acad_close",
)


def _good_link_facts():
    return {"system_libraries": ["m", "dl", "pthread"], "link_args": []}


class TestTheFieldNamesAreFrozen:
    def test_the_driver_declares_exactly_these_fields(self):
        assert tuple(ba.LINKINFO_FIELDS) == FROZEN_LINKINFO_FIELDS

    def test_a_complete_manifest_carries_every_one(self):
        info = ba.make_linkinfo(
            "linux",
            "amd64",
            static_library=ba.STATIC_LIBRARY_NAME,
            static_certified=True,
            **_good_link_facts(),
        )
        assert set(info) == set(FROZEN_LINKINFO_FIELDS)

    def test_schema_version_is_one(self):
        assert ba.linkinfo_skeleton("linux", "amd64")["schema_version"] == 1

    def test_it_round_trips_through_json(self):
        info = ba.make_linkinfo("linux", "arm64", **_good_link_facts())
        assert json.loads(json.dumps(info)) == info


class TestTargetIdentity:
    def test_target_is_the_rust_triple_not_the_dotnet_rid(self):
        # build.rs matches this against `TARGET`, which cargo sets to a
        # Rust triple. A .NET runtime identifier there would never match.
        info = ba.linkinfo_skeleton("musl", "arm64")
        assert info["target"] == "aarch64-unknown-linux-musl"
        assert info["target"] != ba.rid_for("musl", "arm64")

    def test_platform_and_cpu_match_the_archive_name(self):
        for plat, arch in ba.MATRIX:
            info = ba.linkinfo_skeleton(plat, arch)
            assert ba.archive_name(plat, arch) == f"acadsharp-{info['platform']}-{info['cpu']}.tgz"


class TestTheAbiFieldsComeFromTheHeader:
    def test_header_sha256_is_the_hash_of_the_shipped_header(self):
        with open(HEADER, "rb") as f:
            expected = hashlib.sha256(f.read()).hexdigest()
        assert ba.header_sha256() == expected
        assert ba.linkinfo_skeleton("linux", "amd64")["abi_header_sha256"] == expected

    def test_fingerprint_is_the_first_eight_bytes_of_that_hash(self):
        assert ba.abi_fingerprint() == ba.header_sha256()[:16]
        assert len(ba.abi_fingerprint()) == 16

    def test_abi_and_wire_versions_are_read_out_of_the_header(self):
        abi, wire = ba.header_versions()
        assert (abi, wire) == (1, 1)
        info = ba.linkinfo_skeleton("linux", "amd64")
        assert info["abi_version"] == abi
        assert info["wire_version"] == wire

    def test_the_versions_are_not_hardcoded_next_to_the_header(self, tmp_path):
        # If the header bumps VIPRS_ACAD_ABI_VERSION, the manifest has to
        # move with it rather than keep claiming 1.
        fake = tmp_path / "viprs_acadsharp.h"
        fake.write_text("#define VIPRS_ACAD_ABI_VERSION 4u\n#define VIPRS_ACAD_WIRE_VERSION 7u\n")
        assert ba.header_versions(str(fake)) == (4, 7)

    def test_a_header_without_the_defines_is_refused(self, tmp_path):
        fake = tmp_path / "viprs_acadsharp.h"
        fake.write_text("/* nothing useful */\n")
        with pytest.raises(ValueError, match="VIPRS_ACAD_ABI_VERSION"):
            ba.header_versions(str(fake))


class TestTheEntryPointList:
    def test_every_declared_function_is_found(self):
        assert tuple(ba.header_entry_points()) == ENTRY_POINTS

    def test_struct_type_names_are_not_mistaken_for_functions(self):
        # `viprs_acad_limits_v1` is a struct and is never exported, while
        # `viprs_acad_capabilities_v1` is both a struct and a function.
        found = ba.header_entry_points()
        assert "viprs_acad_limits_v1" not in found
        assert "viprs_acad_capabilities_v1" in found

    def test_a_header_with_no_entry_points_is_refused(self, tmp_path):
        fake = tmp_path / "viprs_acadsharp.h"
        fake.write_text("#define VIPRS_ACAD_ABI_VERSION 1u\n")
        with pytest.raises(ValueError, match="no entry points"):
            ba.header_entry_points(str(fake))


class TestStaticLibraryIsAbsentNotEmpty:
    def test_a_shared_only_target_omits_the_key_entirely(self):
        # An empty string here would read as "there is a static library
        # at the path ''" to anything that only checks the key exists.
        info = ba.make_linkinfo("mac", "arm64", **_good_link_facts())
        assert "static_library" not in info
        assert info["static_certified"] is False

    def test_a_static_target_carries_the_path(self):
        info = ba.make_linkinfo(
            "linux",
            "amd64",
            static_library=ba.STATIC_LIBRARY_NAME,
            static_certified=True,
            **_good_link_facts(),
        )
        assert info["static_library"] == "lib/libacadsharp_native.a"

    def test_an_empty_static_library_path_is_refused(self):
        with pytest.raises(ValueError, match="absent"):
            ba.make_linkinfo("linux", "amd64", static_library="", **_good_link_facts())

    def test_the_shared_library_path_is_relative_to_the_archive_root(self):
        info = ba.linkinfo_skeleton("mac", "arm64")
        assert info["shared_library"] == "lib/libacadsharp_native.dylib"


class TestStaticCertifiedIsNeverWrittenByHand:
    def test_certifying_without_a_static_library_is_refused(self):
        with pytest.raises(ValueError, match="static_certified"):
            ba.make_linkinfo("linux", "amd64", static_certified=True, **_good_link_facts())

    def test_the_default_is_false(self):
        assert ba.make_linkinfo("linux", "amd64", **_good_link_facts())["static_certified"] is False

    def test_it_is_a_json_boolean_not_a_string(self):
        info = ba.make_linkinfo(
            "linux",
            "amd64",
            static_library=ba.STATIC_LIBRARY_NAME,
            static_certified=True,
            **_good_link_facts(),
        )
        assert info["static_certified"] is True

    def test_a_static_library_that_did_not_certify_still_ships(self):
        # Shipping the archive uncertified is a recorded outcome, not a
        # failure — the consumer then knows to link shared.
        info = ba.make_linkinfo(
            "linux", "arm64", static_library=ba.STATIC_LIBRARY_NAME, **_good_link_facts()
        )
        assert info["static_library"] == "lib/libacadsharp_native.a"
        assert info["static_certified"] is False


class TestLinkFactsComeFromTheRealLink:
    def test_the_dead_nativeaot_initialiser_flag_is_refused(self):
        # ADR 0001 measured it: `NativeAOT_StaticInitialization` does not
        # exist in .NET 10, and `--require-defined` for it fails the link
        # outright. A manifest carrying it would break the consumer's
        # build rather than harden it.
        with pytest.raises(ValueError, match="NativeAOT_StaticInitialization"):
            ba.make_linkinfo(
                "linux",
                "amd64",
                system_libraries=["m"],
                link_args=["-Wl,--require-defined,NativeAOT_StaticInitialization"],
                static_library=ba.STATIC_LIBRARY_NAME,
                static_certified=True,
            )

    def test_the_macos_spelling_of_the_same_flag_is_refused(self):
        with pytest.raises(ValueError, match="NativeAOT_StaticInitialization"):
            ba.make_linkinfo(
                "mac",
                "arm64",
                system_libraries=[],
                link_args=["-Wl,-u,_NativeAOT_StaticInitialization"],
            )

    def test_system_libraries_are_bare_names_not_flags(self):
        # build.rs emits `cargo:rustc-link-lib={}`, so a `-l` prefix here
        # produces `-l-lm` downstream.
        with pytest.raises(ValueError, match="bare"):
            ba.make_linkinfo("linux", "amd64", system_libraries=["-lm"], link_args=[])

    def test_a_path_in_system_libraries_is_refused(self):
        with pytest.raises(ValueError, match="bare"):
            ba.make_linkinfo("linux", "amd64", system_libraries=["/usr/lib/libm.a"], link_args=[])

    def test_both_lists_are_preserved_in_order(self):
        info = ba.make_linkinfo(
            "linux", "amd64", system_libraries=["m", "dl", "pthread"], link_args=["-Wl,-z,now"]
        )
        assert info["system_libraries"] == ["m", "dl", "pthread"]
        assert info["link_args"] == ["-Wl,-z,now"]

    def test_the_example_from_the_issue_is_not_a_default(self):
        # `linkinfo_skeleton` deliberately has no link facts in it: they
        # are measured per target and passed in.
        skeleton = ba.linkinfo_skeleton("linux", "amd64")
        assert "system_libraries" not in skeleton
        assert "link_args" not in skeleton


class TestBuildInfo:
    FIELDS = (
        "driver_commit",
        "builder_image",
        "dotnet_version",
        "clang_version",
        "linker_version",
        "publish_aot",
        "invariant_globalization",
        "trimmer_roots",
        "trimmer_single_warn",
        "aot_warning_count",
        "built_utc",
    )

    def _info(self, **kwargs):
        base = dict(
            driver_commit="0" * 40,
            builder_image="debian:bookworm-slim",
            dotnet_version="10.0.401",
            clang_version="clang version 14.0.6",
            linker_version="GNU ld (GNU Binutils for Debian) 2.40",
            aot_warning_count=16,
        )
        base.update(kwargs)
        return ba.make_buildinfo(**base)

    def test_it_carries_every_field_the_issue_asks_for(self):
        assert set(self._info()) == set(self.FIELDS)
        assert tuple(ba.BUILDINFO_FIELDS) == self.FIELDS

    def test_the_aot_settings_are_recorded_not_assumed(self):
        info = self._info()
        assert info["publish_aot"] is True
        assert info["invariant_globalization"] is True
        assert info["trimmer_roots"] == ["ACadSharp"]
        assert info["trimmer_single_warn"] is False

    def test_the_warning_count_is_an_integer(self):
        assert self._info(aot_warning_count=0)["aot_warning_count"] == 0

    def test_an_unknown_warning_count_is_refused(self):
        # "the AOT warning count" is a measurement. None of it can be a
        # guess, and 16 was the spike's number for one configuration.
        with pytest.raises(ValueError, match="aot_warning_count"):
            self._info(aot_warning_count=None)

    def test_the_build_time_is_utc_and_parseable(self):
        import datetime

        stamp = self._info()["built_utc"]
        assert stamp.endswith("Z")
        datetime.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")


class TestChecksums:
    def test_it_covers_every_other_file_and_not_itself(self, tmp_path):
        root = tmp_path / "acadsharp-linux-x64"
        (root / "lib").mkdir(parents=True)
        (root / "metadata").mkdir()
        (root / "lib" / "libacadsharp_native.so").write_bytes(b"binary")
        (root / "README.md").write_text("hello\n")
        path = ba.write_checksums(str(root))
        assert os.path.basename(path) == "CHECKSUMS.txt"
        with open(path) as f:
            lines = [line.split(None, 1) for line in f.read().splitlines()]
        named = {rel.strip() for _digest, rel in lines}
        assert named == {"lib/libacadsharp_native.so", "README.md"}

    def test_the_digests_are_right(self, tmp_path):
        root = tmp_path / "acadsharp-linux-x64"
        (root / "metadata").mkdir(parents=True)
        (root / "README.md").write_text("hello\n")
        ba.write_checksums(str(root))
        with open(os.path.join(str(root), "metadata", "CHECKSUMS.txt")) as f:
            digest, rel = f.read().split()
        assert rel == "README.md"
        assert digest == hashlib.sha256(b"hello\n").hexdigest()

    def test_the_paths_are_relative_to_the_archive_root(self, tmp_path):
        root = tmp_path / "acadsharp-linux-x64"
        (root / "metadata").mkdir(parents=True)
        (root / "include").mkdir()
        (root / "include" / "viprs_acadsharp.h").write_text("x\n")
        ba.write_checksums(str(root))
        with open(os.path.join(str(root), "metadata", "CHECKSUMS.txt")) as f:
            text = f.read()
        assert "include/viprs_acadsharp.h" in text
        assert str(tmp_path) not in text


class TestTheGeneratedStagingScript:
    """The script that measures the link facts is generated, so its holes
    have to be filled. A placeholder left in place would be a shell script
    that runs and silently records nothing."""

    @pytest.mark.parametrize("plat", ["linux", "musl", "mac"])
    def test_no_placeholder_survives_substitution(self, plat):
        script = ba.stage_script(plat)
        assert "RUNTIME_ARCHIVE_LIST" not in script
        assert "STATIC_SYSTEM_LIBRARY_LADDER" not in script

    def test_it_forces_the_runtime_initialiser_it_finds(self):
        # libbootstrapperdll.o defines no global symbol at all, so nothing
        # can pull it out of an archive and the first managed call aborts.
        # The merge globalises the initialiser and the link forces it.
        script = ba.stage_script("linux")
        assert "--globalize-symbol" in script
        assert "_GLOBAL__sub_I" in script
        assert "-Wl,-u," in script

    def test_it_does_not_reach_for_the_symbol_that_no_longer_exists(self):
        # Comments come out first: the script explains at length why that
        # symbol is gone, and a whole-file grep would fire on the prose
        # explaining the rule. zstd/tests/test_ci_coverage.py grew
        # `without_comments()` for exactly this.
        for plat in ("linux", "musl", "mac"):
            code = "\n".join(
                line
                for line in ba.stage_script(plat).splitlines()
                if not line.lstrip().startswith("#")
            )
            assert ba.DEAD_STATIC_INIT_SYMBOL not in code

    def test_the_system_library_ladder_starts_narrow(self):
        # Recording more than the link needs would make every consumer
        # carry flags nothing measured.
        for plat, ladder in ba.STATIC_SYSTEM_LIBRARY_LADDER.items():
            assert ladder[0] == "m", plat
            for rung, wider in zip(ladder, ladder[1:]):
                assert set(rung.split()) < set(wider.split())

    def test_only_one_of_each_mutually_exclusive_runtime_archive(self):
        # Merging both GC flavours, or both eventpipe flavours, is a
        # duplicate-symbol link error rather than belt and braces.
        for a, b in (
            ("libRuntime.WorkstationGC.a", "libRuntime.ServerGC.a"),
            ("libeventpipe-disabled.a", "libeventpipe-enabled.a"),
            ("libstandalonegc-disabled.a", "libstandalonegc-enabled.a"),
        ):
            assert (a in ba.RUNTIME_ARCHIVES) != (b in ba.RUNTIME_ARCHIVES)


class TestTheGeneratedSmokes:
    def test_the_shared_smoke_resolves_every_entry_point(self):
        source = ba.archive_smoke_source()
        for name in ba.header_entry_points():
            assert f'"{name}",' in source

    def test_the_shared_smoke_checks_the_live_fingerprint(self):
        source = ba.archive_smoke_source()
        assert "viprs_acad_abi_fingerprint" in source
        assert "ABI_FINGERPRINT=" in source

    def test_the_static_smoke_references_every_entry_point(self):
        source = ba.static_smoke_source()
        for name in ba.header_entry_points():
            assert name in source


class TestAnUncertifiedStaticLibraryIsNotShipped:
    def _stage(self, tmp_path, facts):
        root = tmp_path / "acadsharp-linux-x64"
        (root / "lib").mkdir(parents=True)
        (root / "include").mkdir()
        (root / "lib" / ba.STATIC_LIBRARY_NAME).write_bytes(b"!<arch>\n")
        (root / "lib" / "libacadsharp_native.so").write_bytes(b"x")
        ba.finish_archive(str(root), "linux", "amd64", facts, builder_image="debian:bookworm-slim")
        return root

    def test_a_failed_static_smoke_removes_the_archive(self, tmp_path):
        # A managed-only or unlinkable `.a` is useless to a consumer, and
        # shipping one invites a link nobody has watched succeed.
        root = self._stage(tmp_path, {"static_ok": "0", "aot_warning_count": "16"})
        assert not (root / "lib" / ba.STATIC_LIBRARY_NAME).exists()
        with open(root / "metadata" / "LINKINFO.json") as f:
            info = json.load(f)
        assert "static_library" not in info
        assert info["static_certified"] is False

    def test_a_passed_static_smoke_keeps_it_and_records_the_link(self, tmp_path):
        root = self._stage(
            tmp_path,
            {
                "static_ok": "1",
                "aot_warning_count": "16",
                "static_system_libraries": "m",
                "static_link_args": "-Wl,-u,_GLOBAL__sub_I_main.cpp",
            },
        )
        assert (root / "lib" / ba.STATIC_LIBRARY_NAME).exists()
        with open(root / "metadata" / "LINKINFO.json") as f:
            info = json.load(f)
        assert info["static_certified"] is True
        assert info["system_libraries"] == ["m"]
        assert info["link_args"] == ["-Wl,-u,_GLOBAL__sub_I_main.cpp"]

    def test_a_shared_only_target_records_what_the_library_needs(self, tmp_path):
        root = self._stage(
            tmp_path, {"static_ok": "0", "aot_warning_count": "16", "shared_needed": "m"}
        )
        with open(root / "metadata" / "LINKINFO.json") as f:
            info = json.load(f)
        assert info["system_libraries"] == ["m"]
        assert info["link_args"] == []
