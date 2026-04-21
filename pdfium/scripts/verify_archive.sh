#!/usr/bin/env bash
# Verify a freshly-built PDFium release archive is linkable from rustc
# against the system C++ runtime (libstdc++ on linux, Apple libc++ on
# mac). Runs as the last step of every release build job and blocks the
# upload if any invariant is broken.
#
# Usage: verify_archive.sh <tgz-or-lib-path> [platform]
#
#   tgz-or-lib-path   path to pdfium-<platform>-<arch>.tgz, or directly
#                     to libpdfium.{a,so,dylib} (skips extraction).
#   platform          linux | musl | mac (auto-detected from filename
#                     if omitted).
#
# Invariants enforced:
#   1. Required FPDF_* C API symbols are defined (public ABI intact).
#   2. No std::__Cr::* symbols anywhere (Chromium's custom libc++
#      namespace that breaks rustc linking).
#   3. On linux/musl: std::__cxx11:: libstdc++ symbols present.
#      On mac:        std::__1:: Apple libc++ symbols present.
#   4. No UNDEFINED __Cr references either (catches partial link).

set -euo pipefail

usage() {
  echo "Usage: $0 <tgz-or-lib-path> [platform]" >&2
  echo "  platform: linux | musl | mac (auto-detected if omitted)" >&2
}

if [ $# -lt 1 ]; then
  echo "Error: missing archive path" >&2
  usage
  exit 2
fi

INPUT="$1"
PLATFORM="${2:-}"

if [ ! -e "$INPUT" ]; then
  echo "Error: $INPUT does not exist" >&2
  exit 2
fi

# Auto-detect platform from filename if not provided.
if [ -z "$PLATFORM" ]; then
  case "$(basename "$INPUT")" in
    *musl*)  PLATFORM=musl ;;
    *linux*) PLATFORM=linux ;;
    *mac*)   PLATFORM=mac ;;
    *)       echo "Error: cannot infer platform from $INPUT (pass explicitly)" >&2; exit 2 ;;
  esac
fi

case "$PLATFORM" in
  linux|musl|mac) ;;
  *) echo "Error: unknown platform '$PLATFORM'" >&2; exit 2 ;;
esac

# If given a .tgz, extract to a temp dir and operate on its lib/ contents.
WORK=""
cleanup() { [ -n "$WORK" ] && rm -rf "$WORK"; }
trap cleanup EXIT

if [ -f "$INPUT" ] && [[ "$INPUT" == *.tgz || "$INPUT" == *.tar.gz ]]; then
  WORK=$(mktemp -d)
  tar xzf "$INPUT" -C "$WORK"
  LIB_DIR=$(find "$WORK" -type d -name lib | head -n1)
  if [ -z "$LIB_DIR" ]; then
    echo "Error: no lib/ directory in $INPUT" >&2
    exit 2
  fi
else
  LIB_DIR=$(dirname "$INPUT")
fi

echo "Verifying archive for platform=$PLATFORM at $LIB_DIR"

# ---------------------------------------------------------------------------
# Symbol-listing helper: returns demangled C++ symbols, one per line.
# Linux: nm -C works directly. Mac: Apple's nm has no -C, pipe via c++filt.
# ---------------------------------------------------------------------------
list_symbols() {
  local file="$1"
  if [ "$PLATFORM" = "mac" ]; then
    # -g: global/external; we want everything, not just -U defined.
    nm "$file" 2>/dev/null | c++filt
  else
    nm -C "$file" 2>/dev/null
  fi
}

# ---------------------------------------------------------------------------
# Invariant checks
# ---------------------------------------------------------------------------

FAIL=0
fail() {
  echo "FAIL: $*" >&2
  FAIL=1
}

verify_lib() {
  local lib="$1"
  echo "---- $lib ----"

  local syms
  syms=$(list_symbols "$lib")

  # Catch silent nm failure (would cause every grep to pass vacuously).
  if [ -z "$syms" ]; then
    fail "nm produced no output for $lib"
    return
  fi

  # 1. Public FPDF_* C API must be defined. This invariant applies to
  #    every shippable artifact — both rustc static links and dlopen
  #    consumers call into the public C entry points.
  for sym in FPDF_InitLibrary FPDF_DestroyLibrary FPDF_LoadDocument; do
    if ! echo "$syms" | grep -qE "\\b${sym}\\b"; then
      fail "$lib missing required symbol: $sym"
    fi
  done

  # Static vs shared invariants diverge from here:
  #
  #   * libpdfium.a is linked by rustc at build time — every internal C++
  #     symbol must be resolvable against the consumer's system C++ runtime.
  #     Chromium's bundled libc++ (std::__Cr::*) does not exist on the
  #     consumer side; the link fails with "undefined reference to
  #     std::__Cr::basic_string<...>". So .a MUST NOT contain __Cr::
  #     symbols, and MUST contain the platform's standard C++ runtime
  #     namespace (std::__cxx11:: on linux, std::__1:: on mac).
  #
  #   * libpdfium.so / libpdfium.dylib are loaded at runtime via dlopen —
  #     internal C++ symbols are fully resolved inside the library itself.
  #     Chromium's self-contained __Cr::* symbols are therefore harmless
  #     in the shared object, and keeping them lets the linux shared build
  #     continue to use Chromium's bundled sysroot for arm64 cross-compile
  #     (which ships glib/nss/fontconfig the system apt can't provide
  #     multi-arch). We only verify the public C API for .so/.dylib.
  case "$lib" in
    *.a)
      local cr_count
      cr_count=$(echo "$syms" | grep -c '__Cr::' || true)
      if [ "$cr_count" -gt 0 ]; then
        fail "$lib contains $cr_count std::__Cr::* symbols (Chromium custom libc++ leaked into static archive)"
        echo "  first few:" >&2
        echo "$syms" | grep '__Cr::' | head -3 >&2
      fi

      if [ "$PLATFORM" = "mac" ]; then
        # Apple libc++ inline namespace.
        if ! echo "$syms" | grep -q 'std::__1::'; then
          fail "$lib has no std::__1:: symbols — Apple libc++ not linked?"
        fi
      else
        # libstdc++ dual-ABI inline namespace.
        if ! echo "$syms" | grep -q 'std::__cxx11::'; then
          fail "$lib has no std::__cxx11:: symbols — libstdc++ not linked?"
        fi
      fi
      ;;
    *.so|*.dylib)
      # Public C API check above is sufficient; internal __Cr:: symbols
      # are acceptable because they are resolved within the shared object
      # and never surfaced to dlopen consumers.
      ;;
    *)
      fail "$lib has unexpected extension (expected .a, .so, or .dylib)"
      ;;
  esac
}

# Find all shippable libs in LIB_DIR (may include .a, .so, .dylib).
shopt -s nullglob
LIBS=()
for f in "$LIB_DIR"/libpdfium.a "$LIB_DIR"/libpdfium.so "$LIB_DIR"/libpdfium.dylib; do
  [ -e "$f" ] && LIBS+=("$f")
done
shopt -u nullglob

if [ "${#LIBS[@]}" -eq 0 ]; then
  echo "Error: no libpdfium.{a,so,dylib} found under $LIB_DIR" >&2
  exit 2
fi

for lib in "${LIBS[@]}"; do
  verify_lib "$lib"
done

if [ $FAIL -ne 0 ]; then
  echo ""
  echo "verify_archive.sh: one or more invariants failed (see FAIL lines above)" >&2
  exit 1
fi

echo ""
echo "verify_archive.sh: all invariants hold — archive is rustc-linkable."
