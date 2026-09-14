"""What this dependency is allowed to target.

The org ships no Windows artifact for any dependency, and that is an
acceptance bullet on the spike rather than a preference, so it is a test
over the whole directory and not just over the target list.
"""

import os
import re

import build_acadsharp as ba

ACAD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Binary fixtures and captured measurements are not searched for prose.
SKIP_SUFFIXES = (".dwg", ".dxf", ".so", ".a", ".png")


def source_files():
    for dirpath, dirs, files in os.walk(ACAD_DIR):
        dirs[:] = [d for d in dirs if d not in {"bin", "obj", "__pycache__"}]
        for name in files:
            if name.endswith(SKIP_SUFFIXES):
                continue
            yield os.path.join(dirpath, name)


class TestTargets:
    def test_the_three_shared_targets_are_present(self):
        assert {"linux-x64", "linux-arm64", "osx-arm64"} <= set(ba.TARGETS)

    def test_no_target_is_a_windows_rid(self):
        for rid in ba.TARGETS:
            assert not rid.startswith("win"), f"{rid} is a Windows target"

    def test_the_publish_command_names_the_rid_and_release(self):
        cmd = ba.publish_command("linux-arm64")
        assert "publish" in cmd
        assert "-r" in cmd and "linux-arm64" in cmd
        assert "-c" in cmd and "Release" in cmd

    def test_the_static_publish_asks_for_a_static_library(self):
        cmd = ba.publish_command("linux-x64", static=True)
        assert "-p:NativeLib=Static" in cmd

    def test_the_shared_publish_does_not(self):
        assert "-p:NativeLib=Static" not in ba.publish_command("linux-x64")


class TestTheMacPublishSetsTheInstallName:
    """#95: the dylib has to record the name it ships under.

    The publish emits the assembly name and the archive ships a name
    `-lacadsharp_native` can take, and on Mach-O that rename cannot reach
    LC_ID_DYLIB, so 3.7.1-viprs.1 shipped a library recording
    `@rpath/viprs_acadsharp.dylib` with no such file in the archive.
    scripts/verify_archive.sh refuses that from the bytes now; this is the
    half that stops it being written in the first place.

    `SharedLibraryInstallName` is the SDK's own property, defaulted in
    Microsoft.NETCore.Native.Unix.targets to `@rpath/$(TargetName)...`
    under a `== ''` condition and turned into the single
    `-Wl,-install_name,...` the Apple link gets. Naming it replaces that
    default; adding a LinkerArg of our own would put a second
    `-install_name` on the line instead.
    """

    def test_the_install_name_is_the_file_the_archive_ships(self):
        assert ba.mac_install_name() == f"@rpath/{ba.shared_library_name('mac')}"

    def test_the_mac_publish_passes_it(self):
        cmd = ba.publish_command("osx-arm64")
        assert f"-p:SharedLibraryInstallName={ba.mac_install_name()}" in cmd

    def test_no_linux_publish_carries_it(self):
        # ELF records no SONAME here, so the rename is invisible on Linux
        # and a linker argument there would only be noise.
        for rid, info in ba.TARGETS.items():
            if info["platform"] == "mac":
                continue
            carried = [a for a in ba.publish_command(rid) if "InstallName" in a]
            assert carried == [], f"{rid} is not a mac target"


class TestNothingNamesAMicrosoftTarget:
    """The acceptance bullet, as a check over the whole directory.

    Deliberately not a search for the bare word: `Windows-1252` is a DWG
    code page and has to be able to appear in prose and in code. What is
    forbidden is a runtime identifier, an SDK or a toolchain, none of
    which turns up by accident.
    """

    FORBIDDEN = re.compile(r"\bwin-(x64|x86|arm64)\b|\bwin10-|\bmsvc\b|Visual Studio", re.I)

    def test_no_file_under_acadsharp_names_one(self):
        offenders = []
        for path in source_files():
            if os.path.abspath(path) == os.path.abspath(__file__):
                continue  # this file spells the patterns out on purpose
            with open(path, encoding="utf-8", errors="replace") as f:
                for lineno, line in enumerate(f, 1):
                    if self.FORBIDDEN.search(line):
                        offenders.append(f"{os.path.relpath(path, ACAD_DIR)}:{lineno}")
        assert not offenders, f"a Microsoft target or toolchain is named in {offenders}"

    def test_the_guard_would_catch_one(self):
        # An assertion over a directory that happens to be clean proves
        # nothing about the pattern, so the pattern gets its own case.
        assert self.FORBIDDEN.search("<RuntimeIdentifier>win-x64</RuntimeIdentifier>")
        assert not self.FORBIDDEN.search("Windows-1252 is the DWG code page")


class TestTheLinuxBaseMatchesTheOrgFloor:
    """All three dependencies have to agree on the oldest glibc we support.

    One consumer links pdfium, zstd and acadsharp into the same binary, so a
    base image that is newer than the others quietly raises the floor for
    everything. The stock .NET SDK image does exactly that: it is Ubuntu
    noble, and the library it publishes needs GLIBC_2.38, which fails to
    dlopen on bookworm. This reads zstd's own constant rather than repeating
    a string, so moving either one without the other fails here.
    """

    def test_it_is_the_same_base_zstd_builds_linux_on(self):
        import importlib.util

        zstd_driver = os.path.join(ACAD_DIR, "..", "zstd", "build_zstd.py")
        spec = importlib.util.spec_from_file_location("zstd_driver_for_floor_check", zstd_driver)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert ba.LINUX_BUILD_IMAGE == mod.BASE_IMAGES["linux"], (
            f"acadsharp builds Linux on {ba.LINUX_BUILD_IMAGE} and zstd on "
            f"{mod.BASE_IMAGES['linux']}, so the two disagree about the glibc floor"
        )

    def test_it_is_not_the_stock_sdk_image(self):
        assert "dotnet" not in ba.LINUX_BUILD_IMAGE, (
            "the stock SDK image is Ubuntu noble and publishes a library needing "
            "GLIBC_2.38, which will not load on the floor this org ships against"
        )
