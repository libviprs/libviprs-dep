#!/usr/bin/env bash
# Refuse a library whose libc is not the one the caller is asking about.
#
#     readelf -d <library> \
#       | sed -n 's/.*NEEDED.*\[\(.*\)\]/\1/p' \
#       | expect_libc.sh musl
#
# The positive control for a conformance run, and those runs need one. A musl
# .so and a glibc .so are both ELF, find_shim.sh finds either, and a consumer
# linked against either reports the same passes, so a green run does not say on
# its own which library it ran against. NEEDED does: it is the library's own
# statement about its libc, written by the linker that built it, and the two
# archives this repo builds from one tree for one architecture disagree about
# it. So the sonames go in here and a cell that is somehow holding the other
# libc's archive fails before anything runs against it.
#
# It reads the entries rather than running readelf itself, because the readelf
# that can read the library is the one inside the container of that
# architecture, and choosing that container is the caller's job. What is left
# is the decision, and a decision in a file is a decision a test can run both
# ways. It used to be a block of shell inside one step of one workflow, which
# was correct and had never once been shown to refuse anything.
#
# Exit 0 when the entries name the expected libc, 1 when they do not, and 2
# when the question itself is malformed: no platform, too many arguments, or a
# platform this has no expectation for. "I have never heard of this cell" is
# not a pass.
set -euo pipefail
export LC_ALL=C

usage() {
  echo "Usage: readelf -d <library> | ... | $(basename "$0") <musl|linux>" >&2
}

if [ $# -ne 1 ]; then
  echo "Error: expected exactly one platform, got $#" >&2
  usage
  exit 2
fi

PLATFORM=$1

case "$PLATFORM" in
  musl)
    WANT='^libc\.musl-.+\.so\.1$'
    WANT_HUMAN='libc.musl-<arch>.so.1'
    OTHER='^libc\.so\.6$'
    OTHER_HUMAN='glibc'
    ;;
  linux)
    WANT='^libc\.so\.6$'
    WANT_HUMAN='libc.so.6'
    OTHER='^libc\.musl-'
    OTHER_HUMAN='musl'
    ;;
  *)
    echo "Error: there is no libc expectation for platform '$PLATFORM'." >&2
    echo "  This knows musl and linux. A cell it has never heard of is a cell it" >&2
    echo "  cannot vouch for, so it refuses rather than waving the run through." >&2
    exit 2
    ;;
esac

NEEDED=$(cat)

echo "the library asks for:"
if [ -z "${NEEDED//[[:space:]]/}" ]; then
  echo "  (nothing)"
else
  echo "  ${NEEDED//$'\n'/$'\n'  }"
fi

# An empty list is the failure that looks most like a pass. A readelf whose
# output never arrived, a sed that matched nothing, a static object with no
# dynamic section: all three print no lines, and none of them is evidence that
# this is a "$PLATFORM" library.
if [ -z "${NEEDED//[[:space:]]/}" ]; then
  echo "Error: the library names no libc at all, so there is nothing here that" >&2
  echo "  says it is a $PLATFORM build. An empty NEEDED list is usually a step" >&2
  echo "  in front of this one that failed without saying so." >&2
  exit 1
fi

if ! printf '%s\n' "$NEEDED" | grep -qE "$WANT"; then
  echo "Error: the library about to be run does not ask for $WANT_HUMAN," >&2
  echo "  so a $PLATFORM consumer run against it would prove nothing about $PLATFORM." >&2
  exit 1
fi

if printf '%s\n' "$NEEDED" | grep -qE "$OTHER"; then
  echo "Error: the library about to be run asks for $OTHER_HUMAN as well." >&2
  echo "  Whatever this is, it is not the $PLATFORM archive this cell built." >&2
  exit 1
fi

echo "libc: $PLATFORM, which is the one this cell is about"
