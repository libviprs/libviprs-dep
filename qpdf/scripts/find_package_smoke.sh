#!/bin/sh
# Smoke-test cmake find_package(qpdf) against an unpacked qpdf archive.
#
# Usage: find_package_smoke.sh <unpacked-archive-dir> <fixture-dir> <expected-version> [run|norun]
#
#   fixture-dir  qpdf/tests/fixtures/find_package (the consumer project)
#   norun        build only, for an archive of a foreign architecture
#
# The archive is copied to a fresh directory first, so what is exercised is a
# prefix at a path nothing was built for. Every library and include directory
# has to resolve inside that copy; a path baked in from the build machine
# either fails the build here or shows up in the link lines, which are checked.
set -eu

ROOT="${1:?usage: find_package_smoke.sh <unpacked-archive-dir> <fixture-dir> <expected-version> [run|norun]}"
FIXTURE="${2:?missing the consumer fixture dir}"
EXPECTED="${3:?missing expected version}"
RUN_MODE="${4:-run}"

[ -f "$ROOT/lib/cmake/qpdf/qpdfConfig.cmake" ] || { echo "find_package: no qpdfConfig.cmake under $ROOT" >&2; exit 1; }
[ -f "$FIXTURE/CMakeLists.txt" ] || { echo "find_package: no CMakeLists.txt in $FIXTURE" >&2; exit 1; }

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

cp -R "$ROOT" "$WORK/prefix"
cp -R "$FIXTURE" "$WORK/consumer"

cmake -S "$WORK/consumer" -B "$WORK/build" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_PREFIX_PATH="$WORK/prefix"
cmake --build "$WORK/build"
echo "find_package: configured and built against a relocated copy"

# Nothing the consumer linked may point back at the tree it was copied from.
if grep -rFl "$ROOT/" "$WORK/build" >/dev/null 2>&1; then
    echo "find_package: the build refers to the original location $ROOT/:" >&2
    grep -rF "$ROOT/" "$WORK/build" | head -n 5 >&2
    exit 1
fi

if [ "$RUN_MODE" != "run" ]; then
    echo "find_package: foreign architecture, build-only (no execution)"
    exit 0
fi
"$WORK/build/smoke_shared" "$EXPECTED"
"$WORK/build/smoke_static" "$EXPECTED"
echo "find_package: ok (shared and static targets, version $EXPECTED)"
