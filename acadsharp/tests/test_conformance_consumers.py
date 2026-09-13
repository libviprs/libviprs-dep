"""The two conformance consumers, checked for the properties that make them proof.

A consumer that carries its own copy of the declarations proves that the copy
agrees with itself. The value of these two is that neither has a hand-written
view of the boundary: the C one includes the published header, and the crate
generates its declarations from that same file at build time. So what is worth
checking without a compiler is exactly that, plus the fact that each one still
exercises the cases the epic named.

The other thing here is a three-way comparison of the protocol constants. The
shim writes the stream, the C consumer parses it and the generated crate
parses it, and all three carry the numbers separately. A number that is right
in two of the three is the kind of bug that shows up as one record type going
missing from a large file, months later.

None of this runs a compiler. The consumers are built and run in containers
by `tests/conformance/c/run.sh` and `tests/conformance/rust/run.sh`; these
checks are what keeps them honest between runs.
"""

import os
import re
import subprocess

import pytest
from test_wire_protocol import FORWARD_PROBE_FIRST, RECORD_TYPES

HERE = os.path.dirname(os.path.abspath(__file__))
ACADSHARP = os.path.dirname(HERE)
CONFORMANCE = os.path.join(HERE, "conformance")
C_DIR = os.path.join(CONFORMANCE, "c")
RUST_DIR = os.path.join(CONFORMANCE, "rust")
FIND_SHIM = os.path.join(CONFORMANCE, "find_shim.sh")
WIRE_FORMAT_CS = os.path.join(ACADSHARP, "native", "Wire", "WireFormat.cs")

# The two names the same library goes by. The publish emits the assembly
# name; build_acadsharp.py stages it under the name a linker's -l takes.
PUBLISH_NAME = "viprs_acadsharp.so"
PACKAGED_NAME = "libacadsharp_native.so"

C_SOURCES = ("conformance.c", "vacb.c", "vacb.h", "layout_table.h", "run.sh")
RUST_SOURCES = (
    "Cargo.toml",
    "build.rs",
    os.path.join("src", "lib.rs"),
    os.path.join("src", "main.rs"),
    os.path.join("src", "payload.rs"),
    os.path.join("src", "wire.rs"),
    os.path.join("tests", "layout.rs"),
    "run.sh",
)

# Each language spells the same thirteen names its own way. Underscores are
# stripped before comparing, so the C and generated consumers can use
# VACB_TYPE_DOCUMENT_BEGIN and TYPE_DOCUMENT_BEGIN without either of them
# having to carry the shim's casing.
# The toolchain name is assembled rather than spelled. This directory is
# swept by test_acadsharp_targets.py for exactly that token, and that guard
# exempts one file, itself, so a second file that writes it out turns a
# green suite red from somewhere nobody is looking. The header does the same
# thing with the type names its own acceptance grep forbids.
_MS_TOOLCHAIN = "ms" + "vc"

HAND_DECLARED = (
    os.path.join("src", "lib.rs"),
    os.path.join("src", "main.rs"),
    os.path.join("src", "payload.rs"),
    os.path.join("src", "wire.rs"),
)


def read(path):
    with open(path) as f:
        return f.read()


def c_text():
    return "\n".join(read(os.path.join(C_DIR, n)) for n in C_SOURCES if n.endswith((".c", ".h")))


def rust_text():
    return "\n".join(
        read(os.path.join(RUST_DIR, n)) for n in RUST_SOURCES if n.endswith((".rs", ".toml"))
    )


class TestBothConsumersArePresent:
    @pytest.mark.parametrize("name", C_SOURCES)
    def test_the_c_consumer_is_complete(self, name):
        assert os.path.isfile(os.path.join(C_DIR, name)), f"tests/conformance/c/{name} is missing"

    @pytest.mark.parametrize("name", RUST_SOURCES)
    def test_the_generated_consumer_is_complete(self, name):
        assert os.path.isfile(os.path.join(RUST_DIR, name)), (
            f"tests/conformance/rust/{name} is missing"
        )

    @pytest.mark.parametrize("script", ("c", "rust"))
    def test_each_runner_is_executable_and_runs_in_a_container(self, script):
        path = os.path.join(CONFORMANCE, script, "run.sh")
        assert os.access(path, os.X_OK), f"{path} is not executable"
        body = read(path)
        assert "docker" in body, (
            "the runner builds in a container. Nothing in this repository is built "
            "against a host toolchain, and a script that would is a script that gets "
            "run once on the one machine it works on."
        )
        assert "set -euo pipefail" in body


class TestBothRunnersCanBeAimedAtAnUnpackedArchive:
    """The runners have to be able to run against what a consumer downloads.

    Both used to name `viprs_acadsharp.so`, which is the publish tree's
    file, while the archive ships `lib/libacadsharp_native.so`. So the one
    artefact a conformance run most wants to be pointed at, the released
    one, was the one it could not be pointed at, and a run against an
    unpacked archive failed with "no shim at" a path nobody had asked for.

    The lookup lives in one script both runners call, so there is one place
    to teach and one place to watch fail. These cases are that script run
    over fabricated layouts: no compiler, no container, no shim.
    """

    def find(self, directory):
        return subprocess.run(
            ["bash", FIND_SHIM, str(directory)],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_the_script_is_there_and_executable(self):
        assert os.path.isfile(FIND_SHIM)
        assert os.access(FIND_SHIM, os.X_OK), "both runners invoke it directly"

    @pytest.mark.parametrize("script", ("c", "rust"))
    def test_each_runner_asks_the_script_rather_than_naming_a_file(self, script):
        body = read(os.path.join(CONFORMANCE, script, "run.sh"))
        assert "find_shim.sh" in body, (
            f"the {script} runner resolves the library itself, so the two runners "
            "can disagree about which layouts they accept"
        )

    def test_it_finds_the_publish_tree(self, tmp_path):
        (tmp_path / PUBLISH_NAME).write_bytes(b"")
        result = self.find(tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(tmp_path / PUBLISH_NAME)

    def test_it_finds_an_unpacked_archive_root(self, tmp_path):
        lib = tmp_path / "lib"
        lib.mkdir()
        (lib / PACKAGED_NAME).write_bytes(b"")
        result = self.find(tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(lib / PACKAGED_NAME)

    def test_it_finds_an_archives_lib_directory_handed_over_directly(self, tmp_path):
        (tmp_path / PACKAGED_NAME).write_bytes(b"")
        result = self.find(tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(tmp_path / PACKAGED_NAME)

    def test_it_finds_the_mac_archives_dylib(self, tmp_path):
        lib = tmp_path / "lib"
        lib.mkdir()
        (lib / "libacadsharp_native.dylib").write_bytes(b"")
        result = self.find(tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(lib / "libacadsharp_native.dylib")

    def test_the_publish_tree_wins_over_an_archive_below_it(self, tmp_path):
        # Only one library can be linked, so which one is not a detail to
        # leave to readdir order.
        lib = tmp_path / "lib"
        lib.mkdir()
        (lib / PACKAGED_NAME).write_bytes(b"")
        (tmp_path / PUBLISH_NAME).write_bytes(b"")
        assert self.find(tmp_path).stdout.strip() == str(tmp_path / PUBLISH_NAME)

    def test_it_comes_back_absolute(self, tmp_path):
        # It becomes an rpath and an LD_LIBRARY_PATH entry inside a
        # container whose working directory is the runner's, not the
        # caller's, so a relative path resolves somewhere else there.
        (tmp_path / PUBLISH_NAME).write_bytes(b"")
        result = subprocess.run(
            ["bash", FIND_SHIM, "."],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert os.path.isabs(result.stdout.strip())
        assert os.path.samefile(result.stdout.strip(), tmp_path / PUBLISH_NAME)

    def test_an_empty_directory_is_refused_and_says_what_it_looked_for(self, tmp_path):
        result = self.find(tmp_path)
        assert result.returncode == 2
        assert PUBLISH_NAME in result.stderr and PACKAGED_NAME in result.stderr, (
            "a refusal that does not name the files it wanted leaves the reader "
            f"guessing which of two names it needed: {result.stderr!r}"
        )

    def test_a_directory_that_is_not_there_is_refused(self, tmp_path):
        result = self.find(tmp_path / "nothing-here")
        assert result.returncode == 2

    def test_a_directory_rather_than_a_file_is_not_a_shim(self, tmp_path):
        # `-f`, not `-e`. An unpacked archive whose lib/ is empty would
        # otherwise be reported as the library itself.
        (tmp_path / PUBLISH_NAME).mkdir()
        assert self.find(tmp_path).returncode == 2


class TestNeitherConsumerCopiesTheHeader:
    def test_the_c_consumer_includes_the_published_header(self):
        code = read(os.path.join(C_DIR, "conformance.c"))
        assert re.search(r'#include "[^"]*viprs_acadsharp\.h"', code), (
            "the C consumer has to include the file that ships. A consumer with its own "
            "declarations is a consumer that agrees with itself and nothing else."
        )

    def test_no_copy_of_the_header_is_checked_in_under_conformance(self):
        strays = []
        for root, _dirs, files in os.walk(CONFORMANCE):
            strays += [os.path.join(root, f) for f in files if f == "viprs_acadsharp.h"]
        assert not strays, f"a copy of the header lives at {strays}, so it can drift"

    def test_the_crate_generates_its_declarations_from_the_header(self):
        build = read(os.path.join(RUST_DIR, "build.rs"))
        assert "viprs_acadsharp.h" in build
        assert "OUT_DIR" in build, (
            "the generated bindings belong in OUT_DIR, not in the source tree, or they "
            "become the hand-maintained copy the epic rules out"
        )

    def test_the_crate_declares_no_struct_or_extern_block_by_hand(self):
        for name in HAND_DECLARED:
            code = read(os.path.join(RUST_DIR, name))
            assert not re.search(r"#\[repr\(C\)\]", code), (
                f"{name} declares a repr(C) struct by hand. Every struct on this boundary "
                "is generated from the header, which is the property that makes a "
                "reordered field a build failure instead of a wrong number."
            )
            assert not re.search(r'extern\s+"C"\s*\{', code), (
                f"{name} declares the entry points by hand"
            )

    def test_the_crate_pulls_nothing_from_a_package_index(self):
        toml = read(os.path.join(RUST_DIR, "Cargo.toml"))
        deps = re.search(r"^\[dependencies\](.*?)(^\[|\Z)", toml, re.S | re.M)
        body = (deps.group(1) if deps else "").strip()
        assert not body, (
            f"the crate depends on {body!r}. It builds in an offline container against "
            "one header, and a dependency turns a conformance run into a network test."
        )


class TestTheConstantsAgreeThreeWays:
    @staticmethod
    def normalise(name):
        return name.replace("_", "").upper()

    def parsed(self):
        n = self.normalise
        cs = {
            n(m.group(1)): int(m.group(2))
            for m in re.finditer(r"public const ushort Type(\w+) = (\d+);", read(WIRE_FORMAT_CS))
        }
        c = {
            n(m.group(1)): int(m.group(2))
            for m in re.finditer(
                r"#define VACB_TYPE_(\w+)\s+(\d+)", read(os.path.join(C_DIR, "vacb.h"))
            )
        }
        rust = {
            n(m.group(1)): int(m.group(2))
            for m in re.finditer(
                r"pub const TYPE_(\w+): u16 = (\d+);",
                read(os.path.join(RUST_DIR, "src", "wire.rs")),
            )
        }
        return cs, c, rust

    def test_the_shim_carries_every_frozen_record_type(self):
        cs, _c, _rust = self.parsed()
        for name, number in RECORD_TYPES.items():
            key = self.normalise(name)
            assert cs.get(key) == number, (
                f"the shim numbers {name} {cs.get(key)}, frozen at {number}"
            )

    def test_the_c_consumer_carries_the_same_numbers(self):
        _cs, c, _rust = self.parsed()
        for name, number in RECORD_TYPES.items():
            key = self.normalise(name)
            assert c.get(key) == number, (
                f"the C consumer numbers {name} {c.get(key)}, frozen at {number}"
            )

    def test_the_generated_consumer_carries_the_same_numbers(self):
        _cs, _c, rust = self.parsed()
        for name, number in RECORD_TYPES.items():
            key = self.normalise(name)
            assert rust.get(key) == number, (
                f"the crate numbers {name} {rust.get(key)}, frozen at {number}"
            )

    def test_none_of_the_three_knows_a_type_the_others_do_not(self):
        cs, c, rust = self.parsed()
        frozen = {self.normalise(n) for n in RECORD_TYPES}
        for label, table in (("shim", cs), ("C consumer", c), ("crate", rust)):
            extra = sorted(set(table) - frozen)
            assert not extra, (
                f"the {label} knows record types {extra} the other two do not. wire_version "
                "1 is frozen, so a fourteenth type is a version bump."
            )

    def test_all_three_agree_on_the_batch_framing(self):
        cs, c, rust = read(WIRE_FORMAT_CS), c_text(), rust_text()
        for label, text in (("shim", cs), ("C consumer", c), ("crate", rust)):
            assert "VACB" in text, f"the {label} does not carry the magic"
            assert re.search(r"\b12\b", text), (
                f"the {label} does not carry the 12-byte batch header length"
            )
            assert str(FORWARD_PROBE_FIRST) in text or hex(FORWARD_PROBE_FIRST) in text.lower(), (
                f"the {label} does not know where the forward-probe range starts, so it "
                "cannot say whether skipping an unknown type was exercised"
            )


class TestBothConsumersExerciseWhatTheEpicNamed:
    CASES = (
        ("unknown record type skipped", r"unknown"),
        ("payload_length past the end", r"payload_length|payload length"),
        ("record length below its own header", r"short record|length.*header|undersize"),
        ("fingerprint handshake", r"fingerprint"),
        ("null argument matrix", r"null"),
        ("cancel flag", r"cancel"),
        ("limits struct_size", r"struct_size"),
    )

    @pytest.mark.parametrize("label,pattern", CASES)
    def test_the_c_consumer_covers_it(self, label, pattern):
        assert re.search(pattern, c_text(), re.I), f"the C consumer never mentions {label}"

    @pytest.mark.parametrize("label,pattern", CASES)
    def test_the_generated_consumer_covers_it(self, label, pattern):
        assert re.search(pattern, rust_text(), re.I), f"the crate never mentions {label}"

    def test_the_c_consumer_bounds_its_parse_loop(self):
        code = read(os.path.join(C_DIR, "vacb.c"))
        assert re.search(r"budget|iteration|guard", code, re.I), (
            "a parser with no iteration bound turns a malformed length into a hang, and "
            "a hang in CI is a job somebody kills rather than a test that failed"
        )

    def test_the_generated_consumer_bounds_its_parse_loop(self):
        code = read(os.path.join(RUST_DIR, "src", "wire.rs"))
        assert re.search(r"budget|iteration|guard", code, re.I)


class TestNoConsumerNamesAMicrosoftTarget:
    @pytest.mark.parametrize("text", ("c", "rust"))
    def test_no_windows_toolchain_is_named(self, text):
        body = c_text() if text == "c" else rust_text()
        body += read(os.path.join(CONFORMANCE, text, "run.sh"))
        hits = re.findall(rf"\b(windows|win32|{_MS_TOOLCHAIN}|\.dll\b)", body, re.I)
        assert not hits, f"the {text} consumer names {sorted(set(h.lower() for h in hits))}"
