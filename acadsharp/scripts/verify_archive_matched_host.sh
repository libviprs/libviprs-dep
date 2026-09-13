#!/usr/bin/env bash
# Run verify_archive.sh on a host whose libc matches the archive's.
#
# Usage: verify_archive_matched_host.sh <tgz-path> [platform] [cpu]
#
# verify_archive.sh holds `static_certified: true` to a link that runs
# "when this host can build for this target", and otherwise prints
#
#   static_certified: not link-tested (this host cannot build for musl/x64)
#
# into a log and exits 0. Every musl cell in the release workflow builds
# on a glibc runner, so that line is what both musl archives got, every
# release, and they shipped a documented cargo recipe nothing had ever
# run. It did not work. The skip is not the bug on its own: the bug is a
# skip with no way to say "there was supposed to be a link here".
#
# So this picks the host instead of accepting whatever host it is on. A
# musl archive on a glibc runner is verified inside an Alpine container of
# the same architecture, where the script's own musl detection does the
# right thing and cc, cargo and rustc are all musl-native. Anything else
# runs straight through. Either way VIPRS_REQUIRE_LINK_TEST is exported,
# which turns that skip line into a failure, so a cell that ends up on the
# wrong host says so rather than going green.
#
# The architecture is never emulated. A cross-architecture link is the one
# ADR 0001 measured producing an object file and then failing at the
# native link, and a link test under qemu answers a question nobody asked.
# A cpu that does not match the host is a refusal.
set -euo pipefail
export LC_ALL=C

usage() {
  echo "Usage: $0 <tgz-path> [platform] [cpu]" >&2
}

if [ $# -lt 1 ]; then
  echo "Error: missing archive path" >&2
  usage
  exit 2
fi

INPUT=$1
PLATFORM=${2:-}
CPU=${3:-}

if [ ! -f "$INPUT" ]; then
  echo "Error: $INPUT does not exist" >&2
  exit 2
fi

BASE=$(basename "$INPUT")
if [ -z "$PLATFORM" ]; then
  case "$BASE" in
    acadsharp-musl-*)  PLATFORM=musl ;;
    acadsharp-linux-*) PLATFORM=linux ;;
    acadsharp-mac-*)   PLATFORM=mac ;;
    *) echo "Error: cannot infer platform from $BASE (pass it explicitly)" >&2; exit 2 ;;
  esac
fi
if [ -z "$CPU" ]; then
  case "$BASE" in
    *-x64.tgz)   CPU=x64 ;;
    *-arm64.tgz) CPU=arm64 ;;
    *) echo "Error: cannot infer cpu from $BASE (pass it explicitly)" >&2; exit 2 ;;
  esac
fi

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
VERIFY="$HERE/verify_archive.sh"
ARCHIVE_DIR=$(cd "$(dirname "$INPUT")" && pwd)

# The same three questions verify_archive.sh asks itself, asked here so
# the answer decides where it runs rather than what it skips. musl's ldd
# exits 1 for --version, so the pipeline has to tolerate that.
HOST_CPU=unknown
case "$(uname -m)" in
  x86_64|amd64) HOST_CPU=x64 ;;
  arm64|aarch64) HOST_CPU=arm64 ;;
esac
LIBC_LINE=$(ldd --version 2>&1 | sed -n 1p || true)
HOST_PLATFORM=linux
if [ "$(uname -s)" = "Darwin" ]; then
  HOST_PLATFORM=mac
elif printf '%s' "$LIBC_LINE" | grep -qi musl; then
  HOST_PLATFORM=musl
fi

export VIPRS_REQUIRE_LINK_TEST=1

if [ "$HOST_PLATFORM" = "$PLATFORM" ] && [ "$HOST_CPU" = "$CPU" ]; then
  echo "verify_archive_matched_host.sh: this host is $HOST_PLATFORM/$HOST_CPU, running here"
  exec bash "$VERIFY" "$INPUT" "$PLATFORM" "$CPU"
fi

if [ "$HOST_CPU" != "$CPU" ]; then
  echo "Error: this host is $HOST_CPU and the archive is $CPU." >&2
  echo "  Verify it on a $CPU runner. Emulating the link proves nothing." >&2
  exit 2
fi

if [ "$PLATFORM" != "musl" ]; then
  echo "Error: no container is defined for platform '$PLATFORM'." >&2
  echo "  Verify a $PLATFORM archive on a $PLATFORM host." >&2
  exit 2
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: a musl archive needs a musl host and there is no docker here to make one." >&2
  exit 2
fi

# Pinned, and the same rust the conformance workflow runs. An image that
# floats picks up a new rustc the day it is published, and whether rustc
# links its self-contained libunwind is exactly what this is measuring.
IMAGE=${VIPRS_MUSL_VERIFY_IMAGE:-rust:1.98.1-alpine}
case "$CPU" in
  x64)   DOCKER_PLATFORM=linux/amd64 ;;
  arm64) DOCKER_PLATFORM=linux/arm64 ;;
esac

echo "verify_archive_matched_host.sh: this host is $HOST_PLATFORM/$HOST_CPU and the archive is"
echo "  $PLATFORM/$CPU, so verifying inside $IMAGE ($DOCKER_PLATFORM)"

# --platform explicitly, in a repo whose developers are on arm64 Macs
# where DOCKER_DEFAULT_PLATFORM is often linux/amd64: an architecture
# result from an unpinned run means nothing.
#
# The checkout goes in read-only. verify_archive.sh unpacks into a mktemp
# of its own and link_consumer_smoke.sh copies the cargo workspace out
# before building it, so nothing here needs to write into the tree, and a
# run that leaves a target/ or a Cargo.lock behind is one that changes
# what the next check sees.
exec docker run --rm --platform "$DOCKER_PLATFORM" \
  -v "$REPO":/repo:ro \
  -v "$ARCHIVE_DIR":/archive:ro \
  -e VIPRS_REQUIRE_LINK_TEST=1 \
  "$IMAGE" sh -c '
    set -eu
    apk add --no-cache bash build-base binutils python3 file >/dev/null
    exec bash /repo/acadsharp/scripts/verify_archive.sh "/archive/$1" "$2" "$3"
  ' _ "$BASE" "$PLATFORM" "$CPU"
