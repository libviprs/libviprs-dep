#!/usr/bin/env bash
# Build and run the generated conformance consumer, in a container, against a
# published shim.
#
# The crate has no dependencies, so this runs offline. build.rs reads the
# published header, computes the fingerprint from it and generates every
# declaration the consumer calls through, which is what makes a reordered field
# a build failure rather than a plausible wrong number.
#
# The image is a plain toolchain image with no .NET in it, which is the other
# half of the point: the library carries its own runtime.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../../../.." && pwd)"

image="${VIPRS_CONFORMANCE_IMAGE:-viprs-rust:arm64}"
platform="${VIPRS_CONFORMANCE_PLATFORM:-linux/arm64}"
lib_dir="${VIPRS_LIB_DIR:-$repo/acadsharp/native/bin/AbiTest/net10.0/linux-arm64/publish}"
library="$lib_dir/viprs_acadsharp.so"

if [ ! -f "$library" ]; then
    echo "no shim at $library" >&2
    echo "publish one first, in a container:" >&2
    echo "  dotnet publish acadsharp/native/Viprs.ACadSharp.Native.csproj \\" >&2
    echo "    -r linux-arm64 -c AbiTest -p:AcadSharpProject=<acadsharp>/src/ACadSharp/ACadSharp.csproj" >&2
    exit 2
fi

if nm -D --defined-only "$library" 2>/dev/null | grep -q 'viprs_acad__test_throw'; then
    test_exports=1
else
    test_exports=""
fi

echo "library: $library"
echo "image:   $image ($platform)"

docker run --rm --platform "$platform" \
    -v "$repo":"$repo" \
    -w "$here" \
    -e HOME=/tmp \
    -e CARGO_HOME=/tmp/cargo \
    -e CARGO_TARGET_DIR=/tmp/target \
    -e VIPRS_ACAD_LIB_DIR=/tmp/viprs-lib \
    -e VIPRS_ACAD_HEADER="$repo/acadsharp/include/viprs_acadsharp.h" \
    -e VIPRS_ACAD_TEST_EXPORTS="$test_exports" \
    -e LD_LIBRARY_PATH=/tmp/viprs-lib \
    --user "$(id -u):$(id -g)" \
    "$image" \
    sh -euc '
        # The published file is viprs_acadsharp.so, and a linker asked for
        # -lviprs_acadsharp wants the lib prefix. One symlink in a scratch
        # directory, rather than renaming what the build produced.
        mkdir -p /tmp/viprs-lib
        ln -sf "$1" /tmp/viprs-lib/libviprs_acadsharp.so

        if [ -z "${VIPRS_ACAD_TEST_EXPORTS:-}" ]; then
            unset VIPRS_ACAD_TEST_EXPORTS
        fi

        cargo test --offline --tests
        cargo run --offline --quiet --bin conformance
    ' _ "$library"
