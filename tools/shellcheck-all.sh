#!/usr/bin/env bash
# Shellcheck every tracked shell script in this repo.
#
# Why this is a script and not an inline block in ci.yml: the shared
# pre-commit hook (libviprs-tests/tools/install-hooks.sh) mirrors this
# repo's CI command for command, and its guard refuses to stand in for a
# multi-line `run:` block. Such a step has to be exempted instead, and an
# exempted step is one the local hook does not run. So an inline block
# would not make the hook stricter, it would quietly drop shellcheck from
# it, which is the same false green this repo's own installer used to
# have (libviprs-dep#36).
#
# Keeping it here also puts the discovery logic under shellcheck itself,
# since this file is a tracked *.sh and so is found by its own glob.
set -euo pipefail

cd "$(dirname "$0")/.."

# Discovered, never listed: a dependency directory added later is covered
# the day it lands rather than the day someone remembers this file.
scripts=$(git ls-files '*.sh')

# `xargs` with no input runs shellcheck over zero files and exits 0, so a
# discovery regression would report a pass for a check that inspected
# nothing. Refuse instead.
if [ -z "$scripts" ]; then
    echo "no shell scripts found, so script discovery is broken" >&2
    exit 1
fi

echo "$scripts"
echo "$scripts" | xargs shellcheck
