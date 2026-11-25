"""qpdf/scripts/verify_archive.sh, driven against small synthetic archives.

The archives hold a fake ELF header and no runnable binary, and they claim the
architecture the host is NOT, so the script's execution step is skipped by
design and everything before it (layout, licence, checksums, architecture,
version record) is what these tests exercise. The execution step is covered by
the real archives the build and the release workflow run the script on.
"""

import hashlib
import os
import platform
import shutil
import subprocess
import tarfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "verify_archive.sh")
VERSION = open(os.path.join(HERE, "..", "VERSION")).read().strip()

# e_machine: x86-64 is 0x3e, aarch64 is 0xb7.
MACHINE = {"x64": b"\x3e\x00", "arm64": b"\xb7\x00"}


def elf(cpu):
    return b"\x7fELF\x02\x01\x01" + b"\x00" * 9 + b"\x03\x00" + MACHINE[cpu] + b"\x00" * 40


# Mach-O cputype (little endian): CPU_TYPE_ARM64 is 0x0100000c, x86_64 is 0x01000007.
CPUTYPE = {"x64": b"\x07\x00\x00\x01", "arm64": b"\x0c\x00\x00\x01"}


def macho(cpu):
    return b"\xcf\xfa\xed\xfe" + CPUTYPE[cpu] + b"\x00" * 40


def foreign_cpu():
    host = platform.machine()
    return "arm64" if host in ("x86_64", "AMD64") else "x64"


FILES = {
    "include/qpdf/QPDF.hh": b"// header\n",
    "lib/libqpdf.a": b"!<arch>\n",
    "lib/pkgconfig/libqpdf.pc": b"prefix=${pcfiledir}/../..\nlibdir=${prefix}/lib\nincludedir=${prefix}/include\nName: libqpdf\n",
    "cmake-args.txt": b"-DCMAKE_BUILD_TYPE=Release\n-DCMAKE_INSTALL_PREFIX=/staging\n",
    "LICENSE": b"Apache License\nVersion 2.0, January 2004\n",
    "NOTICE.md": b"notice\n",
    "LICENSES/libjpeg-turbo-LICENSE.md": b"licence\n",
}


def make_archive(tmp_path, cpu=None, mutate=None, plat="linux"):
    cpu = cpu or foreign_cpu()
    name = f"qpdf-{plat}-{cpu}"
    mac = plat == "mac"
    obj = macho if mac else elf
    shared = "lib/libqpdf.30.dylib" if mac else "lib/libqpdf.so.29"
    root = tmp_path / name
    files = dict(FILES)
    files["bin/qpdf"] = obj(cpu)
    files[shared] = obj(cpu)
    files["metadata/BUILDINFO.txt"] = (
        f"qpdf_version={VERSION}\nplatform={plat}\ncpu={cpu}\n".encode()
    )
    if mutate:
        mutate(files)
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    if shared in files:
        link = "libqpdf.dylib" if mac else "libqpdf.so"
        (root / "lib" / link).symlink_to(os.path.basename(shared))
    lines = [
        f"{hashlib.sha256(d).hexdigest()}  {rel}"
        for rel, d in sorted(files.items())
        if rel != "metadata/CHECKSUMS.txt"
    ]
    if "metadata/CHECKSUMS.txt" not in files:
        (root / "metadata").mkdir(exist_ok=True)
        (root / "metadata" / "CHECKSUMS.txt").write_text("\n".join(lines) + "\n")
    tgz = tmp_path / f"{name}.tgz"
    with tarfile.open(tgz, "w:gz") as t:
        t.add(root, arcname=name)
    return str(tgz)


def run(tgz):
    return subprocess.run(["bash", SCRIPT, tgz], capture_output=True, text=True)


pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def test_a_complete_archive_passes(tmp_path):
    r = run(make_archive(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize(
    "missing",
    [
        "bin/qpdf",
        "lib/libqpdf.a",
        "lib/pkgconfig/libqpdf.pc",
        "LICENSE",
        "NOTICE.md",
        "cmake-args.txt",
        "include/qpdf/QPDF.hh",
        "metadata/BUILDINFO.txt",
    ],
)
def test_a_missing_file_is_refused(tmp_path, missing):
    r = run(make_archive(tmp_path, mutate=lambda f: f.pop(missing)))
    assert r.returncode != 0
    assert missing in r.stdout + r.stderr


def test_a_licence_that_is_not_apache_is_refused(tmp_path):
    r = run(make_archive(tmp_path, mutate=lambda f: f.update({"LICENSE": b"MIT\n"})))
    assert r.returncode != 0
    assert "Apache" in r.stdout + r.stderr


def test_an_archive_without_licenses_is_refused(tmp_path):
    r = run(make_archive(tmp_path, mutate=lambda f: f.pop("LICENSES/libjpeg-turbo-LICENSE.md")))
    assert r.returncode != 0
    assert "LICENSES" in r.stdout + r.stderr


def test_a_dynamically_linked_libjpeg_is_refused(tmp_path):
    cpu = foreign_cpu()
    r = run(
        make_archive(
            tmp_path,
            mutate=lambda f: f.update({"bin/qpdf": elf(cpu) + b"\x00libjpeg.so.62\x00"}),
        )
    )
    assert r.returncode != 0
    assert "libjpeg" in r.stdout + r.stderr


def test_a_checksum_mismatch_is_refused(tmp_path):
    def tamper(files):
        files["metadata/CHECKSUMS.txt"] = b"0" * 64 + b"  bin/qpdf\n"

    r = run(make_archive(tmp_path, mutate=tamper))
    assert r.returncode != 0
    assert "CHECKSUMS" in r.stdout + r.stderr


def test_a_binary_for_the_wrong_architecture_is_refused(tmp_path):
    other = "x64" if foreign_cpu() == "arm64" else "arm64"
    r = run(make_archive(tmp_path, mutate=lambda f: f.update({"bin/qpdf": elf(other)})))
    assert r.returncode != 0
    assert "architecture" in r.stdout + r.stderr


def test_a_version_record_that_disagrees_with_the_pin_is_refused(tmp_path):
    r = run(
        make_archive(
            tmp_path, mutate=lambda f: f.update({"metadata/BUILDINFO.txt": b"qpdf_version=0.0.1\n"})
        )
    )
    assert r.returncode != 0
    assert "version" in r.stdout + r.stderr


def test_a_thin_static_archive_is_refused(tmp_path):
    r = run(make_archive(tmp_path, mutate=lambda f: f.update({"lib/libqpdf.a": b"!<thin>\n"})))
    assert r.returncode != 0
    assert "thin" in r.stdout + r.stderr


def test_a_missing_archive_path_is_an_error():
    assert run("/nonexistent/qpdf-linux-x64.tgz").returncode == 2


# The mac slice is verified on a Linux host here, so the run step is skipped by
# design (the name says mac-arm64, the host is not a Mac) and the Mach-O header
# read is what gets exercised.
on_mac_arm64 = platform.system() == "Darwin" and platform.machine() == "arm64"
needs_other_host = pytest.mark.skipif(on_mac_arm64, reason="a mac-arm64 archive would be run here")


@needs_other_host
def test_a_complete_mac_archive_passes(tmp_path):
    r = run(make_archive(tmp_path, cpu="arm64", plat="mac"))
    assert r.returncode == 0, r.stdout + r.stderr


@needs_other_host
def test_a_mac_binary_built_for_x86_64_is_refused(tmp_path):
    r = run(
        make_archive(
            tmp_path, cpu="arm64", plat="mac", mutate=lambda f: f.update({"bin/qpdf": macho("x64")})
        )
    )
    assert r.returncode != 0
    assert "architecture" in r.stdout + r.stderr


@needs_other_host
def test_a_mac_archive_without_its_dylib_is_refused(tmp_path):
    r = run(
        make_archive(
            tmp_path, cpu="arm64", plat="mac", mutate=lambda f: f.pop("lib/libqpdf.30.dylib")
        )
    )
    assert r.returncode != 0
    assert "shared" in r.stdout + r.stderr


@needs_other_host
def test_a_mac_archive_that_links_libjpeg_dynamically_is_refused(tmp_path):
    r = run(
        make_archive(
            tmp_path,
            cpu="arm64",
            plat="mac",
            mutate=lambda f: f.update({"bin/qpdf": macho("arm64") + b"\x00libjpeg.8.dylib\x00"}),
        )
    )
    assert r.returncode != 0
    assert "libjpeg" in r.stdout + r.stderr


# libqpdf.pc and cmake-args.txt must not carry the build host's paths. The
# first mac archive shipped libdir and includedir pointing into the builder's
# home directory, which pkg-config follows even though prefix is relative.
def test_a_pkgconfig_file_with_an_absolute_libdir_is_refused(tmp_path):
    pc = b"prefix=${pcfiledir}/../..\nlibdir=/home/builder/out/lib\nincludedir=${prefix}/include\n"
    r = run(make_archive(tmp_path, mutate=lambda f: f.update({"lib/pkgconfig/libqpdf.pc": pc})))
    assert r.returncode != 0
    assert "libdir" in r.stdout + r.stderr


def test_a_pkgconfig_file_with_an_absolute_includedir_is_refused(tmp_path):
    pc = b"prefix=${pcfiledir}/../..\nlibdir=${prefix}/lib\nincludedir=/home/builder/out/include\n"
    r = run(make_archive(tmp_path, mutate=lambda f: f.update({"lib/pkgconfig/libqpdf.pc": pc})))
    assert r.returncode != 0
    assert "includedir" in r.stdout + r.stderr


def test_a_pkgconfig_file_with_prefix_relative_dirs_passes(tmp_path):
    pc = b"prefix=${pcfiledir}/../..\nlibdir=${prefix}/lib\nincludedir=${prefix}/include\n"
    r = run(make_archive(tmp_path, mutate=lambda f: f.update({"lib/pkgconfig/libqpdf.pc": pc})))
    assert r.returncode == 0, r.stdout + r.stderr


def test_cmake_args_naming_a_build_host_prefix_are_refused(tmp_path):
    args = b"-DCMAKE_INSTALL_PREFIX=/home/builder/out/qpdf-linux-x64\n"
    r = run(make_archive(tmp_path, mutate=lambda f: f.update({"cmake-args.txt": args})))
    assert r.returncode != 0
    assert "cmake-args" in r.stdout + r.stderr
