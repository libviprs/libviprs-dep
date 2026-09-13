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
#      the documented layout: the shared library, the header, both
#      manifests, CHECKSUMS.txt, both licence files and the README.
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
#      from the bytes alone.
#   5. Every library is the architecture the filename claims, is the
#      right kind of object, is not truncated, and exports every entry
#      point the shipped header declares.
#   6. A static library is a real `!<arch>` archive, never a GNU thin
#      one, and `static_certified: true` is backed by an archive that
#      actually links here when this host can link for this target.
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

# Smallest plausible sizes. A real NativeAOT shim is about 9.5 MB shared
# and 44 MB static once the runtime archives are merged in, so anything
# under a tenth of a megabyte is a stub or a truncated copy rather than a
# library.
MIN_SHARED_BYTES=100000
MIN_STATIC_BYTES=100000

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
import sys

root, want_platform, want_cpu, facts_path = sys.argv[1:5]

SHARED_EXT = "dylib" if want_platform == "mac" else "so"
EXPECTED_SHARED = f"lib/libacadsharp_native.{SHARED_EXT}"
EXPECTED_STATIC = "lib/libacadsharp_native.a"

LINKINFO_FIELDS = (
    "schema_version", "artifact_version", "acadsharp_version", "acadsharp_commit",
    "dotnet_sdk", "target", "platform", "cpu", "abi_version", "wire_version",
    "abi_header_sha256", "abi_fingerprint", "shared_library", "static_library",
    "static_certified", "system_libraries", "link_args",
)
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
    for field in LINKINFO_FIELDS:
        if field == "static_library":
            continue
        if field not in link:
            problems.append(f"LINKINFO.json is missing the frozen field {field!r}")
    static_library = link.get("static_library")
    if static_library is not None and not str(static_library).strip():
        problems.append("LINKINFO.json has an empty static_library; it must be absent instead")
    if link.get("static_certified") and static_library is None:
        problems.append(
            "LINKINFO.json says static_certified but carries no static_library"
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
    if static_library is not None:
        if static_library != EXPECTED_STATIC:
            problems.append(
                f"LINKINFO.json static_library is {static_library!r}, "
                f"but the archive ships {EXPECTED_STATIC}"
            )
        elif not os.path.isfile(os.path.join(root, EXPECTED_STATIC)):
            problems.append(
                f"LINKINFO.json static_library {EXPECTED_STATIC} is not in the archive"
            )
    for field in ("system_libraries", "link_args"):
        value = link.get(field)
        if value is not None and not isinstance(value, list):
            problems.append(f"LINKINFO.json field {field!r} is not a list")
    for arg in link.get("link_args") or []:
        if "NativeAOT_StaticInitialization" in str(arg):
            problems.append(
                f"LINKINFO.json link_args carries {arg!r}; that symbol does not exist "
                "in .NET 10 and linking against it fails outright"
            )

    header = os.path.join(root, "include", "viprs_acadsharp.h")
    if os.path.isfile(header):
        with open(header, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
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

    facts["shared_library"] = str(link.get("shared_library", ""))
    facts["static_library"] = str(static_library or "")
    facts["static_certified"] = "1" if link.get("static_certified") else "0"
    facts["system_libraries"] = " ".join(str(x) for x in (link.get("system_libraries") or []))
    facts["link_args"] = " ".join(str(x) for x in (link.get("link_args") or []))

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
    fail "$SHARED_NAME is only $SHARED_SIZE bytes — expected at least $MIN_SHARED_BYTES"
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
# The static archive
# ---------------------------------------------------------------------------

STATIC_LIB="$ROOT/lib/$STATIC_NAME"
if [ -n "$STATIC_REL" ] && [ ! -f "$STATIC_LIB" ]; then
  fail "LINKINFO.json promises $STATIC_REL but $STATIC_NAME is not in the archive"
fi

if [ -f "$STATIC_LIB" ]; then
  echo "---- $EXPECTED_DIR/lib/$STATIC_NAME ----"
  if [ -z "$STATIC_REL" ]; then
    fail "$STATIC_NAME is in the archive but LINKINFO.json does not mention it"
  fi

  MAGIC=$(read_ascii "$STATIC_LIB" 0 8)
  case "$MAGIC" in
    '!<arch>')
      echo "  fat-archive magic ok"
      ;;
    '!<thin>')
      fail "$STATIC_NAME is a GNU thin archive — it only references the build sandbox's objects"
      ;;
    *)
      fail "$STATIC_NAME has unexpected magic '$MAGIC'"
      ;;
  esac

  STATIC_SIZE=$(wc -c < "$STATIC_LIB" | tr -d ' ')
  echo "  size: $STATIC_SIZE bytes"
  if [ "$STATIC_SIZE" -lt "$MIN_STATIC_BYTES" ]; then
    fail "$STATIC_NAME is only $STATIC_SIZE bytes — expected at least $MIN_STATIC_BYTES"
  fi

  if [ "$MAGIC" = '!<arch>' ]; then
    # Walk the ar member headers: 60 bytes each, name[0:16], size[48:58],
    # magic[58:60], data following and padded to an even offset. BSD ar
    # stores any name longer than 16 bytes in the front of the member's
    # own data and writes `#1/<len>` in the name field, so the object
    # starts <len> bytes further in; miss that and every member of a mac
    # archive looks like garbage.
    OFF=8
    MEMBERS=0
    OBJECTS=0
    INDEX_SYMS="$WORK/index-symbols.txt"
    : > "$INDEX_SYMS"
    while [ "$OFF" -lt "$STATIC_SIZE" ] && [ "$MEMBERS" -lt 2000 ]; do
      HDR_MAGIC=$(read_bytes "$STATIC_LIB" $((OFF + 58)) 2)
      if [ "$HDR_MAGIC" != "600a" ]; then
        fail "$STATIC_NAME member header at offset $OFF has bad magic '$HDR_MAGIC' (truncated?)"
        break
      fi
      NAME=$(read_ascii "$STATIC_LIB" "$OFF" 16)
      SIZE=$(read_ascii "$STATIC_LIB" $((OFF + 48)) 10 | tr -d ' ')
      DATA=$((OFF + 60))
      END=$((DATA + SIZE))
      MEMBERS=$((MEMBERS + 1))
      case "$NAME" in
        '#1/'*)
          NLEN=${NAME#\#1/}
          NAME=$(read_ascii "$STATIC_LIB" "$DATA" "$NLEN")
          DATA=$((DATA + NLEN))
          ;;
      esac
      DATA_SIZE=$((END - DATA))
      case "$NAME" in
        '/'|'__.SYMDEF'*|'/SYM64/')
          slice "$STATIC_LIB" "$DATA" "$DATA_SIZE" | tr '\000' '\n' \
            | sed 's/^_//' > "$INDEX_SYMS"
          ;;
        '//')
          : # GNU long-name table, no objects in it
          ;;
        *)
          OBJECTS=$((OBJECTS + 1))
          # Every object, not a sample. A merged NativeAOT archive holds
          # a few hundred, and one of them being for another architecture
          # is exactly the mislabelling a sampled check sails past.
          object_arch_ok "$STATIC_LIB" "$DATA" "$STATIC_NAME member '$NAME'" || true
          ;;
      esac
      OFF=$END
      if [ $((OFF % 2)) -ne 0 ]; then
        OFF=$((OFF + 1))
      fi
    done

    echo "  members: $MEMBERS ($OBJECTS objects)"
    if [ "$OBJECTS" -lt 1 ]; then
      fail "$STATIC_NAME holds no object files"
    fi
    if [ ! -s "$INDEX_SYMS" ]; then
      fail "$STATIC_NAME has no symbol index — consumers would need a manual ranlib"
    else
      if require_symbols "$INDEX_SYMS" "$STATIC_NAME's symbol index"; then
        echo "  symbol index defines every entry point the header declares"
      fi
    fi
  fi

  # `static_certified: true` is a claim that the static smoke linked and
  # ran. When this host can link for this target, hold the claim to it.
  if [ "$STATIC_CERTIFIED" = "1" ]; then
    HOST_CPU=arm64
    case "$(uname -m)" in
      x86_64|amd64) HOST_CPU=x64 ;;
      arm64|aarch64) HOST_CPU=arm64 ;;
      *) HOST_CPU=unknown ;;
    esac
    HOST_PLATFORM=linux
    if [ "$(uname -s)" = "Darwin" ]; then
      HOST_PLATFORM=mac
    elif ldd --version 2>&1 | sed -n 1p | grep -qi musl; then
      HOST_PLATFORM=musl
    fi
    if [ "$HOST_CPU" = "$CPU" ] && [ "$HOST_PLATFORM" = "$PLATFORM" ] \
       && command -v cc >/dev/null 2>&1; then
      cat > "$WORK/probe.c" <<'PROBE'
#include <stdint.h>
extern uint32_t viprs_acad_abi_version(void);
int main(void) { return viprs_acad_abi_version() == 0u; }
PROBE
      SYSLIB_FLAGS=""
      for lib in $(mfact system_libraries); do
        SYSLIB_FLAGS="$SYSLIB_FLAGS -l$lib"
      done
      LINK_ARGS=$(mfact link_args)
      # shellcheck disable=SC2086  # both lists are deliberate word-split arg lists
      if cc "$WORK/probe.c" "$STATIC_LIB" $LINK_ARGS $SYSLIB_FLAGS \
            -o "$WORK/probe" > "$WORK/probe.log" 2>&1; then
        echo "  static_certified holds: the archive links here"
      else
        fail "static_certified is true but the static smoke cannot link the archive:
$(tail -20 "$WORK/probe.log")"
      fi
    else
      echo "  static_certified: not link-tested (this host cannot link for $PLATFORM/$CPU)"
    fi
  fi
fi

if [ "$FAIL" -ne 0 ]; then
  echo ""
  echo "verify_archive.sh: one or more invariants failed (see FAIL lines above)" >&2
  exit 1
fi

echo ""
echo "verify_archive.sh: all invariants hold for $BASE"
