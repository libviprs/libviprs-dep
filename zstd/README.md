# zstd

Pre-compiled [zstd](https://github.com/facebook/zstd) libraries for [libviprs](https://github.com/libviprs/libviprs). Built from source here and published as GitHub Releases on this repo. No release has been cut yet, so see [Download](#download).

zstd is the compression codec behind libviprs' packfile support. Today `libviprs --features packfile` pulls in `zip` → `zstd` → `zstd-safe` → `zstd-sys`, and `zstd-sys`' build script compiles 216 vendored C files on every clean build. `zstd-sys` already knows how to link a prebuilt library instead (it has a `pkg-config` feature and honours `ZSTD_SYS_USE_PKG_CONFIG`), so publishing the library here removes that compile without forking anything.

We build it here rather than trusting a distro package to:
- Pin one version and one build configuration across every platform we ship
- Ship glibc **and** musl builds, so a consumer isn't forced onto a particular libc
- Ship a static archive **and** a shared library, so a consumer isn't forced into a linking strategy
- Ship a relocatable `libzstd.pc`, so `ZSTD_SYS_USE_PKG_CONFIG` works against an unpacked archive with no rewriting

## Version

`zstd/VERSION` is the single source of truth, and it currently says **1.5.7**.

That number is not a guess at "latest". `libviprs` gets zstd through `zstd-sys v2.0.16+zstd.1.5.7`, whose version suffix names the zstd release it vendors, so 1.5.7 is exactly what the crate compiles today. Publishing the same version means swapping the vendored build for this one is a link-time change and nothing else — no format, API or ABI difference to reason about. Bump this only alongside a `zstd-sys` bump, and add the new release tarball's sha256 to `SOURCE_SHA256` in `build_zstd.py` when you do; the build refuses to download a version it has no pinned hash for.

## Download

**No archives are published yet.** There is no `zstd-1.5.7` release on this repo, so there is no URL to hand you. Build them yourself with [Building from source](#building-from-source), or cut the release with [Publishing](#publishing). This section gets the six URLs and their sha256s the day that runs.

The six archives the build produces, which is what a release carries:

```
zstd-linux-x64.tgz
zstd-linux-arm64.tgz
zstd-musl-x64.tgz
zstd-musl-arm64.tgz
zstd-mac-arm64.tgz
zstd-mac-x64.tgz
```

| Archive suffix | libc | Compatible runtime |
| --- | --- | --- |
| `linux-x64`, `linux-arm64` | glibc | Debian, Ubuntu, RHEL, most mainstream distros |
| `musl-x64`, `musl-arm64` | musl | Alpine, any image built `FROM alpine:*`, musl-based distroless |
| `mac-arm64`, `mac-x64` | — | macOS 11+ on Apple Silicon / Intel |

Pick the archive whose libc matches the process that will load it. A glibc `.so` loaded from a musl process fails at `dlopen` time, and static-linking a glibc `libzstd.a` into a musl binary fails at link time.

Each archive extracts to a self-contained directory:

```bash
tar xzf zstd-linux-x64.tgz
```

```
zstd-linux-x64/
  lib/
    libzstd.a                 # static archive, PIC, for rustc/`-l static=zstd`
    libzstd.so -> libzstd.so.1.5.7
    libzstd.so.1 -> libzstd.so.1.5.7
    libzstd.so.1.5.7          # shared library, for dlopen / dynamic linking
    pkgconfig/libzstd.pc      # prefix is relative to the file's own directory
    cmake/zstd/*.cmake        # CMake package config, for find_package(zstd)
  include/
    zstd.h
    zstd_errors.h
    zdict.h
  cmake-args.txt              # the exact CMake configure flags used
  LICENSE                     # zstd's own BSD-3-Clause licence
```

On mac the shared library is `libzstd.dylib` → `libzstd.1.dylib` → `libzstd.1.5.7.dylib`, built with `install_name` `@rpath/libzstd.1.dylib` so it loads from wherever the consumer puts it.

### Using it with pkg-config

`libzstd.pc` ships with its prefix written as `${pcfiledir}/../..`, so it resolves relative to wherever the archive was unpacked:

```bash
tar xzf zstd-linux-x64.tgz
export PKG_CONFIG_PATH="$PWD/zstd-linux-x64/lib/pkgconfig"
pkg-config --modversion libzstd     # 1.5.7
pkg-config --libs libzstd           # -L/…/zstd-linux-x64/lib -lzstd
```

That is the form `zstd-sys` consumes when built with its `pkg-config` feature or with `ZSTD_SYS_USE_PKG_CONFIG=1` in the environment. Wiring libviprs up to it is a change to that repo and is deliberately not part of this one.

### Using the static archive directly

```bash
cc main.c -I zstd-linux-x64/include zstd-linux-x64/lib/libzstd.a -pthread -o main
```

`-pthread` is needed because the library is built with `ZSTD_MULTITHREAD_SUPPORT=ON`, matching every distro package and zstd's own release binaries. `libzstd.pc` records it in `Libs.private`, so `pkg-config --static --libs libzstd` picks it up for you.

## Building from source

```bash
# Default matrix — {linux, musl} × {amd64, arm64}, version read from zstd/VERSION
python3 zstd/build_zstd.py

# One platform, one arch
python3 zstd/build_zstd.py --platform musl --arch arm64

# macOS (needs a macOS host — there is no macOS container image)
python3 zstd/build_zstd.py --platform mac --arch arm64

# Everything at once
python3 zstd/build_zstd.py --parallel

# Build, verify, and publish to the zstd-<version> GitHub Release (see Publishing)
python3 zstd/build_zstd.py --upload
```

The pipeline per `(platform, arch)`:

1. Generate a Dockerfile pinned to the **target** architecture (`FROM --platform=linux/arm64 …`). Nothing is cross-compiled; a foreign-arch build runs under the daemon's QEMU emulation, which for a library this size costs minutes.
2. Download the pinned upstream release tarball and check it against the sha256 recorded in `build_zstd.py`. A checksum mismatch fails the build before anything is compiled.
3. Configure once with both `ZSTD_BUILD_STATIC=ON` and `ZSTD_BUILD_SHARED=ON` — CMake emits `libzstd.a` and `libzstd.so` from a single pass, so there is no two-phase build here.
4. `cmake --install` into a staging directory that becomes the archive.
5. Rewrite `libzstd.pc`'s prefix to `${pcfiledir}/../..` so the archive is relocatable, and stage `cmake-args.txt` plus upstream's `LICENSE` alongside the binaries.
6. **Smoke test**: compile a compress/decompress round-trip against the staged static archive *and* against the staged shared library, and run both. Because the container is the target architecture, this executes the real artifacts rather than merely linking them. It also asserts `ZSTD_versionString()` matches `VERSION`, so an archive can't be published under the wrong number.
7. Package as `zstd-{platform}-{cpu}.tgz` and run `scripts/verify_archive.sh` over the finished tarball. `--upload` only ever sees archives that passed.

macOS takes the same path without Docker: CMake runs natively on the host, with `-DCMAKE_OSX_ARCHITECTURES` selecting the slice. The smoke test still links both libraries there; it only runs them when the slice matches the host CPU, since an arm64 Mac can't execute an x86_64 binary without Rosetta.

### Requirements

- Docker with buildx (linux + musl targets)
- CMake and a C compiler (mac target)
- `gh` CLI (only for `--upload`)

### Build configuration

| CMake argument | Value | Reason |
| --- | --- | --- |
| `CMAKE_BUILD_TYPE` | `Release` | Optimised, no assertions |
| `CMAKE_INSTALL_LIBDIR` | `lib` | GNUInstallDirs would resolve this to `lib/x86_64-linux-gnu` on Debian and break the documented layout |
| `CMAKE_POSITION_INDEPENDENT_CODE` | `ON` | The static archive gets linked into Rust cdylibs and PIEs |
| `ZSTD_BUILD_STATIC` / `ZSTD_BUILD_SHARED` | `ON` / `ON` | Both artifacts from one configure pass |
| `ZSTD_BUILD_PROGRAMS` / `ZSTD_BUILD_TESTS` / `ZSTD_BUILD_CONTRIB` | `OFF` | We publish a library, not the CLI |
| `ZSTD_LEGACY_SUPPORT` | `OFF` | Matches `zstd-sys`' default, which builds without its `legacy` feature — v0.4–v0.7 frames aren't decodable by what libviprs uses today either |
| `ZSTD_MULTITHREAD_SUPPORT` | `ON` | Matches distro packages and zstd's own binaries; costs consumers a `-pthread` |
| `CMAKE_OSX_ARCHITECTURES` | `arm64` / `x86_64` | mac only — selects the slice |
| `CMAKE_INSTALL_NAME_DIR` | `@rpath` | mac only — without it the dylib records the build machine's staging path |

The same list is written into `cmake-args.txt` inside every archive, so a consumer debugging a link error can read the exact flags that produced the binaries.

## Verification

`scripts/verify_archive.sh <tgz>` runs over the packaged tarball, and `build_zstd.py` runs it on every archive it produces before `--upload` can publish anything. It checks:

1. The archive holds exactly one top-level directory, named to match the tarball, with the documented layout: both libraries, all three public headers, the pkg-config file, `cmake-args.txt`, `LICENSE`.
2. `libzstd.a` is a real archive (`!<arch>`, never `!<thin>`), of a plausible size, holding at least ten objects, with a symbol index that **defines** `ZSTD_compress`, `ZSTD_decompress`, `ZSTD_versionNumber`, `ZSTD_createCCtx` and `ZDICT_trainFromBuffer`.
3. Every object inside `libzstd.a`, and the shared library, is built for the architecture the filename claims. Machine type is read straight out of the ELF / Mach-O headers, so verifying an arm64 archive on an x64 runner needs no cross toolchain.
4. The shared library is a shared object (`ET_DYN` / `MH_DYLIB`) and exports the public API — not an executable, not a stray relocatable object.
5. `libzstd.pc` is relocatable and carries no build-machine absolute paths.

The script understands both GNU and BSD `ar` layouts, because a macOS archive stores long member names inside the member data (`#1/<len>`) and a linux one doesn't.

## Publishing

`.github/workflows/release-zstd.yml` cuts the release, so a version bump ships the same way pdfium's does rather than from whoever owns a Mac that week. It reads `zstd/VERSION`, refuses a version with no `SOURCE_SHA256` entry before anything builds, creates the `zstd-<version>` release, and then runs one job per cell: four `ubuntu-latest` jobs for `{linux, musl} × {amd64, arm64}` (the arm64 pair under QEMU, registered per job) and two `macos-15` jobs for the mac slices. Every job builds, runs `scripts/verify_archive.sh` over the archive it just produced, and only then uploads it with `gh release upload --clobber`. `--upload` is deliberately not passed to the driver there, because that would publish before the verifier got a look.

Two ways to fire it:

- Merge the bump into the `release` branch. That is the push that publishes pdfium too.
- Run it by hand (`workflow_dispatch`), optionally overriding the version. The first publish of a version that is already committed changes no file, so there is nothing to push, which makes this the way to cut `zstd-1.5.7`.

The local path still works and runs the same checks:

```bash
python3 zstd/build_zstd.py --parallel --upload
```

It needs Docker for the four Linux cells and a macOS host for the two mac ones, which is the whole reason the workflow exists.

Once a release is up, put the six URLs and their digests into [Download](#download) and drop the note saying there are none:

```bash
gh release download zstd-1.5.7 -R libviprs/libviprs-dep
shasum -a 256 zstd-linux-x64.tgz zstd-linux-arm64.tgz zstd-musl-x64.tgz zstd-musl-arm64.tgz zstd-mac-arm64.tgz zstd-mac-x64.tgz
```

`tests/test_zstd_version.py` fails if the README ends up claiming both or neither, and if URLs appear without digests beside them.

## Testing

```bash
pip install pytest
pytest zstd/tests -v
```

| File | What it covers |
| --- | --- |
| `test_zstd_naming.py` | Archive / staging directory / release tag naming, and that they agree with each other |
| `test_zstd_version.py` | `VERSION` parses, has a pinned source hash, drives the release notes, and agrees with what this README claims about the release |
| `test_zstd_dockerfile.py` | Generated Dockerfile per platform and arch: base image, pinned checksum, CMake flags, step ordering |
| `test_zstd_resolve_jobs.py` | `--platform` / `--arch` resolution and arch aliases |
| `test_zstd_verify_archive.py` | Builds a real archive with the host compiler, then breaks it one way at a time and asserts `verify_archive.sh` rejects each break |
| `test_zstd_release_workflow.py` | `release-zstd.yml`: what triggers it, that an unpinned version stops it before any build, that every cell the driver builds is in the matrix, and that verify runs between build and upload |
| `test_ci_coverage.py` | That CI's paths are discovered from the tree rather than hardcoded, so the next dependency directory is covered the day it lands |

## Reference

See [`MANUAL.md`](../MANUAL.md) at the repo root for the full man-page-style reference.
