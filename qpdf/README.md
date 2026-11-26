# qpdf

[qpdf](https://github.com/qpdf/qpdf) built from source, shipped as a **test-time PDF validator**. libviprs' streaming plot writer produces PDFs, and `qpdf --check` is how they get checked against a pinned version instead of whatever one machine has installed. Nothing in the libviprs crate links it or depends on it.

## Version

`qpdf/VERSION` is the single source of truth (currently 12.4.2), and `qpdf/REVISION` counts packaging revisions of that version (currently 1). `build_qpdf.py` refuses a version that has no entry in `SOURCE_SHA256`, so a bump without a pinned upstream digest fails before anything is downloaded.

## Download

Archives are published to a GitHub Release named after the version: `qpdf-<version>` for the first build, and `qpdf-<version>-viprs.<n>` for revision n, the way the acadsharp tags work. A tag that was published once never gets different bytes, so a rebuild of the same qpdf version always goes under a new revision tag. Each archive has a `.sha256` sidecar with its own digest. `qpdf-latest` (see below) follows the newest complete release.

| Archive | Target |
| --- | --- |
| `qpdf-linux-x64.tgz` | Linux, glibc 2.36 or newer, x86_64 |
| `qpdf-linux-arm64.tgz` | Linux, glibc 2.36 or newer, aarch64 |
| `qpdf-mac-arm64.tgz` | macOS 11 or newer, Apple Silicon (every Mach-O in it, the static libraries included, has a deployment target of 11.0) |

The `qpdf-12.4.2` archives (published 2026-11-25 and left up unchanged). Their mac archive links a libjpeg built for a newer macOS than the README promises (#171), so use the `qpdf-12.4.2-viprs.1` archives below instead:

| Archive | Size (bytes) | sha256 |
| --- | --- | --- |
| `qpdf-linux-x64.tgz` | 4441722 | `1b6befdc17e110b6ba57fc73e3250aa95ff77a0996497c4c5f5b8fbdaa5ced0b` |
| `qpdf-linux-arm64.tgz` | 4383675 | `c90b00092315eba2cc750fb77b8c021f04a00dcb1e91619af132385ca42bb75d` |
| `qpdf-mac-arm64.tgz` | 3499049 | `57e518f306f1e1e69cf7c434b4a271393e955032fae05d28d0cbb8f6e4c05854` |

```
qpdf-<platform>-<cpu>/
├── bin/qpdf                       # finds ../lib by itself (rpath), no library path needed
├── lib/libqpdf.so                 # or .dylib on mac, with the usual symlinks
├── lib/libqpdf.a
├── lib/libjpeg.a                  # the static libjpeg that libqpdf.a needs, for the cmake static target
├── lib/pkgconfig/libqpdf.pc       # relocatable, prefix is ${pcfiledir}/../..
├── lib/cmake/qpdf/                # CMake package config, relocatable (see below)
├── include/qpdf/
├── cmake-args.txt                 # the configure flags used
├── LICENSE                        # qpdf, Apache License 2.0
├── NOTICE.md
├── LICENSES/                      # the statically linked libjpeg
└── metadata/
    ├── BUILDINFO.txt              # version, platform, source digest, crypto, libjpeg-turbo version and digest, driver commit, runtime libraries
    └── CHECKSUMS.txt              # sha256 of every other file in the archive
```

Run it with the binary straight out of the unpacked archive:

```bash
tar xzf qpdf-linux-x64.tgz
qpdf-linux-x64/bin/qpdf --check streamed-plot.pdf
```

## Using it from CMake

```cmake
find_package(qpdf CONFIG REQUIRED)    # with -DCMAKE_PREFIX_PATH=<unpacked archive>
target_link_libraries(app PRIVATE qpdf::libqpdf)          # or qpdf::libqpdf_static
```

The targets resolve inside the unpacked archive wherever it lives: includes and libraries hang off `${_IMPORT_PREFIX}`, the static target links the shipped `lib/libjpeg.a`, and zlib is the plain `z` linker name, so a static consumer needs zlib's development files (`zlib1g-dev` on Debian, already there on macOS). CMake bakes the build machine's libjpeg and zlib paths into the static target on its own, so `relocate-cmake.sh` rewrites them during the build and the verifier refuses an archive that still has one.

## Licences

qpdf is Apache-2.0 since v11, and the Apache License text and upstream's NOTICE ship in every archive. Crypto is qpdf's own native provider, so no OpenSSL or GnuTLS is involved. libjpeg-turbo is linked statically, so its licence (IJG, BSD-3-Clause and zlib terms) is in `LICENSES/`. zlib is linked dynamically and is not redistributed. Runtime libraries are libc, zlib and the C++ runtime (libstdc++ on linux, libc++ on mac), recorded in `metadata/BUILDINFO.txt`. libjpeg-turbo is built from its pinned source tarball on both platforms, so `BUILDINFO` can name its version and digest.

## Building from source

```bash
python3 qpdf/build_qpdf.py                 # linux amd64 + arm64, in containers
python3 qpdf/build_qpdf.py --arch arm64    # one cell
python3 qpdf/build_qpdf.py --platform mac  # mac/arm64, needs a macOS host with cmake and the command line tools
python3 qpdf/build_qpdf.py --upload        # verify, then attach to a draft release on an explicit commit (no --clobber)
```

`qpdf/scripts/find_package_smoke.sh <unpacked-archive> qpdf/tests/fixtures/find_package <version> [run|norun]` copies an unpacked archive to a fresh directory and builds a small cmake consumer of both targets against it (it needs cmake, a C and C++ compiler and zlib's headers). The Linux Dockerfile and the native mac build run it on the tree they just staged, and `norun` builds without executing, for an archive of a foreign architecture.

Every linux cell builds in a container of its own architecture (arm64 under QEMU on an x86_64 host), so the smoke test runs the real binary: it checks the version, links the shared and the static library, and runs `qpdf --check` over `qpdf/tests/fixtures/good.pdf` (must pass) and `corrupt.pdf` (must fail). The fixtures are regenerated with `python3 -I qpdf/tests/make_fixtures.py`.

## Verification

`qpdf/scripts/verify_archive.sh <archive.tgz>` runs on every archive before it is published. It checks the layout, that the licence is Apache-2.0, that `metadata/CHECKSUMS.txt` matches the files exactly, the architecture read from the ELF or Mach-O header, that `libqpdf.a` is not a thin archive, that the pkg-config file is relocatable (prefix, libdir and includedir all hang off `${pcfiledir}`) and that `cmake-args.txt` records the neutral `/staging` prefix instead of a path from the build host, that nothing a consumer reads names a path on the build machine (no absolute path in `lib/cmake`, `lib/pkgconfig`, `share` outside the upstream `doc` and `man` prose, or a text script in `bin`, and every `${_IMPORT_PREFIX}` file the cmake targets name is in the archive), that BUILDINFO records the version in `qpdf/VERSION`, the libjpeg-turbo version and digest, the driver commit and the C++ runtime the platform really uses, that on mac every Mach-O in `bin/` and `lib/` (the members of the static libraries too) has a deployment target of 11.0 or older (read from the load commands by `scripts/macho_minos.py`, so a Linux host can check it), and, when the host can run the binary, the same good-PDF and corrupt-PDF checks as the smoke test.

## Publishing

`.github/workflows/release-qpdf.yml` fires on a push to `release` and on manual dispatch (with a `version` input). It builds the two Linux cells, verifies each archive, uploads it with its sidecar, and moves `qpdf-latest` when both published. The macOS arm64 archive comes from an optional job (a `build_mac` dispatch input, off by default, `continue-on-error`) on a `macos-15` runner, and it does not gate `qpdf-latest`. The 12.4.2 mac archives were built by hand on an Apple Silicon Mac (the `--platform mac` command above, which needs only cmake and the command line tools), verified with the same script, and attached to the release next to the Linux ones.

## The qpdf-latest alias

`qpdf-latest` is a floating release that holds copies of the `.tgz` archives and their `.sha256` sidecars of the newest qpdf release that published every linux cell. It exists so a consumer tracking the edge has one URL. Pin a build to the versioned tag (`qpdf-12.4.2-viprs.1`), never to `qpdf-latest`, which moves.

It does not exist until the first revision is published, and it is moved last, after the versioned release is public and its digests are read back. `.github/workflows/release-qpdf.yml` does that through `tools/publish_latest_tag.sh qpdf <tag> 'qpdf-*.tgz*'` (the trailing star is what carries the sidecars along). Done by hand it is the same steps, with `--latest=false` on the create so the repository's "Latest" badge stays on `pdfium-latest`:

```bash
gh release download qpdf-12.4.2-viprs.1 -R libviprs/libviprs-dep --dir latest --pattern 'qpdf-*.tgz*'
gh release create qpdf-latest -R libviprs/libviprs-dep --target <commit> --latest=false \
  --title 'qpdf (edge, moves)' --notes 'Floating pointer to qpdf-12.4.2-viprs.1. Do not pin to this tag.' latest/*
```

Moving it later means deleting the old `qpdf-latest` release and creating it again, which is what the script does.

## Testing

```bash
pytest qpdf
```
