"""Red tests for #115 and #124: what the two manifests say has to be measured.

#115: `BUILDINFO.json` asserted `invariant_globalization` as a literal, never
looked at the compiler package, and wrote an empty `linker_version` on a mac.
#124: `shared_system_libraries` kept libc on a mac (`System.B` slips past a
filter written for `System`), and its documented derivation did not say that
framework dependencies are left out.

The shell is exercised, not grepped: the two functions are cut out of
`scripts/stage.sh` and run against stub tools, so what is asserted is what the
script does with an `ld` that rejects `--version` and an `otool -L` listing.
"""

import hashlib
import json
import os
import re
import stat
import subprocess

import build_acadsharp as ba
import pytest

ACAD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STAGE = os.path.join(ACAD_DIR, "scripts", "stage.sh")
LINKINFO_MD = os.path.join(ACAD_DIR, "docs", "LINKINFO.md")

CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <InvariantGlobalization>{value}</InvariantGlobalization>
  </PropertyGroup>
</Project>
"""

LOCK = {
    "version": 2,
    "dependencies": {
        "net10.0": {
            "Microsoft.DotNet.ILCompiler": {
                "type": "Direct",
                "requested": "[10.0.12, )",
                "resolved": "10.0.12",
            }
        }
    },
}


def _native(directory, value="true"):
    native = directory / "native"
    native.mkdir()
    (native / "Viprs.ACadSharp.Native.csproj").write_text(CSPROJ.format(value=value))
    (native / "packages.lock.json").write_text(json.dumps(LOCK))
    return str(native)


def _info(**kwargs):
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


class TestBuildinfoReadsWhatItReports:
    def test_flipping_the_csproj_flips_the_record(self, tmp_path):
        on = tmp_path / "on"
        on.mkdir()
        off = tmp_path / "off"
        off.mkdir()
        assert _info(native_dir=_native(on, "true"))["invariant_globalization"] is True
        assert _info(native_dir=_native(off, "false"))["invariant_globalization"] is False

    def test_the_default_reads_the_real_csproj(self):
        with open(os.path.join(ba.NATIVE_DIR, "Viprs.ACadSharp.Native.csproj")) as f:
            text = f.read()
        want = re.search(r"<InvariantGlobalization>(\w+)<", text).group(1) == "true"
        assert _info()["invariant_globalization"] is want

    def test_a_csproj_that_does_not_say_is_refused(self, tmp_path):
        native = _native(tmp_path)
        with open(os.path.join(native, "Viprs.ACadSharp.Native.csproj"), "w") as f:
            f.write("<Project/>\n")
        with pytest.raises(ValueError, match="InvariantGlobalization"):
            _info(native_dir=native)

    def test_the_compiler_package_version_is_recorded(self, tmp_path):
        assert _info(native_dir=_native(tmp_path))["ilcompiler_version"] == "10.0.12"

    def test_the_real_lock_file_names_the_compiler(self):
        with open(os.path.join(ba.NATIVE_DIR, "packages.lock.json")) as f:
            lock = json.load(f)
        resolved = {
            pkg["resolved"]
            for deps in lock["dependencies"].values()
            for name, pkg in deps.items()
            if name == "Microsoft.DotNet.ILCompiler"
        }
        assert len(resolved) == 1
        assert _info()["ilcompiler_version"] == resolved.pop()

    def test_the_inputs_the_shim_digest_cannot_see_are_hashed(self, tmp_path):
        native = _native(tmp_path)
        info = _info(native_dir=native)
        for field, name in (
            ("csproj_sha256", "Viprs.ACadSharp.Native.csproj"),
            ("packages_lock_sha256", "packages.lock.json"),
        ):
            with open(os.path.join(native, name), "rb") as f:
                assert info[field] == hashlib.sha256(f.read()).hexdigest()

    def test_the_new_fields_are_declared(self):
        for field in ("ilcompiler_version", "csproj_sha256", "packages_lock_sha256"):
            assert field in ba.BUILDINFO_FIELDS

    def test_the_linkinfo_field_list_does_not_grow(self):
        # shim_sha256 stays byte-equal to MANIFEST.json's, so the compiler
        # and csproj identity live in BUILDINFO and nowhere near that number.
        for field in ("ilcompiler_version", "csproj_sha256", "packages_lock_sha256"):
            assert field not in ba.LINKINFO_FIELDS


def _facts(**extra):
    facts = {
        "aot_warning_count": "0",
        "shared_needed": "",
        "static_ok": "0",
        "dwg_version_min": "1014",
        "dwg_version_max": "1032",
    }
    facts.update(extra)
    return facts


class TestAnEmptyToolVersionIsNotMeasured:
    @pytest.mark.parametrize("fact", ["linker_version", "clang_version"])
    def test_present_and_empty_becomes_unknown(self, tmp_path, fact):
        root = tmp_path / "acadsharp-mac-arm64"
        (root / "lib").mkdir(parents=True)
        (root / "lib" / ba.shared_library_name("mac")).write_bytes(b"binary")
        ba.finish_archive(
            str(root), "mac", "arm64", _facts(**{fact: ""}), builder_image="macos 15.7.9"
        )
        with open(root / "metadata" / "BUILDINFO.json") as f:
            assert json.load(f)[fact] == "unknown"


def _function(name):
    with open(STAGE) as f:
        text = f.read()
    found = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.S | re.M)
    assert found, f"scripts/stage.sh defines no {name}()"
    return found.group(0)


def _run(tmp_path, function, call, plat, stubs):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in stubs.items():
        path = bin_dir / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    script = f"PLAT={plat}\n{_function(function)}\n{call}\n"
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    out = subprocess.run(["sh", "-c", script], capture_output=True, text=True, env=env, check=True)
    return out.stdout.strip()


APPLE_LD = """case "$1" in
  --version) echo "ld: unknown options: --version" >&2; exit 1 ;;
  -v) echo "@(#)PROGRAM:ld PROJECT:ld-1115.7.3" >&2; exit 0 ;;
esac
"""

GNU_LD = """case "$1" in
  --version) echo "GNU ld (GNU Binutils for Debian) 2.40"; echo "second line"; exit 0 ;;
esac
exit 1
"""


class TestTheLinkerVersionIsMeasuredOnAMac:
    def test_apple_ld_answers_dash_v(self, tmp_path):
        got = _run(tmp_path, "read_linker_version", "read_linker_version", "mac", {"ld": APPLE_LD})
        assert got == "@(#)PROGRAM:ld PROJECT:ld-1115.7.3"

    def test_gnu_ld_still_answers_dash_dash_version(self, tmp_path):
        got = _run(tmp_path, "read_linker_version", "read_linker_version", "linux", {"ld": GNU_LD})
        assert got == "GNU ld (GNU Binutils for Debian) 2.40"

    def test_the_script_records_it_through_that_function(self):
        with open(STAGE) as f:
            assert 'fact linker_version "$(read_linker_version)"' in f.read()


OTOOL = """cat <<'STUB'
/work/lib/libacadsharp_native.dylib:
\t@rpath/libacadsharp_native.dylib (compatibility version 0.0.0, current version 0.0.0)
\t/usr/lib/libicucore.A.dylib (compatibility version 1.0.0, current version 74.1.0)
\t/usr/lib/libobjc.A.dylib (compatibility version 1.0.0, current version 228.0.0)
\t/usr/lib/swift/libswiftCore.dylib (compatibility version 1.0.0, current version 6.0.0)
\t/System/Library/Frameworks/CoreFoundation.framework/Versions/A/CoreFoundation (compat 150.0.0)
\t/System/Library/Frameworks/Security.framework/Versions/A/Security (compat 1.0.0)
\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1351.0.0)
STUB
"""

READELF = """cat <<'STUB'
 0x0000000000000001 (NEEDED)             Shared library: [libm.so.6]
 0x0000000000000001 (NEEDED)             Shared library: [libdl.so.2]
 0x0000000000000001 (NEEDED)             Shared library: [libc.so.6]
 0x0000000000000001 (NEEDED)             Shared library: [ld-linux-x86-64.so.2]
STUB
"""


class TestSharedSystemLibrariesNameNoLibc:
    def test_a_mac_list_has_no_libc(self, tmp_path):
        got = _run(tmp_path, "read_needed", "read_needed x.dylib", "mac", {"otool": OTOOL}).split()
        assert "System.B" not in got and "System" not in got, got
        assert got == ["icucore.A", "objc.A", "swiftCore"], got

    def test_a_linux_list_is_unchanged(self, tmp_path):
        got = _run(tmp_path, "read_needed", "read_needed x.so", "linux", {"readelf": READELF})
        assert got.split() == ["dl", "m"]

    def test_the_document_says_what_the_list_is_on_each_platform(self):
        with open(LINKINFO_MD) as f:
            row = next(line for line in f if line.startswith("| `shared_system_libraries`"))
        assert "framework" in row.lower(), (
            "the row does not say Mach-O framework dependencies are deliberately left "
            "out, so the documented derivation is still false on a mac"
        )
