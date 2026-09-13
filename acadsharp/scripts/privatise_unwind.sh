#!/bin/sh
# Give the runtime's bundled libunwind private names, so a consumer's own
# copy can live in the same binary.
#
# Usage: privatise_unwind.sh <archive> [<archive>...]
#
# NativeAOT statically links its own llvm-libunwind into the runtime
# archives, and rustc links a `self-contained/libunwind.a` of its own for
# every musl target and for no glibc one. Both define `__unw_step`,
# `unw_local_addr_space`, `libunwind::LocalAddressSpace::sThisAddressSpace`
# and about fifty more, so the documented cargo recipe against a musl
# archive dies at the link with a screen of `multiple definition of
# __unw_*`. Nothing about `__modules` is involved and neither is lld: the
# linker is whatever `cc` drives, GNU ld on Alpine.
#
# The two obvious fixes are measured dead. Dropping the runtime's
# libunwind member from the merge breaks the C recipe too, because the
# runtime reaches into that copy's C++ internals
# (`libunwind::LocalAddressSpace::sThisAddressSpace` from
# `UnixNativeCodeManager` and `RhRegisterOSModule`) and not only its
# `__unw_*` C API. And no consumer-side flag helps: `-C
# link-self-contained=no` breaks the build script's own link and `-C
# link-self-contained=-unwind` is rejected outright.
#
# So the copy that ships here gets renamed instead. Both copies then sit
# in the binary under different names, each self-consistent: the runtime
# unwinds through ours, Rust panics unwind through rustc's.
#
# Renaming the *archive* rather than its members is the whole trick.
# `objcopy --redefine-syms` rewrites a definition and every reference to
# it by name, so applying it to the merged archive moves all of them
# together, in one pass, and no member is left calling a name that is no
# longer there. It preserves member order, which is load-bearing here
# because the linker emits `.init_array` in the order it pulls members,
# and it preserves `SHF_GNU_RETAIN`, which is what keeps `__modules`
# alive under `--gc-sections`. Both are measured, and both are checked
# by verify_archive.sh against the shipped bytes.
#
# `_Unwind_*` is deliberately not in the selection. That is the public
# personality ABI a C++ or Rust landing pad calls, it is the name a
# compiler emits rather than one this code chooses, and renaming it would
# split the language runtimes instead of the unwinder. Measured, the
# merged archive defines none of them anyway: every collision is an
# internal `__unw_*`, `unw_*` or `libunwind::*` name.
set -eu
export LC_ALL=C

usage() {
  echo "Usage: $0 <archive> [<archive>...]" >&2
}

if [ $# -lt 1 ]; then
  echo "Error: no archive given" >&2
  usage
  exit 2
fi

for tool in nm objcopy; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Error: $tool is required and is not on PATH" >&2
    exit 2
  fi
done

for archive in "$@"; do
  if [ ! -f "$archive" ]; then
    echo "Error: $archive is not a file" >&2
    exit 2
  fi
done

WORK=$(mktemp -d)
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

# Chosen by name, not by which member they came from. The runtime's
# libunwind arrives spread over five objects whose names change with
# every runtime pack layout, and one that is missed is a collision that
# ships. A name is the thing the linker actually compares.
#
# `_Z[A-Za-z]*N?9libunwind` catches every mangling of a name in the
# libunwind namespace, so typeinfo and vtables travel with the functions.
# The `9` is the length prefix of `libunwind`, and requiring it right
# after the `_Z<letters>` prefix is what stops the rule mistaking the
# prefix of a nineteen-character identifier for it.
SELECT='^(__unw_|unw_|__libunwind_|_Z[A-Za-z]*N?9libunwind)'

: > "$WORK/defined.txt"
for archive in "$@"; do
  nm --defined-only -g "$archive" 2>/dev/null \
    | awk 'NF >= 3 { print $3 }' >> "$WORK/defined.txt"
done

sort -u "$WORK/defined.txt" | grep -E "$SELECT" > "$WORK/selected.txt" || true
COUNT=$(wc -l < "$WORK/selected.txt" | tr -d ' ')

if [ "$COUNT" -eq 0 ]; then
  # Not an error here. It is one downstream: verify_archive.sh asks the
  # shipped archive for a privatised name and refuses one that has none,
  # so a runtime that stops bundling libunwind, or a change that makes
  # this selection stop matching, comes back as a red release rather than
  # as an archive that quietly cannot be linked from cargo.
  echo "privatise_unwind: nothing matched, so this archive bundles no libunwind"
  exit 0
fi

# `libunwind` is nine characters and `viprs_libunwind` is fifteen, so the
# length prefix moves with the name and the result still demangles.
awk '{
  old = $0
  new = old
  if (new ~ /^_Z[A-Za-z]*N?9libunwind/) {
    sub(/9libunwind/, "15viprs_libunwind", new)
  } else if (new ~ /^__/) {
    sub(/^__/, "__viprs_", new)
  } else {
    new = "viprs_" new
  }
  print old, new
}' "$WORK/selected.txt" > "$WORK/redefine.txt"

for archive in "$@"; do
  objcopy --redefine-syms="$WORK/redefine.txt" "$archive" "$archive.privatised"
  mv "$archive.privatised" "$archive"
done

# Asked of the result rather than assumed from the exit status. objcopy
# reports success for a map whose left-hand names it never found, which
# is what a silently-changed nm output format looks like.
: > "$WORK/after.txt"
for archive in "$@"; do
  nm --defined-only -g "$archive" 2>/dev/null \
    | awk 'NF >= 3 { print $3 }' >> "$WORK/after.txt"
done
LEFT=$(sort -u "$WORK/after.txt" | grep -cE "$SELECT" || true)
if [ "$LEFT" != "0" ]; then
  echo "Error: $LEFT libunwind symbols survived the rename" >&2
  sort -u "$WORK/after.txt" | grep -E "$SELECT" | sed 's/^/  /' >&2
  exit 1
fi

echo "privatise_unwind: renamed $COUNT libunwind symbols in $# archive(s)"
