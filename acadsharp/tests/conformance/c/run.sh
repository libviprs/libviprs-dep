#!/usr/bin/env bash
# Build and run the C conformance consumer, in a container, against a published
# shim.
#
# The expected fingerprint is computed here, from the header, at compile time,
# and never written into a source file. That is what makes the drift check
# real rather than decorative: edit one byte of the header, run this again
# without rebuilding the shim, and the handshake reports VIPRS_ACAD_ABI_MISMATCH
# because this program now expects the hash of a file the library has never
# seen.
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

# The test-only export exists only in the AbiTest configuration. Asking the
# library rather than assuming, so a run against a release build skips that
# section instead of failing to link.
if nm -D --defined-only "$library" 2>/dev/null | grep -q 'viprs_acad__test_throw'; then
    test_exports="-DVIPRS_WITH_TEST_EXPORTS=1"
else
    test_exports=""
fi

echo "library: $library"
echo "image:   $image ($platform)"

docker run --rm --platform "$platform" \
    -v "$repo":"$repo" \
    -w "$here" \
    -e HOME=/tmp \
    --user "$(id -u):$(id -g)" \
    "$image" \
    sh -euc '
        header="$1"
        library="$2"
        lib_dir="$3"
        test_exports="$4"

        digest=$(sha256sum "$header" | cut -d" " -f1)
        fingerprint=$(printf "%s" "$digest" | cut -c1-16)
        echo "header sha256: $digest"
        echo "fingerprint:   0x$fingerprint"

        gcc -std=c11 -Wall -Wextra -Werror -O1 \
            -DVIPRS_EXPECTED_FINGERPRINT=0x${fingerprint}ULL \
            -DVIPRS_EXPECTED_HEADER_SHA256=\"$digest\" \
            $test_exports \
            conformance.c vacb.c \
            "$library" -Wl,-rpath,"$lib_dir" \
            -o /tmp/viprs_conformance

        /tmp/viprs_conformance
    ' _ "$repo/acadsharp/include/viprs_acadsharp.h" "$library" "$lib_dir" "$test_exports"
