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
# library is a level down from its root.
lib_dir="$(dirname "$library")"

# An unpacked archive is usually not inside the checkout, and a path the
# container cannot see fails at the link with a message about a missing file
# rather than about a missing mount.
mounts=(-v "$repo":"$repo")
case "$lib_dir/" in
    "$repo"/*) ;;
    *) mounts+=(-v "$lib_dir":"$lib_dir") ;;
esac

# The upstream half of acadsharp/VERSION. build.rs turns it into a constant
# the consumer compares the capability string against, for the same reason the
# fingerprint is computed here: a version this crate carried would agree with
# the shim on the day it was typed.
version="$(tr -d '[:space:]' < "$repo/acadsharp/VERSION")"
acadsharp_version="${version%%-viprs.*}"
if [ "$acadsharp_version" = "$version" ]; then
    echo "acadsharp/VERSION reads '$version', which is not <upstream>-viprs.<revision>" >&2
    exit 2
fi

echo "library: $library"
echo "image:   $image ($platform)"
echo "upstream: $acadsharp_version"

docker run --rm --platform "$platform" \
    "${mounts[@]}" \
    -w "$here" \
    -e HOME=/tmp \
    -e CARGO_HOME=/tmp/cargo \
    -e CARGO_TARGET_DIR=/tmp/target \
    -e VIPRS_ACAD_LIB_DIR=/tmp/viprs-lib \
    -e VIPRS_ACAD_HEADER="$repo/acadsharp/include/viprs_acadsharp.h" \
    -e VIPRS_ACAD_EXPECTED_ACADSHARP_VERSION="$acadsharp_version" \
    -e LD_LIBRARY_PATH=/tmp/viprs-lib \
    --user "$(id -u):$(id -g)" \
    "$image" \
    sh -euc '
        # Whatever the file is called where it was found, a linker asked for
        # -lviprs_acadsharp wants lib<name>.so. One symlink in a scratch
        # directory, rather than renaming what the build or the archive
        # produced.
        mkdir -p /tmp/viprs-lib
        ln -sf "$1" /tmp/viprs-lib/libviprs_acadsharp.so

        # build.rs generates the declaration of the test-only exports when
        # this is set, so it has to say what the library actually has. Asked
        # in here, by the toolchain that is about to link it: it used to be
        # asked outside, of the host, where a macOS nm does not take these
        # flags, and the failure went to /dev/null. A run against a library
        # that had the exports then skipped every case that needs them and
        # reported a pass. A probe that cannot run is now an error.
        if ! symbols=$(nm -D --defined-only "$1"); then
            echo "nm could not read $1 in this image, so whether the" >&2
            echo "test-only exports are present cannot be established" >&2
            exit 2
        fi
        if printf "%s" "$symbols" | grep -q "viprs_acad__test_throw"; then
            VIPRS_ACAD_TEST_EXPORTS=1
            export VIPRS_ACAD_TEST_EXPORTS
            echo "test exports: present"
        else
            unset VIPRS_ACAD_TEST_EXPORTS || true
            echo "test exports: absent, so the cases that need them are skipped"
        fi

        # A musl toolchain links crt-static by default, and this crate links
        # a shared library, so the result is an executable that carries an
        # INTERP because it needs one and never got told which. rustc omits
        # -dynamic-linker under crt-static, ld falls back to its own aarch64
        # default of /lib/ld-linux-aarch64.so.1, and Alpine has no such file.
        # The binary then builds and links cleanly and the kernel refuses to
        # exec it, which cargo reports as
        #
        #   error: could not execute process .../conformance (never executed)
        #   Caused by: No such file or directory (os error 2)
        #
        # and that reads like a missing build far more than like a missing
        # loader. Asking for a dynamic musl gets the interpreter written, and
        # the consumer then NEEDs libc.musl-<arch>.so.1, the same libc as the
        # shim it is testing. Appended rather than assigned so RUSTFLAGS set
        # by a caller survives.
        #
        # x86_64 fails differently, which is worth knowing before somebody
        # spends an afternoon on it. #74 took this case back out and ran the
        # consumer against the musl/x64 archive on a native x86_64 host: it
        # linked, it executed, `cargo test --tests` passed all four layout
        # cases, and then it died on the first call into the library:
        #
        #   VIPRS CAD ABI conformance, generated consumer
        #   --- handshake
        #   Segmentation fault (core dumped)
        #
        # So on this architecture the binary starts, and what starts is a
        # program holding two libcs: the static one rustc linked into it and
        # the dynamic one the shim asks for. Same missing flag, and nothing
        # about the symptom points at it. With the flag, 145 checks, no
        # failures.
        case "$(rustc -vV | sed -n "s/^host: //p")" in
            *-musl)
                RUSTFLAGS="${RUSTFLAGS:-} -C target-feature=-crt-static"
                export RUSTFLAGS
                echo "libc: musl, so the consumer is linked against a dynamic one"
                ;;
        esac

        cargo test --offline --tests
        cargo run --offline --quiet --bin conformance
    ' _ "$library"
