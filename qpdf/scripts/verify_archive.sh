#!/usr/bin/env bash
# Verify a packaged qpdf release archive before it is published.
#
# Runs against the .tgz itself (not the build tree), so it catches anything
# that goes wrong between "the compiler was happy" and "this is what consumers
# download". build_qpdf.py runs it on every archive it produces and the release
# workflow runs it again before uploading.
#
# Usage: verify_archive.sh <tgz-path>
#
#   tgz-path   path to qpdf-<platform>-<cpu>.tgz (linux|mac, x64|arm64)
#
# Invariants enforced:
#   1. One top-level directory, named after the tarball, holding the
#      documented layout: bin/qpdf, the shared and static libqpdf, the public
#      headers, the pkg-config file, cmake-args.txt, LICENSE, NOTICE.md and
#      metadata/{BUILDINFO.txt,CHECKSUMS.txt}.
#   1b. LICENSES/ holds the licence files of the statically linked libjpeg, and
#      neither bin/qpdf nor the shared library names libjpeg as a dependency.
#   2. LICENSE is the Apache License 2.0 (qpdf is Apache-2.0 since v11).
#   3. metadata/CHECKSUMS.txt lists every other regular file, with the right
#      sha256, and nothing is listed that is not there.
#   4. bin/qpdf and the shared library are built for the architecture the
#      filename claims (ELF e_machine / Mach-O cputype, read from the header
#      bytes so a foreign-arch archive checks fine on any host).
#   5. libqpdf.a is a real archive, not a thin one that points at build paths.
#   6. libqpdf.pc is relocatable (prefix relative to ${pcfiledir}, libdir and
#      includedir under ${prefix}), and cmake-args.txt records the neutral
#      /staging install prefix rather than a path from the build host.
#   6b. Nothing relocatable names a path on the build machine: no absolute path
#      in lib/cmake/**, lib/pkgconfig/**, share/** (the upstream prose under
#      share/doc and share/man aside) or a text script in bin/, and every
#      ${_IMPORT_PREFIX}/... the cmake targets name exists in the archive
#      (the static libjpeg ships as lib/libjpeg.a).
#   7. metadata/BUILDINFO.txt records the version in qpdf/VERSION.
#   7b. BUILDINFO also records the libjpeg-turbo version and source digest, the
#      driver commit, and the C++ runtime the platform really uses (libstdc++
#      on linux, libc++ on mac).
#   7c. On mac, every Mach-O in bin/ and lib/ (members of the static libraries
#      too) has a deployment target no newer than macOS 11.0, which is what
#      the README promises.
#   8. When the host can run the binary: `bin/qpdf --version` reports that
#      version with no library path set, `--check` passes a known-good tiny
#      PDF, and `--check` fails a corrupted one.

set -euo pipefail
export LC_ALL=C

fail() {
  echo "verify: $*" >&2
  exit 1
}

if [ $# -ne 1 ]; then
  echo "Usage: $0 <tgz-path>" >&2
  exit 2
fi
INPUT="$1"
[ -f "$INPUT" ] || {
  echo "Error: $INPUT does not exist" >&2
  exit 2
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QPDF_DIR="$(dirname "$SCRIPT_DIR")"
BASE="$(basename "$INPUT" .tgz)"
case "$BASE" in
  qpdf-linux-x64 | qpdf-linux-arm64 | qpdf-mac-arm64) ;;
  *) fail "unexpected archive name '$BASE' (want qpdf-linux-x64, qpdf-linux-arm64 or qpdf-mac-arm64)" ;;
esac
PLATFORM="$(echo "$BASE" | cut -d- -f2)"
CPU="$(echo "$BASE" | cut -d- -f3)"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
tar xzf "$INPUT" -C "$WORK"

tops="$(tar tzf "$INPUT" | cut -d/ -f1 | sort -u)"
[ "$tops" = "$BASE" ] || fail "archive must hold exactly one top-level directory named $BASE, found: $tops"
ROOT="$WORK/$BASE"

# 1. layout
for f in bin/qpdf include/qpdf/QPDF.hh lib/libqpdf.a lib/pkgconfig/libqpdf.pc \
  lib/cmake/qpdf/qpdfConfig.cmake lib/cmake/qpdf/libqpdfTargets.cmake cmake-args.txt LICENSE NOTICE.md metadata/BUILDINFO.txt metadata/CHECKSUMS.txt; do
  [ -f "$ROOT/$f" ] || fail "missing $f"
done
SHARED="$(find "$ROOT/lib" -maxdepth 1 -type f \( -name 'libqpdf.so*' -o -name 'libqpdf*.dylib' \) | sort | head -n 1)"
[ -n "$SHARED" ] || fail "missing the shared libqpdf in lib/"

# 1b. LICENSES/ ships the statically linked libjpeg's licence
if [ -z "$(find "$ROOT/LICENSES" -type f 2>/dev/null | head -n 1)" ]; then
  fail "missing LICENSES/ (the statically linked libjpeg's licence files)"
fi

# 1c. libjpeg is statically linked: no libjpeg library name in the binary or
# the shared library (a dynamic link records it as a NEEDED / load command)
for f in "$ROOT/bin/qpdf" "$SHARED"; do
  if grep -aq 'libjpeg\.\(so\|[0-9]*\.dylib\|dylib\)' "$f"; then
    fail "$(basename "$f") links libjpeg dynamically; it must be linked statically"
  fi
done

# 2. licence
if ! grep -q 'Apache License' "$ROOT/LICENSE" || ! grep -q 'Version 2.0' "$ROOT/LICENSE"; then
  fail "LICENSE is not the Apache License 2.0"
fi

# 3. checksums
if command -v sha256sum >/dev/null 2>&1; then
  sha() { sha256sum "$1" | cut -d' ' -f1; }
else
  sha() { shasum -a 256 "$1" | cut -d' ' -f1; }
fi
(cd "$ROOT" && find . -type f ! -path ./metadata/CHECKSUMS.txt | sed 's|^\./||' | sort) >"$WORK/actual.txt"
sed 's/^[0-9a-f]*  //' "$ROOT/metadata/CHECKSUMS.txt" | sort >"$WORK/listed.txt"
if ! diff "$WORK/actual.txt" "$WORK/listed.txt" >"$WORK/diff.txt"; then
  fail "metadata/CHECKSUMS.txt does not list exactly the files in the archive: $(tr '\n' ' ' <"$WORK/diff.txt")"
fi
while read -r want rel; do
  [ "$(sha "$ROOT/$rel")" = "$want" ] || fail "metadata/CHECKSUMS.txt: sha256 mismatch for $rel"
done <"$ROOT/metadata/CHECKSUMS.txt"

# 4. architecture
arch_of() {
  local magic machine
  magic="$(od -An -tx1 -N4 "$1" | tr -d ' \n')"
  case "$magic" in
    7f454c46)
      machine="$(od -An -tx1 -j18 -N2 "$1" | tr -d ' \n')"
      case "$machine" in
        3e00) echo x64 ;;
        b700) echo arm64 ;;
        *) echo "elf-$machine" ;;
      esac
      ;;
    cffaedfe)
      machine="$(od -An -tx1 -j4 -N4 "$1" | tr -d ' \n')"
      case "$machine" in
        0c000001) echo arm64 ;;
        07000001) echo x64 ;;
        *) echo "macho-$machine" ;;
      esac
      ;;
    *) echo "unknown-$magic" ;;
  esac
}
for f in "$ROOT/bin/qpdf" "$SHARED"; do
  got="$(arch_of "$f")"
  [ "$got" = "$CPU" ] || fail "architecture mismatch: $(basename "$f") is $got, the archive name says $CPU"
done

# 5. static archive
[ "$(head -c 8 "$ROOT/lib/libqpdf.a")" = '!<arch>' ] ||
  fail "lib/libqpdf.a is a thin or malformed archive (a thin one references build paths)"

# 6. relocatable pkg-config
# shellcheck disable=SC2016  # the dollar is literal text in the .pc file
grep -q '^prefix=${pcfiledir}/\.\./\.\.$' "$ROOT/lib/pkgconfig/libqpdf.pc" ||
  fail "libqpdf.pc prefix is not relative to \${pcfiledir}"
# shellcheck disable=SC2016
grep -q '^libdir=${prefix}/lib$' "$ROOT/lib/pkgconfig/libqpdf.pc" ||
  fail "libqpdf.pc libdir is not under \${prefix}"
# shellcheck disable=SC2016
grep -q '^includedir=${prefix}/include$' "$ROOT/lib/pkgconfig/libqpdf.pc" ||
  fail "libqpdf.pc includedir is not under \${prefix}"
grep -qx -e '-DCMAKE_INSTALL_PREFIX=/staging' "$ROOT/cmake-args.txt" ||
  fail "cmake-args.txt does not record -DCMAKE_INSTALL_PREFIX=/staging (a path from the build host?)"

# 6b. no path from the build machine in anything a consumer reads. An absolute
# path is a / that starts a word (after whitespace, a quote, ; = : ( , or a
# -I/-L flag) and goes on into a second component, so ${prefix}/lib and a bare
# "/" do not match. Comment lines and a shebang are not paths in use.
abs_re="(^|[[:space:]\"';=:(,]|-[IL])/[A-Za-z0-9_.+~-]+([/\"';[:space:]]|\$)"
while IFS= read -r f; do
  grep -Iq . "$f" || continue
  hit="$(sed -e '1{/^#!/d;}' -e '/^[[:space:]]*#/d' "$f" | grep -E -- "$abs_re" | head -n 1 || true)"
  if [ -n "$hit" ]; then
    fail "absolute path outside the archive in ${f#"$ROOT"/}: $(echo "$hit" | tr -s '[:space:]' ' ')"
  fi
done < <(find "$ROOT/lib/cmake" "$ROOT/lib/pkgconfig" "$ROOT/share" "$ROOT/bin" -type f \
  ! -path "$ROOT/share/doc/*" ! -path "$ROOT/share/man/*" 2>/dev/null | sort)
while IFS= read -r ref; do
  [ -e "$ROOT/${ref#\$\{_IMPORT_PREFIX\}/}" ] ||
    fail "the cmake targets name ${ref}, which is not in the archive"
done < <(grep -rhoE '\$\{_IMPORT_PREFIX\}/[A-Za-z0-9_./+-]+' "$ROOT/lib/cmake" | sort -u)

# 7. version record
WANT="$(tr -d '[:space:]' <"$QPDF_DIR/VERSION")"
grep -qx "qpdf_version=$WANT" "$ROOT/metadata/BUILDINFO.txt" ||
  fail "metadata/BUILDINFO.txt does not record qpdf_version=$WANT (the version in qpdf/VERSION)"

# 7b. build provenance
for key in jpeg_version jpeg_source_sha256 driver_commit runtime_libraries; do
  grep -q "^$key=" "$ROOT/metadata/BUILDINFO.txt" || fail "metadata/BUILDINFO.txt has no $key"
done
grep -Eqx 'jpeg_version=[0-9]+\.[0-9]+\.[0-9]+' "$ROOT/metadata/BUILDINFO.txt" ||
  fail "metadata/BUILDINFO.txt: jpeg_version is not a version"
grep -Eqx 'jpeg_source_sha256=[0-9a-f]{64}' "$ROOT/metadata/BUILDINFO.txt" ||
  fail "metadata/BUILDINFO.txt: jpeg_source_sha256 is not a sha256"
grep -Eqx 'driver_commit=[0-9a-f]{40}(-dirty)?' "$ROOT/metadata/BUILDINFO.txt" ||
  fail "metadata/BUILDINFO.txt: driver_commit is not a full commit id"
if [ "$PLATFORM" = mac ]; then
  want_runtime="libc libc++ libz"
else
  want_runtime="libc libstdc++ libz"
fi
grep -qx "runtime_libraries=$want_runtime" "$ROOT/metadata/BUILDINFO.txt" ||
  fail "metadata/BUILDINFO.txt: runtime_libraries must be '$want_runtime' on $PLATFORM"

# 7c. the mac slice keeps its macOS 11 promise, in every object it ships
MAC_MINOS=11.0
if [ "$PLATFORM" = mac ]; then
  command -v python3 >/dev/null 2>&1 || fail "python3 is needed to read the Mach-O deployment targets"
  minos_out="$(cd "$ROOT" && find bin lib -type f -print0 | sort -z |
    xargs -0 python3 -I "$SCRIPT_DIR/macho_minos.py" "$MAC_MINOS" 2>&1)" ||
    fail "deployment target above macOS $MAC_MINOS: $(echo "$minos_out" | tr '\n' ' ')"
fi

# 8. run it, when this host can
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) HOST=linux-x64 ;;
  Linux-aarch64 | Linux-arm64) HOST=linux-arm64 ;;
  Darwin-arm64) HOST=mac-arm64 ;;
  *) HOST=unknown ;;
esac
if [ "$HOST" != "$PLATFORM-$CPU" ]; then
  echo "verify: $BASE is not runnable on this host ($HOST), layout and header checks only"
  echo "verify: ok $BASE"
  exit 0
fi

FIX="$QPDF_DIR/tests/fixtures"
[ -f "$FIX/good.pdf" ] && [ -f "$FIX/corrupt.pdf" ] || fail "missing fixtures in $FIX"
run_qpdf() { env -u LD_LIBRARY_PATH -u DYLD_LIBRARY_PATH -u DYLD_FALLBACK_LIBRARY_PATH "$ROOT/bin/qpdf" "$@"; }

first="$(run_qpdf --version | head -n 1)"
case "$first" in
  "qpdf version $WANT") ;;
  *) fail "bin/qpdf reports '$first', want 'qpdf version $WANT'" ;;
esac
out="$(run_qpdf --check "$FIX/good.pdf" 2>&1)" || fail "qpdf --check rejected the known-good PDF: $out"
case "$out" in
  *"No syntax or stream encoding errors found"*) ;;
  *) fail "qpdf --check on the good PDF did not report a clean file: $out" ;;
esac
if run_qpdf --check "$FIX/corrupt.pdf" >/dev/null 2>&1; then
  fail "qpdf --check accepted the corrupted PDF"
fi
echo "verify: ok $BASE (version $WANT, good PDF passes, corrupt PDF fails)"
