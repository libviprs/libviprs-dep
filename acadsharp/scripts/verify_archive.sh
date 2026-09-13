#!/usr/bin/env bash
# Verify a packaged ACadSharp release archive before it is published.
#
# Runs against the .tgz itself, not the build tree, so it catches anything
# that goes wrong between "the compiler was happy" and "this is what a
# consumer downloads". build_acadsharp.py runs it on every archive it
# produces, and the release workflow runs it again before every upload.
#
# Usage: verify_archive.sh <tgz-path> [platform] [cpu]
#
#   tgz-path   path to acadsharp-<platform>-<cpu>.tgz
#   platform   linux | musl | mac   (inferred from the filename if omitted)
#   cpu        x64 | arm64          (inferred from the filename if omitted)
#
# Invariants enforced:
#   1. Exactly one top-level directory, named after the tarball, holding
#      the documented layout: the shared library, the header, the three
#      frozen contract documents under docs/, both manifests,
#      CHECKSUMS.txt, both licence files and the README. The documents
#      are load-bearing rather than decoration: the wire format and the
#      manifest schema are defined nowhere else, so an archive without
#      them is one a consumer has to read the producer's source to use.
#   2. CHECKSUMS.txt covers every other file in the archive and every
#      digest matches. A file the manifest does not mention is as much a
#      defect as one whose digest is wrong.
#   3. Both manifests parse and carry every frozen field. `acadsharp-rs`
#      reads LINKINFO.json by those names, so a missing one is a
#      downstream break rather than cosmetic.
#   4. `abi_header_sha256` is the hash of the header shipped beside it,
#      and `abi_fingerprint` is that hash's first eight bytes, which is
#      what `viprs_acad_abi_fingerprint()` is defined to return. The live
#      call is the build's smoke; this is the half that can be checked
#      from the bytes alone. `abi_version` and `wire_version` are the
#      numbers the shipped header defines, not whatever the manifest
#      happens to say: a consumer refuses a library whose `abi_version`
#      it does not know, so a manifest that disagrees with the header
#      beside it is a consumer making that decision on a wrong number.
#   5. Every library is the architecture the filename claims, is the
#      right kind of object, is not truncated, and exports every entry
#      point the shipped header declares.
#   6. A static archive is a real `!<arch>` archive, never a GNU thin
#      one, and holds no two members of the same name: duplicates link
#      today only because the linker takes the first definition it finds,
#      and they make `--whole-archive` on that file impossible.
#   7. `static_certified: true` is backed by an archive that links *and
#      runs* here, when this host can build for this target. Linking is
#      not enough: an archive whose runtime initialiser was never forced
#      links perfectly and aborts on the first managed call, so a
#      link-only check cannot tell a working archive from a broken one.
#      The probe allocates before it asks the library anything, and the
#      documented cargo recipe is run against the archive too, because
#      that is the path a consumer actually takes and the one where a
#      requirement expressed as a link argument silently does not
#      arrive. `VIPRS_REQUIRE_LINK_TEST=1` turns "this host cannot build
#      for that target" from a line in a log into a failure, which is
#      what the release and conformance workflows set: both musl cells
#      build on glibc runners, so that line is what both musl archives
#      got, every release, while shipping a cargo recipe nothing had ever
#      run. scripts/verify_archive_matched_host.sh is the way to satisfy
#      it.
#   8. The AC10xx read range in LINKINFO.json is the range the library
#      answers with. This is the one manifest field nothing in the bytes
#      can settle: the header does not state the range, because it is a
#      fact about the backing reader rather than part of the ABI, so a
#      manifest claiming a range the library does not read is well-formed
#      and a consumer would refuse or accept a drawing on the wrong
#      number. The probe in 7 is already linked to the library, so it
#      asks. Shape is checked from the bytes either way: both ends are
#      integers, both are DWG version signatures, and the low end is not
#      above the high one.
#   9. The runtime's bundled llvm-libunwind ships under private names.
#      rustc links a self-contained libunwind.a of its own for every musl
#      target, the two copies define the same fifty-odd `__unw_*` and
#      `libunwind::*` symbols, and the collision is a link error a
#      consumer cannot work around from their side. This one reads the
#      symbol index, so it holds on any host for any target, which is
#      how it covers the cells the consumer link above skips.
#
# Why the binary readers are hand-rolled rather than `nm`: this script has
# to verify a foreign-architecture archive on whatever runner is to hand.
# GNU nm cannot read Mach-O, macOS nm cannot read ELF, and a check that
# skips itself when the tool cannot read the file is not a check. So the
# dynamic symbol table is walked out of the bytes, which works everywhere
# and is the same reasoning zstd/scripts/verify_archive.sh gives for
# reading the machine type by hand.
#
# Why python3 rather than more shell: parsing JSON and computing sha256 in
# POSIX shell means hand-rolling both. python3 is on every runner this
# repo uses and both build drivers are written in it. A missing python3 is
# a refusal, not a skipped check.

set -euo pipefail

# Every byte-level read below treats binary as bytes. Without this, tr and
# grep on a UTF-8 host abort with "Illegal byte sequence" the moment they
# meet a non-UTF-8 byte in an object file.
export LC_ALL=C

usage() {
  echo "Usage: $0 <tgz-path> [platform] [cpu]" >&2
  echo "  platform: linux | musl | mac  (inferred from filename if omitted)" >&2
  echo "  cpu:      x64 | arm64         (inferred from filename if omitted)" >&2
}

if [ $# -lt 1 ]; then
  echo "Error: missing archive path" >&2
  usage
  exit 2
fi

INPUT="$1"
PLATFORM="${2:-}"
CPU="${3:-}"

if [ ! -f "$INPUT" ]; then
  echo "Error: $INPUT does not exist" >&2
  exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 is required (it parses the manifests and the checksums)" >&2
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

case "$PLATFORM" in
  linux|musl|mac) ;;
  *) echo "Error: unknown platform '$PLATFORM'" >&2; exit 2 ;;
esac
case "$CPU" in
  x64|arm64) ;;
  *) echo "Error: unknown cpu '$CPU'" >&2; exit 2 ;;
esac

if [ "$PLATFORM" = "mac" ]; then
  SHARED_EXT=dylib
else
  SHARED_EXT=so
fi

SHARED_NAME="libacadsharp_native.$SHARED_EXT"
STATIC_NAME="libacadsharp_native.a"
STATIC_INIT_NAME="libacadsharp_native_init.a"

# Smallest plausible sizes. A real NativeAOT shim is about 9.5 MB shared
# and 44 MB static once the runtime archives are merged in, so anything
# under a tenth of a megabyte is a stub or a truncated copy rather than a
# library.
MIN_SHARED_BYTES=100000
MIN_STATIC_BYTES=100000
# The initialiser archive is one small object and nothing else. A big one
# means the merge put the runtime in the wrong file.
MAX_STATIC_INIT_BYTES=65536

WORK=$(mktemp -d)
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

FAIL=0
fail() {
  echo "FAIL: $*" >&2
  FAIL=1
}

# ---------------------------------------------------------------------------
# Byte-level readers
# ---------------------------------------------------------------------------

# read_bytes <file> <offset> <count> -> space-free hex, e.g. 7f454c46
read_bytes() {
  dd if="$1" bs=1 skip="$2" count="$3" 2>/dev/null | od -An -v -tx1 | tr -d ' \n'
}

# read_ascii <file> <offset> <count> -> raw text with padding trimmed
read_ascii() {
  dd if="$1" bs=1 skip="$2" count="$3" 2>/dev/null | tr -d '\000' | sed 's/ *$//'
}

# slice <file> <offset> <count> -> raw bytes on stdout, without dd's
# byte-at-a-time cost, which matters for a multi-megabyte symbol table.
# `head` closes the pipe as soon as it has its bytes, which kills `tail`
# with SIGPIPE, which `pipefail` then reports as a failed verification. The
# subshell turns pipefail off for exactly this pipeline rather than for the
# whole script.
slice() {
  ( set +o pipefail; tail -c "+$(( $2 + 1 ))" "$1" 2>/dev/null | head -c "$3" )
}

le16() { local h="$1"; echo $(( 0x${h:2:2}${h:0:2} )); }
be32() { local h="$1"; echo $(( 0x${h:0:2}${h:2:2}${h:4:2}${h:6:2} )); }
be64() {
  local h="$1"
  echo $(( 0x${h:0:2}${h:2:2}${h:4:2}${h:6:2}${h:8:2}${h:10:2}${h:12:2}${h:14:2} ))
}
le32() { local h="$1"; echo $(( 0x${h:6:2}${h:4:2}${h:2:2}${h:0:2} )); }
le64() {
  local h="$1"
  echo $(( 0x${h:14:2}${h:12:2}${h:10:2}${h:8:2}${h:6:2}${h:4:2}${h:2:2}${h:0:2} ))
}

if [ "$CPU" = "x64" ]; then
  WANT_ELF_MACHINE=62          # EM_X86_64
  WANT_MACHO_CPUTYPE=07000001  # CPU_TYPE_X86_64, little-endian on disk
  ARCH_LABEL="x86-64"
else
  WANT_ELF_MACHINE=183         # EM_AARCH64
  WANT_MACHO_CPUTYPE=0c000001  # CPU_TYPE_ARM64, little-endian on disk
  ARCH_LABEL="aarch64"
fi

# object_arch_ok <file> <offset> <label>
object_arch_ok() {
  local file="$1" off="$2" label="$3" magic
  magic=$(read_bytes "$file" "$off" 4)
  case "$magic" in
    7f454c46)
      local machine
      machine=$(le16 "$(read_bytes "$file" $((off + 18)) 2)")
      if [ "$machine" != "$WANT_ELF_MACHINE" ]; then
        fail "$label is ELF machine $machine, expected $WANT_ELF_MACHINE ($ARCH_LABEL)"
        return 1
      fi
      ;;
    cffaedfe)
      local cputype
      cputype=$(read_bytes "$file" $((off + 4)) 4)
      if [ "$cputype" != "$WANT_MACHO_CPUTYPE" ]; then
        fail "$label is Mach-O cputype 0x$cputype, expected 0x$WANT_MACHO_CPUTYPE ($ARCH_LABEL)"
        return 1
      fi
      ;;
    *)
      fail "$label has unrecognised object magic '$magic' (not ELF or 64-bit Mach-O)"
      return 1
      ;;
  esac
  return 0
}

# Turns a hex dump of a symbol table plus a raw string table into the list
# of names the table *defines*. Two formats, one awk: ELF entries are 24
# bytes with st_name at 0 and st_shndx at 6 (SHN_UNDEF, 0, means the
# symbol is imported rather than defined), Mach-O nlist_64 entries are 16
# bytes with n_strx at 0 and n_type at 4 (N_SECT|N_EXT means a defined
# external, and every name carries a leading underscore).
# shellcheck disable=SC2016  # this is an awk program, not a shell string
SYMBOL_AWK='
function hexval(h) { return H[h] }
BEGIN { for (i = 0; i < 256; i++) H[sprintf("%02x", i)] = i }
FNR == NR { names[off] = $0; off += length($0) + 1; next }
{ for (i = 1; i <= NF; i++) b[n++] = $i }
END {
  if (fmt == "elf") { stride = 24 } else { stride = 16 }
  for (s = 0; s + stride <= n; s += stride) {
    nameoff = hexval(b[s + 3]) * 16777216 + hexval(b[s + 2]) * 65536 \
            + hexval(b[s + 1]) * 256 + hexval(b[s])
    if (fmt == "elf") {
      defined = (hexval(b[s + 7]) * 256 + hexval(b[s + 6])) != 0
    } else {
      ntype = hexval(b[s + 4])
      defined = (ntype % 2 == 1) && (int(ntype / 2) % 8 == 7)
    }
    if (defined && (nameoff in names) && names[nameoff] != "") {
      name = names[nameoff]
      sub(/^_/, "", name)
      print name
    }
  }
}'

# elf_defined_symbols <file> <out> -> 0 ok, 1 truncated, 2 no table
elf_defined_symbols() {
  local f="$1" out="$2" size shoff shentsize shnum i off type
  local dynsym_off=0 dynsym_size=0 dynstr_idx=-1 dynstr_off=0 dynstr_size=0
  size=$(wc -c < "$f" | tr -d ' ')
  shoff=$(le64 "$(read_bytes "$f" 40 8)")
  shentsize=$(le16 "$(read_bytes "$f" 58 2)")
  shnum=$(le16 "$(read_bytes "$f" 60 2)")
  if [ "$shoff" -le 0 ] || [ "$shentsize" -lt 64 ] || [ "$shnum" -lt 1 ]; then
    return 2
  fi
  if [ $((shoff + shnum * shentsize)) -gt "$size" ]; then
    return 1
  fi
  i=0
  while [ "$i" -lt "$shnum" ]; do
    off=$((shoff + i * shentsize))
    type=$(le32 "$(read_bytes "$f" $((off + 4)) 4)")
    if [ "$type" = "11" ]; then
      dynsym_off=$(le64 "$(read_bytes "$f" $((off + 24)) 8)")
      dynsym_size=$(le64 "$(read_bytes "$f" $((off + 32)) 8)")
      dynstr_idx=$(le32 "$(read_bytes "$f" $((off + 40)) 4)")
      break
    fi
    i=$((i + 1))
  done
  if [ "$dynstr_idx" -lt 0 ]; then
    return 2
  fi
  off=$((shoff + dynstr_idx * shentsize))
  dynstr_off=$(le64 "$(read_bytes "$f" $((off + 24)) 8)")
  dynstr_size=$(le64 "$(read_bytes "$f" $((off + 32)) 8)")
  if [ $((dynsym_off + dynsym_size)) -gt "$size" ] || \
     [ $((dynstr_off + dynstr_size)) -gt "$size" ]; then
    return 1
  fi
  slice "$f" "$dynstr_off" "$dynstr_size" | tr '\000' '\n' > "$WORK/strtab.txt"
  slice "$f" "$dynsym_off" "$dynsym_size" | od -An -v -tx1 > "$WORK/symtab.hex"
  awk -v fmt=elf "$SYMBOL_AWK" "$WORK/strtab.txt" "$WORK/symtab.hex" > "$out"
  return 0
}

# macho_defined_symbols <file> <out> -> 0 ok, 1 truncated, 2 no table
macho_defined_symbols() {
  local f="$1" out="$2" size ncmds off i cmd cmdsize
  local symoff=0 nsyms=0 stroff=0 strsize=0 found=0
  size=$(wc -c < "$f" | tr -d ' ')
  ncmds=$(le32 "$(read_bytes "$f" 16 4)")
  off=32
  i=0
  while [ "$i" -lt "$ncmds" ]; do
    if [ $((off + 8)) -gt "$size" ]; then
      return 1
    fi
    cmd=$(le32 "$(read_bytes "$f" "$off" 4)")
    cmdsize=$(le32 "$(read_bytes "$f" $((off + 4)) 4)")
    if [ "$cmdsize" -le 0 ]; then
      return 1
    fi
    if [ "$cmd" = "2" ]; then
      symoff=$(le32 "$(read_bytes "$f" $((off + 8)) 4)")
      nsyms=$(le32 "$(read_bytes "$f" $((off + 12)) 4)")
      stroff=$(le32 "$(read_bytes "$f" $((off + 16)) 4)")
      strsize=$(le32 "$(read_bytes "$f" $((off + 20)) 4)")
      found=1
      break
    fi
    off=$((off + cmdsize))
    i=$((i + 1))
  done
  if [ "$found" != "1" ]; then
    return 2
  fi
  if [ $((symoff + nsyms * 16)) -gt "$size" ] || [ $((stroff + strsize)) -gt "$size" ]; then
    return 1
  fi
  slice "$f" "$stroff" "$strsize" | tr '\000' '\n' > "$WORK/strtab.txt"
  slice "$f" "$symoff" $((nsyms * 16)) | od -An -v -tx1 > "$WORK/symtab.hex"
  awk -v fmt=macho "$SYMBOL_AWK" "$WORK/strtab.txt" "$WORK/symtab.hex" > "$out"
  return 0
}

# ---------------------------------------------------------------------------
# Unpack and check the layout
# ---------------------------------------------------------------------------

echo "Verifying $BASE (platform=$PLATFORM cpu=$CPU)"

tar xzf "$INPUT" -C "$WORK/"

EXPECTED_DIR="${BASE%.tgz}"
TOP_COUNT=$(find "$WORK" -mindepth 1 -maxdepth 1 | wc -l | tr -d ' ')
if [ "$TOP_COUNT" != "1" ]; then
  fail "archive has $TOP_COUNT top-level entries, expected exactly 1"
fi

ROOT="$WORK/$EXPECTED_DIR"
if [ ! -d "$ROOT" ]; then
  echo "FAIL: archive does not unpack to a directory named $EXPECTED_DIR" >&2
  echo "  found: $(find "$WORK" -mindepth 1 -maxdepth 1 -exec basename {} \; | tr '\n' ' ')" >&2
  exit 1
fi

for rel in \
  "lib/$SHARED_NAME" \
  "include/viprs_acadsharp.h" \
  "docs/ABI.md" \
  "docs/WIRE.md" \
  "docs/LINKINFO.md" \
  "metadata/LINKINFO.json" \
  "metadata/BUILDINFO.json" \
  "metadata/CHECKSUMS.txt" \
  "LICENSES/ACadSharp-LICENSE" \
  "LICENSES/THIRD_PARTY_NOTICES" \
  "README.md"
do
  if [ ! -e "$ROOT/$rel" ]; then
    fail "missing $EXPECTED_DIR/$rel"
  fi
done

# ---------------------------------------------------------------------------
# Manifests and checksums
# ---------------------------------------------------------------------------

FACTS="$WORK/facts.txt"
: > "$FACTS"

if ! python3 - "$ROOT" "$PLATFORM" "$CPU" "$FACTS" <<'PYEOF' >"$WORK/py.out" 2>&1
import hashlib
import json
import os
import re
import sys

root, want_platform, want_cpu, facts_path = sys.argv[1:5]

SHARED_EXT = "dylib" if want_platform == "mac" else "so"
EXPECTED_SHARED = f"lib/libacadsharp_native.{SHARED_EXT}"
EXPECTED_STATIC = "lib/libacadsharp_native.a"
EXPECTED_STATIC_INIT = "lib/libacadsharp_native_init.a"

LINKINFO_FIELDS = (
    "schema_version", "artifact_version", "acadsharp_version", "acadsharp_commit",
    "dotnet_sdk", "target", "platform", "cpu", "abi_version", "wire_version",
    "abi_header_sha256", "abi_fingerprint", "shared_library", "shared_system_libraries",
    "static_library", "static_init_library", "static_certified",
    "static_system_libraries", "static_link_args",
    "dwg_version_min", "dwg_version_max",
)
# The four digits behind the AC in a drawing's first six bytes. A shape
# check and not a list of the codes this build reads: the range is
# measured off the library rather than known here, and a check that knew
# the answer would be checking itself.
DWG_CODE_MIN, DWG_CODE_MAX = 1000, 1099
# Present together when the static smoke ran and passed, absent together
# otherwise. A field that describes the static link while static_certified
# is false is a field a build.rs author will read as the shared link's.
STATIC_FIELDS = (
    "static_library", "static_init_library", "static_system_libraries",
    "static_link_args",
)
# Any of these in static_link_args is refused. Not because forcing a
# symbol is wrong, but because `cargo:rustc-link-arg` from a dependency's
# build script binds to that package's own targets and never reaches a
# dependent's link line, so a requirement expressed as an argument silently
# does not arrive.
SYMBOL_FORCING_FLAGS = ("-u", "--undefined", "--require-defined")
BUILDINFO_FIELDS = (
    "driver_commit", "builder_image", "dotnet_version", "clang_version",
    "linker_version", "publish_aot", "invariant_globalization", "trimmer_roots",
    "trimmer_single_warn", "aot_warning_count", "built_utc",
)
# The whole matrix. A triple that does not belong to the cell the
# filename names means the archive and the manifest disagree about what
# was built, which is exactly the mislabelling a consumer cannot detect.
TRIPLES = {
    ("linux", "x64"): "x86_64-unknown-linux-gnu",
    ("linux", "arm64"): "aarch64-unknown-linux-gnu",
    ("musl", "x64"): "x86_64-unknown-linux-musl",
    ("musl", "arm64"): "aarch64-unknown-linux-musl",
    ("mac", "arm64"): "aarch64-apple-darwin",
}

problems = []
facts = {}


def load(name):
    path = os.path.join(root, "metadata", name)
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        problems.append(f"metadata/{name} is missing")
    except ValueError as exc:
        problems.append(f"metadata/{name} does not parse as JSON: {exc}")
    return None


link = load("LINKINFO.json")
build = load("BUILDINFO.json")

if link is not None:
    certified = bool(link.get("static_certified"))
    for field in LINKINFO_FIELDS:
        if field in STATIC_FIELDS:
            continue
        if field not in link:
            problems.append(f"LINKINFO.json is missing the frozen field {field!r}")

    static_library = link.get("static_library")
    static_init_library = link.get("static_init_library")
    for field in ("static_library", "static_init_library"):
        value = link.get(field)
        if value is not None and not str(value).strip():
            problems.append(f"LINKINFO.json has an empty {field}; it must be absent instead")

    if certified:
        for field in STATIC_FIELDS:
            if field not in link:
                problems.append(
                    f"LINKINFO.json says static_certified but is missing {field!r}; the "
                    "flag records that the static smoke linked and ran, so every part "
                    "of that link was observed"
                )
    else:
        for field in STATIC_FIELDS:
            if field in link:
                problems.append(
                    f"LINKINFO.json carries {field!r} without static_certified; a static "
                    "link nobody ran is not something to describe"
                )
    if link.get("platform") != want_platform:
        problems.append(
            f"LINKINFO.json says platform {link.get('platform')!r}, "
            f"the archive name says {want_platform!r}"
        )
    if link.get("cpu") != want_cpu:
        problems.append(
            f"LINKINFO.json says cpu {link.get('cpu')!r}, "
            f"the archive name says {want_cpu!r}"
        )
    expected_triple = TRIPLES.get((want_platform, want_cpu))
    if link.get("target") != expected_triple:
        problems.append(
            f"LINKINFO.json says target {link.get('target')!r}, but "
            f"{want_platform}/{want_cpu} is {expected_triple!r}"
        )
    # A manifest pointing at a library that is not the one in the archive
    # is a link the consumer cannot make, and nothing else here would
    # notice: the layout check looks for the conventional name and the
    # manifest is what build.rs actually reads.
    if link.get("shared_library") != EXPECTED_SHARED:
        problems.append(
            f"LINKINFO.json shared_library is {link.get('shared_library')!r}, "
            f"but a {want_platform} archive ships {EXPECTED_SHARED}"
        )
    elif not os.path.isfile(os.path.join(root, EXPECTED_SHARED)):
        problems.append(f"LINKINFO.json shared_library {EXPECTED_SHARED} is not in the archive")
    for field, expected in (
        ("static_library", EXPECTED_STATIC),
        ("static_init_library", EXPECTED_STATIC_INIT),
    ):
        value = link.get(field)
        if value is None:
            continue
        if value != expected:
            problems.append(
                f"LINKINFO.json {field} is {value!r}, but the archive ships {expected}"
            )
        elif not os.path.isfile(os.path.join(root, expected)):
            problems.append(f"LINKINFO.json {field} {expected} is not in the archive")
    for field in ("shared_system_libraries", "static_system_libraries", "static_link_args"):
        value = link.get(field)
        if value is not None and not isinstance(value, list):
            problems.append(f"LINKINFO.json field {field!r} is not a list")
    for arg in link.get("static_link_args") or []:
        if "NativeAOT_StaticInitialization" in str(arg):
            problems.append(
                f"LINKINFO.json static_link_args carries {arg!r}; that symbol does not "
                "exist in .NET 10 and linking against it fails outright"
            )
        elif any(flag in str(arg) for flag in SYMBOL_FORCING_FLAGS):
            problems.append(
                f"LINKINFO.json static_link_args carries {arg!r}, which forces a symbol. "
                "cargo:rustc-link-arg from a dependency's build script binds to that "
                "package's own targets and never reaches a dependent's link line, so the "
                "requirement would silently not arrive. Ship it as static_init_library."
            )

    # The read range, as far as the bytes can tell. Whether the library
    # agrees is asked below, where it is already being linked and run.
    codes = {}
    for field in ("dwg_version_min", "dwg_version_max"):
        value = link.get(field)
        if isinstance(value, bool) or not isinstance(value, int):
            problems.append(
                f"LINKINFO.json {field} is {value!r}. It is an AC10xx code as an "
                "integer, because a consumer compares it against one, and a string "
                "there is a field build.rs reads wrong rather than refuses."
            )
        elif not DWG_CODE_MIN <= value <= DWG_CODE_MAX:
            problems.append(
                f"LINKINFO.json {field} is {value}, which is not a DWG version "
                f"signature: those are the four digits behind the AC in a drawing's "
                f"first six bytes, so {DWG_CODE_MIN} to {DWG_CODE_MAX}."
            )
        else:
            codes[field] = value
    if len(codes) == 2 and codes["dwg_version_min"] > codes["dwg_version_max"]:
        problems.append(
            f"LINKINFO.json dwg_version_min is {codes['dwg_version_min']} and "
            f"dwg_version_max is {codes['dwg_version_max']}, which is a range holding "
            "no drawing at all"
        )

    header = os.path.join(root, "include", "viprs_acadsharp.h")
    if os.path.isfile(header):
        with open(header, "rb") as f:
            header_bytes = f.read()
        digest = hashlib.sha256(header_bytes).hexdigest()
        if link.get("abi_header_sha256") != digest:
            problems.append(
                f"LINKINFO.json abi_header_sha256 is {link.get('abi_header_sha256')!r} "
                f"but the shipped header hashes to {digest}"
            )
        if link.get("abi_fingerprint") != digest[:16]:
            problems.append(
                f"LINKINFO.json abi_fingerprint is {link.get('abi_fingerprint')!r} "
                f"but viprs_acad_abi_fingerprint() is defined as the first eight bytes "
                f"of the header hash, {digest[:16]}"
            )

        # The two version fields, against the header they are shipped
        # beside. They were listed as required and never compared, so a
        # manifest claiming abi_version 7 next to a version-1 header passed
        # every check here, and abi_version is a field a consumer acts on:
        # it is the coarse half of the handshake, and a consumer built for
        # version 1 is supposed to refuse a library reporting 2.
        text = header_bytes.decode("utf-8", "replace")
        for field, macro in (
            ("abi_version", "VIPRS_ACAD_ABI_VERSION"),
            ("wire_version", "VIPRS_ACAD_WIRE_VERSION"),
        ):
            found = re.search(rf"^#define\s+{macro}\s+(\d+)u?\s*$", text, re.M)
            if not found:
                problems.append(
                    f"the shipped header does not define {macro}, so LINKINFO.json's "
                    f"{field} cannot be checked against anything"
                )
                continue
            stated = int(found.group(1))
            if link.get(field) != stated:
                problems.append(
                    f"LINKINFO.json {field} is {link.get(field)!r} but the shipped "
                    f"header defines {macro} as {stated}"
                )

    facts["shared_library"] = str(link.get("shared_library", ""))
    facts["static_library"] = str(static_library or "")
    facts["static_init_library"] = str(static_init_library or "")
    facts["static_certified"] = "1" if certified else "0"
    facts["abi_fingerprint"] = str(link.get("abi_fingerprint", ""))
    facts["dwg_version_min"] = str(codes.get("dwg_version_min", ""))
    facts["dwg_version_max"] = str(codes.get("dwg_version_max", ""))
    facts["static_system_libraries"] = " ".join(
        str(x) for x in (link.get("static_system_libraries") or [])
    )
    facts["static_link_args"] = " ".join(
        str(x) for x in (link.get("static_link_args") or [])
    )

if build is not None:
    for field in BUILDINFO_FIELDS:
        if field not in build:
            problems.append(f"BUILDINFO.json is missing the frozen field {field!r}")

# CHECKSUMS.txt has to cover every other file, and every digest has to be
# the file's. A file nobody listed is as much a defect as a wrong digest:
# it is a file that arrived without anyone measuring it.
checksums = os.path.join(root, "metadata", "CHECKSUMS.txt")
if os.path.isfile(checksums):
    listed = {}
    with open(checksums) as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                problems.append(f"CHECKSUMS.txt line {lineno} is not '<sha256>  <path>'")
                continue
            listed[parts[1].strip()] = parts[0]

    present = set()
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            if rel == os.path.join("metadata", "CHECKSUMS.txt"):
                continue
            present.add(rel)

    for rel, digest in sorted(listed.items()):
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            problems.append(f"CHECKSUMS.txt lists {rel}, which is not in the archive")
            continue
        with open(path, "rb") as f:
            actual = hashlib.sha256(f.read()).hexdigest()
        if actual != digest:
            problems.append(f"CHECKSUMS.txt digest for {rel} does not match the file")

    for rel in sorted(present - set(listed)):
        problems.append(f"{rel} is in the archive but not in CHECKSUMS.txt")

with open(facts_path, "w") as f:
    for key, value in facts.items():
        f.write(f"{key}\t{value}\n")

for problem in problems:
    print(f"FAIL: {problem}", file=sys.stderr)
sys.exit(1 if problems else 0)
PYEOF
then
  cat "$WORK/py.out" >&2
  FAIL=1
else
  cat "$WORK/py.out"
  echo "  manifests parse, carry every frozen field, and CHECKSUMS.txt covers the archive"
fi

mfact() { awk -F'\t' -v k="$1" '$1 == k { print $2; exit }' "$FACTS"; }

STATIC_REL=$(mfact static_library)
STATIC_CERTIFIED=$(mfact static_certified)

# ---------------------------------------------------------------------------
# The entry points the shipped header declares
# ---------------------------------------------------------------------------

ENTRY_POINTS="$WORK/entry-points.txt"
: > "$ENTRY_POINTS"
if [ -f "$ROOT/include/viprs_acadsharp.h" ]; then
  grep -oE '^(uint32_t|uint64_t|void)[[:space:]]+viprs_acad_[a-z0-9_]+[[:space:]]*\(' \
    "$ROOT/include/viprs_acadsharp.h" \
    | grep -oE 'viprs_acad_[a-z0-9_]+' | sort -u > "$ENTRY_POINTS" || true
fi
if [ ! -s "$ENTRY_POINTS" ]; then
  fail "the shipped header declares no entry points, so the export check would pass over anything"
fi

# require_symbols <symbol-file> <label> -> 0 when every entry point is there
require_symbols() {
  local have="$1" label="$2" sym missing=0
  while read -r sym; do
    [ -n "$sym" ] || continue
    if ! grep -qxF "$sym" "$have"; then
      fail "$label does not define $sym, which the shipped header declares"
      missing=1
    fi
  done < "$ENTRY_POINTS"
  return "$missing"
}

# ---------------------------------------------------------------------------
# The shared library
# ---------------------------------------------------------------------------

SHARED_LIB="$ROOT/lib/$SHARED_NAME"
if [ -f "$SHARED_LIB" ]; then
  echo "---- $EXPECTED_DIR/lib/$SHARED_NAME ----"
  SHARED_SIZE=$(wc -c < "$SHARED_LIB" | tr -d ' ')
  echo "  size: $SHARED_SIZE bytes"
  if [ "$SHARED_SIZE" -lt "$MIN_SHARED_BYTES" ]; then
    fail "$SHARED_NAME is only $SHARED_SIZE bytes, expected at least $MIN_SHARED_BYTES"
  fi

  SHARED_MAGIC=$(read_bytes "$SHARED_LIB" 0 4)
  if object_arch_ok "$SHARED_LIB" 0 "$SHARED_NAME"; then
    SYMS="$WORK/shared-symbols.txt"
    : > "$SYMS"
    if [ "$SHARED_MAGIC" = "7f454c46" ]; then
      ETYPE=$(le16 "$(read_bytes "$SHARED_LIB" 16 2)")
      if [ "$ETYPE" != "3" ]; then
        fail "$SHARED_NAME has ELF type $ETYPE, expected 3 (ET_DYN)"
      fi
      set +e
      elf_defined_symbols "$SHARED_LIB" "$SYMS"
      rc=$?
      set -e
      case "$rc" in
        1) fail "$SHARED_NAME is truncated: its section or symbol tables run past the end" ;;
        2) fail "$SHARED_NAME has no dynamic symbol table, so it exports nothing" ;;
        *) if require_symbols "$SYMS" "$SHARED_NAME"; then
             echo "  exports every entry point the header declares"
           fi ;;
      esac
    else
      FILETYPE=$(read_bytes "$SHARED_LIB" 12 4)
      if [ "$FILETYPE" != "06000000" ]; then
        fail "$SHARED_NAME has Mach-O filetype 0x$FILETYPE, expected 0x06000000 (MH_DYLIB)"
      fi
      set +e
      macho_defined_symbols "$SHARED_LIB" "$SYMS"
      rc=$?
      set -e
      case "$rc" in
        1) fail "$SHARED_NAME is truncated: its symbol or string table runs past the end" ;;
        2) fail "$SHARED_NAME has no LC_SYMTAB, so it exports nothing" ;;
        *) if require_symbols "$SYMS" "$SHARED_NAME"; then
             echo "  exports every entry point the header declares"
           fi ;;
      esac
    fi
  fi
fi

# ---------------------------------------------------------------------------
# The static archives
# ---------------------------------------------------------------------------

STATIC_LIB="$ROOT/lib/$STATIC_NAME"
STATIC_INIT_LIB="$ROOT/lib/$STATIC_INIT_NAME"
STATIC_INIT_REL=$(mfact static_init_library)

for pair in "$STATIC_REL|$STATIC_LIB|$STATIC_NAME" \
            "$STATIC_INIT_REL|$STATIC_INIT_LIB|$STATIC_INIT_NAME"
do
  rel=${pair%%|*}
  rest=${pair#*|}
  path=${rest%%|*}
  label=${rest#*|}
  if [ -n "$rel" ] && [ ! -f "$path" ]; then
    fail "LINKINFO.json promises $rel but $label is not in the archive"
  fi
  if [ -z "$rel" ] && [ -f "$path" ]; then
    fail "$label is in the archive but LINKINFO.json does not mention it"
  fi
done

# walk_archive <path> <label> <min-objects> <symbols-out>
#
# Checks the ar magic, walks every member header, refuses a repeated
# member name, checks every object's architecture and leaves the symbol
# index in <symbols-out>. Returns non-zero when the archive is not
# walkable at all.
walk_archive() {
  local lib="$1" label="$2" min_objects="$3" index_out="$4"
  local magic size off members objects names hdr_magic name member_size data end data_size

  magic=$(read_ascii "$lib" 0 8)
  case "$magic" in
    '!<arch>')
      echo "  fat-archive magic ok"
      ;;
    '!<thin>')
      fail "$label is a GNU thin archive, so it only references the build sandbox's objects"
      return 1
      ;;
    *)
      fail "$label has unexpected magic '$magic'"
      return 1
      ;;
  esac

  size=$(wc -c < "$lib" | tr -d ' ')
  echo "  size: $size bytes"

  off=8
  members=0
  objects=0
  names="$WORK/$label-member-names.txt"
  : > "$names"
  : > "$index_out"
  while [ "$off" -lt "$size" ] && [ "$members" -lt 4000 ]; do
    hdr_magic=$(read_bytes "$lib" $((off + 58)) 2)
    if [ "$hdr_magic" != "600a" ]; then
      fail "$label member header at offset $off has bad magic '$hdr_magic' (truncated?)"
      break
    fi
    name=$(read_ascii "$lib" "$off" 16)
    member_size=$(read_ascii "$lib" $((off + 48)) 10 | tr -d ' ')
    data=$((off + 60))
    end=$((data + member_size))
    members=$((members + 1))
    # BSD ar stores any name longer than 16 bytes in the front of the
    # member's own data and writes `#1/<len>` in the name field, so the
    # object starts <len> bytes further in. Miss that and every member of
    # a mac archive looks like garbage.
    case "$name" in
      '#1/'*)
        local nlen=${name#\#1/}
        name=$(read_ascii "$lib" "$data" "$nlen")
        data=$((data + nlen))
        ;;
    esac
    data_size=$((end - data))
    case "$name" in
      '/'|'__.SYMDEF'*|'/SYM64/')
        # The names are at the back of the index, behind a count and an
        # offset table. Reading the whole member and splitting on NUL
        # glues that table's trailing bytes onto the first name, and
        # those bytes are often printable: a one-symbol archive came
        # back as `d_GLOBAL__sub_I_fixture`. So the header is parsed and
        # only the name blob is read.
        local count names_at names_len
        case "$name" in
          '/')
            count=$(be32 "$(read_bytes "$lib" "$data" 4)")
            names_at=$((data + 4 + 4 * count))
            ;;
          '/SYM64/')
            count=$(be64 "$(read_bytes "$lib" "$data" 8)")
            names_at=$((data + 8 + 8 * count))
            ;;
          *)
            # BSD: a little-endian byte count for the ranlib table, the
            # table, then a little-endian byte count for the names.
            count=$(le32 "$(read_bytes "$lib" "$data" 4)")
            names_at=$((data + 4 + count + 4))
            ;;
        esac
        names_len=$((data + data_size - names_at))
        if [ "$names_len" -gt 0 ]; then
          slice "$lib" "$names_at" "$names_len" | tr '\000' '\n' \
            | sed 's/^_//' > "$index_out"
        fi
        ;;
      '//')
        : # GNU long-name table, no objects in it
        ;;
      *)
        objects=$((objects + 1))
        echo "$name" >> "$names"
        # Every object, not a sample. A merged NativeAOT archive holds a
        # few hundred, and one of them being for another architecture is
        # exactly the mislabelling a sampled check sails past.
        object_arch_ok "$lib" "$data" "$label member '$name'" || true
        ;;
    esac
    off=$end
    if [ $((off % 2)) -ne 0 ]; then
      off=$((off + 1))
    fi
  done

  echo "  members: $members ($objects objects)"
  if [ "$objects" -lt "$min_objects" ]; then
    fail "$label holds $objects objects, expected at least $min_objects"
  fi

  # Two members of the same name link today only because the linker takes
  # the first definition it finds, and they make --whole-archive on this
  # file impossible, which is the one thing a consumer may have to do.
  local dupes
  dupes=$(sort "$names" | uniq -d | tr '\n' ' ')
  if [ -n "$dupes" ]; then
    fail "$label holds more than one member called: $dupes"
  fi

  if [ ! -s "$index_out" ]; then
    fail "$label has no symbol index, so consumers would need a manual ranlib"
    return 1
  fi
  return 0
}

if [ -f "$STATIC_LIB" ]; then
  echo "---- $EXPECTED_DIR/lib/$STATIC_NAME ----"
  STATIC_SIZE=$(wc -c < "$STATIC_LIB" | tr -d ' ')
  if [ "$STATIC_SIZE" -lt "$MIN_STATIC_BYTES" ]; then
    fail "$STATIC_NAME is only $STATIC_SIZE bytes, expected at least $MIN_STATIC_BYTES"
  fi
  if walk_archive "$STATIC_LIB" "$STATIC_NAME" 1 "$WORK/static-index.txt"; then
    if require_symbols "$WORK/static-index.txt" "$STATIC_NAME's symbol index"; then
      echo "  symbol index defines every entry point the header declares"
    fi

    # The runtime bundles its own llvm-libunwind, and rustc links a
    # `self-contained/libunwind.a` of its own for every musl target and
    # for no glibc one. Both define `__unw_step`, `unw_local_addr_space`
    # and `libunwind::LocalAddressSpace::sThisAddressSpace`, so a musl
    # cargo link against an archive carrying the original names dies with
    # a screen of `multiple definition of __unw_*`. stage.sh renames ours
    # through scripts/privatise_unwind.sh; this asks the shipped bytes
    # whether it did.
    #
    # Read out of the symbol index, which means no linker, no cargo and
    # no matching architecture, so it holds on an arm64 Mac for an x64
    # musl archive. That is the point: the consumer link further down
    # runs only where the host matches, and a check that runs only there
    # is how this shipped broken twice.
    #
    # Both halves, and the second is the one that earns its place. A
    # check for the old names alone passes for an archive that bundles no
    # libunwind at all, which is also what "the rename step silently
    # stopped running" looks like from here if the runtime ever drops the
    # copy. Requiring a privatised name gives the zero a positive
    # control.
    #
    # `_Unwind_*` is not in either pattern. It is the public personality
    # ABI, the merged archive defines none of them, and if it ever does
    # they are not this problem.
    if [ "$PLATFORM" != "mac" ]; then
      # The index has had one leading underscore stripped, so the
      # patterns are written against the bare name.
      sed 's/^_*//' "$WORK/static-index.txt" > "$WORK/static-index-bare.txt"
      BUNDLED_UNWIND=$(grep -cE '^(unw_|libunwind_|Z[A-Za-z]*N?9libunwind)' \
        "$WORK/static-index-bare.txt" || true)
      PRIVATE_UNWIND=$(grep -cE '^(viprs_unw_|viprs_libunwind_|Z[A-Za-z]*N?15viprs_libunwind)' \
        "$WORK/static-index-bare.txt" || true)
      if [ "$BUNDLED_UNWIND" != "0" ]; then
        fail "$STATIC_NAME defines $BUNDLED_UNWIND libunwind symbols under their original
    names, so the documented cargo recipe cannot link this archive on any musl
    target: rustc brings its own self-contained libunwind.a and every one of
    these collides. stage.sh runs scripts/privatise_unwind.sh for exactly this.
$(sed 's/^_*//' "$WORK/static-index.txt" \
        | grep -E '^(unw_|libunwind_|Z[A-Za-z]*N?9libunwind)' | head -6 | sed 's/^/    /')"
      elif [ "$PRIVATE_UNWIND" = "0" ]; then
        fail "$STATIC_NAME defines no libunwind symbol at all, under either name. Either
    the runtime stopped bundling libunwind, which wants a look, or the rename
    in stage.sh stopped running and the next runtime that bundles one again
    ships an archive no musl consumer can link."
      else
        echo "  the bundled libunwind is private ($PRIVATE_UNWIND symbols), so rustc's own copy"
        echo "  can sit beside it"
      fi
    fi
  fi

  # The runtime's module headers live in `__modules` and the bootstrapper
  # reaches them through `__start___modules` and `__stop___modules`.
  # Nothing relocates against that section, and lld has defaulted to
  # `-z start-stop-gc` since 13, so under `--gc-sections` it collects the
  # section and the encapsulation symbols come out undefined. The build
  # sets SHF_GNU_RETAIN to stop that, and this checks the archive rather
  # than the build script that set it.
  #
  # It runs for every ELF target on every host, which is the point.
  # Reading a section header needs no linker and no matching
  # architecture, so this covers the targets the consumer link below
  # skips because the host cannot build for them. That skip is how both
  # musl archives shipped a static recipe nothing had ever linked.
  if [ "$PLATFORM" != "mac" ]; then
    RETAIN_SECTIONS="$(dirname "$0")/retain_sections.py"
    if [ ! -f "$RETAIN_SECTIONS" ]; then
      fail "retain_sections.py is not beside this script, so __modules cannot be checked"
    elif python3 "$RETAIN_SECTIONS" --check "$STATIC_LIB" \
        __modules __managedcode __unbox > "$WORK/retain.log" 2>&1; then
      echo "  the encapsulation sections are retained, so --gc-sections cannot collect them"
    else
      fail "$STATIC_NAME would fail to link under any linker that defaults to
    -z start-stop-gc, which is every rustc on x86_64-unknown-linux-gnu:
$(sed 's/^/    /' "$WORK/retain.log")"
    fi
  fi
fi

if [ -f "$STATIC_INIT_LIB" ]; then
  echo "---- $EXPECTED_DIR/lib/$STATIC_INIT_NAME ----"
  # One object, and a small one: this archive exists to carry the
  # runtime's static initialiser and nothing else. Anything bigger means
  # the merge put the runtime in the wrong file.
  if walk_archive "$STATIC_INIT_LIB" "$STATIC_INIT_NAME" 1 "$WORK/init-index.txt"; then
    INIT_OBJECTS=$(wc -l < "$WORK/$STATIC_INIT_NAME-member-names.txt" | tr -d ' ')
    if [ "$INIT_OBJECTS" != "1" ]; then
      fail "$STATIC_INIT_NAME holds $INIT_OBJECTS objects, expected exactly 1"
    fi
    INIT_SIZE=$(wc -c < "$STATIC_INIT_LIB" | tr -d ' ')
    if [ "$INIT_SIZE" -gt "$MAX_STATIC_INIT_BYTES" ]; then
      fail "$STATIC_INIT_NAME is $INIT_SIZE bytes, which is too big to be one initialiser"
    fi
    # The whole point of the file: the initialiser has to be reachable by
    # name, because it is what the consumer's --whole-archive pulls in and
    # what nothing else references.
    # The index has had a leading underscore stripped, which is the
    # Mach-O convention; ELF's own name starts with one too.
    if ! grep -qE '^_*GLOBAL__sub_I' "$WORK/init-index.txt"; then
      fail "$STATIC_INIT_NAME defines no _GLOBAL__sub_I* initialiser, so it carries nothing"
    else
      echo "  carries the runtime's static initialiser"
    fi
  fi
fi

# ---------------------------------------------------------------------------
# `static_certified: true` is a claim that the static smoke linked and
# ran. When this host can build for this target, hold the claim to it,
# and to running rather than only linking. An archive without the forced
# initialiser links perfectly and aborts on the first managed call, so a
# link-only check cannot tell a working archive from a broken one.
# ---------------------------------------------------------------------------

if [ "$STATIC_CERTIFIED" = "1" ] && [ -f "$STATIC_LIB" ] && [ -f "$STATIC_INIT_LIB" ]; then
  HOST_CPU=unknown
  case "$(uname -m)" in
    x86_64|amd64) HOST_CPU=x64 ;;
    arm64|aarch64) HOST_CPU=arm64 ;;
  esac
  # musl's ldd exits 1 for --version, and under `pipefail` that made the
  # test below false on every Alpine host, so a musl archive was never
  # link-tested even on the one machine that could do it.
  LIBC_LINE=$(ldd --version 2>&1 | sed -n 1p || true)
  HOST_PLATFORM=linux
  if [ "$(uname -s)" = "Darwin" ]; then
    HOST_PLATFORM=mac
  elif printf '%s' "$LIBC_LINE" | grep -qi musl; then
    HOST_PLATFORM=musl
  fi

  if [ "$HOST_CPU" = "$CPU" ] && [ "$HOST_PLATFORM" = "$PLATFORM" ] \
     && command -v cc >/dev/null 2>&1; then
    # The header the archive ships, not one from this checkout: the
    # capabilities struct below has to be laid out the way the consumer
    # reading this archive would lay it out.
    cat > "$WORK/probe.c" <<'PROBE'
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "viprs_acadsharp.h"

int main(int argc, char **argv)
{
	/* Allocate before asking anything. A managed export that returns a
	   constant can be answered by a binary whose runtime never came up;
	   a megabyte off its heap cannot. */
	char *scratch = (char *)malloc(1 << 20);
	if (!scratch) {
		return 3;
	}
	memset(scratch, 0x5a, 1 << 20);
	if (scratch[4095] != 0x5a) {
		return 4;
	}
	free(scratch);

	char got[32];
	snprintf(got, sizeof(got), "%016llx", (unsigned long long)viprs_acad_abi_fingerprint());
	printf("ABI_VERSION=%u ABI_FINGERPRINT=%s\n", viprs_acad_abi_version(), got);
	if (argc > 1 && strcmp(got, argv[1]) != 0) {
		fprintf(stderr, "probe: library reports %s, manifest says %s\n", got, argv[1]);
		return 5;
	}

	/* And the AC10xx range. Nothing in the bytes can check this one: the
	   header does not state it, so a manifest claiming a range the library
	   does not read is well-formed, and the number a consumer refuses a
	   drawing on is the wrong one. The library is already linked and
	   running here, so ask it. */
	if (argc > 3) {
		struct viprs_acad_capabilities_v1 caps;
		uint64_t needed = 0;
		uint32_t rc;
		unsigned long want_min, want_max;

		memset(&caps, 0, sizeof caps);
		caps.struct_size = (uint32_t)sizeof caps;
		caps.struct_version = 1;
		rc = VIPRS_CAPS_CALL(&caps, NULL, 0, &needed);
		if (rc != 0) {
			fprintf(stderr, "probe: the capabilities sizing call returned %u\n",
				(unsigned)rc);
			return 6;
		}
		printf("DWG_VERSION_MIN=%u DWG_VERSION_MAX=%u\n",
			(unsigned)caps.dwg_version_min, (unsigned)caps.dwg_version_max);
		want_min = strtoul(argv[2], NULL, 10);
		want_max = strtoul(argv[3], NULL, 10);
		if (caps.dwg_version_min != want_min || caps.dwg_version_max != want_max) {
			fprintf(stderr, "probe: LINKINFO.json dwg_version_min %lu and "
				"dwg_version_max %lu, library answers %u and %u\n",
				want_min, want_max, (unsigned)caps.dwg_version_min,
				(unsigned)caps.dwg_version_max);
			return 6;
		}
	}
	return 0;
}
PROBE
    # The capabilities call by whatever name the shipped header declares
    # it, handed to the compiler rather than typed into the probe. This is
    # a static link, so the name is resolved when the probe compiles: a
    # literal here would stop compiling the day the call is renamed, and
    # the verifier would refuse every archive with "the static smoke
    # cannot link the archive", which says nothing about what is wrong.
    # `|| true` because the script runs under `set -e` with pipefail: a
    # header declaring no capabilities call makes grep exit 1, which
    # would abort the whole verification here, before the refusal below
    # ever ran.
    CAPS_CALL=$(grep -E 'capabilities' "$ENTRY_POINTS" | head -1 || true)
    if [ -z "$CAPS_CALL" ]; then
      fail "the shipped header declares no capabilities call, so the probe cannot ask
    the library what it reads"
      CAPS_CALL=viprs_acad_capabilities_v1
    fi

    SYSLIB_FLAGS=""
    for lib in $(mfact static_system_libraries); do
      SYSLIB_FLAGS="$SYSLIB_FLAGS -l$lib"
    done
    LINK_ARGS=$(mfact static_link_args)
    # The documented order: the initialiser archive whole, ahead of the
    # main one. Reversed, the link fails on RhRegisterOSModule.
    # shellcheck disable=SC2086  # both lists are deliberate word-split arg lists
    if cc "$WORK/probe.c" -I"$ROOT/include" "-DVIPRS_CAPS_CALL=$CAPS_CALL" \
          -Wl,--whole-archive "$STATIC_INIT_LIB" -Wl,--no-whole-archive \
          "$STATIC_LIB" $LINK_ARGS $SYSLIB_FLAGS \
          -o "$WORK/probe" > "$WORK/probe.log" 2>&1; then
      set +e
      "$WORK/probe" "$(mfact abi_fingerprint)" \
        "$(mfact dwg_version_min)" "$(mfact dwg_version_max)" > "$WORK/probe.out" 2>&1
      PROBE_STATUS=$?
      set -e
      if [ "$PROBE_STATUS" -eq 0 ]; then
        echo "  static_certified holds: the archive links and runs here"
        echo "  the library reads AC$(mfact dwg_version_min) to AC$(mfact dwg_version_max), \
which is what LINKINFO.json says"
        sed 's/^/    /' "$WORK/probe.out"
      elif [ "$PROBE_STATUS" -eq 6 ]; then
        fail "the library does not read the AC10xx range LINKINFO.json states. The manifest
    is what a consumer branches on, and the header never states the range, so nothing
    in the bytes can catch this:
$(sed 's/^/    /' "$WORK/probe.out")"
      else
        fail "static_certified is true but the linked probe exits $PROBE_STATUS (134 is the
    runtime aborting, which is what an unforced initialiser looks like):
$(sed 's/^/    /' "$WORK/probe.out")"
      fi
    else
      fail "static_certified is true but the static smoke cannot link the archive:
$(tail -20 "$WORK/probe.log")"
    fi

    # The path MANUAL.md actually tells a consumer to take. The C link
    # above proves the archives are linkable; only this proves the
    # directives a dependency's build script emits reach the binary that
    # needs them, which is where the requirement went missing once.
    CONSUMER_SMOKE="$(dirname "$0")/link_consumer_smoke.sh"
    if [ -x "$CONSUMER_SMOKE" ] && command -v cargo >/dev/null 2>&1; then
      if bash "$CONSUMER_SMOKE" "$ROOT" > "$WORK/consumer.log" 2>&1; then
        echo "  the documented cargo recipe links and runs against this archive"
      else
        fail "the documented cargo recipe fails against this archive:
$(tail -25 "$WORK/consumer.log")"
      fi
    elif [ "${VIPRS_REQUIRE_LINK_TEST:-0}" = "1" ]; then
      fail "VIPRS_REQUIRE_LINK_TEST is set and there is no cargo here, so the one
    recipe MANUAL.md tells a consumer to use went unrun."
    else
      echo "  cargo recipe: not run (no cargo on this host)"
    fi
  elif [ "${VIPRS_REQUIRE_LINK_TEST:-0}" = "1" ]; then
    fail "VIPRS_REQUIRE_LINK_TEST is set, this host is $HOST_PLATFORM/$HOST_CPU and the
    archive is $PLATFORM/$CPU, so nothing linked it. Run it through
    scripts/verify_archive_matched_host.sh, which puts a musl archive in front
    of a musl host rather than writing a line about it into a log."
  else
    echo "  static_certified: not link-tested (this host cannot build for $PLATFORM/$CPU)"
    echo "  set VIPRS_REQUIRE_LINK_TEST=1 to make that a failure instead of this line"
  fi
fi


if [ "$FAIL" -ne 0 ]; then
  echo ""
  echo "verify_archive.sh: one or more invariants failed (see FAIL lines above)" >&2
  exit 1
fi

echo ""
echo "verify_archive.sh: all invariants hold for $BASE"
