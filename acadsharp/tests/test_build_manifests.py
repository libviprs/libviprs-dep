"""`LINKINFO.json` and `BUILDINFO.json`, which are the consumer contract.

`acadsharp-rs`'s `build.rs` parses `LINKINFO.json` to emit
`cargo:rustc-link-search` and `cargo:rustc-link-lib`, and checks
`abi_version`, `abi_fingerprint` and `wire_version` at first use. It emits
no `cargo:rustc-link-arg` and never will: that directive binds to the
emitting package's own targets and never reaches a dependent's link line,
which is why the runtime's initialiser ships as a library instead. So the
field names are frozen, the fingerprint is derived from the shipped header
rather than typed, and the two fields that describe the real link cannot be
filled in from an example.

The field list is not the whole contract either. `docs/LINKINFO.md` is its
specification, it ships inside the archive, and the tests below hold the two
to each other so a field cannot be added here without a row over there.
"""

import hashlib
import inspect
import json
import os
import re
import shutil
import subprocess

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
    "dwg_version_min",
    "dwg_version_max",
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
    "viprs_acad_get_capabilities_v1",
    "viprs_acad_open_path_utf8",
    "viprs_acad_open_memory",
    "viprs_acad_view_count",
    "viprs_acad_get_view_info_v1",
    "viprs_acad_decode_begin",
    "viprs_acad_decode_next_batch",
    "viprs_acad_decode_close",
    "viprs_acad_close",
)


# The range the pinned build answers with, written here rather than read
# out of the shim. These tests are an oracle of the manifest writer the
# same way the conformance consumers are an oracle of the library: a
# number this file computed from the same place the driver does could
# only ever agree with itself.
MEASURED_RANGE = {"dwg_version_min": 1014, "dwg_version_max": 1032}

# The same pair as the staging script writes them: facts.txt is text, and
# finish_archive is what turns them into integers.
MEASURED_FACTS = {"dwg_version_min": "1014", "dwg_version_max": "1032"}


def _shared_only(**kwargs):
    base = {"shared_system_libraries": ["m", "dl", "pthread"]}
    base.update(MEASURED_RANGE)
    base.update(kwargs)
    return base


def _certified(**kwargs):
    base = dict(
        **MEASURED_RANGE,
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
        assert (abi, wire) == (2, 2)
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


class TestTheReadRangeIsAMeasuredFact:
    """The AC10xx codes this build reads are a fact about the backing
    reader, not a clause in the ABI. The header's own preamble says it
    describes a VIPRS boundary and never ACadSharp, so the range is not a
    macro in it: the library answers with it at run time, the archive smoke
    asks the library that question in the container, and what the library
    said is what the manifest records. Nothing here reads a number out of
    the shim, which is the point: a manifest built from the same constant
    the library is built from agrees with itself whatever either of them
    says.
    """

    def test_the_shared_smoke_asks_the_library(self):
        source = ba.archive_smoke_source()
        assert "caps.dwg_version_min" in source and "caps.dwg_version_max" in source, (
            "the smoke does not read the range off the capabilities struct, so the "
            "fact recorded downstream of it is not a measurement"
        )
        assert "DWG_VERSION_MIN=" in source and "DWG_VERSION_MAX=" in source, (
            "the smoke never prints the range, so the staging script has nothing to "
            "record and the manifest has nothing to write"
        )

    @pytest.mark.parametrize("plat", ["linux", "musl", "mac"])
    def test_the_staging_script_records_both(self, plat):
        script = ba.stage_script()
        assert "fact dwg_version_min" in script and "fact dwg_version_max" in script, (
            f"the {plat} staging script records no read range, so finish_archive has "
            "no measurement to put in LINKINFO.json"
        )
        assert "DWG_VERSION_MIN=" in script and "DWG_VERSION_MAX=" in script, (
            "the script records the fact without reading it out of the smoke output"
        )

    def test_the_manifest_carries_them_as_integers(self):
        info = ba.make_linkinfo("linux", "amd64", **_shared_only())
        assert info["dwg_version_min"] == 1014
        assert info["dwg_version_max"] == 1032
        for field in ("dwg_version_min", "dwg_version_max"):
            assert isinstance(info[field], int) and not isinstance(info[field], bool), (
                f"{field} is {info[field]!r}. build.rs compares it against a number, and "
                '"1014" is not 1014'
            )

    @pytest.mark.parametrize("field", ["dwg_version_min", "dwg_version_max"])
    def test_a_range_nobody_measured_is_refused(self, field):
        # Same rule aot_warning_count has: a measurement is taken or the
        # build was not watched. A default here would be a number that
        # describes the build the default was written for.
        with pytest.raises(ValueError, match=field):
            ba.make_linkinfo("linux", "amd64", **_shared_only(**{field: None}))

    @pytest.mark.parametrize("value", ["1014", 1014.0, True, 0, 99, 20000])
    def test_a_range_that_is_not_an_ac10xx_code_is_refused(self, value):
        with pytest.raises(ValueError, match="dwg_version_min"):
            ba.make_linkinfo("linux", "amd64", **_shared_only(dwg_version_min=value))

    def test_an_inverted_range_is_refused(self):
        # A library reading AC1032 up to AC1014 reads nothing, and a
        # consumer branching on the pair would decide that quietly.
        with pytest.raises(ValueError, match="dwg_version"):
            ba.make_linkinfo(
                "linux", "amd64", **_shared_only(dwg_version_min=1032, dwg_version_max=1014)
            )

    def test_finish_archive_takes_them_from_the_recorded_facts(self, tmp_path):
        root = tmp_path / "acadsharp-linux-x64"
        (root / "lib").mkdir(parents=True)
        (root / "lib" / ba.shared_library_name("linux")).write_bytes(b"binary")
        info = ba.finish_archive(
            str(root),
            "linux",
            "amd64",
            {
                "aot_warning_count": "0",
                "shared_needed": "",
                "static_ok": "0",
                "dwg_version_min": "1009",
                "dwg_version_max": "1099",
            },
            builder_image="debian:bookworm-slim",
        )
        assert (info["dwg_version_min"], info["dwg_version_max"]) == (1009, 1099), (
            "finish_archive is not reading the range off the facts the container "
            "recorded, so the manifest states something the library was never asked"
        )

    def test_facts_with_no_range_stop_the_packaging(self, tmp_path):
        # The smoke prints it on every target, so a facts file without it
        # is a smoke that did not run or a staging script that stopped
        # recording. Both are archives whose manifest would be guessing.
        root = tmp_path / "acadsharp-linux-x64"
        (root / "lib").mkdir(parents=True)
        (root / "lib" / ba.shared_library_name("linux")).write_bytes(b"binary")
        with pytest.raises(ValueError, match="dwg_version_min"):
            ba.finish_archive(
                str(root),
                "linux",
                "amd64",
                {"aot_warning_count": "0", "shared_needed": "", "static_ok": "0"},
                builder_image="debian:bookworm-slim",
            )


class TestTheEntryPointList:
    def test_every_declared_function_is_found(self):
        assert tuple(ba.header_entry_points()) == ENTRY_POINTS

    def test_struct_type_names_are_not_mistaken_for_functions(self):
        # Every struct on the boundary is a tag and none of them is exported.
        # `viprs_acad_capabilities_v1` used to be both a struct and a function
        # and was the reason this check exists; the calls carry `get_` now, so
        # the three struct names must all be absent and the two renamed calls
        # must both be present.
        found = ba.header_entry_points()
        for struct in (
            "viprs_acad_limits_v1",
            "viprs_acad_capabilities_v1",
            "viprs_acad_view_info_v1",
        ):
            assert struct not in found, (
                f"{struct} is a struct tag and the extractor read it as an export, so "
                "the verifier would demand a symbol no library has"
            )
        assert "viprs_acad_get_capabilities_v1" in found
        assert "viprs_acad_get_view_info_v1" in found

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


class TestTheContractDocumentsShip:
    """The archive has to carry every contract it tells a consumer to read.

    The header could always be consumed from the archive. The other two
    contracts could not: `WIRE.md` is the only definition of the batch
    stream anywhere, `LINKINFO.md` is the only definition of the manifest
    a build script parses, and both stayed in the repository while the
    binaries shipped. So whoever wrote a consumer read `build_acadsharp.py`
    to find out what a field meant, which is exactly the coupling freezing
    the ABI was meant to remove.
    """

    DOCS_DIR = os.path.join(ACAD_DIR, "docs")

    def _staged(self, tmp_path, plat="linux", arch="amd64"):
        root = tmp_path / f"acadsharp-{plat}-x64"
        (root / "lib").mkdir(parents=True)
        (root / "lib" / ba.shared_library_name(plat)).write_bytes(b"binary")
        ba.finish_archive(
            str(root),
            plat,
            arch,
            {
                "aot_warning_count": "0",
                "shared_needed": "",
                "static_ok": "0",
                "dwg_version_min": "1014",
                "dwg_version_max": "1032",
            },
            builder_image="debian:bookworm-slim",
        )
        return root

    def test_every_markdown_file_at_the_top_of_docs_ships(self):
        present = {
            name
            for name in os.listdir(self.DOCS_DIR)
            if name.endswith(".md") and os.path.isfile(os.path.join(self.DOCS_DIR, name))
        }
        assert set(ba.PUBLISHED_DOCS) == present, (
            "acadsharp/docs/ and PUBLISHED_DOCS disagree. A contract document that "
            "lands in the repository and not in the archive is one a consumer author "
            "cannot read without this repository, which is the whole defect."
        )

    def test_the_decision_records_stay_behind(self):
        # The positive control for the rule above. Without something in
        # docs/adr/, "top-level *.md only" would be a distinction over an
        # empty set and the test would pass whatever the rule said.
        adr = os.path.join(self.DOCS_DIR, "adr")
        records = [n for n in os.listdir(adr) if n.endswith(".md")]
        assert records, "docs/adr/ is empty, so the top-level-only rule is untested"
        for name in records:
            assert name not in ba.PUBLISHED_DOCS, (
                f"{name} is a decision record. Those are the producer's history and "
                "not the consumer's contract, so they stay out of the archive."
            )

    def test_finish_archive_stages_them_beside_the_header(self, tmp_path):
        root = self._staged(tmp_path)
        for name in ba.PUBLISHED_DOCS:
            staged = root / "docs" / name
            assert staged.is_file(), f"docs/{name} is not in the staged tree"
            with open(os.path.join(self.DOCS_DIR, name), "rb") as f:
                original = f.read()
            assert staged.read_bytes() == original, (
                f"docs/{name} in the archive is not byte-identical to the repository's"
            )

    def test_checksums_covers_them_like_everything_else(self, tmp_path):
        root = self._staged(tmp_path)
        with open(root / "metadata" / "CHECKSUMS.txt") as f:
            listed = {line.split(None, 1)[1].strip() for line in f if line.strip()}
        for name in ba.PUBLISHED_DOCS:
            rel = f"docs/{name}"
            assert rel in listed, f"{rel} shipped without a digest beside it"

    def test_a_missing_document_stops_the_packaging(self, tmp_path):
        # Shipping the binaries and quietly dropping a contract is the
        # failure this whole change is about, so it is a refusal rather
        # than a warning.
        partial = tmp_path / "docs"
        partial.mkdir()
        (partial / ba.PUBLISHED_DOCS[0]).write_text("# a contract\n")
        root = tmp_path / "acadsharp-linux-x64"
        root.mkdir()
        with pytest.raises(ValueError, match=ba.PUBLISHED_DOCS[1]):
            ba.stage_docs(str(root), docs_dir=str(partial))

    def test_the_spec_documents_every_frozen_manifest_field(self):
        # The field list used to exist only as this tuple, which meant a
        # consumer author learned what `static_init_library` was by
        # reading the producer. Now it is specified, and the two are held
        # to each other: a field added here without a row over there
        # ships undocumented.
        with open(os.path.join(self.DOCS_DIR, "LINKINFO.md")) as f:
            spec = f.read()
        rows = set(re.findall(r"^\| `([a-z0-9_]+)` \|", spec, re.M))
        assert rows == set(ba.LINKINFO_FIELDS), (
            f"LINKINFO.md's field table and LINKINFO_FIELDS disagree: "
            f"{sorted(set(ba.LINKINFO_FIELDS) - rows)} have no row, "
            f"{sorted(rows - set(ba.LINKINFO_FIELDS))} describe a field nothing writes"
        )

    def test_the_spec_marks_exactly_the_static_fields_optional(self):
        with open(os.path.join(self.DOCS_DIR, "LINKINFO.md")) as f:
            spec = f.read()
        optional = set()
        for name, presence in re.findall(r"^\| `([a-z0-9_]+)` \| [^|]+ \| ([^|]+) \|", spec, re.M):
            if "always" not in presence:
                optional.add(name)
        assert optional == set(ba.STATIC_LINKINFO_FIELDS), (
            "the four static link fields are the only optional ones, and which "
            "fields a consumer may find missing is the thing it cannot discover "
            "from one archive"
        )

    def test_the_verifier_requires_exactly_the_documents_that_ship(self):
        # Comments come out first: the script's own header paragraph
        # explains why the documents are load-bearing, and a whole-file
        # grep fires on the explanation as readily as on a real entry.
        with open(ba.VERIFY_ARCHIVE_SCRIPT) as f:
            code = "\n".join(
                line for line in f.read().splitlines() if not line.lstrip().startswith("#")
            )
        required = set(re.findall(r'"docs/([A-Za-z0-9_.-]+\.md)"', code))
        assert required == set(ba.PUBLISHED_DOCS), (
            "verify_archive.sh and PUBLISHED_DOCS disagree about which documents an "
            "archive must carry, so one of them is refusing an archive the other "
            "happily builds"
        )


class TestTheStagingScript:
    """The script that measures the link facts is a tracked file.

    It was 185 lines of shell inside a Python string with two placeholder
    words substituted before it was written out, which meant
    `tools/shellcheck-all.sh` never saw a line of it: that script
    discovers its work with `git ls-files '*.sh'`, so the most intricate
    shell in the repository was the one part of it no linter had ever
    looked at, and the `# shellcheck disable` comment in it was addressed
    to nobody.

    The two lists arrive in the environment now, so the file that runs in
    the container is byte for byte the file in the tree, and the tests
    below are about that rather than about substitution.
    """

    def test_it_is_a_shell_script_the_linter_will_find(self):
        assert os.path.isfile(ba.STAGE_SCRIPT), "there is no scripts/stage.sh"
        assert ba.STAGE_SCRIPT.endswith(".sh"), (
            "tools/shellcheck-all.sh discovers its work with `git ls-files '*.sh'`, so a "
            "staging script under any other name is one shellcheck never opens"
        )
        assert os.access(ba.STAGE_SCRIPT, os.X_OK), "scripts/stage.sh is not executable"

    def test_git_actually_tracks_it(self):
        # The point of the move is that the repo-wide glob finds it, and
        # an untracked file passes every check above and is still invisible
        # to `git ls-files`.
        listed = subprocess.run(
            ["git", "ls-files", "*.sh"],
            cwd=os.path.dirname(ACAD_DIR),
            capture_output=True,
            text=True,
            check=False,
        )
        if listed.returncode != 0:
            pytest.skip("not a git checkout")
        assert "acadsharp/scripts/stage.sh" in listed.stdout.split(), (
            "stage.sh is not tracked, so tools/shellcheck-all.sh does not lint it and "
            "the move accomplished nothing"
        )

    def test_it_passes_shellcheck(self):
        if not shutil.which("shellcheck"):
            pytest.skip("shellcheck not installed")
        done = subprocess.run(
            ["shellcheck", ba.STAGE_SCRIPT], capture_output=True, text=True, check=False
        )
        assert done.returncode == 0, done.stdout + done.stderr

    def test_the_driver_carries_no_shell_script_of_its_own(self):
        with open(ba.__file__) as f:
            driver = f.read()
        assert "#!/bin/sh" not in driver, (
            "build_acadsharp.py holds a shell script in a string again. Whatever it is, "
            "shellcheck cannot see it: tools/shellcheck-all.sh only opens tracked *.sh"
        )

    def test_what_runs_is_what_is_in_the_tree(self):
        with open(ba.STAGE_SCRIPT) as f:
            assert ba.stage_script() == f.read()

    @pytest.mark.parametrize("plat", ["linux", "musl", "mac"])
    def test_the_two_lists_arrive_in_the_environment(self, plat):
        env = ba.stage_env(plat)
        assert env["RUNTIME_ARCHIVES"].split() == list(ba.RUNTIME_ARCHIVES)
        assert env["STATIC_SYSTEM_LIBRARY_LADDER"] == ";".join(
            ba.STATIC_SYSTEM_LIBRARY_LADDER.get(plat, [""])
        )

    def test_a_list_that_never_arrives_is_a_refusal(self):
        # Unset, `for name in $RUNTIME_ARCHIVES` is an empty loop: the
        # merge produces an archive of one object, the static smoke fails
        # to link it, and the cell ships shared-only, which is a recorded
        # outcome nobody reads. `:?` makes it a failure instead.
        script = ba.stage_script()
        assert "${RUNTIME_ARCHIVES:?}" in script
        assert "${STATIC_SYSTEM_LIBRARY_LADDER:?}" in script

    @pytest.mark.parametrize("plat,arch", [("linux", "amd64"), ("musl", "arm64")])
    def test_the_dockerfile_hands_them_over(self, plat, arch):
        dockerfile = ba.make_dockerfile(ba.read_version(), plat, arch)
        env = ba.stage_env(plat)
        for name, value in env.items():
            assert f'{name}="{value}"' in dockerfile, (
                f"the {plat} Dockerfile does not set {name}, so stage.sh refuses at "
                "the line that needs it"
            )

    def test_the_notices_carry_no_restore_timing(self):
        # `dotnet list package` restores first and prints how long that
        # took, so the notices file carried `Restored ... (in 198 ms).`
        # and two builds of one commit produced a different
        # THIRD_PARTY_NOTICES, a different CHECKSUMS.txt and a different
        # archive digest. Measured while proving this move changed
        # nothing: that line was the only thing in the archive besides
        # BUILDINFO's timestamp that moved between two builds of the same
        # tree, and the release notes publish those digests.
        script = ba.stage_script()
        assert "/^ *Restored /d" in script, (
            "the package list goes into THIRD_PARTY_NOTICES with its restore timing, "
            "so the archive's digest depends on how fast the runner was"
        )

    def test_the_mac_path_hands_them_over_too(self):
        # No Dockerfile there, so the same two have to reach the process
        # environment. The mac cell never attempts a static link, and the
        # ladder is empty for it, which the script only ever reads inside
        # the half that does.
        source = inspect.getsource(ba._build_mac_native)
        assert 'stage_env("mac")' in source, (
            "the mac build runs stage.sh without the lists the container gets"
        )

    def test_it_ships_the_initialiser_as_its_own_archive(self):
        # libbootstrapperdll.o defines no global symbol at all, so nothing
        # can pull it out of an archive and the first managed call aborts.
        # Forcing it with -Wl,-u works for a hand-written cc line and not
        # for the consumer, because a build script's link arguments do not
        # reach a dependent. So it ships as a library instead.
        script = ba.stage_script()
        assert "--globalize-symbol" in script
        assert "_GLOBAL__sub_I" in script
        assert ba.STATIC_INIT_LIBRARY_NAME in script

    def test_the_static_smoke_links_it_whole_and_first(self):
        # Reversed, the link fails on RhRegisterOSModule; without the
        # whole-archive it links clean and aborts at the first call.
        script = ba.stage_script()
        whole = script.index('-Wl,--whole-archive "$INIT_A"')
        main = script.index('"$MERGED" $SYSLIBS')
        assert whole < main
        assert "-Wl,--no-whole-archive" in script

    @pytest.mark.parametrize("plat", ["linux", "musl", "mac"])
    def test_it_never_forces_a_symbol_on_the_link_line(self, plat):
        code = "\n".join(
            line for line in ba.stage_script().splitlines() if not line.lstrip().startswith("#")
        )
        for flag in ba.SYMBOL_FORCING_FLAGS:
            assert f"-Wl,{flag}" not in code

    def test_it_merges_by_extracting_so_no_two_members_share_a_name(self):
        # `ar addlib` keeps each source archive's member names and two
        # runtime archives ship objects called the same thing, which
        # forecloses --whole-archive on the result.
        # Comments out first: the script explains at length why addlib
        # is not used, and a whole-file grep fires on the explanation.
        script = ba.stage_script()
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
                line for line in ba.stage_script().splitlines() if not line.lstrip().startswith("#")
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


class TestEveryFileTheBuildReadsIsStaged:
    """The container only has what the build context puts in it.

    `acadsharp/VERSION` was not one of those things. The csproj reads it
    during the build to generate the string `viprs_acad_get_capabilities_v1`
    reports, msbuild's `ReadLinesFromFile` returns nothing for a file that is
    not there rather than failing, and so every archive published shipped a
    library that answers the version question with an empty string. The C
    conformance consumer found it the first time CI ran it against an
    unpacked archive.

    So this reads the paths out of the csproj rather than listing them, and a
    third file added to that list is covered the day it lands.
    """

    @staticmethod
    def project_inputs():
        with open(os.path.join(ACAD_DIR, "native", "Viprs.ACadSharp.Native.csproj")) as f:
            csproj = f.read()
        found = re.findall(r"\$\(MSBuildProjectDirectory\)/\.\./([^<\s]+)", csproj)
        assert found, "the csproj no longer names its build inputs where this reader looks"
        return sorted(set(found))

    def test_the_csproj_reads_the_header_and_the_version(self):
        inputs = self.project_inputs()
        assert "VERSION" in inputs
        assert any(name.endswith("viprs_acadsharp.h") for name in inputs)

    def test_the_build_context_carries_every_one_of_them(self, tmp_path):
        ctx = str(tmp_path / "ctx")
        os.makedirs(ctx)
        ba._write_build_context(ctx)
        for name in self.project_inputs():
            assert os.path.isfile(os.path.join(ctx, name)), (
                f"the csproj reads {name} during the build and the build context does "
                "not carry it, so msbuild reads nothing and says nothing about it"
            )

    def test_the_staged_version_is_the_repository_version(self, tmp_path):
        ctx = str(tmp_path / "ctx")
        os.makedirs(ctx)
        ba._write_build_context(ctx)
        with open(os.path.join(ctx, "VERSION")) as f:
            staged = f.read().strip()
        assert staged == ba.read_version(), (
            f"the build would stamp {staged!r} into the library while the repository "
            f"says {ba.read_version()!r}"
        )

    def test_the_dockerfile_copies_every_one_of_them(self):
        dockerfile = ba.make_dockerfile(ba.read_version(), "linux", "arm64")
        copied = " ".join(line for line in dockerfile.splitlines() if line.startswith("COPY "))
        for name in self.project_inputs():
            top = name.split("/")[0]
            assert top in copied, (
                f"the Dockerfile never copies {top}, so the build reads {name} from a "
                "path that does not exist in the image"
            )


class TestTheShippedLibraryHasToSayWhatItIs:
    """The smoke asks, because nothing else did.

    `verify_archive.sh` reads symbols out of the bytes and the conformance
    consumers were not run by anything, so a library that loaded and answered
    every numeric question with the right number, and the one text question
    with nothing at all, shipped four times.
    """

    def test_the_smoke_asks_for_the_backing_version(self):
        # The symbol it resolves, not the struct it fills in. Both are
        # named in this source and only one of them is the export, which
        # is how a rename of the call reached CI through an assertion
        # that looked like it was checking the call.
        source = ba.archive_smoke_source()
        assert f'dlsym(h, "{ba.capabilities_entry_point()}")' in source
        assert "BACKING_VERSION=" in source

    def test_the_smoke_refuses_an_empty_one(self):
        source = ba.archive_smoke_source()
        assert "needed == 0" in source, (
            "the smoke reads the backing version and does not refuse an empty one, "
            "which is the state every published archive was in"
        )

    def test_the_smoke_includes_the_header_rather_than_declaring_the_struct(self):
        source = ba.archive_smoke_source()
        assert '#include "viprs_acadsharp.h"' in source
        assert "struct viprs_acad_capabilities_v1 {" not in source, (
            "the smoke declares the frozen struct by hand, so it agrees with whoever "
            "typed it and with nothing else"
        )

    def test_the_staging_script_compiles_it_against_the_staged_header(self):
        stage = ba.stage_script()
        assert '-I"$WORK/include"' in stage, (
            "the smoke includes the header, so the compile line has to say where it is"
        )

    def test_the_backing_version_is_recorded_as_a_fact(self):
        assert "fact backing_version" in ba.stage_script()


class TestTheSmokeResolvesOnlyWhatTheHeaderDeclares:
    """The generated smoke was half generated.

    It took the entry-point list from the header and then hardcoded the
    capabilities symbol, and its full C signature, as a string. Rename
    that call in the header and every other name follows while this one
    does not: the library resolves ten exports, the eleventh comes back
    NULL, the smoke exits 7, `shared_smoke_ok` is recorded as 0 and the
    driver refuses the archive. The whole conformance workflow dies at its
    first step, and the failure reads as a library that cannot say what it
    is rather than as a generator that asked for the wrong name.

    The check that closes it is not "the name is right", it is "every
    symbol this smoke hands to dlsym is one the header declares". The old
    assertion here matched `viprs_acad_capabilities_v1`, which is also the
    struct's name and appears in the same source, so a rename of the call
    went through it untouched.
    """

    def _resolved(self, source):
        """Every symbol the generated smoke resolves, by either route."""
        direct = set(re.findall(r'dlsym\(h, "([a-z0-9_]+)"\)', source))
        listed = set(re.findall(r'^\t"([a-z0-9_]+)",$', source, re.M))
        return direct | listed

    def test_every_symbol_it_resolves_is_declared_by_the_header(self):
        resolved = self._resolved(ba.archive_smoke_source())
        declared = set(ba.header_entry_points())
        undeclared = sorted(resolved - declared)
        assert not undeclared, (
            f"the smoke resolves {undeclared}, which the shipped header does not "
            "declare. dlsym returns NULL, the smoke exits non-zero, and the driver "
            "refuses every archive with a message about the library rather than "
            "about this generator."
        )

    def test_it_resolves_something_at_all(self):
        # The control. An empty set is a subset of anything, so the check
        # above passes over a smoke that resolves nothing.
        assert len(self._resolved(ba.archive_smoke_source())) >= 3

    def test_a_renamed_capabilities_call_carries(self):
        # The rename in libviprs-dep#59, done to a header this test writes
        # so it can be checked before the header moves. `get_` in front of
        # the call, and the struct keeps its name.
        renamed = [
            n.replace("viprs_acad_capabilities_v1", "viprs_acad_get_capabilities_v1")
            for n in ba.header_entry_points()
        ]
        source = ba.archive_smoke_source(entry_points=renamed)
        assert 'dlsym(h, "viprs_acad_get_capabilities_v1")' in source
        assert 'dlsym(h, "viprs_acad_capabilities_v1")' not in source, (
            "the smoke still asks for the old name, so it resolves NULL and exits 7 "
            "against a library that is perfectly fine"
        )
        assert self._resolved(source) <= set(renamed)

    def test_the_struct_is_not_renamed_with_it(self):
        # Only the call was renamed. The struct is still
        # `struct viprs_acad_capabilities_v1`, and a generator that
        # rewrote both would produce a smoke that does not compile.
        renamed = [
            n.replace("viprs_acad_capabilities_v1", "viprs_acad_get_capabilities_v1")
            for n in ba.header_entry_points()
        ]
        source = ba.archive_smoke_source(entry_points=renamed)
        assert "struct viprs_acad_capabilities_v1 caps;" in source

    def test_a_header_with_no_capabilities_call_is_refused(self):
        with pytest.raises(ValueError, match="capabilities"):
            ba.capabilities_entry_point(["viprs_acad_abi_version"])

    def test_a_header_with_two_is_refused(self):
        # Ambiguous rather than wrong: during a rename both spellings can
        # be declared at once, and the smoke calls exactly one through a
        # typed pointer.
        with pytest.raises(ValueError, match="capabilities"):
            ba.capabilities_entry_point(
                ["viprs_acad_capabilities_v1", "viprs_acad_get_capabilities_v1"]
            )


class TestAnUncertifiedStaticLibraryIsNotShipped:
    def _stage(self, tmp_path, facts, *, init=True):
        root = tmp_path / "acadsharp-linux-x64"
        (root / "lib").mkdir(parents=True)
        (root / "include").mkdir()
        (root / "lib" / ba.STATIC_LIBRARY_NAME).write_bytes(b"!<arch>\n")
        if init:
            (root / "lib" / ba.STATIC_INIT_LIBRARY_NAME).write_bytes(b"!<arch>\n")
        (root / "lib" / "libacadsharp_native.so").write_bytes(b"x")
        # The smoke records the read range on every target, so these
        # cases are about the static half rather than about that.
        ba.finish_archive(
            str(root),
            "linux",
            "amd64",
            dict(MEASURED_FACTS, **facts),
            builder_image="debian:bookworm-slim",
        )
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
        script = ba.stage_script()
        managed = script.index('echo "$MANAGED" > /tmp/merge-sources.txt')
        runtime = script.index("for name in")
        assert managed < runtime

    def test_members_are_listed_in_each_archives_own_order(self):
        script = ba.stage_script()
        assert 'ar t "$src" | while read -r member' in script

    def test_they_are_appended_rather_than_replaced(self):
        # `ar r` reorders on replace and xargs may split the list, so the
        # order only survives with `q`.
        script = ba.stage_script()
        assert 'xargs -0 ar qc "$MERGED"' in script

    def test_nothing_sorts_the_member_list(self):
        code = "\n".join(
            line for line in ba.stage_script().splitlines() if not line.lstrip().startswith("#")
        )
        assert "merge-members.txt | sort" not in code
        assert "sort" not in code.split("merge-members.txt")[1].split("ranlib")[0]

    def test_a_lost_member_stops_the_merge(self):
        # Extracting fewer files than the archive lists means two members
        # shared a name inside one source archive and one overwrote the
        # other, which would drop a definition silently.
        script = ba.stage_script()
        assert "so a member was lost" in script
        assert "MERGE_OK=0" in script
