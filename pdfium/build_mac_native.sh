#!/bin/bash
# Native macOS build path for build_pdfium.py.
#
# Invoked when the host is macOS (Darwin) and --platform mac is requested,
# because PDFium's mac build calls `xcodebuild` during `gn gen` and that
# tool doesn't exist inside the Debian build container used for glibc/musl.
# bblanchon/pdfium-binaries runs mac builds on macos-15 GitHub Actions
# runners for the same reason — this script is that path.
#
# Step markers (`[N/TOTAL]`) match the format emitted by Docker BuildKit
# so the build_pdfium.py progress UI parses ETA and percent complete from
# this output exactly like a Docker build.
#
# Usage: build_mac_native.sh <version> <arch> <workspace> <repo_root> <output_dir>
#
#   version     PDFium chromium branch number (e.g. 7725)
#   arch        amd64 | arm64
#   workspace   scratch dir (depot_tools + pdfium checkout + staging) — wiped on start
#   repo_root   libviprs-dep repo root (we read LICENSE + patches from here)
#   output_dir  where the final .tgz is written

set -euo pipefail

VERSION="${1:?missing version}"
ARCH="${2:?missing arch}"
WORKSPACE="${3:?missing workspace dir}"
REPO_ROOT="${4:?missing repo root}"
OUTPUT_DIR="${5:?missing output dir}"

case "$ARCH" in
  arm64)            GN_CPU=arm64 ;;
  amd64|x86_64|x64) GN_CPU=x64 ;;
  *) echo "unsupported arch: $ARCH" >&2; exit 2 ;;
esac

DEPOT_TOOLS="$WORKSPACE/depot_tools"
BUILD_DIR="$WORKSPACE/build"
PDFIUM="$BUILD_DIR/pdfium"
STAGING="$WORKSPACE/staging"
DIR_NAME="pdfium-mac-${GN_CPU}"
STAGE_DEST="$STAGING/$DIR_NAME"
ARCHIVE="$OUTPUT_DIR/$DIR_NAME.tgz"

TOTAL=14

mkdir -p "$WORKSPACE" "$OUTPUT_DIR"
rm -rf "$BUILD_DIR" "$STAGING" "$ARCHIVE"

# Pin Xcode to 26.0 — the macos-15 runner's default is Xcode 16.4, which
# ships MacOSX15.5.sdk. Chromium's 7725 branch generates ninja rules that
# reference DarwinFoundation1.modulemap from the SDK, but Apple removed
# that file in the 15.5 SDK layout — ninja then fails with
# "missing and no known rule to make it". Xcode 26.0's MacOSX26.0.sdk
# still has the file. bblanchon/pdfium-binaries pins the same version
# in steps/01-install.sh for the same reason. Export DEVELOPER_DIR
# instead of running `sudo xcode-select -s` so the script is safe to
# run locally without modifying the developer's global toolchain.
XCODE_PIN="/Applications/Xcode_26.0.app"
if [ -d "$XCODE_PIN" ]; then
  export DEVELOPER_DIR="$XCODE_PIN/Contents/Developer"
  echo "Using $DEVELOPER_DIR"
else
  echo "WARNING: $XCODE_PIN not installed — using current xcode-select ($(xcode-select -p))"
fi

echo "[1/$TOTAL] Install depot_tools"
if [ ! -d "$DEPOT_TOOLS" ]; then
  i=0
  until git clone --depth=1 https://chromium.googlesource.com/chromium/tools/depot_tools.git "$DEPOT_TOOLS"; do
    i=$((i + 1))
    [ $i -ge 5 ] && { echo "depot_tools clone failed after 5 attempts" >&2; exit 1; }
    sleep 10
  done
fi
export PATH="$DEPOT_TOOLS:$PATH"

echo "[2/$TOTAL] Bootstrap depot_tools + gsutil"
# First gclient call triggers depot_tools' first-run bootstrap, which
# unpacks the hermetic python3 into .cipd_bin and writes the
# python3_bin_reldir.txt file that `gn gen` reads later. Setting
# DEPOT_TOOLS_UPDATE=0 here would short-circuit that bootstrap and
# make `gn gen` fail with "python3_bin_reldir.txt not found. need to
# initialize depot_tools". Freeze updates AFTER the bootstrap runs.
gclient --version
python3 "$DEPOT_TOOLS/gsutil.py" --version
# Pre-warming gsutil serializes the bundle download so gclient's parallel
# workers don't race on flock (same fix as the Linux/musl Dockerfiles).
export DEPOT_TOOLS_UPDATE=0

echo "[3/$TOTAL] gclient config"
mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"
gclient config --unmanaged https://pdfium.googlesource.com/pdfium.git \
  --custom-var "checkout_configuration=small"
printf "\ntarget_os = [ 'mac' ]\n" >> .gclient

echo "[4/$TOTAL] gclient sync origin/chromium/$VERSION"
# --jobs=8 matches the Linux/musl Dockerfiles so macos runners don't
# saturate their DNS resolver with a cpu_count-sized worker pool.
gclient sync -r "origin/chromium/$VERSION" --no-history --shallow --jobs=8

echo "[5/$TOTAL] gclient runhooks"
cd "$PDFIUM"
gclient runhooks

echo "[6/$TOTAL] Apply mac base patches"
# Two-phase build (mirrors the linux/musl path in build_pdfium.py): the
# base patches (fpdfview.h symbol visibility + Apple toolchain headerpad)
# apply to BOTH the static archive and the dylib. The BUILD.gn
# component()->shared_library rewrite is deferred to the shared pass
# (step 10) so the Static pass keeps the pristine component("pdfium"),
# which resolves to a static_library once pdf_is_complete_lib = true
# fires PDFium's own fat-archive branch.
python3 "$REPO_ROOT/pdfium/patches/mac.py" "$PDFIUM" --mode base

echo "[7/$TOTAL] gn gen out/Static (libpdfium.a)"
# The libc++ override is SCOPED TO out/Static ONLY:
#   pdf_is_complete_lib = true         -> PDFium's own BUILD.gn branch
#     emits a fat static_library (drops the //build/config/compiler:
#     thin_archive config) instead of a GNU thin archive, whose absolute
#     .o paths vanish when the build dir is cleaned and would break
#     rustc's rlib bundling downstream.
#   use_custom_libcxx = false          -> link Apple's system libc++
#   use_custom_libcxx_for_host = false    (inline namespace std::__1::) so
#     rustc can resolve PDFium's internal C++ symbols against the system
#     runtime rather than Chromium's std::__Cr:: bundled libc++.
# This override MUST NOT leak into out/Release (the dylib): see the
# out/Release comment below for why it re-breaks the Xcode 26.0 build.
mkdir -p out/Static
cat > out/Static/args.gn <<EOF
is_debug = false
pdf_is_standalone = true
pdf_enable_v8 = false
pdf_enable_xfa = false
is_component_build = false
treat_warnings_as_errors = false
pdf_use_skia = false
pdf_use_partition_alloc = false
clang_use_chrome_plugins = false
target_cpu = "$GN_CPU"
target_os = "mac"
pdf_is_complete_lib = true
use_custom_libcxx = false
use_custom_libcxx_for_host = false
EOF
gn gen out/Static

echo "[8/$TOTAL] ninja -C out/Static pdfium"
ninja -C out/Static pdfium

echo "[9/$TOTAL] Verify libpdfium.a is a complete (fat) static archive"
# Mirror build_pdfium.py's VERIFY_COMPLETE_STATIC_LIB gate. GN's
# static_library writes the archive to obj/<pkg>/lib<name>.a — for the
# root pdfium target that's out/Static/obj/libpdfium.a. A thin archive
# (!<thin>) references absolute .o paths that disappear once the build
# dir is cleaned, so rustc's rlib bundling fails downstream. mac uses
# BSD stat (-f%z) rather than GNU stat (-c %s).
STATIC_A="out/Static/obj/libpdfium.a"
ls -lh "$STATIC_A"
file "$STATIC_A"
MAGIC=$(head -c 7 "$STATIC_A")
case "$MAGIC" in
  '!<arch>') echo "OK: libpdfium.a has fat-archive magic" ;;
  '!<thin>') echo "ERROR: libpdfium.a is a GNU thin archive — pdf_is_complete_lib regressed" >&2; exit 1 ;;
  *) echo "ERROR: libpdfium.a has unexpected magic '$MAGIC'" >&2; exit 1 ;;
esac
MEMBERS=$(ar t "$STATIC_A" | wc -l | tr -d ' ')
SIZE=$(stat -f%z "$STATIC_A")
echo "libpdfium.a: $MEMBERS members, $SIZE bytes"
if [ "$MEMBERS" -lt 100 ]; then
  echo "ERROR: only $MEMBERS members — expected hundreds for a complete pdfium build" >&2
  exit 1
fi
if [ "$SIZE" -lt 10000000 ]; then
  echo "ERROR: libpdfium.a is only $SIZE bytes — expected tens of MB for a complete build" >&2
  exit 1
fi

echo "[10/$TOTAL] Apply mac shared patch (component -> shared_library)"
# Layer the shared_library rewrite on top of base so the second ninja
# pass emits libpdfium.dylib. The Static pass above already produced the
# archive from the pristine component() target.
python3 "$REPO_ROOT/pdfium/patches/mac.py" "$PDFIUM" --mode shared

echo "[11/$TOTAL] gn gen out/Release (libpdfium.dylib)"
# Keep Chromium's bundled libc++ (use_custom_libcxx = true, the default)
# for the mac dylib — dlopen consumers never see internal __Cr:: symbols
# so the Chromium-namespaced libc++ inside the shared object is harmless.
# This mirrors the linux .so path: libcxx overrides apply to the static
# archive only (out/Static above), because rustc (the only consumer that
# actually links against internal C++ symbols) can't resolve __Cr::.
#
# Setting use_custom_libcxx = false here additionally breaks on Xcode
# 26.0 / MacOSX26.0.sdk: Chromium's gen/third_party/libc++ module sources
# still get compiled but miss macros like
# _LIBCPP_BEGIN_UNVERSIONED_NAMESPACE_STD that are only defined when the
# bundled libc++ is active, failing with "unknown type name" on every
# std:: template. That is exactly why the override stays scoped to
# out/Static and never appears in this heredoc.
mkdir -p out/Release
cat > out/Release/args.gn <<EOF
is_debug = false
pdf_is_standalone = true
pdf_enable_v8 = false
pdf_enable_xfa = false
is_component_build = false
treat_warnings_as_errors = false
pdf_use_skia = false
pdf_use_partition_alloc = false
clang_use_chrome_plugins = false
target_cpu = "$GN_CPU"
target_os = "mac"
EOF
gn gen out/Release

echo "[12/$TOTAL] ninja -C out/Release pdfium"
ninja -C out/Release pdfium

echo "[13/$TOTAL] Verify libpdfium.dylib"
ls -lh out/Release/libpdfium.dylib
file out/Release/libpdfium.dylib

echo "[14/$TOTAL] Stage + tar + verify"
mkdir -p "$STAGE_DEST/lib" "$STAGE_DEST/include"
cp out/Static/obj/libpdfium.a  "$STAGE_DEST/lib/"
cp out/Release/libpdfium.dylib "$STAGE_DEST/lib/"
cp out/Release/args.gn         "$STAGE_DEST/args.gn"
cp out/Static/args.gn          "$STAGE_DEST/args.static.gn"
cp -R public/*.h               "$STAGE_DEST/include/"
cp "$REPO_ROOT/LICENSE"        "$STAGE_DEST/LICENSE"

tar czf "$ARCHIVE" -C "$STAGING" "$DIR_NAME"
ls -lh "$ARCHIVE"

# Final gate: reuse the shared verify_archive.sh so the mac .a and .dylib
# are checked for the FPDF_* public ABI plus the libc++ namespace
# invariants (std::__Cr:: absent + std::__1:: present in the .a; dylib
# exempt from the __Cr:: check) exactly like the linux/musl matrix.
echo "Verifying staged archive with verify_archive.sh"
bash "$REPO_ROOT/pdfium/scripts/verify_archive.sh" "$ARCHIVE" mac
