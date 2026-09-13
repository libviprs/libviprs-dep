#!/usr/bin/env bash
# Print the shim the conformance consumers should run against.
#
#     find_shim.sh <directory>
#
# Both runners used to name one file, `viprs_acadsharp.so`, which is the
# assembly name the .NET publish emits into the build tree. The archive a
# consumer actually downloads ships `lib/libacadsharp_native.so`, because that
# is the name `-lacadsharp_native` takes. So pointing VIPRS_LIB_DIR at an
# unpacked archive found nothing, and the artefact the consumers most want to
# be run against was the one they could not be run against.
#
# Three layouts, tried in this order:
#
#   <dir>/viprs_acadsharp.so          a publish tree
#   <dir>/libacadsharp_native.so      an archive's lib/ handed over directly
#   <dir>/lib/libacadsharp_native.so  an unpacked archive root
#
# `.dylib` counts too, because the mac archive ships one and the file name is
# the half this script owns. Whether a Mach-O can run in the Linux container
# the runner uses is the runner's problem, and a link error naming the file it
# found is a better answer than "no shim at <a path nobody asked for>".
#
# The path comes back absolute. It becomes an rpath and an LD_LIBRARY_PATH
# entry inside a container whose working directory is not the caller's, and a
# relative one silently resolves somewhere else there.
set -euo pipefail

if [ "$#" -ne 1 ]; then
    echo "usage: $(basename "$0") <directory>" >&2
    exit 2
fi

dir="$1"

candidates=()
for base in "$dir" "$dir/lib"; do
    for stem in viprs_acadsharp libacadsharp_native; do
        for ext in so dylib; do
            candidates+=("$base/$stem.$ext")
        done
    done
done

for candidate in "${candidates[@]}"; do
    if [ -f "$candidate" ]; then
        printf '%s/%s\n' "$(cd "$(dirname "$candidate")" && pwd)" "$(basename "$candidate")"
        exit 0
    fi
done

{
    echo "no shim under $dir. Looked for:"
    for candidate in "${candidates[@]}"; do
        echo "  $candidate"
    done
} >&2
exit 2
