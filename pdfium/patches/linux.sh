#!/bin/bash
# Platform patch for Linux shared library builds.
#
# Changes the component("pdfium") target to shared_library("pdfium")
# so the output is a .so rather than a static archive.  The component()
# macro resolves to static_library when is_component_build=false.
set -euo pipefail

PDFIUM_DIR="${1:-.}"

sed -i 's/component("pdfium")/shared_library("pdfium")/' "$PDFIUM_DIR/BUILD.gn"

echo "Applied linux patch: BUILD.gn → shared_library"
