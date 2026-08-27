#!/usr/bin/env bash
# Verify a packaged zstd release archive before it is published.
#
# Runs against the .tgz itself (not the build tree), so it catches
# anything that goes wrong between "the compiler was happy" and "this is
# what consumers download". build_zstd.py runs it on every archive it
# produces, and CI runs it again on the artifacts.
#
# Usage: verify_archive.sh <tgz-path> [platform] [cpu]
#
#   tgz-path   path to zstd-<platform>-<cpu>.tgz
#   platform   linux | musl | mac   (inferred from the filename if omitted)
#   cpu        x64 | arm64          (inferred from the filename if omitted)
#
# Invariants enforced:
#   1. The archive holds exactly one top-level directory, named to match
#      the tarball, containing the documented layout: both libraries,
#      the public headers, the pkg-config file, cmake-args.txt, LICENSE.
#   2. libzstd.a is a real (fat) archive — `!<arch>`, never `!<thin>` —
#      of a plausible size, carrying a symbol index that defines the
#      public API. A thin archive references object files by build-time
#      path and is unlinkable once unpacked.
#   3. Every object inside libzstd.a, and the shared library, is built
#      for the architecture the filename claims. Machine type is read
#      out of the ELF / Mach-O headers directly, so this works for
#      foreign-arch archives on a host with no cross toolchain.
#   4. The shared library is a shared object (ET_DYN / MH_DYLIB), not an
#      executable or a stray relocatable object, and exports the API.
#   5. libzstd.pc is relocatable — its prefix is expressed relative to
#      ${pcfiledir}, so pointing PKG_CONFIG_PATH at the unpacked archive
#      works from wherever it was unpacked.

set -euo pipefail

# Every byte-level read below treats binary as bytes. Without this, tr
# and grep on a UTF-8 macOS host abort with "Illegal byte sequence" the
# moment they meet a non-UTF-8 byte in an object file.
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

BASE=$(basename "$INPUT")

if [ -z "$PLATFORM" ]; then
  case "$BASE" in
    zstd-musl-*)  PLATFORM=musl ;;
    zstd-linux-*) PLATFORM=linux ;;
    zstd-mac-*)   PLATFORM=mac ;;
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

# Smallest plausible sizes. A real libzstd.a is ~1 MB and the shared
# library ~700 KB; anything under a tenth of that is a stub, an empty
# `ar` archive, or a truncated copy rather than a library.
MIN_STATIC_BYTES=100000
MIN_SHARED_BYTES=100000
MIN_ARCHIVE_MEMBERS=10

WORK=$(mktemp -d)
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

FAIL=0
fail() {
  echo "FAIL: $*" >&2
  FAIL=1
}

# ---------------------------------------------------------------------------
# Byte-level readers. Deliberately toolchain-free: verifying an arm64
# archive on an amd64 CI runner must not depend on a cross binutils
# being installed, and macOS's nm cannot read ELF at all.
# ---------------------------------------------------------------------------

# read_bytes <file> <offset> <count> -> space-free hex, e.g. 7f454c46
read_bytes() {
  dd if="$1" bs=1 skip="$2" count="$3" 2>/dev/null | od -An -v -tx1 | tr -d ' \n'
}

# read_ascii <file> <offset> <count> -> raw text with padding trimmed
read_ascii() {
  dd if="$1" bs=1 skip="$2" count="$3" 2>/dev/null | tr -d '\000' | sed 's/ *$//'
}

# le16 <hex-of-2-bytes> -> decimal
le16() {
  local hex="$1"
  echo $(( 0x${hex:2:2}${hex:0:2} ))
}

# Expected ELF e_machine / Mach-O cputype for the cpu in the filename.
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
# Checks the object beginning at <offset> is for the expected machine.
object_arch_ok() {
  local file="$1" off="$2" label="$3" magic
  magic=$(read_bytes "$file" "$off" 4)
  case "$magic" in
    7f454c46)  # \x7fELF
      local machine
      machine=$(le16 "$(read_bytes "$file" $((off + 18)) 2)")
      if [ "$machine" != "$WANT_ELF_MACHINE" ]; then
        fail "$label is ELF machine $machine, expected $WANT_ELF_MACHINE ($ARCH_LABEL)"
        return 1
      fi
      ;;
    cffaedfe)  # MH_MAGIC_64
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

# ---------------------------------------------------------------------------
# Unpack and check the layout
# ---------------------------------------------------------------------------

echo "Verifying $BASE (platform=$PLATFORM cpu=$CPU)"

tar xzf "$INPUT" -C "$WORK"

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
  "lib/libzstd.a" \
  "lib/libzstd.$SHARED_EXT" \
  "lib/pkgconfig/libzstd.pc" \
  "include/zstd.h" \
  "include/zstd_errors.h" \
  "include/zdict.h" \
  "cmake-args.txt" \
  "LICENSE"
do
  if [ ! -e "$ROOT/$rel" ]; then
    fail "missing $EXPECTED_DIR/$rel"
  fi
done

STATIC_LIB="$ROOT/lib/libzstd.a"
SHARED_LIB="$ROOT/lib/libzstd.$SHARED_EXT"

# ---------------------------------------------------------------------------
# Static archive: fat, non-trivial, indexed, and all-one-architecture
# ---------------------------------------------------------------------------

if [ -f "$STATIC_LIB" ]; then
  echo "---- $EXPECTED_DIR/lib/libzstd.a ----"
  MAGIC=$(read_ascii "$STATIC_LIB" 0 8)
  case "$MAGIC" in
    '!<arch>')
      echo "  fat-archive magic ok"
      ;;
    '!<thin>')
      fail "libzstd.a is a GNU thin archive — it only references the build sandbox's .o files"
      ;;
    *)
      fail "libzstd.a has unexpected magic '$MAGIC'"
      ;;
  esac

  STATIC_SIZE=$(wc -c < "$STATIC_LIB" | tr -d ' ')
  echo "  size: $STATIC_SIZE bytes"
  if [ "$STATIC_SIZE" -lt "$MIN_STATIC_BYTES" ]; then
    fail "libzstd.a is only $STATIC_SIZE bytes — expected at least $MIN_STATIC_BYTES"
  fi

  # Walk the `ar` member headers. Each is 60 bytes: name[0:16],
  # size[48:58], magic[58:60]; data follows, padded to an even offset.
  OFF=8
  MEMBERS=0
  OBJECTS=0
  INDEX_SYMS="$WORK/index-symbols.txt"
  : > "$INDEX_SYMS"
  while [ "$OFF" -lt "$STATIC_SIZE" ] && [ "$MEMBERS" -lt 500 ]; do
    HDR_MAGIC=$(read_bytes "$STATIC_LIB" $((OFF + 58)) 2)
    if [ "$HDR_MAGIC" != "600a" ]; then
      fail "ar member header at offset $OFF has bad magic '$HDR_MAGIC' (archive truncated?)"
      break
    fi
    NAME=$(read_ascii "$STATIC_LIB" "$OFF" 16)
    SIZE=$(read_ascii "$STATIC_LIB" $((OFF + 48)) 10 | tr -d ' ')
    DATA=$((OFF + 60))
    END=$((DATA + SIZE))
    MEMBERS=$((MEMBERS + 1))

    # BSD ar (what macOS ships) stores any name longer than 16 bytes in
    # the front of the member's own data and writes `#1/<len>` in the
    # name field, so the object itself starts <len> bytes further in.
    # Miss this and every member of a mac archive looks like garbage.
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
        # The symbol index. Its tail is the NUL-separated list of every
        # symbol the archive *defines*, which is exactly what we want to
        # assert the public API against — and it doubles as proof the
        # archive was ranlib'd.
        dd if="$STATIC_LIB" bs=1 skip="$DATA" count="$DATA_SIZE" 2>/dev/null \
          | tr '\000' '\n' > "$INDEX_SYMS"
        ;;
      '//')
        : # GNU long-name table, no objects in it
        ;;
      *)
        OBJECTS=$((OBJECTS + 1))
        object_arch_ok "$STATIC_LIB" "$DATA" "libzstd.a member '$NAME'" || true
        ;;
    esac

    OFF=$END
    if [ $((OFF % 2)) -ne 0 ]; then
      OFF=$((OFF + 1))
    fi
  done

  echo "  members: $MEMBERS ($OBJECTS objects)"
  if [ "$OBJECTS" -lt "$MIN_ARCHIVE_MEMBERS" ]; then
    fail "libzstd.a holds only $OBJECTS objects — expected at least $MIN_ARCHIVE_MEMBERS"
  fi

  if [ ! -s "$INDEX_SYMS" ]; then
    fail "libzstd.a has no symbol index — consumers would need a manual ranlib"
  else
    for sym in ZSTD_compress ZSTD_decompress ZSTD_versionNumber ZSTD_createCCtx \
               ZDICT_trainFromBuffer; do
      if ! grep -qE "^_?${sym}$" "$INDEX_SYMS"; then
        fail "libzstd.a's symbol index does not define $sym"
      fi
    done
    echo "  symbol index defines the public API"
  fi
fi

# ---------------------------------------------------------------------------
# Shared library: right kind of object, right architecture, exports the API
# ---------------------------------------------------------------------------

if [ -e "$SHARED_LIB" ]; then
  echo "---- $EXPECTED_DIR/lib/libzstd.$SHARED_EXT ----"
  REAL_SHARED=$SHARED_LIB
  while [ -L "$REAL_SHARED" ]; do
    LINK_TARGET=$(readlink "$REAL_SHARED")
    case "$LINK_TARGET" in
      /*) REAL_SHARED=$LINK_TARGET ;;
      *)  REAL_SHARED=$(dirname "$REAL_SHARED")/$LINK_TARGET ;;
    esac
  done

  if [ ! -f "$REAL_SHARED" ]; then
    fail "libzstd.$SHARED_EXT does not resolve to a real file (dangling symlink)"
  else
    echo "  resolves to $(basename "$REAL_SHARED")"
    SHARED_SIZE=$(wc -c < "$REAL_SHARED" | tr -d ' ')
    echo "  size: $SHARED_SIZE bytes"
    if [ "$SHARED_SIZE" -lt "$MIN_SHARED_BYTES" ]; then
      fail "libzstd.$SHARED_EXT is only $SHARED_SIZE bytes — expected at least $MIN_SHARED_BYTES"
    fi

    object_arch_ok "$REAL_SHARED" 0 "libzstd.$SHARED_EXT" || true

    # ET_DYN (ELF) / MH_DYLIB (Mach-O). A static-only build that
    # accidentally staged a relocatable .o, or an executable, would be
    # the right architecture but the wrong kind of file.
    SHARED_MAGIC=$(read_bytes "$REAL_SHARED" 0 4)
    if [ "$SHARED_MAGIC" = "7f454c46" ]; then
      ETYPE=$(le16 "$(read_bytes "$REAL_SHARED" 16 2)")
      if [ "$ETYPE" != "3" ]; then
        fail "libzstd.so has ELF type $ETYPE, expected 3 (ET_DYN)"
      fi
    elif [ "$SHARED_MAGIC" = "cffaedfe" ]; then
      FILETYPE=$(read_bytes "$REAL_SHARED" 12 4)
      if [ "$FILETYPE" != "06000000" ]; then
        fail "libzstd.dylib has Mach-O filetype 0x$FILETYPE, expected 0x06000000 (MH_DYLIB)"
      fi
    fi

    # Exported symbols. nm can read this file on the platforms that
    # matter (linux CI for ELF, macOS for Mach-O); when it can't — a
    # macOS host inspecting a linux archive — say so rather than
    # pretending the check ran.
    SYMS="$WORK/shared-symbols.txt"
    : > "$SYMS"
    if command -v nm >/dev/null 2>&1; then
      nm -D "$REAL_SHARED" > "$SYMS" 2>/dev/null || true
      if [ ! -s "$SYMS" ]; then
        nm -g "$REAL_SHARED" > "$SYMS" 2>/dev/null || true
      fi
    fi
    if [ -s "$SYMS" ]; then
      for sym in ZSTD_compress ZSTD_decompress ZSTD_versionNumber; do
        if ! grep -qE "^[0-9a-fA-F]* *[TtDdSsWwBbRr] _?${sym}$" "$SYMS"; then
          fail "libzstd.$SHARED_EXT does not export $sym"
        fi
      done
      echo "  exports the public API"
    else
      echo "  WARNING: nm cannot read this file on $(uname -s); export check skipped" >&2
    fi
  fi
fi

# ---------------------------------------------------------------------------
# pkg-config file: relocatable, and describing this build
# ---------------------------------------------------------------------------

PC="$ROOT/lib/pkgconfig/libzstd.pc"
if [ -f "$PC" ]; then
  echo "---- $EXPECTED_DIR/lib/pkgconfig/libzstd.pc ----"
  # shellcheck disable=SC2016  # ${pcfiledir} is pkg-config's own variable, not the shell's
  if ! grep -q '^prefix=${pcfiledir}/\.\./\.\.$' "$PC"; then
    fail "libzstd.pc prefix is not relative to \${pcfiledir}: $(grep '^prefix=' "$PC" || true)"
  else
    echo "  prefix is relocatable"
  fi
  if grep -qE '^(prefix|libdir|includedir)=/' "$PC"; then
    fail "libzstd.pc still carries a build-machine absolute path"
  fi
fi

if [ "$FAIL" -ne 0 ]; then
  echo ""
  echo "verify_archive.sh: one or more invariants failed (see FAIL lines above)" >&2
  exit 1
fi

echo ""
echo "verify_archive.sh: all invariants hold for $BASE"
