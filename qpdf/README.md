# qpdf

[qpdf](https://github.com/qpdf/qpdf) built from source, shipped as a **test-time PDF validator**. libviprs' streaming plot writer produces PDFs, and `qpdf --check` is how they get checked against a pinned version instead of whatever one machine has installed. Nothing in the libviprs crate links it or depends on it.

## Version

`qpdf/VERSION` is the single source of truth (currently 12.4.2). `build_qpdf.py` refuses a version that has no entry in `SOURCE_SHA256`, so a bump without a pinned upstream digest fails before anything is downloaded.

## Download

Archives are published to the `qpdf-<version>` GitHub Release, plus a `qpdf-latest` tag that always points at the newest complete release. Each archive has a `.sha256` sidecar with its own digest.

| Archive | Target |
| --- | --- |
| `qpdf-linux-x64.tgz` | Linux, glibc 2.36 or newer, x86_64 |
| `qpdf-linux-arm64.tgz` | Linux, glibc 2.36 or newer, aarch64 |
| `qpdf-mac-arm64.tgz` | macOS 11 or newer, Apple Silicon |

```
qpdf-<platform>-<cpu>/
├── bin/qpdf                       # finds ../lib by itself (rpath), no library path needed
├── lib/libqpdf.so                 # or .dylib on mac, with the usual symlinks
├── lib/libqpdf.a
├── lib/pkgconfig/libqpdf.pc       # relocatable, prefix is ${pcfiledir}/../..
├── lib/cmake/                     # CMake package config
├── include/qpdf/
├── cmake-args.txt                 # the configure flags used
├── LICENSE                        # qpdf, Apache License 2.0
├── NOTICE.md
├── LICENSES/                      # the statically linked libjpeg
└── metadata/
    ├── BUILDINFO.txt              # version, platform, source digest, crypto, jpeg
    └── CHECKSUMS.txt              # sha256 of every other file in the archive
```

Run it with the binary straight out of the unpacked archive:

```bash
tar xzf qpdf-linux-x64.tgz
qpdf-linux-x64/bin/qpdf --check streamed-plot.pdf
```

## Licences

qpdf is Apache-2.0 since v11, and the Apache License text and upstream's NOTICE ship in every archive. Crypto is qpdf's own native provider, so no OpenSSL or GnuTLS is involved. libjpeg-turbo is linked statically, so its licence (IJG, BSD-3-Clause and zlib terms) is in `LICENSES/`. zlib is linked dynamically and is not redistributed. Runtime libraries are libc, libstdc++ and zlib, recorded in `metadata/BUILDINFO.txt`.

## Building from source

```bash
python3 qpdf/build_qpdf.py                 # linux amd64 + arm64, in containers
python3 qpdf/build_qpdf.py --arch arm64    # one cell
python3 qpdf/build_qpdf.py --platform mac  # mac/arm64, needs a macOS host with cmake and jpeg-turbo
python3 qpdf/build_qpdf.py --upload        # verify, then publish to the release
```

Every linux cell builds in a container of its own architecture (arm64 under QEMU on an x86_64 host), so the smoke test runs the real binary: it checks the version, links the shared and the static library, and runs `qpdf --check` over `qpdf/tests/fixtures/good.pdf` (must pass) and `corrupt.pdf` (must fail). The fixtures are regenerated with `python3 -I qpdf/tests/make_fixtures.py`.

## Verification

`qpdf/scripts/verify_archive.sh <archive.tgz>` runs on every archive before it is published. It checks the layout, that the licence is Apache-2.0, that `metadata/CHECKSUMS.txt` matches the files exactly, the architecture read from the ELF or Mach-O header, that `libqpdf.a` is not a thin archive, that the pkg-config file is relocatable, that BUILDINFO records the version in `qpdf/VERSION`, and, when the host can run the binary, the same good-PDF and corrupt-PDF checks as the smoke test.

## Publishing

`.github/workflows/release-qpdf.yml` fires on a push to `release` and on manual dispatch (with a `version` input). It builds the two Linux cells, verifies each archive, uploads it with its sidecar, and moves `qpdf-latest` when both published. The macOS arm64 archive is not built yet: its job is optional (a `build_mac` dispatch input, off by default, `continue-on-error`) and does not gate `qpdf-latest`.

## Testing

```bash
pytest qpdf
```
