# acadsharp

A NativeAOT shim over [ACadSharp](https://github.com/DomCR/ACadSharp), so
[libviprs](https://github.com/libviprs/libviprs) can read DWG and DXF through a
plain C ABI without a .NET runtime anywhere near the consumer.

Nothing is published yet. This directory currently holds the feasibility spike
for [#45](https://github.com/libviprs/libviprs-dep/issues/45) and the skeleton
the rest of the epic builds in. Read
[docs/adr/0001-nativeaot-feasibility.md](docs/adr/0001-nativeaot-feasibility.md)
first: it carries the go/no-go and every measurement behind it.

## Why a shim and not a port

DWG is a closed, versioned binary format, and ACadSharp is the most complete
open implementation of it in any language. Writing one in Rust is years of
work. NativeAOT compiles the C# ahead of time into an ordinary ELF or Mach-O
object with its own GC and runtime statically inside it, so the consumer links
a library and never learns that the code started life as C#.

## Version

`acadsharp/VERSION` is the single source of truth, and it says **3.7.1-viprs.1**.

Two numbers, one file. `3.7.1` is the upstream ACadSharp release, and decides
what source the driver fetches and which pinned sha256 it checks against.
`viprs.1` is the shim revision, and moves when this directory changes without
upstream moving, so two different shims over one ACadSharp release never
produce the same artifact name.

Bumping the upstream half means adding the new tarball's sha256 to
`SOURCE_SHA256` in `build_acadsharp.py`. The driver refuses to build a version
it has no pinned digest for.

## The source pin has two halves

`src/CSUtilities` is a git submodule of ACadSharp, and GitHub-generated source
tarballs never contain submodules. `ACadSharp.csproj` imports
`..\CSUtilities\CSMath\CSMath.projitems` during project evaluation, so a
tarball-only tree fails before restore with a missing-import error that never
mentions the submodule. That is why the driver carries `CSUTILITIES_COMMIT`
alongside the tarball digest, and why `check_source_tree()` fails loudly on the
incomplete tree rather than letting msbuild do it obscurely.

## Building

Everything runs in a container. There is no host toolchain to install. Linux
builds happen on `debian:bookworm-slim` with the SDK installed into it, not on
the stock .NET SDK image: that one is Ubuntu noble and publishes a library
needing GLIBC_2.38, which will not load on the glibc 2.36 floor the rest of
this repo ships against. ADR 0001 has the measurement.

```bash
python3 acadsharp/build_acadsharp.py --plan                  # print the commands
python3 acadsharp/build_acadsharp.py --target linux-arm64    # shared library
python3 acadsharp/build_acadsharp.py --target linux-x64 --static
```

## Layout

```
acadsharp/
  build_acadsharp.py        # pins, target list, and the publish commands
  VERSION                   # 3.7.1-viprs.1
  native/                   # the shim: csproj, exports, probe, SDK pin, lock file
  patches/                  # empty; upstream patches go here if a site needs one
  docs/adr/                 # 0001 is the spike's verdict
  tests/                    # pytest guards over the pins, the project and the captures
  tests/fixtures/gen/       # writes the DWG fixtures with ACadSharp's own DwgWriter
  tests/fixtures/captures/  # the recorded JIT and NativeAOT reads of each fixture
```
