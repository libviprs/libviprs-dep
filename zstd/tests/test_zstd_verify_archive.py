"""Tests for zstd/scripts/verify_archive.sh.

Most of these build a real archive with the host's own compiler and then
break it in one specific way, so every invariant in the script is proved
to actually bite. A check nobody has watched fail is decoration.

The fixture archive is compiled, not faked: `ar` writes a real symbol
index (GNU format on linux, BSD `__.SYMDEF` on macOS, which is why the
member walker has to understand both), and the shared library is a real
ELF/Mach-O with real headers to read the machine type out of.
"""

import os
import platform
import shutil
import stat
import subprocess
import sys

import pytest

SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "verify_archive.sh")

HOST_PLATFORM = "mac" if sys.platform == "darwin" else "linux"
HOST_CPU = "arm64" if platform.machine() in ("arm64", "aarch64") else "x64"
SHARED_EXT = "dylib" if HOST_PLATFORM == "mac" else "so"
VERSION = "1.5.7"

API_SYMBOLS = [
    "ZSTD_compress",
    "ZSTD_decompress",
    "ZSTD_versionNumber",
    "ZSTD_createCCtx",
    "ZDICT_trainFromBuffer",
]

# Enough objects, and enough bytes in each, to clear the script's
# "this is not a stub" floors (10 objects, 100 KB per library).
PAD_OBJECTS = 12
PAD_BYTES = 12000


def _require_toolchain():
    for tool in ("cc", "ar"):
        if not shutil.which(tool):
            pytest.skip(f"{tool} not available on this host")


def _write_sources(work, symbols):
    sources = []
    for i in range(PAD_OBJECTS):
        src = os.path.join(work, f"pad{i}.c")
        literal = "".join(chr(ord("a") + ((i + j) % 26)) for j in range(PAD_BYTES))
        body = f'const char pad{i}[] = "{literal}";\n'
        if i == 0:
            for n, sym in enumerate(symbols):
                body += f"int {sym}(void) {{ return {n}; }}\n"
        with open(src, "w") as f:
            f.write(body)
        sources.append(src)
    return sources


def _build_tree(work, symbols=API_SYMBOLS):
    """Compile a stand-in libzstd and lay it out exactly like a release."""
    root = os.path.join(work, f"zstd-{HOST_PLATFORM}-{HOST_CPU}")
    lib = os.path.join(root, "lib")
    os.makedirs(os.path.join(lib, "pkgconfig"))
    os.makedirs(os.path.join(root, "include"))

    objs = []
    for src in _write_sources(work, symbols):
        obj = src[:-2] + ".o"
        subprocess.run(["cc", "-fPIC", "-c", src, "-o", obj], check=True)
        objs.append(obj)

    subprocess.run(["ar", "rcs", os.path.join(lib, "libzstd.a"), *objs], check=True)

    real_shared = f"libzstd.{VERSION}.dylib" if HOST_PLATFORM == "mac" else f"libzstd.so.{VERSION}"
    subprocess.run(["cc", "-shared", "-o", os.path.join(lib, real_shared), *objs], check=True)
    os.symlink(real_shared, os.path.join(lib, f"libzstd.{SHARED_EXT}"))

    with open(os.path.join(lib, "pkgconfig", "libzstd.pc"), "w") as f:
        f.write(
            "prefix=${pcfiledir}/../..\n"
            "exec_prefix=${prefix}\n"
            "includedir=${prefix}/include\n"
            "libdir=${exec_prefix}/lib\n"
            "\nName: zstd\nVersion: 1.5.7\n"
            "Libs: -L${libdir} -lzstd\nCflags: -I${includedir}\n"
        )
    for header in ("zstd.h", "zstd_errors.h", "zdict.h"):
        with open(os.path.join(root, "include", header), "w") as f:
            f.write("/* stub */\n")
    with open(os.path.join(root, "LICENSE"), "w") as f:
        f.write("BSD\n")
    with open(os.path.join(root, "cmake-args.txt"), "w") as f:
        f.write("-DZSTD_BUILD_STATIC=ON\n")
    return root


@pytest.fixture(scope="session")
def good_tree(tmp_path_factory):
    _require_toolchain()
    return _build_tree(str(tmp_path_factory.mktemp("good")))


@pytest.fixture(scope="session")
def tree_without_zstd_compress(tmp_path_factory):
    _require_toolchain()
    symbols = [s for s in API_SYMBOLS if s != "ZSTD_compress"]
    return _build_tree(str(tmp_path_factory.mktemp("nosym")), symbols=symbols)


def _clone(tree, tmp_path):
    dest = os.path.join(str(tmp_path), os.path.basename(tree))
    shutil.copytree(tree, dest, symlinks=True)
    return dest


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


def _patch_bytes(path, offset, data):
    with open(path, "r+b") as f:
        f.seek(offset)
        f.write(data)


def _real_shared(root):
    lib = os.path.join(root, "lib")
    link = os.path.join(lib, f"libzstd.{SHARED_EXT}")
    return os.path.join(lib, os.readlink(link))


class TestScriptShape:
    def test_exists_and_is_executable(self):
        assert os.path.exists(SCRIPT_PATH)
        assert os.stat(SCRIPT_PATH).st_mode & stat.S_IXUSR

    def test_has_bash_shebang(self):
        with open(SCRIPT_PATH) as f:
            first = f.readline().rstrip()
        assert first.startswith("#!") and "bash" in first

    def test_strict_mode(self):
        with open(SCRIPT_PATH) as f:
            text = f.read()
        assert "set -euo pipefail" in text

    def test_forces_c_locale(self):
        # Byte-level reads of object files trip "Illegal byte sequence"
        # in tr/grep under a UTF-8 locale.
        with open(SCRIPT_PATH) as f:
            assert "LC_ALL=C" in f.read()

    def test_passes_shellcheck(self):
        if not shutil.which("shellcheck"):
            pytest.skip("shellcheck not installed on this host")
        result = subprocess.run(
            ["shellcheck", SCRIPT_PATH], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


class TestArguments:
    def test_no_argument_is_usage_error(self):
        result = subprocess.run(["bash", SCRIPT_PATH], capture_output=True, text=True, check=False)
        assert result.returncode == 2
        assert "usage" in (result.stdout + result.stderr).lower()

    def test_missing_file_is_an_error(self, tmp_path):
        result = _verify(str(tmp_path / "zstd-linux-x64.tgz"))
        assert result.returncode == 2

    def test_unparseable_name_is_an_error(self, tmp_path):
        bogus = tmp_path / "something-else.tgz"
        bogus.write_bytes(b"")
        result = _verify(str(bogus))
        assert result.returncode == 2
        assert "cannot infer platform" in result.stderr

    def test_unknown_platform_rejected(self, tmp_path, good_tree):
        tgz = _pack(_clone(good_tree, tmp_path))
        result = _verify(tgz, "solaris")
        assert result.returncode == 2
        assert "unknown platform" in result.stderr


class TestGoodArchivePasses:
    def test_accepts_a_well_formed_archive(self, tmp_path, good_tree):
        tgz = _pack(_clone(good_tree, tmp_path))
        result = _verify(tgz)
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
        assert "all invariants hold" in result.stdout

    def test_reports_what_it_checked(self, tmp_path, good_tree):
        tgz = _pack(_clone(good_tree, tmp_path))
        result = _verify(tgz)
        assert "symbol index defines the public API" in result.stdout
        assert "prefix is relocatable" in result.stdout


class TestStaticArchiveInvariants:
    def test_thin_archive_rejected(self, tmp_path, good_tree):
        # A GNU thin archive only references the .o files by build-time
        # path — unpack it anywhere else and the link fails.
        root = _clone(good_tree, tmp_path)
        _patch_bytes(os.path.join(root, "lib", "libzstd.a"), 0, b"!<thin>\n")
        result = _verify(_pack(root))
        assert result.returncode == 1
        assert "thin archive" in result.stderr

    def test_tiny_archive_rejected(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        path = os.path.join(root, "lib", "libzstd.a")
        with open(path, "r+b") as f:
            f.truncate(2000)
        result = _verify(_pack(root))
        assert result.returncode == 1
        assert "expected at least" in result.stderr

    def test_empty_archive_rejected(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        with open(os.path.join(root, "lib", "libzstd.a"), "wb") as f:
            f.write(b"!<arch>\n")
        result = _verify(_pack(root))
        assert result.returncode == 1

    def test_missing_api_symbol_rejected(self, tmp_path, tree_without_zstd_compress):
        # The archive is otherwise perfect — right size, right member
        # count, right architecture — and still has to be rejected.
        root = _clone(tree_without_zstd_compress, tmp_path)
        result = _verify(_pack(root))
        assert result.returncode == 1
        assert "does not define ZSTD_compress" in result.stderr

    def test_object_of_the_wrong_architecture_rejected(self, tmp_path, good_tree):
        # Patch one member's machine field. A mislabelled archive — arm64
        # objects in a tarball named x64 — is exactly the failure a
        # "does the file exist" check sails past.
        root = _clone(good_tree, tmp_path)
        path = os.path.join(root, "lib", "libzstd.a")
        with open(path, "rb") as f:
            blob = f.read()
        if HOST_PLATFORM == "mac":
            magic, offset, bogus = b"\xcf\xfa\xed\xfe", 4, b"\x08\x00\x00\x01"
        else:
            magic, offset, bogus = b"\x7fELF", 18, b"\xf3\x00"
        first = blob.index(magic)
        _patch_bytes(path, first + offset, bogus)
        result = _verify(_pack(root))
        assert result.returncode == 1
        assert "expected" in result.stderr


class TestSharedLibraryInvariants:
    def test_missing_shared_library_rejected(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        os.remove(os.path.join(root, "lib", f"libzstd.{SHARED_EXT}"))
        result = _verify(_pack(root))
        assert result.returncode == 1
        assert f"libzstd.{SHARED_EXT}" in result.stderr

    def test_dangling_symlink_rejected(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        os.remove(_real_shared(root))
        result = _verify(_pack(root))
        assert result.returncode == 1

    def test_truncated_shared_library_rejected(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        with open(_real_shared(root), "r+b") as f:
            f.truncate(4096)
        result = _verify(_pack(root))
        assert result.returncode == 1
        assert "expected at least" in result.stderr

    def test_wrong_architecture_rejected(self, tmp_path, good_tree):
        root = _clone(good_tree, tmp_path)
        shared = _real_shared(root)
        if HOST_PLATFORM == "mac":
            _patch_bytes(shared, 4, b"\x08\x00\x00\x01")
            needle = "cputype"
        else:
            _patch_bytes(shared, 18, b"\xf3\x00")
            needle = "ELF machine"
        result = _verify(_pack(root))
        assert result.returncode == 1
        assert needle in result.stderr

    def test_not_a_shared_object_rejected(self, tmp_path, good_tree):
        # Right architecture, right size, wrong kind of file: an
        # executable or a relocatable object staged as the library.
        root = _clone(good_tree, tmp_path)
        shared = _real_shared(root)
        if HOST_PLATFORM == "mac":
            _patch_bytes(shared, 12, b"\x01\x00\x00\x00")  # MH_OBJECT
            needle = "filetype"
        else:
            _patch_bytes(shared, 16, b"\x02\x00")  # ET_EXEC
            needle = "ELF type"
        result = _verify(_pack(root))
        assert result.returncode == 1
        assert needle in result.stderr


class TestLayoutInvariants:
    @pytest.mark.parametrize(
        "missing",
        [
            "include/zstd.h",
            "include/zstd_errors.h",
            "include/zdict.h",
            "lib/pkgconfig/libzstd.pc",
            "cmake-args.txt",
            "LICENSE",
        ],
    )
    def test_missing_member_rejected(self, tmp_path, good_tree, missing):
        root = _clone(good_tree, tmp_path)
        os.remove(os.path.join(root, missing))
        result = _verify(_pack(root))
        assert result.returncode == 1
        assert missing in result.stderr

    def test_wrong_top_level_directory_rejected(self, tmp_path, good_tree):
        # The tarball name and the directory it unpacks to are part of
        # the published contract — consumers script against both.
        root = _clone(good_tree, tmp_path)
        renamed = os.path.join(os.path.dirname(root), "zstd")
        os.rename(root, renamed)
        tgz = _pack(renamed, name=f"zstd-{HOST_PLATFORM}-{HOST_CPU}")
        result = _verify(tgz)
        assert result.returncode == 1
        assert "does not unpack to a directory named" in result.stderr


class TestPkgConfigInvariants:
    def test_absolute_prefix_rejected(self, tmp_path, good_tree):
        # CMake bakes the build machine's install prefix into libzstd.pc;
        # shipping that makes ZSTD_SYS_USE_PKG_CONFIG resolve to paths
        # that don't exist on the consumer's disk.
        root = _clone(good_tree, tmp_path)
        pc = os.path.join(root, "lib", "pkgconfig", "libzstd.pc")
        with open(pc) as f:
            text = f.read()
        with open(pc, "w") as f:
            f.write(text.replace("prefix=${pcfiledir}/../..", "prefix=/build/staging"))
        result = _verify(_pack(root))
        assert result.returncode == 1
        assert "not relative to" in result.stderr
