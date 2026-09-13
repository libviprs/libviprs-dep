#!/bin/sh
# Stage one built cell, smoke it, and record what was measured.
#
# Usage: stage.sh <rid> <platform> <source-root> <fingerprint> <publish-log>
#
# Staging, smoking and fact-gathering in one script so the Dockerfile stays
# readable and so the mac path, which has no Dockerfile, runs the same code.
# It never aborts on a failed smoke: it records the outcome in facts.txt,
# build_acadsharp.py turns that into a hard failure after the archive has
# been written, and verify_archive.sh refuses the archive independently from
# its bytes. Two independent refusals, neither of them silent.
#
# This used to be a 185-line string inside build_acadsharp.py with two
# placeholder words substituted into it before it was written out, which
# meant `tools/shellcheck-all.sh` never saw a line of it: that script
# discovers its work with `git ls-files '*.sh'`, and the most intricate
# shell in the repository was not a tracked *.sh. It is a file now, and the
# two lists it used to have pasted into it arrive as environment variables
# instead, so what runs in the container is byte for byte what is in the
# tree.
#
# The environment it expects:
#
#   WANT_STATIC                    1 to attempt the static half, else 0
#   WORK, STAGING                  overridable for the macOS host build,
#                                  which has no container and no /staging
#   RUNTIME_ARCHIVES               space-separated, the runtime archives to
#                                  merge, in the order `ar` merges them
#   STATIC_SYSTEM_LIBRARY_LADDER   semicolon-separated rungs, narrowest
#                                  first, each a space-separated set of
#                                  bare library names
#
# The last two are only read inside the static half, so a target that does
# not attempt one (mac) never needs them. Both are written `${VAR:?}`: a
# missing list would otherwise turn into an empty loop and a shared-only
# archive, which is a downgrade nothing would report.
set -eu

RID="${1:?rid}"
PLAT="${2:?platform}"
SRC="${3:?source root}"
FINGERPRINT="${4:?fingerprint}"
PUBLISH_LOG="${5:?publish log}"

# Both paths are overridable so the macOS host build, which has no
# container and no /staging, runs this same script.
WORK="${WORK:-/work}"
STAGING="${STAGING:-/staging}"
PUB="$WORK/native/bin/Release/net10.0/$RID/publish"
FACTS="$WORK/facts.txt"
: > "$FACTS"

if [ "$PLAT" = "mac" ]; then EXT=dylib; else EXT=so; fi

# One line per key: a later reading replaces an earlier one rather than
# leaving two rows for the same fact in the file the manifests are
# written from.
fact() {
    if [ -s "$FACTS" ]; then
        grep -v "^$1	" "$FACTS" > "$FACTS.tmp" || true
        mv "$FACTS.tmp" "$FACTS"
    fi
    printf '%s\t%s\n' "$1" "$2" >> "$FACTS"
}

# The bare names a consumer would pass to `-l`. libc and the loader are
# dropped: nothing links those by name, and build.rs would emit a
# cargo:rustc-link-lib for each one it is given.
read_needed() {
    if [ "$PLAT" = "mac" ]; then
        otool -L "$1" 2>/dev/null | sed -n 's|.*/lib\([A-Za-z0-9_+.-]*\)\.dylib.*|\1|p'
    else
        readelf -d "$1" 2>/dev/null | sed -n 's/.*NEEDED.*\[\(.*\)\]/\1/p' \
            | sed -e 's/^lib//' -e 's/\.so.*$//'
    fi | grep -vE '^(c|System|acadsharp_native|ld-linux.*|c\.musl.*|ld-musl.*)$' \
        | sort -u | tr '\n' ' '
}

mkdir -p "$STAGING/lib" "$STAGING/include" "$STAGING/LICENSES"

cp "$PUB/viprs_acadsharp.$EXT" "$STAGING/lib/libacadsharp_native.$EXT"
cp "$WORK/include/viprs_acadsharp.h" "$STAGING/include/viprs_acadsharp.h"
cp "$SRC/LICENSE" "$STAGING/LICENSES/ACadSharp-LICENSE"

# Third-party notices: the runtime's own file when the pack ships one,
# plus the package list the publish actually restored. Generated rather
# than written, so it cannot describe a different build.
{
    echo "THIRD PARTY NOTICES"
    echo
    echo "This archive statically contains parts of the .NET runtime, published"
    echo "by Microsoft under the MIT licence, and the NuGet packages listed"
    echo "below. ACadSharp's own licence is in ACadSharp-LICENSE."
    echo
    echo "== NuGet packages restored for this publish =="
    (cd "$WORK/native" && dotnet list package --include-transitive 2>/dev/null) || true
    echo
    NOTICES=$(find "$HOME/.nuget/packages" -maxdepth 3 -iname 'THIRD-PARTY-NOTICES*' \
        2>/dev/null | sort -u)
    for notice in $NOTICES; do
        echo "== $notice =="
        cat "$notice"
        echo
    done
} > "$STAGING/LICENSES/THIRD_PARTY_NOTICES"

fact dotnet_version "$(dotnet --version)"
fact clang_version "$(clang --version 2>/dev/null | head -1)"
fact linker_version "$(ld --version 2>/dev/null | head -1)"
fact aot_warning_count "$(grep -cE 'warning IL[0-9]+' "$PUBLISH_LOG" || true)"

# What the shared library actually needs at load time. libc and the
# loader are dropped: nothing links those by name.
NEEDED=$(read_needed "$STAGING/lib/libacadsharp_native.$EXT")
fact shared_needed "$NEEDED"

# --- shared smoke -----------------------------------------------------
cc -O1 -I"$WORK/include" "$WORK/archive_smoke.c" -o /tmp/archive_smoke -ldl
if /tmp/archive_smoke "$STAGING/lib/libacadsharp_native.$EXT" "$FINGERPRINT" \
        > /tmp/smoke.out 2>&1; then
    fact shared_smoke_ok 1
else
    fact shared_smoke_ok 0
fi
MISSING=$(sed -n 's/^MISSING_EXPORT //p' /tmp/smoke.out | sort -u | tr '\n' ' ')
LIVE_FP=$(sed -n 's/^ABI_FINGERPRINT=//p' /tmp/smoke.out | head -1)
BACKING=$(sed -n 's/^BACKING_VERSION=//p' /tmp/smoke.out | head -1)
# The read range the library itself answered with. Recorded as a fact
# like every other measurement here, so LINKINFO.json states what the
# packed library says it reads rather than what a header says about it.
DWG_MIN=$(sed -n 's/^DWG_VERSION_MIN=//p' /tmp/smoke.out | head -1)
DWG_MAX=$(sed -n 's/^DWG_VERSION_MAX=//p' /tmp/smoke.out | head -1)
fact missing_exports "$MISSING"
fact live_fingerprint "$LIVE_FP"
fact backing_version "$BACKING"
fact dwg_version_min "$DWG_MIN"
fact dwg_version_max "$DWG_MAX"
echo "---- shared smoke ----"
cat /tmp/smoke.out

# --- static, best effort ---------------------------------------------
fact static_ok 0
if [ "${WANT_STATIC:-0}" = "1" ]; then
    echo "---- static publish ----"
    if (cd "$WORK/native" && dotnet publish Viprs.ACadSharp.Native.csproj -r "$RID" \
            -c Release -p:NativeLib=Static \
            -p:AcadSharpProject="$SRC/src/ACadSharp/ACadSharp.csproj" \
            > "$WORK/publish-static.log" 2>&1); then
        MANAGED=$(find "$WORK/native/bin" -name 'viprs_acadsharp.a' | head -1)
        # The runtime archives live next to libbootstrapperdll.o in the
        # NativeAOT runtime pack. Finding them by that file rather than by
        # a path pattern means a pack layout change is a missing-file
        # error here rather than a silent shared-only downgrade.
        BOOTSTRAP=$(find "$HOME/.nuget/packages" -name 'libbootstrapperdll.o' | head -1)
        PACK=$(dirname "$BOOTSTRAP")
        INIT_SYM=""
        if [ -n "$MANAGED" ] && [ -f "$BOOTSTRAP" ]; then
            # libbootstrapperdll.o carries the runtime's static
            # initialiser in .init_array and defines no global symbol at
            # all, so inside an archive nothing can ever pull it in and
            # the first managed call aborts. .NET 10 removed
            # NativeAOT_StaticInitialization, the symbol the sample says
            # to force, so there is nothing left to --require-defined.
            # Globalising the initialiser gives the link something to
            # reach, and it ships in an archive of its own that the
            # consumer whole-archives, because an argument that forces a
            # symbol does not travel from a dependency's build script to
            # the binary that needs it and a library does.
            INIT_SYM=$(nm "$BOOTSTRAP" \
                | awk '$2 == "t" && $3 ~ /^_GLOBAL__sub_I/ { print $3; exit }')
        fi
        if [ -z "$INIT_SYM" ]; then
            echo "no managed archive, runtime pack or static initialiser; shipping shared-only"
        else
            INIT_A="$STAGING/lib/libacadsharp_native_init.a"
            MERGED="$STAGING/lib/libacadsharp_native.a"
            rm -f "$INIT_A" "$MERGED"
            objcopy --globalize-symbol="$INIT_SYM" "$BOOTSTRAP" /tmp/bootstrapperdll.o
            ar rcs "$INIT_A" /tmp/bootstrapperdll.o

            # Merge by extracting rather than by `ar addlib`, because
            # addlib keeps each source's member names and two runtime
            # archives ship objects of the same name. The result was an
            # archive holding two members called entrypoints.c.o, which
            # links today only because the linker takes the first
            # definition it finds. Prefixing each member with the archive
            # it came from keeps every object and leaves no two sharing a
            # name.
            #
            # Order is load-bearing and this is the expensive half of the
            # lesson. Members go in the order each source archive lists
            # them, managed archive first, which is the order ILC linked
            # them in and the order addlib produced. Built from the
            # filesystem's order instead, the archive links perfectly and
            # the binary segfaults on the way out, every time: the
            # linker emits .init_array in the order it pulls members, so
            # the archive's own order decides what runs when. Only
            # running the smoke catches that, which is why it is run.
            MERGE_DIR=/tmp/merge
            rm -rf "$MERGE_DIR"
            mkdir -p "$MERGE_DIR"
            MERGE_OK=1
            echo "$MANAGED" > /tmp/merge-sources.txt
            # A deliberate word split: the caller passes the archive
            # names as one space-separated string, and quoting it would
            # make the loop run once over a filename with spaces in it.
            # No disable here, checked: shellcheck does not raise SC2086
            # on a `for` list, and a directive nothing suppresses is the
            # kind of line this script used to be full of.
            for name in ${RUNTIME_ARCHIVES:?}; do
                [ -f "$PACK/$name" ] && echo "$PACK/$name"
            done >> /tmp/merge-sources.txt
            : > /tmp/merge-members.txt
            while read -r src; do
                [ -n "$src" ] || continue
                stem=$(basename "$src" .a)
                d="$MERGE_DIR/$stem"
                mkdir -p "$d"
                (cd "$d" && ar x "$src")
                WANT=$(ar t "$src" | wc -l)
                GOT=$(find "$d" -maxdepth 1 -type f | wc -l)
                if [ "$WANT" -ne "$GOT" ]; then
                    echo "$src holds $WANT members but extracted $GOT, so a member was lost"
                    MERGE_OK=0
                fi
                ar t "$src" | while read -r member; do
                    [ -f "$d/$member" ] || continue
                    mv "$d/$member" "$MERGE_DIR/${stem}__$member"
                    echo "$MERGE_DIR/${stem}__$member"
                done >> /tmp/merge-members.txt
                rmdir "$d" 2>/dev/null || true
            done < /tmp/merge-sources.txt

            # The module table has to survive --gc-sections, and by
            # default under lld it does not.
            #
            # ILC puts the runtime's module headers in a section called
            # __modules and the bootstrapper walks it through
            # __start___modules and __stop___modules, the symbols a linker
            # synthesises around any section whose name is a C identifier.
            # Nothing relocates against the section, so those two symbols
            # are the only references to it, and lld has defaulted to
            # -z start-stop-gc since version 13, which says a reference
            # through an encapsulation symbol is not a reason to keep a
            # section. rustc asks for --gc-sections, so on every target
            # whose linker is lld the section goes and the link fails with
            # an undefined __start___modules. GNU ld keeps it, which is
            # why the C smoke below passes, why every arm64 job passed,
            # and why this only ever showed up on linux/x64 (#67).
            #
            # Three sections, not one. The bootstrapper references six
            # encapsulation symbols, around __modules, __managedcode and
            # __unbox. Only __modules dies today, because ILC emits each
            # of the other two as one monolithic section and any live
            # symbol in it keeps the whole thing: measured, 59471
            # relocations reach __managedcode and 1259 reach __unbox
            # against zero for __modules. That is an accident of how ILC
            # lays out sections, not a guarantee, so all three get the
            # flag. It is free: retaining all three produces a binary of
            # identical size with a byte-identical .init_array.
            #
            # Setting SHF_GNU_RETAIN on the section makes the archive
            # carry its own requirement. The alternative is asking every
            # consumer to pass -z nostart-stop-gc, and a consumer cannot:
            # cargo:rustc-link-arg does not travel from a dependency's
            # build script to the binary that links it, which is the same
            # limitation that put the initialiser in its own archive.
            RETAINED=0
            for OBJ in "$MERGE_DIR"/*.o; do
                [ -f "$OBJ" ] || continue
                readelf -S -W "$OBJ" 2>/dev/null \
                    | sed -n 's/^ *\[ *[0-9]*\] *\([^ ]*\) .*/\1/p' \
                    | grep -qx __modules || continue
                if python3 "$WORK/retain_sections.py" "$OBJ" \
                        __modules __managedcode __unbox; then
                    RETAINED=$((RETAINED + 1))
                else
                    MERGE_OK=0
                fi
            done
            fact retained_module_sections "$RETAINED"
            # At least one object has to carry it. There is no upper
            # bound on purpose: `__modules` is an encapsulation array and
            # N contributors is its designed shape, so an exact count
            # would redden a release for an archive that links perfectly
            # the day ILC splits its output or a second NativeAOT library
            # joins the merge.
            if [ "$RETAINED" -lt 1 ]; then
                echo "no object carries __modules, so the runtime renamed a section"
                MERGE_OK=0
            fi

            if [ "$MERGE_OK" != "1" ]; then
                echo "the merge would have dropped an object; shipping shared-only"
                rm -f "$INIT_A" "$MERGED"
            else
                # `q` appends without reordering, so a list too long for
                # one argv still goes in the order it was written.
                tr '\n' '\0' < /tmp/merge-members.txt | xargs -0 ar qc "$MERGED"
                ranlib "$MERGED"
                echo "---- static smoke ----"
                LADDER="${STATIC_SYSTEM_LIBRARY_LADDER:?}"
                OLDIFS=$IFS
                IFS=';'
                for CAND in $LADDER; do
                    IFS=$OLDIFS
                    SYSLIBS=""
                    for lib in $CAND; do SYSLIBS="$SYSLIBS -l$lib"; done
                    # The documented link: the initialiser archive whole,
                    # ahead of the main one. Reversing the two fails with
                    # an undefined reference to RhRegisterOSModule, and
                    # dropping the whole-archive links clean and aborts on
                    # the first managed call.
                    # shellcheck disable=SC2086  # $SYSLIBS is a
                    # deliberate word-split list of -l flags, built one
                    # rung at a time; quoting it would pass "-lm -lrt" to
                    # the linker as a single library name.
                    if cc -O1 "$WORK/static_archive_smoke.c" \
                            -Wl,--whole-archive "$INIT_A" -Wl,--no-whole-archive \
                            "$MERGED" $SYSLIBS -o /tmp/static_smoke \
                            > /tmp/static_link.log 2>&1 \
                            && /tmp/static_smoke "$FINGERPRINT" > /tmp/static_smoke.out 2>&1; then
                        fact static_ok 1
                        fact static_system_libraries "$CAND"
                        fact static_link_args ""
                        cat /tmp/static_smoke.out
                        break
                    fi
                    IFS=';'
                done
                IFS=$OLDIFS
                CERTIFIED=$(awk -F'\t' '$1 == "static_ok" { v = $2 } END { print v }' "$FACTS")
                if [ "$CERTIFIED" != "1" ]; then
                    echo "static smoke did not pass, shipping shared-only:"
                    tail -30 /tmp/static_link.log 2>/dev/null || true
                    tail -10 /tmp/static_smoke.out 2>/dev/null || true
                    rm -f "$MERGED" "$INIT_A"
                fi
            fi
        fi
    else
        echo "static publish failed, shipping shared-only:"
        tail -40 "$WORK/publish-static.log" || true
    fi
fi

echo "---- staged ----"
ls -lR "$STAGING"
cat "$FACTS"
