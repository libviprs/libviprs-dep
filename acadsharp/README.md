# acadsharp

A NativeAOT shim over [ACadSharp](https://github.com/DomCR/ACadSharp), so
[libviprs](https://github.com/libviprs/libviprs) can read DWG and DXF through a
plain C ABI without a .NET runtime anywhere near the consumer.

This directory currently holds the feasibility spike for
[#45](https://github.com/libviprs/libviprs-dep/issues/45) and the skeleton the
rest of the epic builds in. Read
[docs/adr/0001-nativeaot-feasibility.md](docs/adr/0001-nativeaot-feasibility.md)
first: it carries the go/no-go and every measurement behind it.

## Downloads

**No archives are published yet.** There is no `acadsharp-3.7.1-viprs.1` release
on this repo, so there is no URL to hand you. Build them yourself with
[Building](#building), or cut the release with [Publishing](#publishing). This
table gets the five URLs and their sha256s the day that runs, and
`tests/test_release_workflow.py` holds the two halves together: a download link
with no digest beside it fails, and so does a digest with nothing saying what it
hashed.

| archive | target | sha256 |
| --- | --- | --- |
| `acadsharp-linux-x64.tgz` | `x86_64-unknown-linux-gnu` | not published |
| `acadsharp-linux-arm64.tgz` | `aarch64-unknown-linux-gnu` | not published |
| `acadsharp-musl-x64.tgz` | `x86_64-unknown-linux-musl` | not published |
| `acadsharp-musl-arm64.tgz` | `aarch64-unknown-linux-musl` | not published |
| `acadsharp-mac-arm64.tgz` | `aarch64-apple-darwin` | not published |

Five archives, not six: there is no Microsoft-platform artifact here, and the
mac slice is Apple Silicon only. The Rust triple lives in each archive's
`metadata/LINKINFO.json`, which is what the `acadsharp-rs` crate's `build.rs`
reads and which [`docs/LINKINFO.md`](docs/LINKINFO.md) specifies; the version
lives in the release tag rather than in the filename, the same convention
`pdfium/` and `zstd/` use.

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

## Publishing

`.github/workflows/release-acadsharp.yml` cuts the release. It fires on a push
to the `release` branch and can be dispatched by hand, which is how the first
one has to happen: publishing a version that is already committed changes no
file, so there is nothing to push.

The shape is the same as `release-zstd.yml`. A `resolve-version` job reads
`VERSION`, asks the driver for the pinned source digest and proves the SDK
`native/global.json` names is installable, so a bad pin dies there rather than
on five claimed runners. `create-release` makes the tag up front so the fan-out
never races on `gh release create`. Each cell then builds, runs
`scripts/verify_archive.sh` over the archive it just made, and only then
uploads, with `--clobber` so a re-run replaces its own assets. `fail-fast` is
off, so one cell flaking does not discard four good archives.

One thing is deliberately different from the zstd workflow: nothing is emulated.
ADR 0001 measured a cross-architecture publish producing the object file and
then failing at the native link, and the .NET runtime documents
`qemu-user-static` as unsupported, so every cell runs on a runner of its own
architecture.

A dispatched `version` input overrides `VERSION` everywhere, not just in
the tag: it reaches `resolve-version`, the tag, the notes and the `--version`
each build passes to the driver. That matters because the first release has to
be cut by dispatch, and an override that only the tag believed would publish
archives built from the committed version under a different tag.

`release-notes` then reads `artifact_version` out of every archive that
reached the release and refuses any that is not the version being published,
or that records none at all. A refused archive is named in the notes, taken
off the release, and fails the run. A release whose assets disagree with its
own tag is worse than one that fails, because it looks right.

The release is not marked a pre-release. `build_acadsharp.upload_release()`
creates a plain release for the same tag, so marking it here would make the
kind of release depend on whether a laptop or CI cut it, and a pre-release is
never "latest".

The release notes are generated rather than typed. The preamble comes from
`VERSION`, `native/global.json`, `include/viprs_acadsharp.h` and
`build_acadsharp.py`; the per-archive rows are hashed from the published assets
and read out of each one's `metadata/LINKINFO.json`. A final job runs whatever
happened above and names every target that did not publish, because a release
that quietly ships four of five is worse than one that ships four and says so.

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

## Conformance

Two consumers link the published library and exercise the boundary from
outside: a C one that includes the header, and a Rust one that generates every
declaration from that same header at build time. Both build and run in a
container with no .NET in it, which is the other half of the point.

```bash
acadsharp/tests/conformance/c/run.sh
acadsharp/tests/conformance/rust/run.sh
```

With no `VIPRS_LIB_DIR` they look in the build tree's publish directory. Point
it at an unpacked `acadsharp-*.tgz` and they run against the archive instead,
which is what CI does:

```bash
tar xzf bin/acadsharp-linux-arm64.tgz -C bin/unpacked
VIPRS_LIB_DIR=$PWD/bin/unpacked/acadsharp-linux-arm64 \
    acadsharp/tests/conformance/c/run.sh
```

Each runner asks the library whether it carries the test-only exports and says
which mode it ran in. They exist only in the `AbiTest` configuration, and the
cases that make the library throw and that read its live handle count need
them, so a run against a release build reports those as skipped rather than
pretending.

`.github/workflows/acadsharp-conformance.yml` runs all of this on a push that
touches `acadsharp/`: it builds the linux/arm64 archive, verifies it, runs both
consumers against the unpacked archive, then publishes the `AbiTest`
configuration in the image the archive build already made and runs both again.
It is a file of its own rather than a job in `ci.yml`, and ADR 0001's C#
coverage section says why.

## Layout

```
acadsharp/
  build_acadsharp.py        # pins, target list, and the publish commands
  VERSION                   # 3.7.1-viprs.1
  native/                   # the shim: csproj, exports, probe, SDK pin, lock file
  patches/                  # empty; upstream patches go here if a site needs one
  docs/                     # ABI.md, WIRE.md and LINKINFO.md, the three frozen
                            # contracts; every one ships inside the archive
  docs/adr/                 # 0001 is the spike's verdict, and stays in the repo
  tests/                    # pytest guards over the pins, the project and the captures
  tests/fixtures/gen/       # writes the DWG fixtures with ACadSharp's own DwgWriter
  tests/fixtures/captures/  # the recorded JIT and NativeAOT reads of each fixture
```
