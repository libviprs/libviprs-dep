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

# Asked for by layout rather than by file name, so VIPRS_LIB_DIR can be an
# unpacked acadsharp-*.tgz and not only a publish tree. find_shim.sh has the
# three layouts and the reason.
if ! library="$("$here/../find_shim.sh" "$lib_dir")"; then
    echo "publish one first, in a container:" >&2
    echo "  dotnet publish acadsharp/native/Viprs.ACadSharp.Native.csproj \\" >&2
    echo "    -r linux-arm64 -c AbiTest -p:AcadSharpProject=<acadsharp>/src/ACadSharp/ACadSharp.csproj" >&2
    echo "or unpack a published archive and point VIPRS_LIB_DIR at it." >&2
    exit 2
fi

# The library found, not the directory asked for: an unpacked archive's
# library is a level down from its root, and the rpath below has to name the
# directory the file is actually in.
lib_dir="$(dirname "$library")"

# An unpacked archive is usually not inside the checkout, and a path the
# container cannot see fails at the link with a message about a missing file
# rather than about a missing mount.
mounts=(-v "$repo":"$repo")
case "$lib_dir/" in
    "$repo"/*) ;;
    *) mounts+=(-v "$lib_dir":"$lib_dir") ;;
esac

echo "library: $library"
echo "image:   $image ($platform)"

docker run --rm --platform "$platform" \
    "${mounts[@]}" \
    -w "$here" \
    -e HOME=/tmp \
    --user "$(id -u):$(id -g)" \
    "$image" \
    sh -euc '
        header="$1"
        library="$2"
        lib_dir="$3"

        digest=$(sha256sum "$header" | cut -d" " -f1)
        fingerprint=$(printf "%s" "$digest" | cut -c1-16)
        echo "header sha256: $digest"
        echo "fingerprint:   0x$fingerprint"

        # The test-only export exists only in the AbiTest configuration, so
        # the library is asked rather than assumed and a run against a
        # release build skips those sections instead of failing to link.
        #
        # Asked in here, by the toolchain that is about to link it. It used
        # to be asked outside, of the host, where a macOS nm does not take
        # these flags: the probe failed, the failure went to /dev/null, and
        # a run against a library that *had* the exports quietly skipped
        # every section that needs them and reported a pass. So a probe that
        # cannot run is now an error rather than an answer.
        if ! symbols=$(nm -D --defined-only "$library"); then
            echo "nm could not read $library in this image, so whether the" >&2
            echo "test-only exports are present cannot be established" >&2
            exit 2
        fi
        if printf "%s" "$symbols" | grep -q "viprs_acad__test_throw"; then
            test_exports="-DVIPRS_WITH_TEST_EXPORTS=1"
            echo "test exports:  present"
        else
            test_exports=""
            echo "test exports:  absent, so the cases that need them are skipped"
        fi

        gcc -std=c11 -Wall -Wextra -Werror -O1 \
            -DVIPRS_EXPECTED_FINGERPRINT=0x${fingerprint}ULL \
            -DVIPRS_EXPECTED_HEADER_SHA256=\"$digest\" \
            $test_exports \
            conformance.c vacb.c \
            "$library" -Wl,-rpath,"$lib_dir" \
            -o /tmp/viprs_conformance

        /tmp/viprs_conformance
    ' _ "$repo/acadsharp/include/viprs_acadsharp.h" "$library" "$lib_dir"
