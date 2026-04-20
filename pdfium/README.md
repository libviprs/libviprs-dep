# PDFium

Pre-compiled PDFium libraries for [libviprs](https://github.com/libviprs/libviprs). Built from source and published as GitHub Releases on this repo.

[PDFium](https://pdfium.googlesource.com/pdfium/) is Google's open-source PDF rendering library, used by libviprs to rasterize PDF pages into pixel buffers for tile pyramid generation.

We compile PDFium from source rather than using third-party prebuilt binaries to:
- Pin to a specific chromium branch for reproducibility
- Ensure the binary includes all symbols required by the `pdfium-render` Rust bindings
- Control build configuration (no V8, no XFA)
- Ship both a shared library and a static archive so downstream crates can pick either linking strategy
- Ship both glibc and musl builds so downstream consumers are not forced onto a particular libc

## Download

Archives are available from [Releases](https://github.com/libviprs/libviprs-dep/releases). Each release tag bundles four archives covering the full matrix of `{linux, musl}` × `{x64, arm64}`:

```
https://github.com/libviprs/libviprs-dep/releases/download/pdfium-7725/pdfium-linux-x64.tgz
https://github.com/libviprs/libviprs-dep/releases/download/pdfium-7725/pdfium-linux-arm64.tgz
https://github.com/libviprs/libviprs-dep/releases/download/pdfium-7725/pdfium-musl-x64.tgz
https://github.com/libviprs/libviprs-dep/releases/download/pdfium-7725/pdfium-musl-arm64.tgz
```

| Archive suffix | libc | Compatible runtime |
| --- | --- | --- |
| `linux-x64`, `linux-arm64` | glibc | Debian, Ubuntu, RHEL, most mainstream distros |
| `musl-x64`, `musl-arm64`   | musl  | Alpine, any container built `FROM alpine:*`, musl-based distroless images |

Pick the archive whose libc matches the runtime that will load PDFium. Loading a glibc `.so` from a musl process (or vice versa) fails at `dlopen` time with opaque errors — the libc mismatch is the single most common source of "pdfium doesn't load in my container" tickets.

Each archive extracts to a self-contained directory:

```bash
tar xzf pdfium-linux-x64.tgz
```

```
pdfium-linux-x64/
  lib/
    libpdfium.so       # shared library, for dlopen and dynamic linking
    libpdfium.a        # static archive, for pdfium-render/static or equivalent
  include/
    fpdfview.h
    fpdf_annot.h
    ...
  args.gn              # GN args used for the shared-library build
  args.static.gn       # GN args used for the static-archive build
  LICENSE
```

### Installing the shared library

```bash
sudo cp pdfium-linux-x64/lib/libpdfium.so /usr/local/lib/
sudo ldconfig
```

### Using the static archive

`pdfium-render`'s `static` feature expects `libpdfium.a` to be discoverable via `PDFIUM_STATIC_LIB_PATH`:

```bash
export PDFIUM_STATIC_LIB_PATH=/path/to/pdfium-linux-x64/lib
cargo build --features pdfium-render/static
```

See the [pdfium-render documentation](https://docs.rs/pdfium-render/) for the full list of feature flags.

## Building from source

The `build_pdfium.py` script compiles PDFium inside Docker containers and produces both a `.so` and a `.a` per platform × architecture. By default it builds the full linux + musl matrix. All builds run on an amd64 host — arm64 binaries are cross-compiled, avoiding slow QEMU emulation.

The build pipeline (inspired by [bblanchon/pdfium-binaries](https://github.com/bblanchon/pdfium-binaries)):

1. Install system dependencies and depot_tools
2. Configure gclient with `checkout_configuration=small` (skips V8, test deps, cipd)
3. Checkout PDFium source at the target chromium branch
4. Install build dependencies and target architecture sysroot (linux) or musl-cross-make toolchain (musl)
5. Apply the **base** platform patch — symbol visibility, musl toolchain GN config where relevant. Leaves `BUILD.gn` alone so `component("pdfium")` resolves to `static_library` under `is_component_build=false`.
6. `gn gen out/Static` + `ninja -C out/Static pdfium` — produces `libpdfium.a`
7. Apply the **shared** platform patch — rewrites `component("pdfium")` to `shared_library("pdfium")`.
8. `gn gen out/Shared` + `ninja -C out/Shared pdfium` — produces `libpdfium.so`
9. Verify both outputs
10. Stage artifacts into a single directory and package as `pdfium-{platform}-{gn_cpu}.tgz`

The two-phase ninja build is the cleanest way to emit both a static archive and a shared library from a single source checkout without duplicating the `component("pdfium")` target body inside `BUILD.gn`. It roughly doubles the ninja time per combo, but the expensive Docker setup steps (apt, depot_tools, gclient sync, runhooks) only run once per combo.

### Requirements

- Docker with buildx support
- Python 3.7+
- `gh` CLI (only for `--upload`)

### Usage

```bash
# Build the full default matrix (linux + musl, amd64 + arm64 — four archives)
python3 build_pdfium.py 7725

# Build a single platform
python3 build_pdfium.py 7725 --platform linux
python3 build_pdfium.py 7725 --platform musl
python3 build_pdfium.py 7725 --platform mac

# Build multiple specific platforms
python3 build_pdfium.py 7725 --platform linux musl

# Build a single architecture across all default platforms
python3 build_pdfium.py 7725 --arch amd64

# Combine: single platform, single arch
python3 build_pdfium.py 7725 --platform musl --arch arm64

# Build archs in parallel (within each platform pass)
python3 build_pdfium.py 7725 --parallel

# Build and upload to GitHub Releases
python3 build_pdfium.py 7725 --upload

# Custom output directory
python3 build_pdfium.py 7725 --output-dir ./artifacts
```

The version argument maps to a PDFium chromium branch. For example, `7725` checks out `origin/chromium/7725` from https://pdfium.googlesource.com/pdfium/.

### Finding version numbers

Available chromium branch numbers can be found by browsing the PDFium Git repository:

```bash
# List all available chromium branches
git ls-remote --heads https://pdfium.googlesource.com/pdfium/ 'refs/heads/chromium/*'
```

Branch numbers increase over time. Higher numbers correspond to newer chromium releases. Pick a branch that aligns with the chromium version you want to target.

### Output

Archives are written to `./bin/` by default (gitignored). With the default platform set, a full run produces:

```
bin/
  pdfium-linux-x64.tgz
  pdfium-linux-arm64.tgz
  pdfium-musl-x64.tgz
  pdfium-musl-arm64.tgz
```

Archives follow the naming convention `pdfium-{platform}-{gn_cpu}.tgz` and extract to a directory of the same name:

```
pdfium-{platform}-{gn_cpu}/
  lib/libpdfium.so       # shared library
  lib/libpdfium.a        # static archive
  include/*.h            # public C headers
  args.gn                # GN build arguments for the shared build
  args.static.gn         # GN build arguments for the static build
  LICENSE                # PDFium license
```

When `--upload` is used, a GitHub Release tagged `pdfium-{version}` is created with all archives attached.

### Parallel builds

By default, architectures within a platform are built sequentially. Use `--parallel` to build all architectures of a platform simultaneously:

```bash
python3 build_pdfium.py 7725 --parallel
```

Platform passes still run sequentially (so the full default invocation builds linux first, then musl), but within each platform pass the amd64 and arm64 Docker builds run concurrently in separate threads.

During a parallel build, you can switch between each architecture's build output:

| Key | Action |
| --- | --- |
| `Tab` | Cycle to the next architecture's output |
| `1` | Show amd64 output |
| `2` | Show arm64 output |

The active view is shown in the header bar. Each architecture's output is buffered independently, so switching views replays recent output without losing anything.

`--parallel` has no effect when building a single architecture with `--arch`.

### Build time

PDFium is a large C++ project. With the two-phase build, expect:
- ~20–40 minutes per architecture per platform (roughly double the single-phase time)
- `--parallel` can cut wall time roughly in half when building both architectures of the same platform

A full default matrix build on a fast machine takes 60–90 minutes wall time with `--parallel` enabled.

### Cross-compilation

**linux** — arm64 binaries are cross-compiled inside an amd64 Docker container. This works by:
- Installing `g++-aarch64-linux-gnu` for the cross-compiler toolchain
- Running `install-sysroot.py --arch=arm64` to install a Debian arm64 sysroot
- Setting `target_cpu = "arm64"` in GN args

PDFium's build system uses its own bundled clang with the sysroot, producing a native arm64 `.so` without needing QEMU.

**musl** — both x86_64 and aarch64 musl builds use the [musl-cross-make](https://musl.cc) toolchains downloaded at build time (`x86_64-linux-musl-cross.tgz`, `aarch64-linux-musl-cross.tgz`). A custom GN toolchain definition is installed at `build/toolchain/linux/musl/BUILD.gn` that routes compilation through the prefixed GCC toolchain. `BUILDCONFIG.gn` is patched to declare an `is_musl` arg and select the musl toolchain when it is set.

## Build configuration

PDFium is compiled with these GN arguments:

| Argument | Value | Reason |
| --- | --- | --- |
| `is_debug` | `false` | Release build |
| `pdf_is_standalone` | `true` | No chromium browser integration |
| `pdf_enable_v8` | `false` | No JavaScript engine needed |
| `pdf_enable_xfa` | `false` | No XFA form support needed |
| `is_component_build` | `false` | Single self-contained library per target |
| `use_custom_libcxx` | `true` (linux) / `false` (musl) | Bundle libc++ on glibc to stay portable; rely on musl-cross-make's libstdc++ on musl |
| `treat_warnings_as_errors` | `false` | Avoid build failures from upstream warnings |
| `pdf_use_skia` | `false` | Use default rendering backend |
| `pdf_use_partition_alloc` | `false` | Skip complex allocator that fails on some platforms |
| `clang_use_chrome_plugins` | `false` | Skip Chrome's custom clang plugins |
| `target_cpu` | `"x64"` / `"arm64"` | Target architecture |
| `target_os` | `"linux"` | Target operating system |
| `is_musl` | `true` (musl only) | Select the musl toolchain routing in BUILDCONFIG.gn |

### Platform patches

Platform-specific patches live in `patches/<platform>.py`. The `--platform` flag selects which patch script to apply during the build. Each patch script accepts a `--mode` flag (`base` / `shared` / `all`) so the build orchestrator can apply only the symbol-visibility patches before the static build and then layer the BUILD.gn shared-library rewrite on top before the second ninja pass.

```
patches/
  linux.py     # glibc linux shared library patches
  mac.py       # macOS shared library patches (single-phase today)
  musl.py      # musl/Alpine patches + toolchain install
```

Each `patches/<name>.py` script takes the PDFium source directory as its positional argument and an optional `--mode` flag:

| Mode | Effect |
| --- | --- |
| `base` | Everything needed for the static-archive build: `fpdfview.h` visibility patch, plus musl's BUILDCONFIG / highway / toolchain install where applicable |
| `shared` | Rewrites `component("pdfium")` → `shared_library("pdfium")` in `BUILD.gn`, applied after the static ninja pass |
| `all` | Both `base` and `shared` (default — equivalent to running `base` then `shared`) |

The Linux patches apply two changes required to produce a `.so` with exported `FPDF_*` symbols:

1. **BUILD.gn** — changes `component("pdfium")` to `shared_library("pdfium")`. The `component()` macro resolves to `static_library` when `is_component_build=false`, so without this patch the output would be a `.a` archive instead of a `.so`. We exploit this deliberately to get the static archive first, then apply the patch and run ninja a second time to get the shared library.

2. **fpdfview.h** — removes the `#if defined(COMPONENT_BUILD)` guard around `FPDF_EXPORT`. PDFium only applies `__attribute__((visibility("default")))` to its public API when `COMPONENT_BUILD` is defined. Since we set `is_component_build=false` (to get a single `.so` instead of many small ones), `FPDF_EXPORT` resolves to nothing without this patch, and all `FPDF_*` symbols get hidden visibility — making the library unusable via `dlopen`/`dlsym` and unusable when linked statically into a Rust binary that relies on the exported C ABI.

These patches match the approach used by [bblanchon/pdfium-binaries](https://github.com/bblanchon/pdfium-binaries) (`shared_library.patch` + `public_headers.patch`).

The musl patches add three more changes on top: declare an `is_musl` GN arg and route the default toolchain to the musl one in `BUILDCONFIG.gn`, disable `HWY_AVX3_SPR` in `third_party/highway/BUILD.gn` (highway emits broken SIMD on musl x86 otherwise), and install the musl GN toolchain definition at `build/toolchain/linux/musl/BUILD.gn`. All of these are part of `--mode base` and run before the static build.

To add a new platform, create a `patches/<name>.py` script that accepts `--mode base|shared|all`, and add the name to the `PLATFORMS` list in `build_pdfium.py`.

## Testing

Unit tests cover the build script's pure functions without requiring Docker:

```bash
pip install pytest
pytest tests/ -v
```

Tests cover:

| File | What it tests |
| --- | --- |
| `test_formatting.py` | `fmt_time`, `make_bar` output |
| `test_naming.py` | Archive and directory naming convention across linux/musl and amd64/arm64 |
| `test_step_regex.py` | Docker buildkit and ninja step marker parsing |
| `test_dockerfile.py` | Generated Dockerfile content for linux amd64/arm64 and musl amd64/arm64, including the two-phase build structure |
| `test_eta.py` | EMA-based ETA estimation lifecycle |

## Reference

See [`MANUAL.md`](../MANUAL.md) at the repo root for a full man-page-style reference on the build tooling.
