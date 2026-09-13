# ADR 0001: ACadSharp under NativeAOT

**GO.** ACadSharp 3.7.1 compiles with NativeAOT and reads real DWG files correctly
without a .NET runtime anywhere. I read four fixtures with four different binaries
(a shared library on linux-arm64, two shared libraries on linux-x64 built against
different glibc versions, and a statically linked executable on linux-x64) and
compared every read against a JIT read of the same file. All sixteen captures are
byte for byte identical to the oracle, including a 163-entity drawing AutoCAD itself
produced. Two build settings are load-bearing and the base image has to change, both
covered below.

Date: 2026-09-12. Issue: libviprs/libviprs-dep#45.

## What I ran it on

| | |
| --- | --- |
| Image | `mcr.microsoft.com/dotnet/sdk:10.0`, which is Ubuntu 24.04 noble, glibc 2.39 |
| SDK | 10.0.401, pinned in `native/global.json` with `rollForward: latestPatch` |
| ILCompiler | 10.0.12, pinned by `native/packages.lock.json` |
| ACadSharp | tag v3.7.1, tarball sha256 `0c6b9de9...7847a7`, plus CSUtilities at `fdf1403e` |
| Host | an Apple Silicon Mac running Docker. The arm64 containers are native; the amd64 ones run under Rosetta, so their timings are inflated and their architecture-specific results still hold |

Everything below ran in a container. Nothing was built on a host toolchain.

## JIT versus AOT

This is the question the spike existed to answer, and it needed more than an entity
count. `DwgReader.Read` builds `CadHeader`'s system-variable map through
`PropertyReflection.cs` (`GetProperties()`, `GetCustomAttribute`, and
`Expression.Lambda(...).Compile()`), and if trimming hollows that map nothing
throws. The map comes back short, the defaults stand, and ACadSharp's defaults are
not zeroes: `Objects/Layout.cs` ships MinExtents (25.7, 19.5, 0) and MaxExtents
(231.3, 175.5, 0), which is a plausible-looking sheet rectangle. A hollowed read
does not look hollow.

So the probe reports the size of that map, the header variables, the layout extents
and per-entity geometry, and the fixture sets values nowhere near the defaults. The
probe is one source file compiled into both the JIT program and the native library,
so a difference cannot come from two versions of the probe.

| Fixture | Entities | Types | `GetHeaderMap()` | JIT read | AOT arm64 | AOT x64 | AOT x64 (bookworm) | static x64 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `g11_shapes.dwg` (AC1032) | 5 | 5 | 249 | 81 ms | identical | identical | identical | identical |
| `g11_codepage.dwg` (AC1018) | 6 | 5 | 249 | 78 ms | identical | identical | identical | identical |
| `real_AC1032.dwg` | 163 | 36 | 249 | 122 ms | identical | identical | identical | identical |
| `real_AC1018.dwg` | 163 | 36 | 249 | 132 ms | identical | identical | identical | identical |

"Identical" means the whole capture, not the count: 249 system variables on both
sides, `text_height_default` 7.25 rather than the default 2.5, model-layout
MaxExtents (101.5, 202.25, 33.75) rather than the default sheet rectangle, and every
entity's geometry to twelve decimal places. The captures are committed under
`tests/fixtures/captures/` and `tests/test_acadsharp_recorded_parity.py` compares
them, including a case that asserts the JIT oracle itself carries the sentinels, so
a matching pair of hollowed reads cannot pass.

`real_AC1032.dwg` carries the weight here: 36 entity types including dimensions,
hatches, multileaders, a table, a raster image and a PDF underlay, 19 layers, 27
block records and 4 layouts.

Speed and memory, for the record. The AOT number is wall clock for the whole
process (start, `dlopen`, and two reads of the file, because the smoke program calls
the count export and the describe export separately), while the JIT number is
measured inside the process for one read and excludes about 50 ms of runtime start.
They are not the same measurement, which makes the comparison generous to the JIT
and the AOT process still finishes inside it.

| Fixture | AOT arm64 wall | AOT arm64 peak RSS | AOT x64 wall (Rosetta) | AOT x64 peak RSS |
| --- | --- | --- | --- | --- |
| `g11_shapes.dwg` | 0.02 s | 16.6 MB | 0.13 s | 24.8 MB |
| `g11_codepage.dwg` | 0.02 s | 16.3 MB | 0.09 s | 24.7 MB |
| `real_AC1032.dwg` | 0.05 s | 29.0 MB | 0.12 s | 38.0 MB |
| `real_AC1018.dwg` | 0.04 s | 30.3 MB | 0.17 s | 38.6 MB |

### The controls

A pass in a container that quietly had a runtime in it would prove nothing, so both
failing states were run in the same container as the passes:

- The managed build of the same project, `viprs_acadsharp.dll`, fails to `dlopen`:
  `invalid ELF header`.
- The framework-dependent publish of the fixture generator fails to run: 131 on
  arm64, 127 on x64. `dotnet` is not on `PATH` and `/usr/share/dotnet` does not
  exist.

Both smokes are there too: a C program that `dlopen`s the library and resolves the
exports by bare name, and a Rust program that links it with `#[link]`. Rust reported
ABI 1 and 163 entities for `real_AC1032.dwg`, matching.

## Source or NuGet

**Build from the pinned source tarball, plus the submodule.** Not the NuGet package.

The NuGet package restores cleanly and would have been less work, but it makes the
patch route impossible: `patches/` exists because a future issue may need a
`DynamicallyAccessedMembers` annotation on an upstream member, and you cannot patch
a package. It also puts a second version pin (the package feed's) between us and the
source we vendored.

The cost is that the GitHub source tarball cannot build on its own. `src/CSUtilities`
is a git submodule, GitHub-generated tarballs never contain submodules, and
`ACadSharp.csproj` imports `..\CSUtilities\CSMath\CSMath.projitems` during project
evaluation, so the failure lands before restore and never mentions the submodule.
That is why `build_acadsharp.py` carries `CSUTILITIES_COMMIT` next to the tarball
digest and why `check_source_tree()` fails loudly on the incomplete tree.

One caveat to record rather than paper over: `SOURCE_SHA256` here pins a GitHub
*generated* tarball, and GitHub does not promise those are byte-for-byte reproducible
forever, unlike zstd, which pins an uploaded release asset. If the digest ever stops
matching without the tag moving, that is the reason, and the fix is to vendor the
tree rather than to relax the check.

## Trimmer roots

**One root, `ACadSharp`, and that is the complete list.**

I expected to need three (`ACadSharp`, `CSUtilities`, `CSMath`) because those last
two carry `Assembly.GetTypes()`, `TypeDescriptor.GetConverter(Type)` and a hard
IL3050 `Enum.GetValues(Type)`. They do carry them, but they are not separate
assemblies: upstream imports them as shared projects (`CSMath.projitems`,
`CSUtilities.projitems`), so their sources compile into `ACadSharp.dll`. The publish
output has exactly two managed assemblies, `ACadSharp.dll` and `viprs_acadsharp.dll`,
and no `CSMath.dll` or `CSUtilities.dll` exists to root. Rooting `ACadSharp` keeps
all of it.

Without the root the publish still succeeds and the binary throws on the first
`new CadDocument()`: `MissingMethodException: No parameterless constructor defined
for type 'ACadSharp.Tables.AppId'`, which is the IL2087 at
`Tables/Collections/Table.cs(107)` coming true. Rooting the assembly costs size: the
same shim without ACadSharp goes from about 3.6 MB to 9.5 MB, and the warning count
from 2 to 16, because rooting the assembly makes the trimmer analyse all of it.

## InvariantGlobalization

**On, and it costs the reader nothing measurable.**

It is not a size tweak. `CadHeader`'s constructor reaches `DateTime.Now` ->
`TimeZoneInfo` -> `CultureInfo`, and a NativeAOT binary on a host with no libicu
calls `FailFast` there. That aborts with SIGABRT straight through a `try`/`catch`
wrapping the whole export body, so the export's own error handling cannot turn it
into a return code. Nothing the shim does can contain it.

The semantics worry is real but does not land on the read path.
`IO/CadReaderBase.cs` registers `CodePagesEncodingProvider` and calls
`Encoding.GetEncoding(code)` for DWG code-page text, so I built a fixture to hit it:
`g11_codepage.dwg` is AC1018, the pre-2007 format that stores text against a code
page rather than as unicode, and carries text Windows-1252 can only partly represent.

Two measurements:

1. Under the JIT, reading it with full ICU and reading it with
   `DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1` produce byte-identical output, on that
   fixture and on all three others. Invariant mode does not change code-page
   decoding, because code pages come from `CodePagesEncodingProvider` and not from
   ICU.
2. Under AOT, the same file reads identically again, lossy mapping and all:
   `ÄÖÜ` survives, `Ω` comes back as `O` and the CJK characters as `??`, which is
   Windows-1252 doing what Windows-1252 does and is what the JIT produced too.

There is also a floor under this: if `Encoding.GetEncoding` ever does fail,
`getListedEncoding` catches it and falls back to `TextEncoding.Windows1252()`, which
is a hand-written 256-character table inside CSUtilities and depends on no framework
encoding data at all.

What is *not* covered: writing DWG text, which this shim does not do, and code pages
other than 1252. A file declaring, say, a Shift-JIS code page could behave
differently, and the epic should add one when it has a real file to add.

## glibc floor

**Build on `debian:bookworm-slim` with the SDK installed by `dotnet-install.sh`. The
org's 2.36 floor holds, and it costs one SDK download per build.**

The library built on the stock SDK image needs `GLIBC_2.38`, on both architectures.
Its only `NEEDED` entries are `libm.so.6`, `libc.so.6` and the loader, so there is no
libicu or libstdc++ dependency, just the version. That number is a problem, because
`zstd/build_zstd.py` builds Linux on `debian:bookworm-slim` precisely to pin glibc
2.36 as the oldest runtime we support, `MANUAL.md` ships that compatibility matrix,
and one consumer links pdfium, zstd and acadsharp into the same binary, so all three
have to agree.

| Library built on | Highest glibc symbol | On bookworm (2.36) | On trixie (2.41) |
| --- | --- | --- | --- |
| `mcr.microsoft.com/dotnet/sdk:10.0` (noble, 2.39) | `GLIBC_2.38` | `dlopen` fails: ``libm.so.6: version `GLIBC_2.38' not found`` | reads all four fixtures |
| `debian:bookworm-slim` (2.36) + `dotnet-install.sh` | `GLIBC_2.32` | reads all four fixtures | (compatible by construction) |

So the floor does not have to rise. There is no `10.0-bookworm-slim` SDK image (I
checked, the tag does not exist), but SDK 10.0.401 installs and runs on bookworm
through `dotnet-install.sh`, the publish takes 24 s there, and the resulting library
tops out at `GLIBC_2.32`, four releases below the floor. It reads all four fixtures
on bookworm, byte for byte identical to the JIT oracle, and those captures are
committed as `*.aot-bookworm-linux-x64.json`.

What it costs, honestly:

- An SDK download per build instead of a cached image layer, because there is no
  image to cache. About 200 MB and most of a minute, and a CI cache keyed on the SDK
  version takes that back.
- A base Microsoft does not test the SDK against. It works today on 10.0.401. It is
  the kind of thing that breaks on a patch bump without anyone announcing it, so the
  build has to fail loudly rather than fall back to the stock image, and the
  packaging issue should record the SDK version that was proven on bookworm.

The alternative, raising the org floor to 2.39, is still on the table but it is no
longer forced, and it would drop Debian 12 and RHEL 9 users for no gain here. That
decision belongs with whoever owns `MANUAL.md`'s matrix, not with this directory.

## Static library

**It links and runs on linux-x64, and the flag the issue asks for no longer exists.**

`-p:NativeLib=Static` publishes in 13 s and produces `viprs_acadsharp.a`,
35,665,210 bytes, a normal `!<arch>` archive with exactly one member,
`viprs_acadsharp.o`, defining `viprs_acad_abi_version`, `viprs_acad_describe` and
`viprs_acad_entity_count`.

The issue asks for `-Wl,--require-defined,NativeAOT_StaticInitialization`, which is
what the NativeAOT sample documents. In .NET 10 that link **fails**:

```
/usr/bin/ld: required symbol `NativeAOT_StaticInitialization' not defined
```

The symbol is not defined anywhere in the 10.0.12 NativeAOT pack. I searched every
`.a` and `.o` in it. Dropping the flag links cleanly, and the resulting executable
runs in a container with no .NET and reads all four fixtures identically to the JIT.
So the initialiser is not something the consumer has to force any more, and a
manifest that carries that flag in `link_args` would break the link rather than
protect it.

What the consumer does still have to do is link the runtime archives itself, because
the managed archive is only the managed half. On linux-x64 that is
`libbootstrapperdll.o`, `libRuntime.WorkstationGC.a`, **`libRuntime.VxsortEnabled.a`**,
`libSystem.Native.a`, `libSystem.IO.Compression.Native.a`,
`libSystem.Net.Security.Native.a`, `libSystem.Security.Cryptography.Native.OpenSsl.a`,
`libeventpipe-disabled.a`, `libstandalonegc-disabled.a`, `libaotminipal.a`,
`libstdc++compat.a`, `libz.a` and the three brotli archives. The vxsort one is easy
to miss because it is x64 only, and leaving it out fails with undefined references
inside the GC (`do_vxsort_avx512`, `do_vxsort_avx2`, `IsSupportedInstructionSet`),
which reads like a broken toolchain rather than a missing library.

On the thin-archive hazard G1.4's verifier is specified to refuse: merging the
managed archive with a runtime archive using `ar -M` / `addlib` produced a normal
44 MB `!<arch>` archive here, not a thin one. A thin archive comes from `ar rcT` or
`--thin`, which stores paths instead of members and breaks the moment the archive
moves. Worth keeping the verifier, but the naive merge is not the way into it.

## Build hosts

**Native runners per architecture. No emulation, no cross-compiling.**

I measured cross-compiling by accident, which is the best kind. The first arm64
publish ran in the amd64 SDK image (Docker's default pull on this Mac), so it was an
x64 host targeting linux-arm64. ILC itself succeeded and produced the object file.
The native link then failed:

```
/usr/bin/ld.bfd: unrecognised emulation mode: aarch64linux
Supported emulations: elf_x86_64 elf32_x86_64 elf_i386 elf_iamcu i386pep i386pe
```

That matches what the .NET docs say: cross-architecture on Linux works, but only
with a cross toolchain (`binutils-aarch64-linux-gnu`, an arm64 sysroot, arm64 zlib
and C runtime objects), none of which the SDK image ships. Adding all that to a CI
image to save a runner is not worth it when GitHub hosts `ubuntu-24.04-arm` for
public repositories and this one is public. On the native arm64 image the same
publish succeeds in 9 s.

QEMU is the option to avoid, and I did not test it because .NET does not support it:
`qemu-user-static` is what GitHub's `setup-qemu-action` provides, and the runtime
documents it as unsupported. zstd's driver emulates, and that pattern must not be
inherited here. Note that this Mac runs amd64 containers under Rosetta rather than
QEMU, so the amd64 results above say nothing either way about QEMU.

So: `ubuntu-latest` for linux-x64, `ubuntu-24.04-arm` for linux-arm64, `macos-15`
for osx-arm64 (already what `release.yml` uses), and an Alpine container on the
matching architecture if musl targets are ever wanted.

`osx-arm64` is the one target this spike did not build. Publishing it needs a macOS
host with the SDK on it, and this lane runs everything in Linux containers by
constraint. Nothing measured here suggests it will behave differently (it is the
same ILC over the same IL), but it is unmeasured and the packaging issue has to run
it on `macos-15` before anyone claims it.

## C# coverage

**Not in `.github/workflows/ci.yml`. Covered by the recorded captures now and by the
release verifier later.**

Two things make a `dotnet` job there expensive right now:

- `zstd/tests/test_ci_coverage.py::test_no_step_hardcodes_a_dependency_directory`
  forbids naming `acadsharp/` in that workflow at all, and a job that builds a csproj
  has to name a path. That guard exists because hardcoded paths are how a whole
  dependency once landed unchecked, so working around it is the wrong move.
- libviprs-tests' `Hook Mirror (every repo in the org)` job reads this repo's ci.yml
  at the `main` tip and mirrors every job into the shared pre-commit hook unless it
  is listed as deferred. A .NET SDK is not something a pre-commit hook should
  require, so the job needs a pairing PR in that repo merging immediately behind this
  one, and CI is required on both.

What covers the C# instead: the JIT and AOT captures are committed and compared by
pytest on every run, which is a real check of the thing that matters (that the
reader still reads the same), and it costs no SDK. `acadsharp/tests/
test_acadsharp_ci_decision.py` fails the moment someone adds a dotnet step, so this
decision cannot be reversed silently.

The packaging issue is where this should be revisited, because that is when there is
an artifact to verify and a release workflow to verify it in. A separate workflow
file is exempt from the hardcoded-path guard and is the cheaper way in when the time
comes.

## AOT and trim warnings

16, and every one of them is in upstream code. The shim itself produces none.

| Count | Code | Where |
| --- | --- | --- |
| 5 | IL2087 | `Table.cs(107)`, `CadDocumentBuilder.cs(323)`, `EnvironmentVars.cs(62)`, `ObjectExtensions.cs(53)`, `LineExtensions.cs(19)` |
| 4 | IL2070 | `ReflectionExtensions.cs(11, 29)`, `AppDomainUtils.cs(19)`, `DxfMapBase.cs(34)` |
| 2 | IL2075 | `CadHeader.cs(3275, 3314)` |
| 2 | IL2026 | `EnvironmentVars.cs(62)`, `AppDomainUtils.cs(33)` |
| 1 | IL2072 | `CadHeader.cs(3349)` |
| 1 | IL2090 | `PropertyReflection.cs(30)` |
| 1 | IL3050 | `EnumExtensions.cs(17)` |

Disposition, in three groups:

**Reflection over ACadSharp's own types (11 of them).** `Table.cs`,
`CadDocumentBuilder.cs`, `CadHeader.cs` x3, `PropertyReflection.cs`, `DxfMapBase.cs`,
`ReflectionExtensions.cs` x2, `AppDomainUtils.cs(19)`, `LineExtensions.cs`. Every one
of these reaches a type that lives in `ACadSharp.dll`, and the trimmer root keeps
that assembly whole, so the members are there. This is not an argument, it is what
the captures show: `GetHeaderMap()` returns 249 on both sides, and the map is built
by exactly the `CadHeader.cs` and `PropertyReflection.cs` sites above.
`LineExtensions.CreateFromPoints` deserves a specific note because its only product
caller is `DimensionAngular2Line.Center`, and `real_AC1032.dwg` contains a
`DimensionAngular2Line`, so that path is exercised by the parity run rather than
argued about.

**Reflection with no product caller (4).** `EnvironmentVars.Get<T>` (IL2026 and
IL2087, `TypeDescriptor.GetConverter`), `AppDomainUtils.getLoadableTypes` (IL2026,
`Assembly.GetTypes()`), and `ObjectExtensions.ThrowIf<T,E>` (IL2087). I grepped the
whole tree: the only callers of any of them are CSUtilities' own test project, which
is not compiled into `ACadSharp.dll`. They are warnings about code that cannot run.

**The one that would actually break (1).** IL3050 at `EnumExtensions.cs(17)`,
`Enum.GetValues(Type)`, which `RequiresDynamicCode` and which NativeAOT cannot
always satisfy. `GetValueByName<T>` has no caller anywhere in ACadSharp or
CSUtilities either, so it is unreachable as well. If a future ACadSharp version
starts calling it, this is the first warning to look at, and the fix is a source
patch under `patches/`.

None are suppressed. `TrimmerSingleWarn` is off, so all 16 are printed one per site
on every publish, and the count is the thing to watch: it was 2 before the trimmer
root and 16 after, because rooting the assembly makes the trimmer analyse all of it.

## What this adds to CI

The SDK image is 917 MB. The publish itself is 9 s on arm64 and 21 s on x64 under
Rosetta, with ACadSharp already compiled; the restore plus the ACadSharp build adds
roughly 40 s from cold, and the `clang` and `zlib1g-dev` install another 20 s on a
base that lacks them. None of that lands on this repo's CI today, because of the C#
coverage decision above.

## Decision

Go on with the epic. The shim reads DWG correctly under NativeAOT with no runtime
present, on both Linux architectures, shared and static.

Carry these forward:

1. `InvariantGlobalization` and the `ACadSharp` trimmer root are load-bearing at run
   time. Both are guarded by tests in `acadsharp/tests/`.
2. Build Linux on `debian:bookworm-slim` with `dotnet-install.sh`, not on the stock
   SDK image, or the library will not load on the org's own floor.
3. `NativeAOT_StaticInitialization` is gone in .NET 10. Do not put it in a manifest's
   `link_args`. Do list every runtime archive, vxsort included.
4. `osx-arm64` is unmeasured. Build it on `macos-15` before claiming it.
5. Native runners per architecture. Never QEMU.
