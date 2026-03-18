# PDFium

Pre-compiled PDFium shared libraries for [libviprs](https://github.com/libviprs/libviprs). Built from source and published as GitHub Releases on this repo.

[PDFium](https://pdfium.googlesource.com/pdfium/) is Google's open-source PDF rendering library, used by libviprs to rasterize PDF pages into pixel buffers for tile pyramid generation.

We compile PDFium from source rather than using third-party prebuilt binaries to:
- Pin to a specific chromium branch for reproducibility
- Ensure the binary includes all symbols required by the `pdfium-render` Rust bindings
- Control build configuration (no V8, no XFA, shared library output)

## Download

Archives are available from [Releases](https://github.com/libviprs/libviprs-dep/releases):

```
https://github.com/libviprs/libviprs-dep/releases/download/pdfium-7725/pdfium-linux-x64.tgz
https://github.com/libviprs/libviprs-dep/releases/download/pdfium-7725/pdfium-linux-arm64.tgz
```

Each archive extracts to a self-contained directory:

```bash
tar xzf pdfium-linux-x64.tgz
```

```
pdfium-linux-x64/
  lib/
    libpdfium.so
  include/
    fpdfview.h
    fpdf_annot.h
    ...
  args.gn
  LICENSE
```

To install the shared library:

```bash
sudo cp pdfium-linux-x64/lib/libpdfium.so /usr/local/lib/
sudo ldconfig
```

## Building from source

The `build_pdfium.py` script compiles PDFium inside Docker containers. All builds run on an amd64 host — arm64 binaries are cross-compiled using PDFium's built-in clang and Debian sysroot, avoiding slow QEMU emulation.

The build pipeline (inspired by [bblanchon/pdfium-binaries](https://github.com/bblanchon/pdfium-binaries)):

1. Install system dependencies and depot_tools
2. Configure gclient with `checkout_configuration=small` (skips V8, test deps, cipd)
3. Checkout PDFium source at the target chromium branch
4. Install build dependencies and target architecture sysroot
5. Apply platform patch from `patches/<platform>.py`
6. Configure GN args and generate build files
7. Build with ninja
8. Verify output binary
9. Stage artifacts (`lib/`, `include/`, `args.gn`, `LICENSE`) and package as `pdfium-{platform}-{gn_cpu}.tgz`

### Requirements

- Docker
- Python 3.7+
- `gh` CLI (only for `--upload`)

### Usage

```bash
# Build both architectures for a specific chromium branch
python3 build_pdfium.py 7725

# Build both architectures in parallel
python3 build_pdfium.py 7725 --parallel

# Build a single architecture
python3 build_pdfium.py 7725 --arch amd64
python3 build_pdfium.py 7725 --arch arm64

# Explicit platform (default: linux)
python3 build_pdfium.py 7725 --platform linux

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

Archives are written to `./bin/` by default (gitignored):

```
bin/
  pdfium-linux-x64.tgz
  pdfium-linux-arm64.tgz
```

Archives follow the naming convention `pdfium-{platform}-{gn_cpu}.tgz` and extract to a directory of the same name:

```
pdfium-{platform}-{gn_cpu}/
  lib/libpdfium.so       # shared library
  include/*.h            # public C headers
  args.gn                # GN build arguments used
  LICENSE                # PDFium license
```

When `--upload` is used, a GitHub Release tagged `pdfium-{version}` is created with the archives attached.

### Parallel builds

By default, architectures are built sequentially. Use `--parallel` to build all targets at the same time:

```bash
python3 build_pdfium.py 7725 --parallel
```

Both Docker builds run concurrently in separate threads. The terminal progress header tracks both architectures independently.

During a parallel build, you can switch between each architecture's build output:

| Key | Action |
| --- | --- |
| `Tab` | Cycle to the next architecture's output |
| `1` | Show amd64 output |
| `2` | Show arm64 output |

The active view is shown in the header bar. Each architecture's output is buffered independently, so switching views replays recent output without losing anything.

`--parallel` has no effect when building a single architecture with `--arch`.

### Build time

PDFium is a large C++ project. Expect:
- ~10-20 minutes per architecture
- `--parallel` can cut total wall time roughly in half when building both architectures

### Cross-compilation

arm64 binaries are cross-compiled inside an amd64 Docker container. This works by:
- Installing `g++-aarch64-linux-gnu` for the cross-compiler toolchain
- Running `install-sysroot.py --arch=arm64` to install a Debian arm64 sysroot
- Setting `target_cpu = "arm64"` in GN args

PDFium's build system uses its own bundled clang with the sysroot, producing a native arm64 `.so` without needing QEMU.

## Build configuration

PDFium is compiled with these GN arguments:

| Argument | Value | Reason |
| --- | --- | --- |
| `is_debug` | `false` | Release build |
| `pdf_is_standalone` | `true` | No chromium browser integration |
| `pdf_enable_v8` | `false` | No JavaScript engine needed |
| `pdf_enable_xfa` | `false` | No XFA form support needed |
| `is_component_build` | `false` | Single self-contained `.so` |
| `use_custom_libcxx` | `true` | Bundle libc++ so the `.so` is portable across distros |
| `treat_warnings_as_errors` | `false` | Avoid build failures from upstream warnings |
| `pdf_use_skia` | `false` | Use default rendering backend |
| `pdf_use_partition_alloc` | `false` | Skip complex allocator that fails on some platforms |
| `clang_use_chrome_plugins` | `false` | Skip Chrome's custom clang plugins |
| `target_cpu` | `"x64"` / `"arm64"` | Target architecture |
| `target_os` | `"linux"` | Target operating system |

### Platform patches

Platform-specific patches live in `patches/<platform>.py`. The `--platform` flag selects which patch script to apply during the build (default: `linux`). Each patch script receives the PDFium source directory as its first argument.

```
patches/
  linux.py     # Linux shared library patches
```

The Linux patch applies two changes required to produce a `.so` with exported `FPDF_*` symbols:

1. **BUILD.gn** — changes `component("pdfium")` to `shared_library("pdfium")`. The `component()` macro resolves to `static_library` when `is_component_build=false`, so without this patch the output would be a `.a` archive instead of a `.so`.

2. **fpdfview.h** — removes the `#if defined(COMPONENT_BUILD)` guard around `FPDF_EXPORT`. PDFium only applies `__attribute__((visibility("default")))` to its public API when `COMPONENT_BUILD` is defined. Since we set `is_component_build=false` (to get a single `.so` instead of many small ones), `FPDF_EXPORT` resolves to nothing without this patch, and all `FPDF_*` symbols get hidden visibility — making the library unusable via `dlopen`/`dlsym`.

These patches match the approach used by [bblanchon/pdfium-binaries](https://github.com/bblanchon/pdfium-binaries) (`shared_library.patch` + `public_headers.patch`).

To add a new platform, create a `patches/<name>.py` script and add the name to the `PLATFORMS` list in `build_pdfium.py`.

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
| `test_naming.py` | Archive and directory naming convention |
| `test_step_regex.py` | Docker buildkit and ninja step marker parsing |
| `test_dockerfile.py` | Generated Dockerfile content for amd64 and arm64 |
| `test_eta.py` | EMA-based ETA estimation lifecycle |
