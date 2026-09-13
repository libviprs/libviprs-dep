#!/usr/bin/env bash
# Move <dep>-latest to whatever a release run just published, so a
# consumer tracking the edge of one dependency has one stable URL
# instead of resolving the newest version tag itself.
#
# Usage: publish_latest_tag.sh <dep-name> <tag> <asset-glob>
#
#   dep-name    what to name the moving tag: "<dep-name>-latest"
#   tag         the version tag this run actually published, e.g.
#               acadsharp-3.7.1-viprs.1, pdfium-8054, zstd-1.5.7
#   asset-glob  which of that release's assets to copy, e.g.
#               'acadsharp-*.tgz'
#
# Called once per release workflow (release-acadsharp.yml,
# release-zstd.yml, release.yml), each after every one of its own build
# jobs has succeeded. That gate belongs to the caller, not to this
# script: a "latest" that can point at a partial release is worse than
# no "latest" at all, because a consumer tracking edge would silently
# start missing whichever target happened to fail, and only the caller
# knows its own matrix well enough to say when that is true.
#
# Requires GITHUB_SHA and GH_TOKEN in the environment, which every GitHub
# Actions job already has. Nothing here is repo-specific beyond that, so
# the three release workflows share this one implementation rather than
# three copies that can drift.
set -euo pipefail
shopt -s nullglob

if [ $# -ne 3 ]; then
  echo "Usage: $0 <dep-name> <tag> <asset-glob>" >&2
  exit 2
fi

DEP=$1
TAG=$2
GLOB=$3
LATEST_TAG="${DEP}-latest"

if ! command -v gh >/dev/null 2>&1; then
  echo "Error: gh is required" >&2
  exit 2
fi

if [ -z "${GITHUB_SHA:-}" ]; then
  echo "Error: GITHUB_SHA must be set (this runs inside a GitHub Actions job)" >&2
  exit 2
fi

WORK=$(mktemp -d)
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

echo "Downloading the assets $TAG just published (pattern: $GLOB)..."
gh release download "$TAG" --dir "$WORK" --pattern "$GLOB"

# `gh release download` exits 0 even when the pattern matched nothing in
# the release, and moving "latest" to an empty release is a worse
# outcome than refusing to move it at all.
# shellcheck disable=SC2206  # $GLOB is meant to expand: nullglob turns
# a pattern that matched nothing into zero words instead of the literal
# pattern string, which is the check right below this.
assets=("$WORK"/$GLOB)
if [ ${#assets[@]} -eq 0 ]; then
  echo "Error: no assets matching '$GLOB' were downloaded from $TAG;" \
       "refusing to move $LATEST_TAG" >&2
  exit 1
fi

echo "Moving $LATEST_TAG to $TAG (commit $GITHUB_SHA)..."
# Delete before create, not the other way round: a stale latest release
# left in place would sit beside the new assets rather than being
# replaced by them, and --clobber only overwrites files with the same
# name, which a version bump's new archive names would not be.
gh release delete "$LATEST_TAG" --cleanup-tag --yes 2>/dev/null || true

gh release create "$LATEST_TAG" \
  --target "$GITHUB_SHA" \
  --title "$DEP (edge, moves)" \
  --notes "Floating pointer to the newest $DEP release that published every target. Currently \`$TAG\`. Do not pin a build to this tag: it moves every time a new edge release lands. Pin to \`$TAG\`, or another versioned tag, for something that stays put." \
  "${assets[@]}"

echo "$LATEST_TAG -> $TAG"
