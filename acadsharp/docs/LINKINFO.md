# LINKINFO.json, schema version 1

`metadata/LINKINFO.json` is the third frozen contract in the archive, beside
`include/viprs_acadsharp.h` and [WIRE.md](WIRE.md). The header says what the
calls are, [ABI.md](ABI.md) says what they mean, WIRE.md says what comes back
out of the decode, and this file says how to get the library onto a link line
at all.

It exists because the other three are useless without it. A consumer that has
the header still has to know which file to link, in what order, with which
system libraries, and whether this particular archive even ships a static half.
None of that is visible in the header, none of it is the same on every target,
and all of it is measured per build rather than assumed. So it is written down
here, in a file a build script can parse, and this document is the definition
of what those field names mean.

The rule the whole archive is built around: a consumer must be buildable from
the published archive alone, without the producer's source. If something here
is ambiguous enough that you would reach for `build_acadsharp.py` to settle it,
that is a defect in this document and worth reporting as one.

## What this document may name, and why

ABI.md and WIRE.md deliberately name no language, build system or package
manager, because they describe a boundary any consumer may sit behind and a
contract that names one consumer has stopped being a contract. This file is
the exception, and it is the exception on purpose.

A manifest is not a boundary. It only matters to whatever reads it at build
time, one such reader exists, and the failure this document is mostly here to
prevent is invisible unless the recipe is written out in that reader's own
directives. So the linking rules below are stated twice: once in linker terms,
which is the part that is true for every consumer, and once as the exact lines
a build script emits, which is the part that has already been got wrong once.
A consumer built with anything else reads the first form and ignores the
second.

## Reading the file

It is a single JSON object, UTF-8, no comments, no trailing commas, no nesting
beyond arrays of strings. The key order is the order of the table below and
happens to be stable, but nothing should depend on that: read by name.

Paths are relative to the root of the unpacked archive and always use `/`. So
`lib/libacadsharp_native.so` means exactly that, joined onto wherever the
archive was unpacked.

### `schema_version` and what to do when it moves

`schema_version` is `1` and describes this file's shape, not the library's.
It moves when a field is removed, renamed or has its meaning changed. Adding
one does not move it, which is the other half of the rule below: an archive
may carry a key a consumer has never heard of, and that consumer skips it and
keeps working. A version that moved every time a field was added would refuse
every older consumer for a field none of them read.
The three versions in the file are different numbers with different jobs:
`schema_version` is about the manifest, `abi_version` is about the header, and
`wire_version` is about the batch stream.

A consumer reading a manifest whose `schema_version` is **higher than the one
it knows** must refuse the archive and say so. It must not press on with the
fields it recognises. A higher number means a field it is reading may no longer
mean what it meant, and the failures that come of guessing here are link-time
or run-time crashes in a downstream binary, a long way from the manifest that
caused them. An archive from the future is not a degraded archive, it is one
this consumer cannot describe.

A consumer reading a manifest whose `schema_version` is **lower than the one it
knows** may accept it, as long as every field it needs is present and it treats
an absent field as absent rather than as a default. Within a schema version,
fields are only ever added in a way that keeps the existing ones meaning what
they meant, so an unknown extra key is skipped rather than refused.

Neither rule is a substitute for the ABI handshake. `schema_version` says the
manifest can be read; `abi_version` and `abi_fingerprint` say the library
matches the header the consumer was built against. A consumer checks both.

## The fields

Every field marked always present is present in every archive this repo
publishes, on every target, and its absence is a malformed manifest rather than
a fact about the build. The four static link fields are the only optional ones
and they appear together or not at all.

| Field | JSON type | Presence | Meaning | What a build script does with it |
| --- | --- | --- | --- | --- |
| `schema_version` | integer | always | Shape of this file. `1` today. | Refuse anything higher than it knows. |
| `artifact_version` | string | always | The full artifact version, `<upstream>-viprs.<revision>`, e.g. `3.7.1-viprs.1`. The revision moves when the shim changes without upstream moving. | Report it, pin against it, print it in a diagnostic. Nothing is emitted from it. |
| `acadsharp_version` | string | always | The upstream library version alone, e.g. `3.7.1`. Never carries the `-viprs.` suffix. | Provenance only. |
| `acadsharp_commit` | string | always | 40 lowercase hex characters: the upstream commit the source tarball was taken from. | Provenance only. |
| `dotnet_sdk` | string | always | The SDK version that published the binaries, e.g. `10.0.401`. | Provenance only. No .NET runtime is needed to consume the archive. |
| `target` | string | always | The Rust target triple this archive is for: `x86_64-unknown-linux-gnu`, `aarch64-unknown-linux-gnu`, `x86_64-unknown-linux-musl`, `aarch64-unknown-linux-musl` or `aarch64-apple-darwin`. | Compare against the target being built for and refuse a mismatch. A glibc archive linked into a musl binary is a link that succeeds and a binary that does not load. |
| `platform` | string | always | `linux`, `musl` or `mac`. The same fact as `target`, in this repo's own vocabulary. | Usually nothing; `target` is the precise one. |
| `cpu` | string | always | `x64` or `arm64`. | Usually nothing, same reason. |
| `abi_version` | integer | always | `VIPRS_ACAD_ABI_VERSION` as the shipped header defines it. `1` today. | Compare against the header the bindings were generated from, and against what `viprs_acad_abi_version()` returns at run time. |
| `wire_version` | integer | always | `VIPRS_ACAD_WIRE_VERSION` as the shipped header defines it. `2` today. | Compare against the wire version the batch parser implements, and refuse a stream that disagrees. |
| `dwg_version_min` | integer | always | The lowest DWG format this build reads, as the four digits behind the `AC` in a drawing's first six bytes. Measured: the build asks the library it is about to pack, through `viprs_acad_capabilities_v1`, and records the answer. The header never states it, because the range is a fact about the backing reader rather than part of the ABI, and it can move without `abi_version` moving. | Refuse a drawing whose signature is below it, or report the range. Read it from the archive rather than pinning the number: a consumer that hardcodes it refuses a format the next archive reads. |
| `dwg_version_max` | integer | always | The highest, in the same form and measured the same way. Never below `dwg_version_min`. | The same, at the other end. |
| `abi_header_sha256` | string | always | 64 lowercase hex characters: the sha256 of `include/viprs_acadsharp.h` as shipped in this same archive. | Verify the shipped header is the one this manifest describes, before generating bindings from it. |
| `abi_fingerprint` | string | always | 16 lowercase hex characters. See below: this one has a format, and the format is load-bearing. | Parse as a base-16 integer and compare against `viprs_acad_abi_fingerprint()` at run time. |
| `shared_library` | string | always | Archive-relative path to the shared library, `lib/libacadsharp_native.so` or `lib/libacadsharp_native.dylib`. | For a dynamic link: a link-search directive for its directory and `cargo:rustc-link-lib=acadsharp_native`. For `dlopen`, the path itself. |
| `shared_system_libraries` | array of strings | always | Bare library names the shared library needs at load time, taken from its own `NEEDED` list with libc and the loader dropped. Frequently empty. | One `cargo:rustc-link-lib=<name>` each, when linking the shared library. Names are bare: no `-l`, no path, no extension. |
| `static_library` | string | only when `static_certified` | Archive-relative path to the merged static archive, `lib/libacadsharp_native.a`. | `cargo:rustc-link-lib=static:-bundle=acadsharp_native`, second. See the recipe. |
| `static_init_library` | string | only when `static_certified` | Archive-relative path to the runtime's static initialiser, on its own, `lib/libacadsharp_native_init.a`. One small object and nothing else. | `cargo:rustc-link-lib=static:-bundle,+whole-archive=acadsharp_native_init`, first. See the recipe. |
| `static_certified` | boolean | always | `true` only when the static archive was linked **and run** on this target during the build. `false` otherwise, including on every target that never attempts one. | Gate the whole static path on it. When it is `false` there is no static archive in the file and nothing to link. |
| `static_system_libraries` | array of strings | only when `static_certified` | Bare library names the certified static link needed, measured from the link that worked rather than assumed. | One `cargo:rustc-link-lib=<name>` each, after both archives. |
| `static_link_args` | array of strings | only when `static_certified` | Extra flags that link needed. Empty in every archive published so far, and a non-empty one is a refusal. See below. | Nothing. Refuse a non-empty list rather than pass it on. |

### `abi_fingerprint` is 16 lowercase hex digits with no `0x`

The value is the first eight bytes of `abi_header_sha256`, read big-endian, as
16 lowercase hexadecimal characters. It carries no `0x` prefix, no separators,
no uppercase and no sign. It is a string in the JSON because 64 unsigned bits do
not survive every JSON number reader intact, not because it is text: it means a
number, and a consumer turns it into one with a base-16 parse before comparing.

So a manifest reading

    "abi_header_sha256": "aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899",
    "abi_fingerprint": "aabbccddeeff0011"

describes a library whose `viprs_acad_abi_fingerprint()` returns
`0xAABBCCDDEEFF0011`, and a consumer compares those two 64-bit numbers. Both
digests there are made up. The live values are in the archive's own manifest and
in the header beside it; nothing is ever copied out of prose, because a hand
carried digest keeps confidently reporting agreement after the file it describes
has moved, which is the one direction that does damage.

Two mistakes are easy here and neither shows up on the happy path. Prefixing the
value with `0x` and then parsing it with a plain base-16 parse fails, because
most base-16 parsers reject the prefix rather than skipping it. And comparing the
*string* to a formatted version of the runtime value makes the check depend on
whether the formatter emitted uppercase and whether it padded to 16 characters,
so a correct library and a correct consumer disagree over the presentation of a
leading zero. Parse, then compare numbers.

`abi_header_sha256` is a plain sha256 of the header file's bytes, all 64
characters, same lowercase-hex-no-prefix rule. Nothing canonicalises the header
before hashing it: comments and whitespace are in.

### The two linking modes have separate fields

`shared_system_libraries` describes the shared library. `static_system_libraries`
describes the static link. They used to be one field, and they are not the same
list: the shared library's `NEEDED` entries are what the loader resolves for a
`.so`, and a static link pulls in a different and usually longer set. A consumer
that reads the wrong one gets undefined symbols at the end of a static link with
nothing pointing at why.

### Absent, not empty

`static_library` and `static_init_library` are **absent** from the JSON on a
target that built none. They are never present-and-empty. An empty string is a
path, an empty array is a measurement of none, and neither of those means "not
measured". A consumer therefore tests for the key, not for truthiness of the
value, and a manifest carrying `"static_library": ""` is malformed and is
refused by the archive verifier.

The four static fields move together. All four present with
`static_certified: true`, or all four absent with `static_certified: false`.
There is no third state, and a field describing the static link while
`static_certified` is `false` is exactly the shape a build script author reads as
belonging to the shared link.

### `static_certified` is a measurement, not an intention

It is `true` only when the build linked the static archive into a probe program
**and ran it**, on that target, and the probe came back clean. It is `false`
whenever that smoke did not run or did not pass, for any reason: the target does
not attempt a static build at all, the static publish failed, the archive merge
would have dropped an object, the link failed, or the binary aborted.

Shipping shared-only is a recorded outcome and not a failure. The archive then
contains no `.a` at all: an uncertified static archive is removed rather than
shipped, because an archive nobody has linked is an invitation rather than an
artifact.

Linking alone is not enough evidence, which is why the flag is defined on
linking *and running*. A static archive whose runtime initialiser was never
pulled in links perfectly, resolves every symbol, and aborts on the first call
into the library. From the outside the two archives are indistinguishable until
something runs.

## The linking recipe

This is the subtle part, it is the reason this file exists, and it was got wrong
once already in a way that shipped.

### In linker terms, for any consumer

Static linking takes both archives, and the order and the mode both matter:

    <init archive>, with whole-archive on
    <main archive>, ordinary
    the system libraries from static_system_libraries

`libacadsharp_native_init.a` holds one object: the .NET runtime's static
initialiser. The initialiser lives in `.init_array` and **defines no global
symbol anything references**. Ordinary archive semantics pull a member in only
when something already on the link line needs a symbol it defines, so nothing
ever pulls this one in. The link succeeds, the binary contains no runtime
startup, and the first call into the library aborts. That is why it is linked
with `--whole-archive` (or `-force_load` on the Apple linker), which takes every
member regardless of whether anything wants it.

The init archive comes **first**, before the main archive. It is the one with
the dangling references: its object calls `RhRegisterOSModule`, which is defined
in a runtime object inside the main archive that nothing else drags in. A
traditional linker reads left to right and resolves each archive against what is
still undefined behind it, so with the init archive last the link fails on
`undefined reference to RhRegisterOSModule`. Reversed, both symbols find each
other.

Dynamic linking has none of this. Link `libacadsharp_native.so` (or the `.dylib`)
the ordinary way and add one `-l` per `shared_system_libraries` entry. The
initialiser problem is a static-archive problem: a shared library runs its own
initialisers when it is loaded.

### As a build script's directives

For a consumer whose `-sys` crate reads this manifest in `build.rs`, the
measured working recipe is exactly:

    cargo:rustc-link-search=native=<archive>/lib
    cargo:rustc-link-lib=static:-bundle,+whole-archive=acadsharp_native_init
    cargo:rustc-link-lib=static:-bundle=acadsharp_native
    cargo:rustc-link-lib=<each static_system_libraries entry>

in that order. Library names are bare stems: `lib/libacadsharp_native_init.a`
becomes `acadsharp_native_init`, with the `lib` prefix and the `.a` suffix
stripped, which is the same transformation `-l` has always done.

Three modifiers, and all three are load-bearing.

**`+whole-archive` on the init archive**, for the reason above: nothing
references what is in it, so without this it is simply not linked and the binary
aborts at the first call.

**`-bundle` on both.** This is the half that is not obvious. With the default
`+bundle`, rustc does not hand the static library to the linker as a `-l` flag
at all; it packs the archive's objects into the `-sys` crate's own rlib. That
rlib then lands on the final link line **ahead of** the whole-archived init
archive. The linker reads left to right: while it is at the rlib it has no
reason to pull the runtime object that defines `RhRegisterOSModule`, because
nothing has asked for that symbol yet, and once the bootstrapper does ask, the
rlib is behind it and it cannot go back. `-bundle` on both makes rustc emit two
ordinary `-l` flags instead, in the order written above, and the ordering rule
from the previous section then applies as written. With the default on either
one, the link fails on `RhRegisterOSModule`.

**Order**, init archive before main archive, for the same left-to-right reason.
Swapping the two lines fails the same way.

### The module table, `--gc-sections`, and why you need no flag for it

The runtime finds its module headers in a section called `__modules`, and it
finds that section through `__start___modules` and `__stop___modules`, the two
symbols a linker synthesises around any section whose name is a C identifier.
Nothing relocates against the section itself, so those two symbols are its only
references.

Since version 13, lld defaults to `-z start-stop-gc`, which says a reference
through an encapsulation symbol is not a reason to keep a section alive. rustc
passes `--gc-sections`, and it links with lld on `x86_64-unknown-linux-gnu`. So
on that target the linker collects `__modules`, then has nothing left for
`__start___modules` to point at, and the link ends with:

```
rust-lld: error: undefined symbol: __start___modules
>>> referenced by bootstrapperdll.o:(InitializeRuntime()) in archive libacadsharp_native_init.a
```

GNU ld keeps the section, which is why the same archive links on aarch64 and
why a `cc` link of it succeeds anywhere.

**You need no flag for this.** The producer sets `SHF_GNU_RETAIN` on the
section, so the archive carries the requirement itself, and `verify_archive.sh`
refuses an archive whose `__modules` lacks it. That is deliberate rather than
tidy: the flag cannot be left to the consumer, because the only way to express
it on a link line is `-z nostart-stop-gc`, and the next section explains why an
argument cannot reach the binary from here.

**If you do see that error**, you are holding an archive built before this was
fixed. Take a newer one. If you cannot, `-Wl,-z,nostart-stop-gc` on your own
final binary is the workaround, and it has to go on the binary rather than on
any crate between you and this library.

**Which linkers this covers.** `SHF_GNU_RETAIN` lives in the OS-specific flag
range, so binutils reads it as "retain" only when the object declares a GNU OS
ABI; the producer sets `EI_OSABI` to `ELFOSABI_GNU` alongside the flag, which is
what the GNU assembler does for any section it assembles with `R`. lld honours
the flag either way, measured from lld 13 through lld 22. Binutils older than
2.36 predate the flag and ignore it silently, with no warning, and they also
predate `-z start-stop-gc`, so nothing breaks there. mold keeps the section
regardless. The exception is **gold**, which never implemented the flag: if you
link with `-fuse-ld=gold` nothing here protects you, and that is untested rather
than known-broken. gold is gone from binutils 2.44 and later.

### The runtime's libunwind ships under private names

NativeAOT statically links its own copy of llvm-libunwind into the runtime
archives, and rustc links a `self-contained/libunwind.a` of its own for every
musl target and for no glibc one. Both define `__unw_step`,
`unw_local_addr_space`, `libunwind::LocalAddressSpace::sThisAddressSpace` and
about fifty-six more, so the cargo recipe above used to die on a musl target
with a screen of

```
multiple definition of `__unw_get_reg'
  libunwind.cpp:(.text.__unw_get_reg+0x0)
  first defined in libacadsharp_native.a(libRuntime.WorkstationGC__libunwind.cpp.o)
```

Nothing about `__modules` is involved and neither is lld: the linker here is
GNU ld from the Alpine toolchain.

So the copy in these archives is renamed at build time, with `objcopy
--redefine-syms` over the merged archive. `__unw_step` ships as
`__viprs_unw_step`, `libunwind::` ships as `viprs_libunwind::`, and the two
unwinders sit in the same binary under different names, each self-consistent:
the runtime unwinds through ours, Rust panics unwind through rustc's.

**`_Unwind_*` is untouched.** That is the public personality ABI a C++ or Rust
landing pad calls by name rather than a name anything here chooses, and these
archives define none of them.

Measured on native x86_64 musl and native arm64 musl, rustc 1.98.1, with the
glibc archive through the same harness on the same machine as the control:
after the rename the cargo recipe links and runs on both, the C recipe is
unchanged, and one binary runs 3000 managed exceptions interleaved with 2000
Rust panics through `catch_unwind` without incident, with and without
`-C target-feature=+crt-static`.

**Nothing is asked of you for this.** It is a property of the bytes you
download, and `verify_archive.sh` reads the shipped symbol index to check it
before the archive is published: an archive carrying the original names is
refused, and so is one carrying neither name, because that is what the rename
silently not running would look like from outside.

Two fixes that look obvious are measured dead, so they are written down rather
than tried again. Dropping the runtime's libunwind member from the archive
breaks the C recipe too, because the runtime references that copy's C++
internals (`libunwind::LocalAddressSpace::sThisAddressSpace`, reached from
`UnixNativeCodeManager` and `RhRegisterOSModule`) and not only the `__unw_*`
C API. And no stable consumer-side flag helps: `-C link-self-contained=no`
breaks the build script's own link and `-C link-self-contained=-unwind` is
rejected.

### Do not express any of this as a link argument

`cargo:rustc-link-arg` does **not** propagate from a dependency's build script
to a downstream binary. Cargo does not treat the directives alike:
`rustc-link-search` and `rustc-link-lib` reach the link line of everything that
depends on the emitting crate, while `rustc-link-arg` binds to the emitting
package's own targets and goes no further.

So the obvious fix for an initialiser nothing references, forcing it with
`-Wl,-u,<symbol>` or with `--require-defined`, produces this: the `-sys` crate's
own tests and examples link correctly and pass, its CI is green, and every binary
that actually depends on the crate is built without the argument, links cleanly,
and aborts on the first call. The requirement never arrives, and nothing on the
producing side can see that it did not.

That is the whole reason the initialiser ships as a **library**,
`static_init_library`, rather than as a flag in `static_link_args`. A library
travels. An argument does not. It is also why `static_link_args` is empty in
every archive published so far, and why a consumer should refuse a non-empty one
loudly rather than pass it along: whatever it asks for cannot be delivered from
where the manifest is being read, so the right response is to stop and say so,
not to emit something that will be silently dropped.

Both the build driver and the archive verifier refuse to write or accept a
manifest whose `static_link_args` contains `-u`, `--undefined` or
`--require-defined`, whatever symbol it names. `NativeAOT_StaticInitialization`
in particular does not exist in .NET 10: linking with `--require-defined` for it
fails outright, and the sample code that tells you to use it predates the
runtime that removed it.

## A complete manifest

Every digest and commit below is invented, and the `abi_fingerprint` is the
first eight bytes of the `abi_header_sha256` above it, which is the relationship
a consumer checks. A certified Linux x86-64 target:

```json
{
  "schema_version": 1,
  "artifact_version": "3.7.1-viprs.1",
  "acadsharp_version": "3.7.1",
  "acadsharp_commit": "0f1e2d3c4b5a69788796a5b4c3d2e1f0abcdef01",
  "dotnet_sdk": "10.0.401",
  "target": "x86_64-unknown-linux-gnu",
  "platform": "linux",
  "cpu": "x64",
  "abi_version": 1,
  "wire_version": 2,
  "abi_header_sha256": "aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899",
  "abi_fingerprint": "aabbccddeeff0011",
  "shared_library": "lib/libacadsharp_native.so",
  "shared_system_libraries": [],
  "static_library": "lib/libacadsharp_native.a",
  "static_init_library": "lib/libacadsharp_native_init.a",
  "static_certified": true,
  "static_system_libraries": ["m", "rt", "dl", "pthread", "stdc++"],
  "static_link_args": [],
  "dwg_version_min": 1014,
  "dwg_version_max": 1032
}
```

And an uncertified one, which is the same file with five differences and no
`.a` in the archive beside it:

```json
{
  "schema_version": 1,
  "artifact_version": "3.7.1-viprs.1",
  "acadsharp_version": "3.7.1",
  "acadsharp_commit": "0f1e2d3c4b5a69788796a5b4c3d2e1f0abcdef01",
  "dotnet_sdk": "10.0.401",
  "target": "aarch64-apple-darwin",
  "platform": "mac",
  "cpu": "arm64",
  "abi_version": 1,
  "wire_version": 2,
  "abi_header_sha256": "aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899",
  "abi_fingerprint": "aabbccddeeff0011",
  "shared_library": "lib/libacadsharp_native.dylib",
  "shared_system_libraries": [],
  "static_certified": false,
  "dwg_version_min": 1014,
  "dwg_version_max": 1032
}
```

## What a consumer does, start to finish

1. Unpack the archive and read `metadata/LINKINFO.json`.
2. Refuse a `schema_version` higher than the one you implement.
3. Check `target` against what you are building for.
4. Check `abi_header_sha256` against the header shipped beside it, then
   generate bindings from that header rather than from a copy.
5. Pick a mode. Shared: link `shared_library` plus every
   `shared_system_libraries` entry. Static: refuse unless `static_certified` is
   `true`, refuse a non-empty `static_link_args`, then emit the recipe above in
   order.
6. At run time, before anything else, compare `viprs_acad_abi_version()` against
   `abi_version` and `viprs_acad_abi_fingerprint()` against `abi_fingerprint`
   parsed as a base-16 integer. A mismatch is
   [`VIPRS_ACAD_ABI_MISMATCH`](ABI.md) and the consumer stops there.

Steps 5 and 6 are both necessary. Step 5 is about getting a binary at all;
step 6 is about whether the binary you got is talking to the header you
generated from. The failure step 6 catches is a library that loads, resolves
every symbol, and reads a struct field four bytes from where the consumer
believes it is.

## Verifying an archive before trusting it

`metadata/CHECKSUMS.txt` covers every other file in the archive, one
`<sha256>  <archive-relative-path>` line each, sorted by path. A file the
manifest does not mention is as much a defect as one whose digest is wrong: it
arrived without anyone measuring it.

`acadsharp/scripts/verify_archive.sh <tgz>` in this repository checks all of
that plus the layout, both manifests, the architecture and kind of every object,
the export table against the shipped header, and, where the host can build for
the target, that the recipe above links and runs and that the library answers
with the read range this manifest states. The build driver runs it on
every archive it produces before calling one done.
