# acadsharp

A NativeAOT shim over [ACadSharp](https://github.com/DomCR/ACadSharp), so
[libviprs](https://github.com/libviprs/libviprs) can read DWG and DXF through a
plain C ABI without a .NET runtime anywhere near the consumer.

The feasibility spike for
[#45](https://github.com/libviprs/libviprs-dep/issues/45) said yes and the rest
of EPIC G built on it: the C ABI is frozen in
[include/viprs_acadsharp.h](include/viprs_acadsharp.h), the batch protocol in
[docs/WIRE.md](docs/WIRE.md), the ownership and error model in
[docs/ABI.md](docs/ABI.md), and the adapter behind them flattens DWG into that
record stream. Read
[docs/adr/0001-nativeaot-feasibility.md](docs/adr/0001-nativeaot-feasibility.md)
for the go/no-go and every measurement behind it, and
[docs/adr/0002-what-this-decoder-refuses.md](docs/adr/0002-what-this-decoder-refuses.md)
for which entity kinds this build will not flatten and what would reopen each
one; read the three contract documents for what a consumer is actually written
against.

## Downloads

Five archives, published as [`acadsharp-3.7.1-viprs.1`](https://github.com/libviprs/libviprs-dep/releases/tag/acadsharp-3.7.1-viprs.1).
Check the digest before you unpack one: `shasum -a 256 <file>`, or `sha256sum`
on Linux. `tests/test_release_workflow.py` holds the two halves together, so a
download link with no digest beside it fails, and so does a digest with nothing
saying what it hashed.

**`acadsharp/VERSION` says 3.7.1-viprs.2, and the tag `acadsharp-3.7.1-viprs.2`
is not cut yet.** Everything in the table is `viprs.1`, which is the newest
thing there is to download: those URLs resolve and those digests are that
release's, and both stay right whatever this directory does next. What they are
not is a build of the tree you are reading. The shim moved after that tag went
up, so the archives below flatten a drawing differently from the source beside
them, and the run that publishes `viprs.2` replaces every row here with its own
digests.

`static_certified` says whether the static half of that archive was measured to
link **and run**. It is false for mac, where NativeAOT emits Mach-O and the
driver has never attempted a static build. On the two musl targets it records
the C recipe, which was measured; the cargo recipe is not usable there yet, and
`docs/LINKINFO.md` in the archive says why.

| archive | target | static_certified | sha256 | notes |
| --- | --- | --- | --- | --- |
| [`acadsharp-linux-x64.tgz`](https://github.com/libviprs/libviprs-dep/releases/download/acadsharp-3.7.1-viprs.1/acadsharp-linux-x64.tgz) | `x86_64-unknown-linux-gnu` | true | `b8a674b989b6dfdd64ca26d917ad67b7bdf12c38adf557914c6d7b03e71bd180` | |
| [`acadsharp-linux-arm64.tgz`](https://github.com/libviprs/libviprs-dep/releases/download/acadsharp-3.7.1-viprs.1/acadsharp-linux-arm64.tgz) | `aarch64-unknown-linux-gnu` | true | `4b41113b4a9c5b001d20740be09989a2d1850e78990936a897b74583e95e7cb3` | |
| [`acadsharp-musl-x64.tgz`](https://github.com/libviprs/libviprs-dep/releases/download/acadsharp-3.7.1-viprs.1/acadsharp-musl-x64.tgz) | `x86_64-unknown-linux-musl` | true | `9674c971d83bf977729bbba5c7e15ab4d86c7ffe6df95dcefc47d336d4b9b4a3` | |
| [`acadsharp-musl-arm64.tgz`](https://github.com/libviprs/libviprs-dep/releases/download/acadsharp-3.7.1-viprs.1/acadsharp-musl-arm64.tgz) | `aarch64-unknown-linux-musl` | true | `2412c178df33214336504c62156763862b5628f44261bd4e37b66c95068240e1` | |
| [`acadsharp-mac-arm64.tgz`](https://github.com/libviprs/libviprs-dep/releases/download/acadsharp-3.7.1-viprs.1/acadsharp-mac-arm64.tgz) | `aarch64-apple-darwin` | false | `bced75cdea2451215a1f78524233c206c35637736630389bace2a3d5ca371471` | **does not load, see below** |

**The mac archive in that table cannot be linked against.**
`lib/libacadsharp_native.dylib` in `acadsharp-3.7.1-viprs.1` records its own
name as `@rpath/viprs_acadsharp.dylib`, which is the assembly name NativeAOT
writes into `LC_ID_DYLIB` at link time, and no file of that name is anywhere in
the archive. The linker copies that string into your binary, so the loader has
nothing to expand and the process dies before `main`. Nothing caught it because
the only mac check was a `dlopen` on an absolute path, and `dlopen` by absolute
path never reads the recorded name. `docs/LINKINFO.md`'s mac paragraph
describes the fixed archive rather than this one, and says so. The fix is in the
tree (#95) and it ships with `viprs.2`; until that tag is cut there is no mac
archive here worth downloading.

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

`acadsharp/VERSION` is the single source of truth, and it says **3.7.1-viprs.2**.

Two numbers, one file. `3.7.1` is the upstream ACadSharp release, and decides
what source the driver fetches and which pinned sha256 it checks against.
`viprs.2` is the shim revision, and moves when this directory changes without
upstream moving, so two different shims over one ACadSharp release never
produce the same artifact name. That only holds if somebody moves it, and for
one campaign nobody did: seven flattener changes landed on a published
`viprs.1`. `SHIM_DIGESTS` in `build_acadsharp.py` now records which shim each
version is and `tests/test_acadsharp_version.py` holds the tree against the row
for the version in `VERSION`, so a change under `native/` with the number left
still is a red test rather than a second library under an old name.

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
`VERSION`, asks the driver for the pinned source digest, asks it which shim
the tree is and refuses a version pinned to a different one, and proves the
SDK `native/global.json` names is installable, so a bad pin dies there rather
than on five claimed runners. `create-release` makes the tag up front so the
fan-out never races on `gh release create`. Each cell then builds, runs
`scripts/verify_archive.sh` over the archive it just made, links the archive
the way a consumer would, and only then uploads, with `--clobber` so a re-run
replaces its own assets. `fail-fast` is off, so one cell flaking does not
discard four good archives.

That consumer step is not symmetric, because the two lanes cannot run the
same thing. The four container cells run the C and Rust conformance consumers
through `docker run`; `macos-15` has no docker, so the mac cell runs
`scripts/link_consumer_smoke.sh`, which links the unpacked archive through the
two-crate cargo recipe MANUAL.md documents and runs the binary. Before that
step existed the mac cell published an archive nothing had ever linked, which
is #95: its only shared check was a `dlopen` on an absolute path, and `dlopen`
never reads the name a dylib records for itself.

The two lanes also differ in where their cargo comes from. The mac cell takes
whatever the `macos-15` image ships, unpinned; the four container cells pin
theirs with the image (`rust:1.98.1`, and `rust:1.98.1-alpine` on musl). An
image with no cargo in it fails that step rather than skipping it:
`link_consumer_smoke.sh` exits 2 when `cargo` or `python3` is missing, so an
unlinked archive is never reported as a linked one.

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
python3 acadsharp/build_acadsharp.py --plan                   # print the commands
python3 acadsharp/build_acadsharp.py --target linux-arm64     # one cell
python3 acadsharp/build_acadsharp.py --platform musl          # musl, both cpus
python3 acadsharp/build_acadsharp.py                          # the four container cells
```

There is no `--static` flag and there never was one. Every Linux cell in
`STATIC_TARGETS` publishes the static archive alongside the shared library as
part of the same run, so a cell either produces both or fails; `--plan` prints
the second publish command for those cells, which is the closest thing to a
switch. `tests/test_doc_examples.py` runs every invocation above through the
driver's own argument parser, because this block used to show `--static` and
the driver has always rejected it.

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
  VERSION                   # 3.7.1-viprs.2
  include/                  # viprs_acadsharp.h, the frozen C ABI
  docs/                     # ABI.md, WIRE.md and LINKINFO.md, the three frozen
                            # contracts; every one ships inside the archive
  docs/adr/                 # 0001 is the spike's verdict and 0002 is what the
                            # decoder refuses; both stay in the repo
  native/                   # the shim: exports, ABI structs, sources, adapter, wire
  scripts/                  # stage.sh, verify_archive.sh and the link-consumer smoke
  patches/                  # empty; upstream patches go here if a site needs one
  tests/                    # pytest guards over the pins, the shim and the captures
  tests/conformance/c/      # the C consumer, built against the published header
  tests/conformance/rust/   # the generated consumer, declarations from that header
  tests/link_consumer/      # a two-crate workspace that links an unpacked archive
  tests/fixtures/gen/       # writes the DWG fixtures with ACadSharp's own DwgWriter
  tests/fixtures/captures/  # the recorded JIT and NativeAOT reads of each fixture
  tests/expectations/       # the canonical record dump per fixture, and the scenarios
  tests/benchmarks/         # the amplification, streaming and path-versus-memory runs
```

Neither conformance consumer runs under pytest, for the reason
[Conformance](#conformance) gives. What pytest holds between runs is that
neither of them carries its own copy of the boundary, and that the captures
under `tests/expectations/` are a recording of the shim in the tree rather
than of whatever it used to be.
