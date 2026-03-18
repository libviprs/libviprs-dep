#!/usr/bin/env python3
"""Platform patch for Linux shared library builds.

Two changes are required to produce a .so with exported FPDF_* symbols:

1. Change component("pdfium") to shared_library("pdfium") in BUILD.gn.
   The component() macro resolves to static_library when
   is_component_build=false, so without this the output would be a .a
   archive instead of a .so.

2. Remove the COMPONENT_BUILD guard around FPDF_EXPORT in fpdfview.h.
   PDFium only defines FPDF_EXPORT (which applies
   __attribute__((visibility("default")))) when COMPONENT_BUILD is defined.
   Since we set is_component_build=false (to get a single .so instead of
   many small ones), FPDF_EXPORT resolves to nothing and all FPDF_*
   symbols get default-hidden visibility.
   Removing the guard ensures FPDF_EXPORT always applies
   visibility("default"), making the public C API accessible via
   dlopen/dlsym.

These two patches match what bblanchon/pdfium-binaries uses
(shared_library.patch + public_headers.patch).

Usage:
    python3 linux.py /path/to/pdfium
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
    """Remove the COMPONENT_BUILD guard around FPDF_EXPORT in fpdfview.h.

    Before (simplified):
        #if defined(COMPONENT_BUILD)
        // FPDF_EXPORT should be consistent ...
        // template in testing/fuzzers/BUILD.gn.
        #if defined(WIN32)
          ...  // dllexport / dllimport
        #else
          ...  // visibility("default")
        #endif
        #else
        #define FPDF_EXPORT
        #endif  // defined(COMPONENT_BUILD)

    After:
        #if defined(WIN32)
          ...  // dllexport / dllimport
        #else
          ...  // visibility("default")
        #endif
    """
    fpdfview = pdfium_dir / "public" / "fpdfview.h"
    text = fpdfview.read_text()

    # Remove the opening guard and its comment lines
    # Match: #if defined(COMPONENT_BUILD) followed by optional comment lines
    text = re.sub(
        r"#if defined\(COMPONENT_BUILD\)\s*\n"
        r"(// [^\n]*\n)*",
        "",
        text,
    )

    # Remove the #else ... #define FPDF_EXPORT ... #endif block that provides
    # the empty fallback when COMPONENT_BUILD is not defined.
    # This block sits between the platform-specific #endif and the next code.
    text = re.sub(
        r"#else\s*\n"
        r"#define FPDF_EXPORT\s*\n"
        r"#endif\s*//\s*defined\(COMPONENT_BUILD\)\s*\n",
        "",
        text,
    )

    fpdfview.write_text(text)
    print("Applied: fpdfview.h -> unconditional FPDF_EXPORT")


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


if __name__ == "__main__":
    main()
