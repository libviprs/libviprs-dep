#!/usr/bin/env python3
"""Platform patch for macOS shared library builds (Apple Silicon / x86_64).

Three changes are required to produce a .dylib with exported FPDF_* symbols:

1. Change component("pdfium") to shared_library("pdfium") in BUILD.gn.
   Same reason as Linux: component() resolves to static_library when
   is_component_build=false.

2. Remove the COMPONENT_BUILD guard around FPDF_EXPORT in fpdfview.h.
   Same reason as Linux: ensures FPDF_EXPORT always applies
   visibility("default") so the public C API is accessible via dlopen/dlsym.

3. Add -Wl,-headerpad_max_install_names to the Apple toolchain linker flags.
   This reserves space in the Mach-O header for install_name_tool to rewrite
   the LC_ID_DYLIB path after the build. Without this, the header may not
   have enough room for longer paths (e.g. when packaging for Homebrew or
   nixpkgs).

Patches 1 and 2 match bblanchon/pdfium-binaries (shared_library.patch +
public_headers.patch). Patch 3 matches bblanchon's patches/mac/build.patch.

Usage:
    python3 mac.py /path/to/pdfium [--mode base|shared|all]

mac builds are single-phase today (only produce .dylib), so base + shared
are both honored but the default ``all`` matches the legacy behavior.
"""

import argparse
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

    # Remove the opening guard and its comment lines
    text = re.sub(
        r"#if defined\(COMPONENT_BUILD\)\s*\n"
        r"(// [^\n]*\n)*",
        "",
        text,
    )

    # Remove the #else ... #define FPDF_EXPORT ... #endif block
    text = re.sub(
        r"#else\s*\n"
        r"#define FPDF_EXPORT\s*\n"
        r"#endif\s*//\s*defined\(COMPONENT_BUILD\)\s*\n",
        "",
        text,
    )

    fpdfview.write_text(text)
    print("Applied: fpdfview.h -> unconditional FPDF_EXPORT")


def patch_apple_toolchain(pdfium_dir: Path) -> None:
    """Add -headerpad_max_install_names to the Apple toolchain linker flags.

    This matches bblanchon/pdfium-binaries patches/mac/build.patch.
    The patch inserts the flag just before the 'link_command' assignment
    in build/toolchain/apple/toolchain.gni.
    """
    toolchain_gni = pdfium_dir / "build" / "toolchain" / "apple" / "toolchain.gni"
    if not toolchain_gni.exists():
        print(f"WARNING: {toolchain_gni} not found — skipping headerpad patch")
        return

    text = toolchain_gni.read_text()
    marker = 'link_command = "$linker_driver_env $linker_driver"'

    if "-headerpad_max_install_names" in text:
        print("headerpad patch already applied — skipping")
        return

    if marker not in text:
        print(f"WARNING: could not find link_command marker in {toolchain_gni}")
        return

    patch_lines = '      linker_driver_args += " -Wl,-headerpad_max_install_names"\n\n'
    updated = text.replace(marker, patch_lines + "      " + marker)
    toolchain_gni.write_text(updated)
    print("Applied: toolchain.gni -> headerpad_max_install_names")


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply PDFium mac patches.")
    parser.add_argument("pdfium_dir", nargs="?", default=".", help="PDFium source dir")
    parser.add_argument(
        "--mode",
        choices=["base", "shared", "all"],
        default="all",
        help=(
            "base = fpdfview/toolchain only; shared adds the BUILD.gn rewrite; "
            "all = both (default)."
        ),
    )
    args = parser.parse_args()

    pdfium_dir = Path(args.pdfium_dir)
    if not (pdfium_dir / "BUILD.gn").exists():
        print(f"Error: {pdfium_dir}/BUILD.gn not found", file=sys.stderr)
        sys.exit(1)

    if args.mode in ("base", "all"):
        patch_fpdfview_h(pdfium_dir)
        patch_apple_toolchain(pdfium_dir)
    if args.mode in ("shared", "all"):
        patch_build_gn(pdfium_dir)


if __name__ == "__main__":
    main()
