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

TOTAL=10

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

echo "[6/$TOTAL] Apply mac patches"
# mac builds are single-phase (.dylib only), so --mode all is fine.
python3 "$REPO_ROOT/pdfium/patches/mac.py" "$PDFIUM" --mode all

echo "[7/$TOTAL] gn gen"
# use_custom_libcxx=false falls back to Apple's system libc++ (inline
# namespace std::__1::) instead of Chromium's std::__Cr::. Apple libc++
# IS the standard C++ runtime on mac, so pdfium-render + rustc consumers
# can link the resulting libpdfium.dylib (and libpdfium.a once the mac
# build gains a static pass). Must match the flag set used for linux/
# musl in build_pdfium.py::GN_ARGS_PLATFORM.
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
use_custom_libcxx = false
use_custom_libcxx_for_host = false
target_cpu = "$GN_CPU"
target_os = "mac"
EOF
gn gen out/Release

echo "[8/$TOTAL] ninja -C out/Release pdfium"
ninja -C out/Release pdfium

echo "[9/$TOTAL] Verify libpdfium.dylib"
ls -lh out/Release/libpdfium.dylib
file out/Release/libpdfium.dylib

echo "[10/$TOTAL] Stage + tar"
mkdir -p "$STAGE_DEST/lib" "$STAGE_DEST/include"
cp out/Release/libpdfium.dylib "$STAGE_DEST/lib/"
cp out/Release/args.gn        "$STAGE_DEST/args.gn"
cp -R public/*.h              "$STAGE_DEST/include/"
cp "$REPO_ROOT/LICENSE"       "$STAGE_DEST/LICENSE"

tar czf "$ARCHIVE" -C "$STAGING" "$DIR_NAME"
ls -lh "$ARCHIVE"
