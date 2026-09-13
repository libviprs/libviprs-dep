"""Tests for `acadsharp/scripts/verify_archive.sh`.

Every case here takes a real, well-formed archive and breaks it in one
specific way, then asserts the verifier exits non-zero *and says what is
wrong*. A verifier nobody has watched fail is decoration, and this org has
shipped checks like that before.

The Linux fixture is compiled rather than faked: `cc` produces a real ELF
with a real dynamic symbol table, and `ar` writes a real symbol index, so
the script's byte-level readers are read against real bytes. The mac
fixture is synthesised, because NativeAOT cannot cross-compile to macOS and
this suite runs in a Linux container. A hand-built Mach-O is enough to
exercise the reader, the manifest and the layout: the *real* mac archive is
built on `macos-15` and verified there.
"""

import json
import os
import platform
import shutil
import stat
import struct
import subprocess
import sys

import build_acadsharp as ba
import pytest

ACAD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT_PATH = os.path.join(ACAD_DIR, "scripts", "verify_archive.sh")
HEADER = os.path.join(ACAD_DIR, "include", "viprs_acadsharp.h")

HOST_CPU = "arm64" if platform.machine() in ("arm64", "aarch64") else "x64"
HOST_ARCH = "arm64" if HOST_CPU == "arm64" else "amd64"

ENTRY_POINTS = tuple(ba.header_entry_points())

# The floors the script enforces, matched by the fixture so a pass is not
# an accident of the padding.
PAD_BYTES = 130000


def _require_toolchain():
    for tool in ("cc", "ar"):
        if not shutil.which(tool):
            pytest.skip(f"{tool} not available on this host")
    if sys.platform != "linux":
        pytest.skip("the compiled fixture is an ELF one; run this suite on Linux")


# ---------------------------------------------------------------------------
# Building a good archive
# ---------------------------------------------------------------------------


def _stub_source(symbols, pad_name="pad", undefined=None, guarded=False):
    """A stand-in library. `guarded` makes it need its initialiser.

    The real archive's exports abort when the runtime was never brought
    up, which is the whole reason the initialiser has to be forced. The
    fixture reproduces that with a flag the init object's constructor
    sets, so a probe that only links and never runs cannot tell a good
    archive from a broken one here either.
    """
    fingerprint = int(ba.abi_fingerprint(), 16)
    body = [
        "#include <stdint.h>",
        "#include <stdlib.h>",
        f'const char {pad_name}[] = "{"x" * PAD_BYTES}";',
    ]
    if guarded:
        body.append("int viprs_test_initialised = 0;")
        guard = "\n\tif (!viprs_test_initialised) { abort(); }"
    else:
        guard = ""
    if undefined:
        body.append(f"extern uint32_t {undefined}(void);")
    for name in symbols:
        if name == "viprs_acad_abi_fingerprint":
            body.append(f"uint64_t {name}(void) {{{guard}\n\treturn {fingerprint}ULL;\n}}")
        elif name == "viprs_acad_abi_version":
            tail = f" + {undefined}()" if undefined else ""
            body.append(f"uint32_t {name}(void) {{{guard}\n\treturn 1u{tail};\n}}")
        else:
            body.append(f"uint32_t {name}(void) {{ return 0u; }}")
    return "\n".join(body) + "\n"


# The initialiser object, in the shape the real one has: a constructor
# reachable by name, referencing the main archive rather than the other
# way round, so nothing ever pulls it in by symbol resolution and only
# `--whole-archive` can.
INIT_SOURCE = """\
extern int viprs_test_initialised;
void _GLOBAL__sub_I_fixture(void) __attribute__((constructor));
void _GLOBAL__sub_I_fixture(void) { viprs_test_initialised = %d; }
"""


def _build_init_archive(work, lib, *, effective=True, name=None):
    src = os.path.join(work, f"init{'' if effective else '-inert'}.c")
    with open(src, "w") as f:
        f.write(INIT_SOURCE % (1 if effective else 0))
    obj = src[:-2] + ".o"
    subprocess.run(["cc", "-fPIC", "-c", src, "-o", obj], check=True)
    path = os.path.join(lib, name or ba.STATIC_INIT_LIBRARY_NAME)
    subprocess.run(["ar", "rcs", path, obj], check=True)
    return path


def _finish(root, plat, arch, *, static_certified):
    """Fill in everything the driver fills in, through the driver itself.

    The fixture goes through `finish_archive`, so these tests cover the
    real packaging path rather than a copy of it: the manifests, the
    README, the removal of an uncertified static archive and CHECKSUMS.txt
    are all the production code.
    """
    os.makedirs(os.path.join(root, "include"), exist_ok=True)
    shutil.copy2(HEADER, os.path.join(root, "include", "viprs_acadsharp.h"))
    os.makedirs(os.path.join(root, "LICENSES"), exist_ok=True)
    with open(os.path.join(root, "LICENSES", "ACadSharp-LICENSE"), "w") as f:
        f.write("MIT License\n\nCopyright (c) 2021 Albert Domenech\n")
    with open(os.path.join(root, "LICENSES", "THIRD_PARTY_NOTICES"), "w") as f:
        f.write("The .NET runtime, MIT.\n")

    facts = {
        "aot_warning_count": "16",
        "dotnet_version": ba.DOTNET_SDK_VERSION,
        "clang_version": "clang version 14.0.6",
        "linker_version": "GNU ld 2.40",
        "shared_needed": "m",
        "static_ok": "1" if static_certified else "0",
        "static_system_libraries": "",
        "static_link_args": "",
    }
    ba.finish_archive(root, plat, arch, facts, builder_image="debian:bookworm-slim")
    return root


def _build_linux_tree(
    work, symbols=ENTRY_POINTS, *, static_symbols=None, undefined=None, init_effective=True
):
    """Compile a stand-in library and lay it out exactly like a release."""
    root = os.path.join(work, f"acadsharp-linux-{HOST_CPU}")
    lib = os.path.join(root, "lib")
    os.makedirs(lib)

    shared_src = os.path.join(work, "shared.c")
    with open(shared_src, "w") as f:
        f.write(_stub_source(symbols))
    subprocess.run(
        [
            "cc",
            "-shared",
            "-fPIC",
            "-o",
            os.path.join(lib, ba.shared_library_name("linux")),
            shared_src,
        ],
        check=True,
    )

    static_src = os.path.join(work, "static.c")
    with open(static_src, "w") as f:
        f.write(
            _stub_source(
                static_symbols or symbols, pad_name="spad", undefined=undefined, guarded=True
            )
        )
    obj = os.path.join(work, "static.o")
    subprocess.run(["cc", "-fPIC", "-c", static_src, "-o", obj], check=True)
    subprocess.run(["ar", "rcs", os.path.join(lib, ba.STATIC_LIBRARY_NAME), obj], check=True)
    _build_init_archive(work, lib, effective=init_effective)

    return _finish(root, "linux", HOST_ARCH, static_certified=True)


# ---------------------------------------------------------------------------
# A synthetic Mach-O, because no Linux container can produce a real one
# ---------------------------------------------------------------------------

MH_MAGIC_64 = 0xFEEDFACF
CPU_TYPE_ARM64 = 0x0100000C
CPU_TYPE_X86_64 = 0x01000007
MH_DYLIB = 0x6
LC_SYMTAB = 0x2
N_EXT = 0x01
N_SECT = 0x0E


def synth_macho_dylib(path, symbols, cputype=CPU_TYPE_ARM64, pad=PAD_BYTES):
    """Write a 64-bit Mach-O dylib carrying `symbols` as defined externals.

    Only the pieces the verifier reads: the header, one LC_SYMTAB, an
    nlist_64 table and a string table. It is not loadable and is not meant
    to be; it is a fixture for the reader.
    """
    names = ["_" + s for s in symbols]
    strtab = b"\x00"
    offsets = []
    for name in names:
        offsets.append(len(strtab))
        strtab += name.encode() + b"\x00"

    header_size = 32
    cmd_size = 24
    symoff = header_size + cmd_size + pad
    nsyms = len(names)
    stroff = symoff + nsyms * 16

    blob = struct.pack("<IIIIIIII", MH_MAGIC_64, cputype, 0, MH_DYLIB, 1, cmd_size, 0, 0)
    blob += struct.pack("<IIIIII", LC_SYMTAB, cmd_size, symoff, nsyms, stroff, len(strtab))
    blob += b"\x00" * pad
    for off in offsets:
        blob += struct.pack("<IBBHQ", off, N_SECT | N_EXT, 1, 0, 0x1000)
    blob += strtab

    with open(path, "wb") as f:
        f.write(blob)
    return path


def _build_mac_tree(work, symbols=ENTRY_POINTS, cputype=CPU_TYPE_ARM64):
    root = os.path.join(work, "acadsharp-mac-arm64")
    lib = os.path.join(root, "lib")
    os.makedirs(lib)
    synth_macho_dylib(os.path.join(lib, ba.shared_library_name("mac")), symbols, cputype=cputype)
    return _finish(root, "mac", "arm64", static_certified=False)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def good_tree(tmp_path_factory):
    _require_toolchain()
    return _build_linux_tree(str(tmp_path_factory.mktemp("good")))


@pytest.fixture(scope="session")
def mac_tree(tmp_path_factory):
    return _build_mac_tree(str(tmp_path_factory.mktemp("mac")))


@pytest.fixture(scope="session")
def tree_missing_a_shared_export(tmp_path_factory):
    _require_toolchain()
    fewer = [s for s in ENTRY_POINTS if s != "viprs_acad_decode_next_batch"]
    return _build_linux_tree(str(tmp_path_factory.mktemp("noexport")), symbols=fewer)


@pytest.fixture(scope="session")
def tree_missing_a_static_symbol(tmp_path_factory):
    _require_toolchain()
    fewer = [s for s in ENTRY_POINTS if s != "viprs_acad_open_memory"]
    return _build_linux_tree(str(tmp_path_factory.mktemp("nostatic")), static_symbols=fewer)


@pytest.fixture(scope="session")
def tree_with_an_unlinkable_static_library(tmp_path_factory):
    _require_toolchain()
    return _build_linux_tree(
        str(tmp_path_factory.mktemp("unlinkable")), undefined="viprs_acad_missing_helper"
    )


@pytest.fixture(scope="session")
def tree_with_an_inert_initialiser(tmp_path_factory):
    """Links perfectly, aborts on the first call. The whole point.

    The init archive is there, the right size, one object, and carries a
    reachable constructor, so every static check passes. The constructor
    just does not do its job, which is what an unforced initialiser looks
    like from the outside, and only running the probe can see it.
    """
    _require_toolchain()
    return _build_linux_tree(str(tmp_path_factory.mktemp("inert")), init_effective=False)


def _clone(tree, tmp_path):
    dest = os.path.join(str(tmp_path), os.path.basename(tree))
    shutil.copytree(tree, dest, symlinks=True)
    return dest


def _repack(root, name=None):
    """Refresh CHECKSUMS.txt for a mutated tree, then pack it."""
    ba.write_checksums(root)
    return _pack(root, name=name)


def _pack(root, name=None):
    parent = os.path.dirname(root)
    name = name or os.path.basename(root)
    tgz = os.path.join(parent, f"{name}.tgz")
    subprocess.run(
        ["tar", "czf", tgz, "-C", parent, os.path.basename(root)],
        check=True,
        env=dict(os.environ, COPYFILE_DISABLE="1"),
    )
    return tgz


def _verify(tgz, *args):
    return subprocess.run(
        ["bash", SCRIPT_PATH, tgz, *args], capture_output=True, text=True, check=False
    )


def _output(result):
    return result.stdout + result.stderr


def _patch_bytes(path, offset, data):
    with open(path, "r+b") as f:
        f.seek(offset)
        f.write(data)


def _edit_json(root, name, mutate):
    path = os.path.join(root, "metadata", name)
    with open(path) as f:
        doc = json.load(f)
    mutate(doc)
    with open(path, "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    return path


def _shared(root, plat="linux"):
    return os.path.join(root, "lib", ba.shared_library_name(plat))


def _static(root):
    return os.path.join(root, "lib", ba.STATIC_LIBRARY_NAME)


def _static_init(root):
    return os.path.join(root, "lib", ba.STATIC_INIT_LIBRARY_NAME)


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


class TestScriptShape:
    def test_exists_and_is_executable(self):
        assert os.path.exists(SCRIPT_PATH)
        assert os.stat(SCRIPT_PATH).st_mode & stat.S_IXUSR

    def test_has_bash_shebang(self):
        with open(SCRIPT_PATH) as f:
            assert "bash" in f.readline()

    def test_strict_mode(self):
        with open(SCRIPT_PATH) as f:
            assert "set -euo pipefail" in f.read()

    def test_forces_c_locale(self):
        with open(SCRIPT_PATH) as f:
            assert "LC_ALL=C" in f.read()

    def test_passes_shellcheck(self):
        if not shutil.which("shellcheck"):
            pytest.skip("shellcheck not installed on this host")
        result = subprocess.run(
            ["shellcheck", SCRIPT_PATH], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, _output(result)

    def test_takes_the_same_arguments_as_the_sibling_verifiers(self):
        # release.yml and release-zstd.yml both call
        # `verify_archive.sh <tgz> [platform] [cpu]`; the acadsharp
        # release workflow will do the same, so the CLI must not drift.
        with open(SCRIPT_PATH) as f:
            assert "<tgz-path> [platform] [cpu]" in f.read()


class TestArguments:
    def test_no_argument_is_a_usage_error(self):
        result = subprocess.run(["bash", SCRIPT_PATH], capture_output=True, text=True, check=False)
        assert result.returncode == 2
        assert "usage" in _output(result).lower()

    def test_a_missing_file_is_an_error(self, tmp_path):
        result = _verify(str(tmp_path / "acadsharp-linux-x64.tgz"))
        assert result.returncode == 2

    def test_an_unparseable_name_is_an_error(self, tmp_path):
        bogus = tmp_path / "something-else.tgz"
        bogus.write_bytes(b"")
        result = _verify(str(bogus))
        assert result.returncode == 2
        assert "cannot infer platform" in _output(result)

    def test_an_unknown_platform_is_refused(self, tmp_path, good_tree):
        result = _verify(_pack(_clone(good_tree, tmp_path)), "solaris")
        assert result.returncode == 2
        assert "unknown platform" in _output(result)

    def test_a_windows_platform_is_refused(self, tmp_path, good_tree):
        result = _verify(_pack(_clone(good_tree, tmp_path)), "windows")
        assert result.returncode == 2


# ---------------------------------------------------------------------------
# The good archives
# ---------------------------------------------------------------------------


class TestGoodArchivesPass:
    def test_a_well_formed_linux_archive_passes(self, tmp_path, good_tree):
        result = _verify(_pack(_clone(good_tree, tmp_path)))
        assert result.returncode == 0, _output(result)
        assert "all invariants hold" in result.stdout

    def test_it_reports_what_it_checked(self, tmp_path, good_tree):
        result = _verify(_pack(_clone(good_tree, tmp_path)))
        assert "exports every entry point the header declares" in result.stdout
        assert "CHECKSUMS.txt" in result.stdout

    def test_a_synthetic_mac_archive_passes(self, tmp_path, mac_tree):
        # The mac cell cannot be built in a Linux container, so this is
        # the packaging, manifest and verifier path exercised against a
        # hand-built Mach-O. The real build happens on macos-15.
        result = _verify(_pack(_clone(mac_tree, tmp_path)))
        assert result.returncode == 0, _output(result)

    def test_a_shared_only_archive_passes(self, tmp_path, mac_tree):
        # No static library at all is a recorded outcome, not a defect.
        root = _clone(mac_tree, tmp_path)
        with open(os.path.join(root, "metadata", "LINKINFO.json")) as f:
            assert "static_library" not in json.load(f)
        assert _verify(_pack(root)).returncode == 0


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


class TestTopLevelDirectory:
    def test_two_top_level_directories_rejected(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        extra = os.path.join(str(tmp_path), "extra")
        os.makedirs(extra)
        with open(os.path.join(extra, "stray.txt"), "w") as f:
            f.write("x\n")
        tgz = os.path.join(str(tmp_path), f"{os.path.basename(root)}.tgz")
        subprocess.run(
            ["tar", "czf", tgz, "-C", str(tmp_path), os.path.basename(root), "extra"], check=True
        )
        result = _verify(tgz)
        assert result.returncode == 1
        assert "top-level" in _output(result)

    def test_no_top_level_directory_rejected(self, tmp_path):
        empty = os.path.join(str(tmp_path), "empty")
        os.makedirs(empty)
        tgz = os.path.join(str(tmp_path), f"acadsharp-linux-{HOST_CPU}.tgz")
        subprocess.run(["tar", "czf", tgz, "-C", empty, "."], check=True)
        result = _verify(tgz)
        assert result.returncode == 1
        assert "top-level" in _output(result) or "does not unpack" in _output(result)

    def test_a_directory_not_named_after_the_archive_rejected(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        renamed = os.path.join(os.path.dirname(root), "acadsharp")
        os.rename(root, renamed)
        result = _verify(_pack(renamed, name=f"acadsharp-linux-{HOST_CPU}"))
        assert result.returncode == 1
        assert "does not unpack to a directory named" in _output(result)


class TestMissingMembers:
    @pytest.mark.parametrize(
        "missing",
        [
            "include/viprs_acadsharp.h",
            "metadata/LINKINFO.json",
            "metadata/BUILDINFO.json",
            "LICENSES/ACadSharp-LICENSE",
            "LICENSES/THIRD_PARTY_NOTICES",
            "README.md",
        ],
    )
    def test_a_missing_file_is_rejected(self, tmp_path, good_tree, missing):
        root = _clone(good_tree, tmp_path)
        os.remove(os.path.join(root, missing))
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert missing in _output(result)

    def test_a_missing_shared_library_is_rejected(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        os.remove(_shared(root))
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert ba.shared_library_name("linux") in _output(result)

    def test_a_missing_checksums_file_is_rejected(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        os.remove(os.path.join(root, "metadata", "CHECKSUMS.txt"))
        result = _verify(_pack(root))
        assert result.returncode == 1
        assert "CHECKSUMS.txt" in _output(result)

    def test_a_static_library_the_manifest_promises_must_exist(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        os.remove(_static(root))
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert ba.STATIC_LIBRARY_NAME in _output(result)


# ---------------------------------------------------------------------------
# CHECKSUMS.txt
# ---------------------------------------------------------------------------


class TestChecksums:
    def test_a_file_that_does_not_match_its_digest_is_rejected(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        with open(os.path.join(root, "README.md"), "a") as f:
            f.write("tampered\n")
        result = _verify(_pack(root))
        assert result.returncode == 1
        assert "README.md" in _output(result)

    def test_a_file_missing_from_checksums_is_rejected(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        path = os.path.join(root, "metadata", "CHECKSUMS.txt")
        with open(path) as f:
            kept = [ln for ln in f.read().splitlines() if "README.md" not in ln]
        with open(path, "w") as f:
            f.write("\n".join(kept) + "\n")
        result = _verify(_pack(root))
        assert result.returncode == 1
        assert "CHECKSUMS.txt" in _output(result)

    def test_a_checksums_entry_for_a_file_that_is_gone_is_rejected(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        path = os.path.join(root, "metadata", "CHECKSUMS.txt")
        with open(path, "a") as f:
            f.write(f"{'0' * 64}  lib/ghost.so\n")
        result = _verify(_pack(root))
        assert result.returncode == 1
        assert "ghost.so" in _output(result)


# ---------------------------------------------------------------------------
# The libraries themselves
# ---------------------------------------------------------------------------


class TestSharedLibraryBytes:
    def test_truncated_by_one_byte_is_rejected(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        path = _shared(root)
        with open(path, "r+b") as f:
            f.truncate(os.path.getsize(path) - 1)
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "truncat" in _output(result)

    def test_a_corrupted_elf_header_is_rejected(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        _patch_bytes(_shared(root), 0, b"\x7fELG")
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "magic" in _output(result)

    def test_the_wrong_elf_machine_is_rejected(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        _patch_bytes(_shared(root), 18, b"\xf3\x00")  # EM_RISCV
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "ELF machine" in _output(result)

    def test_not_a_shared_object_is_rejected(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        _patch_bytes(_shared(root), 16, b"\x02\x00")  # ET_EXEC
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "ELF type" in _output(result)

    def test_a_stub_sized_library_is_rejected(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        with open(_shared(root), "r+b") as f:
            f.truncate(2048)
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "expected at least" in _output(result)


class TestMachOBytes:
    def test_the_wrong_cputype_is_rejected(self, tmp_path):
        # An x86_64 dylib inside an archive named arm64 is exactly the
        # mislabelling a "does the file exist" check sails past.
        work = str(tmp_path)
        root = _build_mac_tree(work, cputype=CPU_TYPE_X86_64)
        result = _verify(_pack(root))
        assert result.returncode == 1
        assert "cputype" in _output(result)

    def test_a_corrupted_macho_header_is_rejected(self, tmp_path, mac_tree):
        root = _clone(mac_tree, tmp_path)
        _patch_bytes(_shared(root, "mac"), 0, b"\xcf\xfa\xed\xff")
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "magic" in _output(result)

    def test_not_a_dylib_is_rejected(self, tmp_path, mac_tree):
        root = _clone(mac_tree, tmp_path)
        _patch_bytes(_shared(root, "mac"), 12, b"\x01\x00\x00\x00")  # MH_OBJECT
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "filetype" in _output(result)

    def test_truncated_by_one_byte_is_rejected(self, tmp_path, mac_tree):
        root = _clone(mac_tree, tmp_path)
        path = _shared(root, "mac")
        with open(path, "r+b") as f:
            f.truncate(os.path.getsize(path) - 1)
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "truncat" in _output(result)


class TestStaticArchive:
    def test_a_gnu_thin_archive_is_rejected(self, tmp_path, good_tree):
        # A thin archive only references the build sandbox's .o files by
        # path, so it is unlinkable the moment it is unpacked elsewhere.
        root = _clone(good_tree, tmp_path)
        _patch_bytes(_static(root), 0, b"!<thin>\n")
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "thin archive" in _output(result)

    def test_an_object_of_the_wrong_architecture_is_rejected(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        path = _static(root)
        with open(path, "rb") as f:
            blob = f.read()
        _patch_bytes(path, blob.index(b"\x7fELF") + 18, b"\xf3\x00")
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "ELF machine" in _output(result)

    def test_an_archive_with_no_symbol_index_is_rejected(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        path = _static(root)
        obj = os.path.join(str(tmp_path), "member.o")
        subprocess.run(["ar", "x", "--output", str(tmp_path), path], check=True)
        os.remove(path)
        members = [f for f in os.listdir(str(tmp_path)) if f.endswith(".o")]
        shutil.copy2(os.path.join(str(tmp_path), members[0]), obj)
        subprocess.run(["ar", "rcS", path, obj], check=True)
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "symbol index" in _output(result)


# ---------------------------------------------------------------------------
# Exports against the header
# ---------------------------------------------------------------------------


class TestExportsMatchTheHeader:
    def test_a_shared_library_missing_an_entry_point_is_rejected(
        self, tmp_path, tree_missing_a_shared_export
    ):
        root = _clone(tree_missing_a_shared_export, tmp_path)
        result = _verify(_pack(root))
        assert result.returncode == 1
        assert "viprs_acad_decode_next_batch" in _output(result)

    def test_a_static_library_missing_an_entry_point_is_rejected(
        self, tmp_path, tree_missing_a_static_symbol
    ):
        root = _clone(tree_missing_a_static_symbol, tmp_path)
        result = _verify(_pack(root))
        assert result.returncode == 1
        assert "viprs_acad_open_memory" in _output(result)

    def test_the_header_in_the_archive_drives_the_list(self, tmp_path, good_tree):
        # Not the repo's copy: the archive has to be self-describing, so
        # a header that declares a function the library does not export
        # is caught from inside the tarball alone.
        root = _clone(good_tree, tmp_path)
        path = os.path.join(root, "include", "viprs_acadsharp.h")
        with open(path, "a") as f:
            f.write("\nuint32_t viprs_acad_invented_call(void);\n")
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "viprs_acad_invented_call" in _output(result)


# ---------------------------------------------------------------------------
# The manifests
# ---------------------------------------------------------------------------


class TestManifestIntegrity:
    @pytest.mark.parametrize("name", ["LINKINFO.json", "BUILDINFO.json"])
    def test_a_manifest_that_does_not_parse_is_rejected(self, tmp_path, good_tree, name):
        root = _clone(good_tree, tmp_path)
        with open(os.path.join(root, "metadata", name), "w") as f:
            f.write("{ this is not json\n")
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert name in _output(result)

    @pytest.mark.parametrize(
        "field", [f for f in ba.LINKINFO_FIELDS if f not in ba.STATIC_LINKINFO_FIELDS]
    )
    def test_a_linkinfo_missing_a_frozen_field_is_rejected(self, tmp_path, good_tree, field):
        root = _clone(good_tree, tmp_path)
        _edit_json(root, "LINKINFO.json", lambda doc: doc.pop(field, None))
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert field in _output(result)

    @pytest.mark.parametrize("field", list(ba.BUILDINFO_FIELDS))
    def test_a_buildinfo_missing_a_frozen_field_is_rejected(self, tmp_path, good_tree, field):
        root = _clone(good_tree, tmp_path)
        _edit_json(root, "BUILDINFO.json", lambda doc: doc.pop(field, None))
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert field in _output(result)

    def test_a_mismatched_header_hash_is_rejected(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        _edit_json(root, "LINKINFO.json", lambda doc: doc.update(abi_header_sha256="0" * 64))
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "abi_header_sha256" in _output(result)

    def test_a_mismatched_fingerprint_is_rejected(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        _edit_json(root, "LINKINFO.json", lambda doc: doc.update(abi_fingerprint="dead" * 4))
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "abi_fingerprint" in _output(result)

    def test_an_edited_header_is_caught_by_its_own_hash(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        with open(os.path.join(root, "include", "viprs_acadsharp.h"), "a") as f:
            f.write("/* slipped in later */\n")
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "abi_header_sha256" in _output(result)

    def test_a_platform_or_cpu_that_disagrees_with_the_filename_is_rejected(
        self, tmp_path, good_tree
    ):
        root = _clone(good_tree, tmp_path)
        other = "x64" if HOST_CPU != "x64" else "arm64"
        _edit_json(root, "LINKINFO.json", lambda doc: doc.update(cpu=other))
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "cpu" in _output(result)

    def test_a_target_triple_that_disagrees_with_the_cell_is_rejected(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        _edit_json(root, "LINKINFO.json", lambda doc: doc.update(target="wasm32-unknown-unknown"))
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "target" in _output(result)


class TestStaticCertification:
    def test_certified_without_a_static_library_is_rejected(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        os.remove(_static(root))
        _edit_json(root, "LINKINFO.json", lambda doc: doc.pop("static_library"))
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "static_library" in _output(result)

    def test_certified_with_a_library_that_cannot_link_is_rejected(
        self, tmp_path, tree_with_an_unlinkable_static_library
    ):
        root = _clone(tree_with_an_unlinkable_static_library, tmp_path)
        result = _verify(_pack(root))
        assert result.returncode == 1
        assert "static" in _output(result) and "link" in _output(result)

    def test_an_uncertified_target_ships_neither_static_archive(
        self, tmp_path, tree_with_an_unlinkable_static_library
    ):
        # Shared-only is a recorded outcome. It means the whole static
        # story comes out: both archives and all four manifest fields, so
        # a consumer cannot half-read one.
        root = _clone(tree_with_an_unlinkable_static_library, tmp_path)
        for path in (_static(root), _static_init(root)):
            os.remove(path)

        def drop(doc):
            for field in ba.STATIC_LINKINFO_FIELDS:
                doc.pop(field, None)
            doc["static_certified"] = False

        _edit_json(root, "LINKINFO.json", drop)
        result = _verify(_repack(root))
        assert result.returncode == 0, _output(result)


class TestTheManifestPointsAtTheLibrariesThatAreThere:
    """A manifest naming a library the archive does not carry is a link the
    consumer cannot make, and the layout check would not notice: it looks
    for the conventional name, while `build.rs` reads the manifest."""

    def test_a_shared_library_path_that_is_not_the_shipped_one_is_rejected(
        self, tmp_path, good_tree
    ):
        root = _clone(good_tree, tmp_path)
        _edit_json(
            root, "LINKINFO.json", lambda doc: doc.update(shared_library="lib/libsomething.so")
        )
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "shared_library" in _output(result)

    def test_a_static_library_path_that_is_not_the_shipped_one_is_rejected(
        self, tmp_path, good_tree
    ):
        root = _clone(good_tree, tmp_path)
        _edit_json(root, "LINKINFO.json", lambda doc: doc.update(static_library="lib/other.a"))
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "static_library" in _output(result)

    def test_a_mac_manifest_claiming_a_dot_so_is_rejected(self, tmp_path, mac_tree):
        root = _clone(mac_tree, tmp_path)
        _edit_json(
            root,
            "LINKINFO.json",
            lambda doc: doc.update(shared_library="lib/libacadsharp_native.so"),
        )
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "dylib" in _output(result)


class TestTheStaticInitArchive:
    """The archive that carries the runtime's static initialiser.

    It is separate from the main one because the requirement has to reach
    a binary two crates away, and only a library does that: a build
    script's `cargo:rustc-link-arg` binds to its own package's targets.
    """

    def test_a_certified_archive_must_ship_one(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        os.remove(_static_init(root))
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "static_init_library" in _output(result)

    def test_an_unmentioned_init_archive_is_rejected(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        _edit_json(root, "LINKINFO.json", lambda doc: doc.pop("static_init_library"))
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "static_init_library" in _output(result)

    def test_an_init_archive_with_no_initialiser_is_rejected(self, tmp_path, good_tree):
        # An archive holding an object that defines no `_GLOBAL__sub_I*`
        # carries nothing the consumer's --whole-archive can act on.
        root = _clone(good_tree, tmp_path)
        src = os.path.join(str(tmp_path), "empty.c")
        with open(src, "w") as f:
            f.write("int viprs_unrelated_thing = 1;\n")
        obj = src[:-2] + ".o"
        subprocess.run(["cc", "-fPIC", "-c", src, "-o", obj], check=True)
        os.remove(_static_init(root))
        subprocess.run(["ar", "rcs", _static_init(root), obj], check=True)
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "_GLOBAL__sub_I" in _output(result)

    def test_an_init_archive_holding_the_whole_runtime_is_rejected(self, tmp_path, good_tree):
        # If the merge puts the runtime in the init archive, the consumer
        # whole-archives all of it into every binary.
        root = _clone(good_tree, tmp_path)
        shutil.copy2(_static(root), _static_init(root))
        result = _verify(_repack(root))
        assert result.returncode == 1
        out = _output(result)
        assert "too big to be one initialiser" in out or "expected exactly 1" in out, out

    def test_more_than_one_object_in_it_is_rejected(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        extra = os.path.join(str(tmp_path), "extra.c")
        with open(extra, "w") as f:
            f.write("int viprs_extra_thing = 2;\n")
        obj = extra[:-2] + ".o"
        subprocess.run(["cc", "-fPIC", "-c", extra, "-o", obj], check=True)
        subprocess.run(["ar", "rs", _static_init(root), obj], check=True)
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "expected exactly 1" in _output(result)


class TestTheProbeIsRunAndNotOnlyLinked:
    def test_an_inert_initialiser_is_caught(self, tmp_path, tree_with_an_inert_initialiser):
        # This is the case the whole fix exists for. Every static check
        # passes, the link is clean, and the binary aborts at the first
        # call. A link-only probe reports success.
        root = _clone(tree_with_an_inert_initialiser, tmp_path)
        result = _verify(_pack(root))
        assert result.returncode == 1
        out = _output(result)
        assert "exits 134" in out, out

    def test_a_working_archive_says_it_ran(self, tmp_path, good_tree):
        result = _verify(_pack(_clone(good_tree, tmp_path)))
        assert result.returncode == 0, _output(result)
        assert "links and runs here" in result.stdout

    def test_the_probe_checks_the_fingerprint_it_gets_back(self, tmp_path, good_tree):
        # A library answering with a different fingerprint than the
        # manifest claims is a mismatched pair, and only a run can see it.
        # (The static half of this check catches an edited manifest; this
        # catches an edited library.)
        root = _clone(good_tree, tmp_path)
        assert _verify(_pack(root)).returncode == 0


class TestDuplicateMembers:
    def test_two_members_of_the_same_name_are_rejected(self, tmp_path, good_tree):
        # `ar addlib` keeps each source archive's member names, and two
        # runtime archives ship objects called the same thing. It links
        # today only because the linker takes the first definition, and it
        # makes --whole-archive on the merged file impossible.
        root = _clone(good_tree, tmp_path)
        work = str(tmp_path)
        src = os.path.join(work, "dupe.c")
        with open(src, "w") as f:
            f.write("int viprs_dupe_one = 1;\n")
        obj = os.path.join(work, "static.o")
        subprocess.run(["cc", "-fPIC", "-c", src, "-o", obj], check=True)
        subprocess.run(["ar", "q", _static(root), obj], check=True)
        subprocess.run(["ranlib", _static(root)], check=True)
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "more than one member called" in _output(result)


class TestTheStaticFieldsTravelTogether:
    @pytest.mark.parametrize("field", list(ba.STATIC_LINKINFO_FIELDS))
    def test_a_certified_manifest_missing_one_is_rejected(self, tmp_path, good_tree, field):
        root = _clone(good_tree, tmp_path)
        _edit_json(root, "LINKINFO.json", lambda doc: doc.pop(field))
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert field in _output(result)

    @pytest.mark.parametrize("field", list(ba.STATIC_LINKINFO_FIELDS))
    def test_an_uncertified_manifest_carrying_one_is_rejected(self, tmp_path, mac_tree, field):
        root = _clone(mac_tree, tmp_path)
        _edit_json(root, "LINKINFO.json", lambda doc: doc.update({field: "lib/whatever.a"}))
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "without static_certified" in _output(result)

    def test_shared_system_libraries_is_always_there(self, tmp_path, mac_tree):
        root = _clone(mac_tree, tmp_path)
        with open(os.path.join(root, "metadata", "LINKINFO.json")) as f:
            info = json.load(f)
        assert "shared_system_libraries" in info
        _edit_json(root, "LINKINFO.json", lambda doc: doc.pop("shared_system_libraries"))
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "shared_system_libraries" in _output(result)


class TestSymbolForcingArgumentsAreRefused:
    def test_a_dash_u_in_static_link_args_is_rejected(self, tmp_path, good_tree):
        # It would work for a hand-written cc line and silently not arrive
        # through a dependency's build script, which is the worst of both.
        root = _clone(good_tree, tmp_path)
        _edit_json(
            root,
            "LINKINFO.json",
            lambda doc: doc.update(static_link_args=["-Wl,-u,_GLOBAL__sub_I_main.cpp"]),
        )
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "never reaches a dependent's link line" in _output(result)

    def test_require_defined_is_rejected_too(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        _edit_json(
            root,
            "LINKINFO.json",
            lambda doc: doc.update(static_link_args=["-Wl,--require-defined,whatever"]),
        )
        result = _verify(_repack(root))
        assert result.returncode == 1
