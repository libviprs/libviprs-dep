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

import hashlib
import json
import os
import platform
import re
import shutil
import stat
import struct
import subprocess
import sys

import build_acadsharp as ba
import pytest

ACAD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT_PATH = os.path.join(ACAD_DIR, "scripts", "verify_archive.sh")
RETAIN_SECTIONS = os.path.join(ACAD_DIR, "scripts", "retain_sections.py")
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


# What the stub answers when it is asked what it reads. The real library
# answers out of AbiConstants; this pair is the fixture's own, so a test
# that edits the manifest is putting the manifest at odds with a library
# that has not moved. _finish records the same numbers as facts, which is
# the path a real build takes from the smoke's output to LINKINFO.json.
STUB_DWG_MIN = 1014
STUB_DWG_MAX = 1032


def _stub_source(symbols, pad_name="pad", undefined=None, guarded=False):
    """A stand-in library. `guarded` makes it need its initialiser.

    The real archive's exports abort when the runtime was never brought
    up, which is the whole reason the initialiser has to be forced. The
    fixture reproduces that with a flag the init object's constructor
    sets, so a probe that only links and never runs cannot tell a good
    archive from a broken one here either.

    `viprs_acad_capabilities_v1` is not here: the verifier now asks the
    library what AC10xx range it reads and holds the manifest to the
    answer, and a `return 0u` stub would make that check pass over
    anything. It is written out properly in `CAPABILITIES_SOURCE`, which
    is a translation unit of its own because it includes the real header
    and every other stub here has a signature the header contradicts.
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
        elif name == "viprs_acad_capabilities_v1":
            continue  # CAPABILITIES_SOURCE, compiled separately
        else:
            body.append(f"uint32_t {name}(void) {{ return 0u; }}")
    return "\n".join(body) + "\n"


# Enough of the real call for the verifier's probe, in a translation unit
# of its own so it can include the header the archive ships. Every other
# stub above is `uint32_t name(void)`, which the header's real prototypes
# contradict, so one file cannot hold both.
#
# It fills the struct the header declares and writes the pinned version
# string out, so a sizing call and a fetching call each behave the way
# ABI.md says they do.
CAPABILITIES_SOURCE = """\
#include <stdint.h>
#include <string.h>

#include "viprs_acadsharp.h"

uint32_t viprs_acad_capabilities_v1(struct viprs_acad_capabilities_v1 *caps, uint8_t *out,
\tuint64_t cap, uint64_t *required)
{
\tstatic const char VERSION[] = "3.7.1";
\tif (!caps || !required) {
\t\treturn 1u;
\t}
\tif (caps->struct_size != (uint32_t)sizeof *caps) {
\t\treturn 1u;
\t}
\tcaps->abi_version = 1u;
\tcaps->wire_version = 2u;
\tcaps->dwg_version_min = %du;
\tcaps->dwg_version_max = %du;
\t*required = (uint64_t)(sizeof VERSION - 1);
\tif (out && cap >= *required) {
\t\tmemcpy(out, VERSION, (size_t)*required);
\t}
\treturn 0u;
}
"""


# The initialiser object, in the shape the real one has: a constructor
# reachable by name, referencing the main archive rather than the other
# way round, so nothing ever pulls it in by symbol resolution and only
# `--whole-archive` can.
#
# `viprs_test_register` is the load-bearing half. The real
# libbootstrapperdll.o calls `RhRegisterOSModule`, which lives in a
# runtime object nothing else in the archive references, so that object
# is only pulled once the bootstrapper is on the link line. An
# initialiser that only touched symbols the entry points already drag in
# would link under any ordering and the fixture would prove nothing.
# The initialiser walks the module table the way the real bootstrapper
# does, through the symbols a linker synthesises around a section whose
# name is a C identifier. That reference is the whole of issue #67: it is
# not a relocation against the section, so lld's default
# `-z start-stop-gc` does not count it as a reason to keep `__modules`
# alive, `--gc-sections` collects the section, and `__start___modules`
# has nothing left to point at. GNU ld keeps it, which is why the recipe
# passed on every arm64 job and failed on the one x64 job.
INIT_SOURCE = """\
extern int viprs_test_initialised;
extern void viprs_test_register(void);
extern const void *const __start___modules[];
extern const void *const __stop___modules[];
void _GLOBAL__sub_I_fixture(void) __attribute__((constructor));
void _GLOBAL__sub_I_fixture(void)
{
	/* volatile so the inert variant keeps the reference: written as a
	plain multiply by the literal, gcc folds the zero away and the inert object ends
	up with no undefined __start___modules at all, which quietly makes it a
	fixture for a different bug than the one it names. */
	volatile int modules = (int)(__stop___modules - __start___modules);
	viprs_test_register();
	viprs_test_initialised = %d * modules;
}
"""

# Nothing but the initialiser wants this, which is the point of it being
# its own object in the main archive.
#
# It also carries the module table, in a section named exactly as ILC
# names it, holding a pointer so the section cannot be empty, and with
# nothing relocating against it. `used` stops the compiler discarding it
# and says nothing to the linker, which is the shape that broke.
REGISTER_SOURCE = """\
void viprs_test_register(void);
void viprs_test_register(void) { }

static const int viprs_test_module_header = 1;
/* Length 1, and the initialiser above multiplies by it, so the marker
 * value every static test asserts on depends on this array surviving.
 * Add an element and those assertions move. */
__attribute__((used, section("__modules")))
static const void *const viprs_test_modules[1] = { &viprs_test_module_header };
"""


# The runtime's bundled llvm-libunwind, in miniature.
#
# NativeAOT statically links its own copy into the runtime archives, and
# rustc links a `self-contained/libunwind.a` of its own for every musl
# target and for no glibc one, so the two define the same `__unw_*` names
# and a musl cargo link against an archive carrying the originals dies on
# all of them. stage.sh renames ours, and verify_archive.sh reads the
# shipped symbol index to check that it did: the original names are a
# refusal, and an archive with neither name is a refusal too, because
# that is what the rename silently not running looks like from here.
#
# So the good fixture carries the renamed name. It is one symbol rather
# than the real fifty-nine because the check is about which names are
# there, not how many.
UNWIND_SOURCE = """\
void %(sym)s(void);
void %(sym)s(void) { }
"""

UNWIND_SYMBOLS = {"private": "__viprs_unw_step", "bundled": "__unw_step", "none": None}


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
        # What the shared smoke printed when it asked the library what it
        # reads. The stub library below answers with the same pair, so a
        # test that breaks one side is breaking it against the other.
        "dwg_version_min": str(STUB_DWG_MIN),
        "dwg_version_max": str(STUB_DWG_MAX),
    }
    ba.finish_archive(root, plat, arch, facts, builder_image="debian:bookworm-slim")
    return root


def _build_linux_tree(
    work,
    symbols=ENTRY_POINTS,
    *,
    static_symbols=None,
    undefined=None,
    init_effective=True,
    unwind="private",
    plat="linux",
):
    """Compile a stand-in library and lay it out exactly like a release.

    `plat` is a label, not a toolchain: the compiler here is whatever the
    container has, and a musl fixture is the same ELF under a musl
    manifest and a musl filename. That is enough for every check that
    reads the archive, and it is the only way to put a musl archive in
    front of a glibc host, which is the situation both musl cells were
    verified in for every release.
    """
    root = os.path.join(work, f"acadsharp-{plat}-{HOST_CPU}")
    lib = os.path.join(root, "lib")
    os.makedirs(lib)

    caps_src = os.path.join(work, "caps.c")
    with open(caps_src, "w") as f:
        f.write(CAPABILITIES_SOURCE % (STUB_DWG_MIN, STUB_DWG_MAX))
    include = os.path.join(ACAD_DIR, "include")

    def with_caps(names, *sources):
        """The capabilities unit joins the link only when it is wanted.

        A fixture that drops an entry point has to drop it from both
        libraries, so the test that removes one sees it removed.
        """
        if "viprs_acad_capabilities_v1" in names:
            return list(sources) + [caps_src]
        return list(sources)

    shared_src = os.path.join(work, "shared.c")
    with open(shared_src, "w") as f:
        f.write(_stub_source(symbols))
    subprocess.run(
        [
            "cc",
            "-shared",
            "-fPIC",
            "-I",
            include,
            "-o",
            os.path.join(lib, ba.shared_library_name(plat)),
        ]
        + with_caps(symbols, shared_src),
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

    static_names = static_symbols or symbols
    caps_objs = []
    if "viprs_acad_capabilities_v1" in static_names:
        caps_obj = os.path.join(work, "caps.o")
        subprocess.run(["cc", "-fPIC", "-I", include, "-c", caps_src, "-o", caps_obj], check=True)
        caps_objs = [caps_obj]

    register_src = os.path.join(work, "register.c")
    with open(register_src, "w") as f:
        f.write(REGISTER_SOURCE)
    register_obj = os.path.join(work, "register.o")
    subprocess.run(["cc", "-fPIC", "-c", register_src, "-o", register_obj], check=True)

    # The one step stage.sh does to the object ILC produces. Without it
    # this archive links under GNU ld and fails under lld, so taking this
    # line out is the mutation that reproduces #67: the consumer link in
    # test_build_link_consumer.py goes red on an x64 host and stays green
    # on an arm64 one, which is exactly how the release behaved.
    subprocess.run([sys.executable, RETAIN_SECTIONS, register_obj, "__modules"], check=True)

    members = [obj, register_obj]
    unwind_symbol = UNWIND_SYMBOLS[unwind]
    if unwind_symbol:
        unwind_src = os.path.join(work, "unwind.c")
        with open(unwind_src, "w") as f:
            f.write(UNWIND_SOURCE % {"sym": unwind_symbol})
        unwind_obj = os.path.join(work, "unwind.o")
        subprocess.run(["cc", "-fPIC", "-c", unwind_src, "-o", unwind_obj], check=True)
        members.append(unwind_obj)

    subprocess.run(
        ["ar", "rcs", os.path.join(lib, ba.STATIC_LIBRARY_NAME), *members] + caps_objs,
        check=True,
    )
    _build_init_archive(work, lib, effective=init_effective)

    return _finish(root, plat, HOST_ARCH, static_certified=True)


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

    def test_the_libc_probe_survives_a_non_zero_exit(self):
        # musl's ldd exits 1 for --version. Piping it straight into a
        # condition under `pipefail` made "is this host musl" false on
        # every Alpine host, so a musl archive was never link-tested even
        # on the one machine that could do it, and the script said so in
        # a line that reads like a deliberate skip.
        with open(SCRIPT_PATH) as f:
            code = "\n".join(
                line for line in f.read().splitlines() if not line.lstrip().startswith("#")
            )
        assert "ldd --version 2>&1 | sed -n 1p | grep" not in code
        assert "|| true)" in code

    def test_takes_the_same_arguments_as_the_sibling_verifiers(self):
        # release.yml and release-zstd.yml both call
        # `verify_archive.sh <tgz> [platform] [cpu]`; the acadsharp
        # release workflow will do the same, so the CLI must not drift.
        with open(SCRIPT_PATH) as f:
            assert "<tgz-path> [platform] [cpu]" in f.read()


class TestTheProbeAsksForTheNameTheHeaderDeclares:
    """The probe links the library, so the capabilities call is resolved
    when it compiles. A literal there stops compiling the day that call is
    renamed, and the verifier then refuses every archive with "the static
    smoke cannot link the archive", which says nothing about what is
    wrong. The name comes off the shipped header's own declarations and
    reaches the compiler as a macro."""

    def _script(self):
        with open(SCRIPT_PATH) as f:
            return f.read()

    def test_the_probe_does_not_name_the_call(self):
        script = self._script()
        assert "VIPRS_CAPS_CALL(&caps" in script
        assert "-DVIPRS_CAPS_CALL=$CAPS_CALL" in script
        assert "viprs_acad_capabilities_v1(&caps" not in script, (
            "the probe calls the capabilities export by a name typed here, so it "
            "stops compiling the day the header renames it"
        )

    def test_the_struct_is_still_named_directly(self):
        # Only the call is renamed. The struct keeps its name, and the
        # probe has to declare one to pass in.
        assert "struct viprs_acad_capabilities_v1 caps;" in self._script()

    def test_a_header_with_no_capabilities_call_does_not_abort_the_run(self, tmp_path):
        # The script runs under `set -euo pipefail`, so an unguarded
        # `grep | head` over a header that declares no such call exits 1
        # and takes the whole verification with it, before the refusal
        # that explains why. The line is pulled out of the script and run
        # under the same options rather than restated.
        line = [ln for ln in self._script().splitlines() if ln.strip().startswith("CAPS_CALL=$(")]
        assert len(line) == 1, "the extraction moved, so this is testing nothing"
        entry_points = tmp_path / "entry-points.txt"
        entry_points.write_text("viprs_acad_abi_version\nviprs_acad_close\n")
        done = subprocess.run(
            [
                "bash",
                "-c",
                "set -euo pipefail\n"
                f'ENTRY_POINTS="{entry_points}"\n'
                f"{line[0].strip()}\n"
                'printf "survived:%s\\n" "${CAPS_CALL:-<empty>}"',
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, (
            f"the lookup aborts a strict-mode script when the header declares no "
            f"capabilities call:\n{done.stdout}{done.stderr}"
        )
        assert "survived:<empty>" in done.stdout


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
            # The three frozen contracts. An archive without them is one a
            # consumer cannot be built from: the batch stream and the
            # manifest schema are defined in these files and nowhere else,
            # so the next person writing a consumer reads the build driver
            # instead, which is the coupling freezing the ABI removed.
            "docs/ABI.md",
            "docs/WIRE.md",
            "docs/LINKINFO.md",
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

    @pytest.mark.parametrize(
        "field,macro",
        [
            ("abi_version", "VIPRS_ACAD_ABI_VERSION"),
            ("wire_version", "VIPRS_ACAD_WIRE_VERSION"),
        ],
    )
    def test_a_version_field_that_disagrees_with_the_header_is_rejected(
        self, tmp_path, good_tree, field, macro
    ):
        # Both were listed as required fields and neither was ever compared
        # against the header shipped beside it, so a manifest claiming
        # abi_version 7 next to a version-1 header passed every check. A
        # consumer refuses a library whose abi_version it does not know, so
        # the number it refuses on has to be the header's.
        root = _clone(good_tree, tmp_path)
        _edit_json(root, "LINKINFO.json", lambda doc: doc.update({field: 7}))
        result = _verify(_repack(root))
        assert result.returncode == 1
        out = _output(result)
        assert field in out and macro in out, out

    @pytest.mark.parametrize("field", ["abi_version", "wire_version"])
    def test_the_good_archive_states_the_headers_own_numbers(self, good_tree, field):
        # The control for the pair above. A check that only ever fires on a
        # value somebody broke could be firing on every value.
        with open(os.path.join(good_tree, "metadata", "LINKINFO.json")) as f:
            link = json.load(f)
        versions = dict(zip(("abi_version", "wire_version"), ba.header_versions()))
        assert link[field] == versions[field]

    def test_a_read_range_that_is_not_an_ac10xx_code_is_rejected(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        _edit_json(root, "LINKINFO.json", lambda doc: doc.update(dwg_version_min="1014"))
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "dwg_version_min" in _output(result)

    def test_an_inverted_read_range_is_rejected(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        _edit_json(
            root,
            "LINKINFO.json",
            lambda doc: doc.update(dwg_version_min=1032, dwg_version_max=1014),
        )
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "dwg_version_min" in _output(result)

    def test_a_read_range_the_library_does_not_report_is_rejected(self, tmp_path, good_tree):
        # The manifest says this build reads up to AC1035 and the library
        # in the same archive answers AC1032. Nothing in the bytes can
        # catch that: the field is well-formed, the header does not state
        # the range, and the number a consumer would act on is the wrong
        # one. So the verifier asks the library, in the block that already
        # links it to check the fingerprint.
        root = _clone(good_tree, tmp_path)
        _edit_json(root, "LINKINFO.json", lambda doc: doc.update(dwg_version_max=1035))
        result = _verify(_repack(root))
        out = _output(result)
        assert result.returncode == 1, out
        assert "dwg_version_max" in out, out
        assert "1035" in out and str(STUB_DWG_MAX) in out, (
            f"the refusal has to name both what was claimed and what was answered:\n{out}"
        )

    def test_the_good_archive_states_what_the_library_answers(self, tmp_path, good_tree):
        # The control for the case above. A check that only ever fires on
        # a number somebody broke could be firing on every number, and
        # this is also what proves the probe reached the library at all.
        with open(os.path.join(good_tree, "metadata", "LINKINFO.json")) as f:
            link = json.load(f)
        assert (link["dwg_version_min"], link["dwg_version_max"]) == (STUB_DWG_MIN, STUB_DWG_MAX)
        result = _verify(_pack(good_tree))
        out = _output(result)
        assert result.returncode == 0, out
        assert f"AC{STUB_DWG_MIN} to AC{STUB_DWG_MAX}" in out, (
            f"the verifier never reports the range it checked, so a run in which the "
            f"probe never happened reads exactly like one where it passed:\n{out}"
        )

    def test_a_header_without_the_version_defines_is_rejected(self, tmp_path, good_tree):
        # The other half: the check has to fail loudly when it cannot read
        # the header rather than skip itself into a pass.
        root = _clone(good_tree, tmp_path)
        path = os.path.join(root, "include", "viprs_acadsharp.h")
        with open(path) as f:
            header = f.read()
        stripped = re.sub(r"^#define\s+VIPRS_ACAD_WIRE_VERSION\s+.*$", "", header, flags=re.M)
        assert stripped != header
        with open(path, "w") as f:
            f.write(stripped)
        # The hash moves too, so this archive is wrong twice; both have to
        # be reported and the version one is what this is here for.
        _edit_json(
            root,
            "LINKINFO.json",
            lambda doc: doc.update(
                abi_header_sha256=hashlib.sha256(stripped.encode()).hexdigest(),
                abi_fingerprint=hashlib.sha256(stripped.encode()).hexdigest()[:16],
            ),
        )
        result = _verify(_repack(root))
        assert result.returncode == 1
        assert "VIPRS_ACAD_WIRE_VERSION" in _output(result)

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


class TestTheModuleSectionIsRetained:
    """The check that reads the artifact instead of the recipe.

    Issue #67 shipped four archives and failed the fifth, and the reason
    the fifth was the only one to say anything is that it was the only
    target whose linker was lld. The two musl archives carry the same
    defect and published green, because the consumer link only runs when
    the host can build for the target and no host could build for either
    of them. Reading a section header needs no linker and no matching
    architecture, so this check runs everywhere the others cannot.
    """

    def _clear_the_flag(self, root):
        """Undo exactly what the build does, on the packed archive."""
        lib = os.path.join(root, "lib", ba.STATIC_LIBRARY_NAME)
        with open(lib, "rb") as handle:
            data = bytearray(handle.read())
        needle = struct.pack("<Q", 0x200000 | 0x2 | 0x1)
        replacement = struct.pack("<Q", 0x2 | 0x1)
        assert data.count(needle) == 1, (
            f"expected one retained section in {lib}, found {data.count(needle)}"
        )
        data[data.index(needle) : data.index(needle) + 8] = replacement
        with open(lib, "wb") as handle:
            handle.write(data)

    def test_a_good_archive_says_the_section_is_retained(self, tmp_path, good_tree):
        result = _verify(_pack(_clone(good_tree, tmp_path)))

        assert result.returncode == 0, _output(result)
        assert "encapsulation sections are retained" in result.stdout

    def test_an_archive_without_the_flag_is_refused(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        self._clear_the_flag(root)

        result = _verify(_repack(root))

        assert result.returncode == 1
        out = _output(result)
        assert "NOT RETAINED" in out, out
        assert "start-stop-gc" in out, out
