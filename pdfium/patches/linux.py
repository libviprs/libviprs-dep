#!/usr/bin/env python3
"""Platform patch for Linux PDFium builds.

The patch is split into two modes so a single source checkout can
produce *both* a static archive and a shared library:

* ``base`` — applied once before any build. Only modifies
  ``public/fpdfview.h`` to strip the ``COMPONENT_BUILD`` guard around
  ``FPDF_EXPORT``, ensuring the public C API symbols always have
  ``visibility("default")``. Leaves ``BUILD.gn`` alone, so the
  ``component("pdfium")`` target resolves to ``static_library`` under
  ``is_component_build=false`` and a subsequent ninja build produces
  ``libpdfium.a``.

* ``shared`` — applied on top of ``base`` before the second ninja pass.
  Rewrites ``component("pdfium")`` to ``shared_library("pdfium")`` in
  ``BUILD.gn`` so the same target now links a ``.so``. Invoking ninja in
  a separate out dir then yields ``libpdfium.so`` using the already-built
  object files would be ideal, but GN invalidates the target type change
  and recompiles — acceptable cost for a single-source build.

Together this gives us both artifacts from one checkout.

These patches match what bblanchon/pdfium-binaries uses
(shared_library.patch + public_headers.patch).

Usage:
    python3 linux.py /path/to/pdfium [--mode base|shared|all]
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
    parser = argparse.ArgumentParser(description="Apply PDFium linux patches.")
    parser.add_argument("pdfium_dir", nargs="?", default=".", help="PDFium source dir")
    parser.add_argument(
        "--mode",
        choices=["base", "shared", "all"],
        default="all",
        help=(
            "base = fpdfview.h only (keeps component() -> static_library, "
            "produces libpdfium.a); shared = adds the BUILD.gn rewrite "
            "to produce libpdfium.so; all = both (default, legacy behavior)."
        ),
    )
    args = parser.parse_args()

    pdfium_dir = Path(args.pdfium_dir)
    if not (pdfium_dir / "BUILD.gn").exists():
        print(f"Error: {pdfium_dir}/BUILD.gn not found", file=sys.stderr)
        sys.exit(1)

    if args.mode in ("base", "all"):
        patch_fpdfview_h(pdfium_dir)
    if args.mode in ("shared", "all"):
        patch_build_gn(pdfium_dir)


if __name__ == "__main__":
    main()
