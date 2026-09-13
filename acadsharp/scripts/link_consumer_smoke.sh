#!/usr/bin/env bash
# Link an unpacked acadsharp archive the way MANUAL.md tells a consumer to,
# through a two-crate cargo workspace, and run the binary.
#
# Usage: link_consumer_smoke.sh <unpacked-archive-directory>
#
# This exists because the C smoke proves the wrong thing. Compiling
# `cc probe.c ... libacadsharp_native.a` shows the archives are linkable;
# it says nothing about whether the directives a `-sys` crate's build
# script emits reach the binary that depends on it. They do not all
# propagate: `cargo:rustc-link-search` and `cargo:rustc-link-lib` travel
# to a dependent's link line and `cargo:rustc-link-arg` does not, so a
# requirement written as an argument stays green in the `-sys` crate's own
# tests and is missing everywhere else. That gap is how a manifest froze
# with an unusable recipe in it once.
#
# The workspace has no dependencies, so this runs offline.

set -euo pipefail
export LC_ALL=C

usage() {
  echo "Usage: $0 <unpacked-archive-directory>" >&2
}

if [ $# -lt 1 ]; then
  echo "Error: missing archive directory" >&2
  usage
  exit 2
fi

ROOT=$1
if [ ! -d "$ROOT" ]; then
  echo "Error: $ROOT is not a directory" >&2
  exit 2
fi
ROOT=$(cd "$ROOT" && pwd)

MANIFEST="$ROOT/metadata/LINKINFO.json"
if [ ! -f "$MANIFEST" ]; then
  echo "Error: $MANIFEST does not exist, so this is not an unpacked archive" >&2
  exit 2
fi

for tool in cargo python3; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Error: $tool is required and is not on PATH" >&2
    exit 2
  fi
done

WORKSPACE="$(cd "$(dirname "$0")/../tests/link_consumer" && pwd)"

WORK=$(mktemp -d)
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

# Copied rather than built in place, so a run never leaves a target/ or a
# Cargo.lock in the checkout.
cp -R "$WORKSPACE/." "$WORK/ws"

EXPECTED=$(python3 -c '
import json, sys
with open(sys.argv[1]) as f:
    print(json.load(f)["abi_fingerprint"])
' "$MANIFEST")

echo "Linking $ROOT through the documented cargo recipe"
set +e
ACADSHARP_ARCHIVE="$ROOT" \
  CARGO_TARGET_DIR="$WORK/target" \
  cargo run --offline --quiet --manifest-path "$WORK/ws/Cargo.toml" -p consumer \
  > "$WORK/out.txt" 2> "$WORK/err.txt"
STATUS=$?
set -e

if [ "$STATUS" -ne 0 ]; then
  echo "FAIL: the consumer binary exited $STATUS" >&2
  echo "  (134 is the runtime aborting, which is what an unforced initialiser looks like)" >&2
  sed 's/^/  /' "$WORK/err.txt" >&2
  sed 's/^/  /' "$WORK/out.txt" >&2
  exit 1
fi

sed 's/^/  /' "$WORK/out.txt"

GOT=$(sed -n 's/^ABI_FINGERPRINT=//p' "$WORK/out.txt" | head -1)
if [ "$GOT" != "$EXPECTED" ]; then
  echo "FAIL: the linked library reports fingerprint '$GOT', LINKINFO.json says '$EXPECTED'" >&2
  exit 1
fi

echo "link_consumer_smoke.sh: the documented recipe links and runs against $(basename "$ROOT")"
