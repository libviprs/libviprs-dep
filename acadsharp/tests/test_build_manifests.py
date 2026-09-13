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
    "shared_system_libraries",
    "static_library",
    "static_init_library",
    "static_certified",
    "static_system_libraries",
    "static_link_args",
)

# The four that describe the static link. They are present together when
# the static smoke ran and passed and absent together otherwise, because
# a field whose meaning depends on another field's value is what a
# build.rs author reads wrong.
FROZEN_STATIC_FIELDS = (
    "static_library",
    "static_init_library",
    "static_system_libraries",
    "static_link_args",
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


def _shared_only(**kwargs):
    base = {"shared_system_libraries": ["m", "dl", "pthread"]}
    base.update(kwargs)
    return base


def _certified(**kwargs):
    base = dict(
        shared_system_libraries=["m"],
        static_library=ba.STATIC_LIBRARY_NAME,
        static_init_library=ba.STATIC_INIT_LIBRARY_NAME,
        static_certified=True,
        static_system_libraries=["m"],
        static_link_args=[],
    )
    base.update(kwargs)
    return base


class TestTheFieldNamesAreFrozen:
    def test_the_driver_declares_exactly_these_fields(self):
        assert tuple(ba.LINKINFO_FIELDS) == FROZEN_LINKINFO_FIELDS

    def test_a_complete_manifest_carries_every_one(self):
        info = ba.make_linkinfo("linux", "amd64", **_certified())
        assert set(info) == set(FROZEN_LINKINFO_FIELDS)

    def test_the_static_fields_are_declared_as_a_group(self):
        assert tuple(ba.STATIC_LINKINFO_FIELDS) == FROZEN_STATIC_FIELDS
        assert set(FROZEN_STATIC_FIELDS) < set(FROZEN_LINKINFO_FIELDS)

    def test_schema_version_is_one(self):
        assert ba.linkinfo_skeleton("linux", "amd64")["schema_version"] == 1

    def test_it_round_trips_through_json(self):
        info = ba.make_linkinfo("linux", "arm64", **_shared_only())
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
    def test_a_shared_only_target_omits_every_static_field(self):
        # An empty string here would read as "there is a static library
        # at the path ''" to anything that only checks the key exists,
        # and an empty list would read as a measured requirement of none.
        info = ba.make_linkinfo("mac", "arm64", **_shared_only())
        for field in FROZEN_STATIC_FIELDS:
            assert field not in info
        assert info["static_certified"] is False

    def test_a_static_target_carries_both_archives(self):
        info = ba.make_linkinfo("linux", "amd64", **_certified())
        assert info["static_library"] == "lib/libacadsharp_native.a"
        assert info["static_init_library"] == "lib/libacadsharp_native_init.a"

    def test_an_empty_static_library_path_is_refused(self):
        with pytest.raises(ValueError, match="absent"):
            ba.make_linkinfo("linux", "amd64", **_certified(static_library=""))

    def test_an_empty_static_init_library_path_is_refused(self):
        with pytest.raises(ValueError, match="absent"):
            ba.make_linkinfo("linux", "amd64", **_certified(static_init_library=""))

    def test_the_shared_library_path_is_relative_to_the_archive_root(self):
        info = ba.linkinfo_skeleton("mac", "arm64")
        assert info["shared_library"] == "lib/libacadsharp_native.dylib"


class TestTheStaticFieldsTravelTogether:
    """Present when the static smoke ran and passed, absent otherwise.

    A `static_link_args` sitting next to `static_certified: false` reads
    as the shared link's arguments to anyone who does not know the rule,
    and `system_libraries` used to be exactly that: the static link's
    list on a certified target and the shared library's NEEDED list
    everywhere else, in one field, with nothing saying which.
    """

    @pytest.mark.parametrize("field", list(FROZEN_STATIC_FIELDS))
    def test_certifying_without_one_of_them_is_refused(self, field):
        with pytest.raises(ValueError, match="not measured"):
            ba.make_linkinfo("linux", "amd64", **_certified(**{field: None}))

    @pytest.mark.parametrize("field", list(FROZEN_STATIC_FIELDS))
    def test_giving_one_without_certifying_is_refused(self, field):
        value = [] if field.endswith(("libraries", "args")) else "lib/whatever.a"
        with pytest.raises(ValueError, match="without static_certified"):
            ba.make_linkinfo("linux", "amd64", **_shared_only(**{field: value}))

    def test_the_default_is_shared_only(self):
        info = ba.make_linkinfo("linux", "amd64", **_shared_only())
        assert info["static_certified"] is False

    def test_certified_is_a_json_boolean_not_a_string(self):
        assert ba.make_linkinfo("linux", "amd64", **_certified())["static_certified"] is True


class TestTheTwoSystemLibraryListsAreSeparate:
    def test_shared_is_always_measured(self):
        # It comes off the shared library's own NEEDED list, which exists
        # on every target, so there is never a reason for it to be absent.
        for cell in (("mac", "arm64"), ("linux", "amd64")):
            info = ba.make_linkinfo(*cell, **_shared_only())
            assert "shared_system_libraries" in info

    def test_they_do_not_have_to_agree(self):
        info = ba.make_linkinfo(
            "linux",
            "amd64",
            **_certified(shared_system_libraries=["m"], static_system_libraries=["m", "rt"]),
        )
        assert info["shared_system_libraries"] == ["m"]
        assert info["static_system_libraries"] == ["m", "rt"]

    def test_both_are_bare_names_not_flags(self):
        with pytest.raises(ValueError, match="bare"):
            ba.make_linkinfo("linux", "amd64", **_shared_only(shared_system_libraries=["-lm"]))
        with pytest.raises(ValueError, match="bare"):
            ba.make_linkinfo("linux", "amd64", **_certified(static_system_libraries=["-lm"]))

    def test_a_path_is_refused(self):
        with pytest.raises(ValueError, match="bare"):
            ba.make_linkinfo(
                "linux", "amd64", **_shared_only(shared_system_libraries=["/usr/lib/libm.a"])
            )


class TestLinkArgumentsCannotCarryARequirement:
    """`cargo:rustc-link-arg` does not reach a dependent's link line.

    A build script's link *libraries* travel to anything that depends on
    the crate; its link *arguments* bind to that package's own targets.
    So a requirement written as an argument passes the `-sys` crate's own
    tests, stays green in its CI, and is missing from every consumer. The
    runtime's initialiser is a library for that reason, and anything that
    forces a symbol is refused here rather than shipped and hoped about.
    """

    def test_the_dead_nativeaot_initialiser_flag_is_refused(self):
        with pytest.raises(ValueError, match="NativeAOT_StaticInitialization"):
            ba.make_linkinfo(
                "linux",
                "amd64",
                **_certified(
                    static_link_args=["-Wl,--require-defined,NativeAOT_StaticInitialization"]
                ),
            )

    def test_the_macos_spelling_of_the_same_flag_is_refused(self):
        with pytest.raises(ValueError, match="NativeAOT_StaticInitialization"):
            ba.make_linkinfo(
                "linux",
                "amd64",
                **_certified(static_link_args=["-Wl,-u,_NativeAOT_StaticInitialization"]),
            )

    @pytest.mark.parametrize(
        "arg",
        [
            "-Wl,-u,_GLOBAL__sub_I_main.cpp",
            "-Wl,--undefined=something",
            "-Wl,--require-defined,something",
        ],
    )
    def test_any_symbol_forcing_argument_is_refused(self, arg):
        with pytest.raises(ValueError, match="never reaches a dependent"):
            ba.make_linkinfo("linux", "amd64", **_certified(static_link_args=[arg]))

    def test_an_ordinary_argument_is_kept(self):
        info = ba.make_linkinfo("linux", "amd64", **_certified(static_link_args=["-Wl,-z,now"]))
        assert info["static_link_args"] == ["-Wl,-z,now"]

    def test_the_example_from_the_issue_is_not_a_default(self):
        skeleton = ba.linkinfo_skeleton("linux", "amd64")
        for field in ("shared_system_libraries", *FROZEN_STATIC_FIELDS):
            assert field not in skeleton


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

    def test_it_ships_the_initialiser_as_its_own_archive(self):
        # libbootstrapperdll.o defines no global symbol at all, so nothing
        # can pull it out of an archive and the first managed call aborts.
        # Forcing it with -Wl,-u works for a hand-written cc line and not
        # for the consumer, because a build script's link arguments do not
        # reach a dependent. So it ships as a library instead.
        script = ba.stage_script("linux")
        assert "--globalize-symbol" in script
        assert "_GLOBAL__sub_I" in script
        assert ba.STATIC_INIT_LIBRARY_NAME in script

    def test_the_static_smoke_links_it_whole_and_first(self):
        # Reversed, the link fails on RhRegisterOSModule; without the
        # whole-archive it links clean and aborts at the first call.
        script = ba.stage_script("linux")
        whole = script.index('-Wl,--whole-archive "$INIT_A"')
        main = script.index('"$MERGED" $SYSLIBS')
        assert whole < main
        assert "-Wl,--no-whole-archive" in script

    @pytest.mark.parametrize("plat", ["linux", "musl", "mac"])
    def test_it_never_forces_a_symbol_on_the_link_line(self, plat):
        code = "\n".join(
            line for line in ba.stage_script(plat).splitlines() if not line.lstrip().startswith("#")
        )
        for flag in ba.SYMBOL_FORCING_FLAGS:
            assert f"-Wl,{flag}" not in code

    def test_it_merges_by_extracting_so_no_two_members_share_a_name(self):
        # `ar addlib` keeps each source archive's member names and two
        # runtime archives ship objects called the same thing, which
        # forecloses --whole-archive on the result.
        # Comments out first: the script explains at length why addlib
        # is not used, and a whole-file grep fires on the explanation.
        script = ba.stage_script("linux")
        code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
        assert "addlib" not in code
        assert "${stem}__" in code

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
    def _stage(self, tmp_path, facts, *, init=True):
        root = tmp_path / "acadsharp-linux-x64"
        (root / "lib").mkdir(parents=True)
        (root / "include").mkdir()
        (root / "lib" / ba.STATIC_LIBRARY_NAME).write_bytes(b"!<arch>\n")
        if init:
            (root / "lib" / ba.STATIC_INIT_LIBRARY_NAME).write_bytes(b"!<arch>\n")
        (root / "lib" / "libacadsharp_native.so").write_bytes(b"x")
        ba.finish_archive(str(root), "linux", "amd64", facts, builder_image="debian:bookworm-slim")
        return root

    def _linkinfo(self, root):
        with open(root / "metadata" / "LINKINFO.json") as f:
            return json.load(f)

    def test_a_failed_static_smoke_removes_both_archives(self, tmp_path):
        # A managed-only or unlinkable `.a` is useless to a consumer, and
        # an initialiser archive with nothing to initialise is worse.
        root = self._stage(tmp_path, {"static_ok": "0", "aot_warning_count": "16"})
        assert not (root / "lib" / ba.STATIC_LIBRARY_NAME).exists()
        assert not (root / "lib" / ba.STATIC_INIT_LIBRARY_NAME).exists()
        info = self._linkinfo(root)
        for field in FROZEN_STATIC_FIELDS:
            assert field not in info
        assert info["static_certified"] is False

    def test_a_missing_init_archive_is_not_certified(self, tmp_path):
        # The smoke said it passed and the file is not there, so the two
        # disagree and the archive ships shared-only rather than claiming
        # a link nothing can reproduce.
        root = self._stage(
            tmp_path,
            {"static_ok": "1", "aot_warning_count": "16", "static_system_libraries": "m"},
            init=False,
        )
        assert self._linkinfo(root)["static_certified"] is False
        assert not (root / "lib" / ba.STATIC_LIBRARY_NAME).exists()

    def test_a_passed_static_smoke_keeps_both_and_records_the_link(self, tmp_path):
        root = self._stage(
            tmp_path,
            {
                "static_ok": "1",
                "aot_warning_count": "16",
                "shared_needed": "m",
                "static_system_libraries": "m",
                "static_link_args": "",
            },
        )
        assert (root / "lib" / ba.STATIC_LIBRARY_NAME).exists()
        assert (root / "lib" / ba.STATIC_INIT_LIBRARY_NAME).exists()
        info = self._linkinfo(root)
        assert info["static_certified"] is True
        assert info["shared_system_libraries"] == ["m"]
        assert info["static_system_libraries"] == ["m"]
        assert info["static_link_args"] == []
        assert info["static_init_library"] == "lib/libacadsharp_native_init.a"

    def test_a_shared_only_target_records_what_the_library_needs(self, tmp_path):
        root = self._stage(
            tmp_path, {"static_ok": "0", "aot_warning_count": "16", "shared_needed": "m dl"}
        )
        info = self._linkinfo(root)
        assert info["shared_system_libraries"] == ["m", "dl"]


class TestTheVersionCanBeOverridden:
    """`--version`, because the release workflow has a dispatch override.

    `release-acadsharp.yml` takes a `version` input, tags from it and
    writes the notes from it. Without a flag the build ignored it, so a
    dispatch published archives built from the committed VERSION under a
    different tag, with LINKINFO.json and the release page disagreeing
    and nothing anywhere noticing. G1.5's own acceptance needs it too: a
    throwaway pre-release on 3.7.1-viprs.0 is not reachable otherwise
    without committing a bump and reverting it.
    """

    def test_the_parser_accepts_one(self):
        parser_help = ba.main.__doc__ or ""
        assert parser_help is not None  # main is documented by the module docstring
        assert ba.main(["--version", "3.7.1-viprs.0", "--plan"]) == 0

    def test_the_resolved_version_reaches_the_manifest(self):
        info = ba.make_linkinfo("linux", "amd64", version="3.7.1-viprs.0", **_shared_only())
        assert info["artifact_version"] == "3.7.1-viprs.0"
        assert info["acadsharp_version"] == "3.7.1"

    def test_it_reaches_the_release_tag_and_the_notes(self):
        assert ba.release_tag("3.7.1-viprs.0") == "acadsharp-3.7.1-viprs.0"
        assert "shim revision 0" in ba.release_notes("3.7.1-viprs.0")

    def test_an_unparseable_override_is_refused(self):
        with pytest.raises(SystemExit):
            ba.main(["--version", "3.7.1", "--plan"])

    def test_an_unpinned_override_is_refused(self):
        with pytest.raises(SystemExit):
            ba.main(["--version", "9.9.9-viprs.1", "--plan"])

    def test_no_flag_falls_back_to_the_file(self):
        info = ba.make_linkinfo("linux", "amd64", **_shared_only())
        assert info["artifact_version"] == ba.read_version()


class TestTheMergeKeepsSourceOrder:
    """Member order in the merged archive is load-bearing.

    The linker emits `.init_array` in the order it pulls members, so the
    archive's own order decides what runs when. Built from the
    filesystem's order the archive links perfectly and the binary
    segfaults on the way out, reproducibly. Members therefore go in the
    order each source archive lists them, managed archive first, which is
    what ILC linked and what `ar addlib` used to produce.
    """

    def test_the_managed_archive_goes_first(self):
        script = ba.stage_script("linux")
        managed = script.index('echo "$MANAGED" > /tmp/merge-sources.txt')
        runtime = script.index("for name in")
        assert managed < runtime

    def test_members_are_listed_in_each_archives_own_order(self):
        script = ba.stage_script("linux")
        assert 'ar t "$src" | while read -r member' in script

    def test_they_are_appended_rather_than_replaced(self):
        # `ar r` reorders on replace and xargs may split the list, so the
        # order only survives with `q`.
        script = ba.stage_script("linux")
        assert 'xargs -0 ar qc "$MERGED"' in script

    def test_nothing_sorts_the_member_list(self):
        code = "\n".join(
            line
            for line in ba.stage_script("linux").splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "merge-members.txt | sort" not in code
        assert "sort" not in code.split("merge-members.txt")[1].split("ranlib")[0]

    def test_a_lost_member_stops_the_merge(self):
        # Extracting fewer files than the archive lists means two members
        # shared a name inside one source archive and one overwrote the
        # other, which would drop a definition silently.
        script = ba.stage_script("linux")
        assert "so a member was lost" in script
        assert "MERGE_OK=0" in script
