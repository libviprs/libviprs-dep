#!/usr/bin/env python3
"""Platform patch for musl (Alpine) shared library builds.

Four changes are required on top of the standard Linux patches:

1. Change component("pdfium") to shared_library("pdfium") in BUILD.gn.
   Same as Linux — component() resolves to static_library when
   is_component_build=false.

2. Remove the COMPONENT_BUILD guard around FPDF_EXPORT in fpdfview.h.
   Same as Linux — ensures FPDF_EXPORT always applies
   visibility("default").

3. Patch build/config/BUILDCONFIG.gn to:
   - Declare the is_musl GN arg.
   - Route the default toolchain to //build/toolchain/linux/musl when
     is_musl is true.
   - Disable -fstack-protector under musl (musl's __stack_chk_fail is
     incompatible with the flags Chromium passes).

4. Patch third_party/highway/BUILD.gn to also disable HWY_AVX3_SPR on
   32-bit builds. Without this, highway emits broken SIMD code on musl
   x86.

5. Install the musl GN toolchain definition at
   build/toolchain/linux/musl/BUILD.gn. This defines gcc_toolchain()
   entries for x86, x64, arm, and arm64 using musl-cross-make prefixed
   compilers.

All patches match bblanchon/pdfium-binaries (patches/musl/).

Usage:
    python3 musl.py /path/to/pdfium
"""

import re
import sys
from pathlib import Path


def patch_build_gn(pdfium_dir: Path) -> None:
    """Patch BUILD.gn: component() -> shared_library()."""
    build_gn = pdfium_dir / "BUILD.gn"
    text = build_gn.read_text()
    updated = text.replace('component("pdfium")', 'shared_library("pdfium")')
    if updated == text:
        print('WARNING: component("pdfium") not found in BUILD.gn — already patched?')
        return
    build_gn.write_text(updated)
    print("Applied: BUILD.gn -> shared_library")


def patch_fpdfview_h(pdfium_dir: Path) -> None:
    """Remove the COMPONENT_BUILD guard around FPDF_EXPORT in fpdfview.h."""
    fpdfview = pdfium_dir / "public" / "fpdfview.h"
    text = fpdfview.read_text()

    text = re.sub(
        r"#if defined\(COMPONENT_BUILD\)\s*\n"
        r"(// [^\n]*\n)*",
        "",
        text,
    )

    text = re.sub(
        r"#else\s*\n"
        r"#define FPDF_EXPORT\s*\n"
        r"#endif\s*//\s*defined\(COMPONENT_BUILD\)\s*\n",
        "",
        text,
    )

    fpdfview.write_text(text)
    print("Applied: fpdfview.h -> unconditional FPDF_EXPORT")


def patch_buildconfig_gn(pdfium_dir: Path) -> None:
    """Patch build/config/BUILDCONFIG.gn for musl support.

    - Declare is_musl arg (defaults to false).
    - Route default toolchain to //build/toolchain/linux/musl when is_musl.
    - Disable -fstack-protector under musl.
    """
    buildconfig = pdfium_dir / "build" / "config" / "BUILDCONFIG.gn"
    if not buildconfig.exists():
        print(f"WARNING: {buildconfig} not found — skipping")
        return

    text = buildconfig.read_text()

    # Add is_musl declaration after is_official_build
    if "is_musl" not in text:
        text = text.replace(
            "is_official_build = false",
            "is_official_build = false\n\n  # Use musl instead of glibc\n  is_musl = false",
        )

    # Route toolchain: insert musl check before is_clang check
    toolchain_sections = text.split("_default_toolchain")[1:]
    already_patched = toolchain_sections and "is_musl" in toolchain_sections[0]
    if not already_patched:
        text = text.replace(
            "  if (is_clang) {\n"
            '    _default_toolchain = "//build/toolchain/linux:clang_$target_cpu"',
            "  if (is_musl) {\n"
            '    _default_toolchain = "//build/toolchain/linux/musl:$target_cpu"\n'
            "  } else if (is_clang) {\n"
            '    _default_toolchain = "//build/toolchain/linux:clang_$target_cpu"',
        )

    # Disable -fstack-protector under musl
    if "!is_musl" not in text:
        text = text.replace(
            "} else if (is_posix || is_fuchsia) {",
            "} else if ((is_posix && !is_musl) || is_fuchsia) {",
        )

    buildconfig.write_text(text)
    print("Applied: BUILDCONFIG.gn -> musl support")


def patch_highway_build_gn(pdfium_dir: Path) -> None:
    """Patch third_party/highway/BUILD.gn to disable HWY_AVX3_SPR on 32-bit."""
    highway_gn = pdfium_dir / "third_party" / "highway" / "BUILD.gn"
    if not highway_gn.exists():
        print(f"WARNING: {highway_gn} not found — skipping")
        return

    text = highway_gn.read_text()
    old = 'defines += [ "HWY_BROKEN_TARGETS=(HWY_AVX2|HWY_AVX3)" ]'
    new = 'defines += [ "HWY_BROKEN_TARGETS=(HWY_AVX2|HWY_AVX3|HWY_AVX3_SPR)" ]'

    if new in text:
        print("highway patch already applied — skipping")
        return

    if old not in text:
        print("WARNING: highway HWY_BROKEN_TARGETS line not found — skipping")
        return

    text = text.replace(old, new)
    highway_gn.write_text(text)
    print("Applied: highway BUILD.gn -> disable HWY_AVX3_SPR")


def install_musl_toolchain(pdfium_dir: Path) -> None:
    """Install the musl GN toolchain definition."""
    toolchain_dir = pdfium_dir / "build" / "toolchain" / "linux" / "musl"
    toolchain_dir.mkdir(parents=True, exist_ok=True)
    toolchain_gn = toolchain_dir / "BUILD.gn"

    toolchain_gn.write_text("""\
import("//build/toolchain/gcc_toolchain.gni")

gcc_toolchain("x86") {
  toolprefix = "i686-linux-musl-"

  cc = "${toolprefix}gcc"
  cxx = "${toolprefix}g++"

  readelf = "${toolprefix}readelf"
  nm = "${toolprefix}nm"
  ar = "${toolprefix}ar"
  ld = cxx

  extra_ldflags = "-static-libgcc -static-libstdc++"

  toolchain_args = {
    current_cpu = "x86"
    current_os = "linux"

    use_remoteexec = false
    is_clang = false
  }
}

gcc_toolchain("x64") {
  toolprefix = "x86_64-linux-musl-"

  cc = "${toolprefix}gcc"
  cxx = "${toolprefix}g++"

  readelf = "${toolprefix}readelf"
  nm = "${toolprefix}nm"
  ar = "${toolprefix}ar"
  ld = cxx

  extra_ldflags = "-static-libgcc -static-libstdc++"

  toolchain_args = {
    current_cpu = "x64"
    current_os = "linux"

    use_remoteexec = false
    is_clang = false
  }
}

gcc_toolchain("arm") {
  toolprefix = "arm-linux-musleabihf-"

  cc = "${toolprefix}gcc"
  cxx = "${toolprefix}g++"

  readelf = "${toolprefix}readelf"
  nm = "${toolprefix}nm"
  ar = "${toolprefix}ar"
  ld = cxx

  extra_ldflags = "-static-libgcc -static-libstdc++"

  toolchain_args = {
    current_cpu = "arm"
    current_os = "linux"

    use_remoteexec = false
    is_clang = false
  }
}

gcc_toolchain("arm64") {
  toolprefix = "aarch64-linux-musl-"

  cc = "${toolprefix}gcc"
  cxx = "${toolprefix}g++"

  readelf = "${toolprefix}readelf"
  nm = "${toolprefix}nm"
  ar = "${toolprefix}ar"
  ld = cxx

  extra_cxxflags= "-flax-vector-conversions"
  extra_ldflags = "-static-libgcc -static-libstdc++"

  toolchain_args = {
    current_cpu = "arm64"
    current_os = "linux"

    use_remoteexec = false
    is_clang = false
  }
}
""")
    print(f"Installed: {toolchain_gn}")


def main() -> None:
    if len(sys.argv) < 2:
        pdfium_dir = Path(".")
    else:
        pdfium_dir = Path(sys.argv[1])

    if not (pdfium_dir / "BUILD.gn").exists():
        print(f"Error: {pdfium_dir}/BUILD.gn not found", file=sys.stderr)
        sys.exit(1)

    patch_build_gn(pdfium_dir)
    patch_fpdfview_h(pdfium_dir)
    patch_buildconfig_gn(pdfium_dir)
    patch_highway_build_gn(pdfium_dir)
    install_musl_toolchain(pdfium_dir)


if __name__ == "__main__":
    main()
